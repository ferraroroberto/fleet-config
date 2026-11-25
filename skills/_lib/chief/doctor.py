"""`chief_ops.py doctor`: a read-only check of this machine's chief setup.

It runs the same way on every computer and prints one ``CHECK <area>.<name>=<status> ...``
line per fact, then ``DOCTOR=pass|fail|unknown fails=<n> unknowns=<n>``:

* install: the checkout's commit and how far it is behind ``origin/main`` (as of the last
  fetch: doctor never fetches), the ``.venv`` interpreter, the live ``~/.claude/hooks`` and
  ``~/.claude/skills`` links resolving to this checkout, and every hook and statusline
  command in ``~/.claude/settings.json`` (a read-only parse) pointing at files that exist,
  written with forward slashes;
* verbs: each read verb the chief uses runs as a child, with its expected line and its time;
* mailbox: pending and claimed-but-unacked events with the oldest age of each, whether the
  launcher's doorbell switch agrees with ``wake``, and the waiter heartbeats;
* logs: errors and tracebacks of the last 24 hours (``doctor_logs.py``).

A check that cannot establish its fact is ``unknown``, never ``pass``. Doctor writes
nothing: no git mutation, no restart, no delete, no message. Every printed line passes
:class:`Redactor`, so the real home folder, user name, host name and absolute paths never
reach the output. The line shape matches app-launcher-lite's doctor, so one report
template reads both.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from fleet_core import hooks_state
from git_run import run_git
from no_window import NO_WINDOW

from . import doctor_logs, launcher, mailbox, rails, timings, waiter_heartbeat

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
CHIEF_OPS = Path(__file__).resolve().parents[1] / "chief_ops.py"
# The checkout this code sits in: skills/_lib/chief/doctor.py.
DEFAULT_CHECKOUT = Path(__file__).resolve().parents[3]
LAUNCHER_REPO = "app-launcher"
# A read verb that needs longer than this is itself the finding (the chief's own calls time out sooner).
VERB_TIMEOUT_S = 90
GIT_TIMEOUT_S = 30
# A pending event older than the doorbell's fallback waiter was not answered by either wake path.
PENDING_STALE_S = mailbox.FALLBACK_WAITER_SECONDS
LOG_GROUPS_SHOWN = 10
LINE_CHARS = 140
# An absolute Windows path in a settings.json command (forward or back slashes).
_ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
# A launcher session id: a report may be shared, and an id ties it to one person's sessions.
_SESSION_ID = re.compile(r"\b[0-9a-f]{32}\b")
_HOOK_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Runner = Callable[[Sequence[str], float], Tuple[int, str]]


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: Dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        parts = [f"CHECK {self.area}.{self.name}={self.status}"]
        for key, value in self.detail.items():
            text = str(value).replace('"', "'")
            parts.append(f'{key}="{text}"' if (" " in text or not text) else f"{key}={text}")
        return " ".join(parts)


class Redactor:
    """Masks named paths, the user name, the host name, then any absolute path left over."""

    def __init__(self, paths: Sequence[Tuple[str, str]], user: str, host: str) -> None:
        self.rules: List[Tuple[re.Pattern, str]] = []
        # Longest first, so the checkout under the home folder reads <checkout>, not <home>\...
        for value, mask in sorted(((v, m) for v, m in paths if v), key=lambda vm: -len(vm[0])):
            value = value.rstrip("\\/")
            variants = {value, value.replace("\\", "/"), value.replace("\\", "\\\\"), value.replace("/", "\\")}
            for variant in sorted(variants, key=len, reverse=True):
                self.rules.append((re.compile(re.escape(variant), re.IGNORECASE), mask))
        for value, mask in ((user, "<user>"), (host, "<host>")):
            if value:
                self.rules.append((re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])",
                                              re.IGNORECASE), mask))

    def __call__(self, text: str) -> str:
        for pattern, mask in self.rules:
            text = pattern.sub(mask, text)
        text = _SESSION_ID.sub("<sid>", text)
        # A path no rule named (another drive, a junction's other spelling) keeps only its last part.
        return _ABS_PATH.sub(lambda m: "<path>/" + re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1], text)

    @classmethod
    def for_machine(cls, checkout: Path) -> "Redactor":
        home = os.environ.get("USERPROFILE") or str(Path.home())
        try:
            user = getpass.getuser()
        except (OSError, KeyError, ImportError):
            user = os.environ.get("USERNAME", "")
        host = os.environ.get("COMPUTERNAME") or socket.gethostname()
        paths = []
        for value, mask in ((checkout, "<checkout>"), (checkout.parent, "<fleet-root>")):
            paths += [(str(value), mask), (os.path.realpath(value), mask)]
        return cls(paths + [(home, "<home>")], user, host)


def run_child(argv: Sequence[str], timeout: float) -> Tuple[int, str]:
    """Exit code and stdout of one read-only child; raises on timeout or a spawn failure."""
    proc = subprocess.run(  # noqa: S603 -- argv built here from this checkout's own paths
        list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    return proc.returncode, proc.stdout


def _short(text: str) -> str:
    return text.strip().splitlines()[0][:LINE_CHARS] if text.strip() else ""


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def settings_commands(settings: object) -> List[str]:
    """Every hook command and the statusline command in a parsed ``settings.json``."""
    commands: List[str] = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for entries in (hooks.values() if isinstance(hooks, dict) else []):
        for entry in entries if isinstance(entries, list) else []:
            for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
    status = settings.get("statusLine") if isinstance(settings, dict) else None
    if isinstance(status, dict) and isinstance(status.get("command"), str):
        commands.append(status["command"])
    return commands


def command_targets(command: str) -> List[str]:
    """The files one command needs: its absolute paths, plus the module each hook name loads."""
    tokens = command.split()
    targets = [m.group(0) for m in _ABS_PATH.finditer(command)]
    for i, token in enumerate(tokens):
        if token == "-Hook" and i + 1 < len(tokens):
            shim = next((t for t in targets if t.endswith("run-hook.ps1")), None)
            if shim:
                targets.append(str(Path(shim).parent / f"{tokens[i + 1]}.py"))
        if token.endswith("hook_dispatch.py"):
            folder = Path(token).parent
            targets += [str(folder / f"{name}.py") for name in tokens[i + 1:] if _HOOK_NAME.match(name)]
    return targets


class Doctor:
    """One read-only run over a checkout and the Claude home it should be installed into."""

    def __init__(self, checkout: Path, claude_home: Path, runner: Runner = run_child,
                 base_url: str = launcher.DEFAULT_BASE_URL, launcher_dir: Optional[Path] = None,
                 launcher_reason: str = "") -> None:
        self.checkout = checkout
        self.home = claude_home
        self.run = runner
        self.base_url = base_url
        self.launcher_dir = launcher_dir
        self.launcher_reason = launcher_reason
        self.verb_lines: Dict[str, str] = {}

    # --- install ---------------------------------------------------------------------------

    def check_commit(self) -> Check:
        head = run_git(["-C", str(self.checkout), "rev-parse", "--short", "HEAD"], timeout=GIT_TIMEOUT_S)
        if head.returncode != 0:
            return Check("install", "commit", UNKNOWN, {"reason": _short(head.stderr) or "git rev-parse failed"})
        detail = {"sha": head.stdout.strip()}
        behind = run_git(["-C", str(self.checkout), "rev-list", "--count", "HEAD..origin/main"], timeout=GIT_TIMEOUT_S)
        if behind.returncode != 0 or not behind.stdout.strip().isdigit():
            detail["behind"] = "unknown"
            return Check("install", "commit", UNKNOWN, detail)
        detail.update(behind=behind.stdout.strip(), as_of="last-fetch")
        if detail["behind"] != "0":
            detail["fix"] = "fast-forward the checkout"
        return Check("install", "commit", PASS if detail["behind"] == "0" else FAIL, detail)

    def check_venv(self) -> Check:
        for candidate in (self.checkout / ".venv" / "Scripts" / "python.exe", self.checkout / ".venv" / "bin" / "python"):
            if candidate.is_file():
                return Check("install", "venv", PASS, {"python": str(candidate)})
        return Check("install", "venv", FAIL, {"reason": "no .venv interpreter in the checkout"})

    def check_link(self, name: str) -> Check:
        live, expected = self.home / name, self.checkout / name
        if not os.path.lexists(live):
            return Check("install", f"link:{name}", FAIL, {"reason": "missing", "fix": "run install.ps1"})
        try:
            if not live.is_dir():
                return Check("install", f"link:{name}", FAIL, {"reason": "broken link", "fix": "run install.ps1"})
            same = _same_path(str(live), str(expected))
        except OSError as exc:
            return Check("install", f"link:{name}", UNKNOWN, {"reason": f"{type(exc).__name__}"})
        if same:
            return Check("install", f"link:{name}", PASS, {"target": "<checkout>"})
        return Check("install", f"link:{name}", FAIL, {"reason": "resolves to another checkout",
                                                        "target": os.path.realpath(live), "fix": "run install.ps1"})

    def check_settings(self) -> List[Check]:
        path = self.home / "settings.json"
        try:
            commands = settings_commands(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return [Check("install", "settings", FAIL, {"reason": "no settings.json", "fix": "run install.ps1"})]
        except (OSError, ValueError) as exc:
            reason = {"reason": f"unreadable: {type(exc).__name__}"}
            return [Check("install", "settings.targets", UNKNOWN, reason),
                    Check("install", "settings.slashes", UNKNOWN, dict(reason))]
        if not commands:
            return [Check("install", "settings.targets", FAIL, {"reason": "no hook command"})]
        targets = sorted({t for c in commands for t in command_targets(c)})
        missing = [t for t in targets if not os.path.exists(t)]
        backslashed = sum("\\" in c for c in commands)
        target_check = (Check("install", "settings.targets", FAIL, {"missing": ",".join(missing)}) if missing
                        else Check("install", "settings.targets", PASS,
                                   {"commands": str(len(commands)), "files": str(len(targets))}))
        slash_check = (Check("install", "settings.slashes", FAIL,
                             {"backslashed": str(backslashed), "reason": "Git Bash strips backslashes"}) if backslashed
                       else Check("install", "settings.slashes", PASS, {"commands": str(len(commands))}))
        return [target_check, slash_check]

    # --- verbs --------------------------------------------------------------------------------

    @staticmethod
    def verb_specs() -> List[Tuple[str, List[str], Callable[[str], Tuple[str, str]]]]:
        def key(prefix: str, unknown: str = "=unknown") -> Callable[[str], Tuple[str, str]]:
            def judge(out: str) -> Tuple[str, str]:
                line = next((ln for ln in out.splitlines() if ln.startswith(prefix)), "")
                if not line:
                    return FAIL, f"no {prefix.strip()} line"
                return (UNKNOWN if unknown in line else PASS), line
            return judge

        def is_json(out: str) -> Tuple[str, str]:
            try:
                json.loads(out)
            except ValueError:
                return FAIL, "not JSON"
            return PASS, "json"

        def owner_items(out: str) -> Tuple[str, str]:
            line = next((ln for ln in out.splitlines() if ln.startswith("OWNER_ITEMS=")), "")
            if not line:
                return FAIL, "no OWNER_ITEMS line"
            unknowns = re.search(r"UNKNOWN=(\d+)", line)
            return (UNKNOWN if unknowns and unknowns.group(1) != "0" else PASS), line

        return [
            ("wake", ["wake"], key("WAKE=", "doorbell=unknown")),
            ("inbox-count", ["inbox", "--count"], key("PENDING=")),
            ("board", ["board", "--json"], is_json),
            ("sessions", ["sessions", "--json"], is_json),
            ("chief-sid", ["chief-sid"], key("CHIEF_SID=")),
            ("plan-show", ["plan", "show"], is_json),
            ("slots-idle", ["slots-idle"], key("SLOTS_IDLE=")),
            ("sweep", ["sweep"], key("SUMMARY ")),
            ("owner-checks", ["owner-checks"], owner_items),
        ]

    def check_verbs(self) -> List[Check]:
        checks = []
        for name, argv, judge in self.verb_specs():
            started = time.monotonic()
            try:
                code, out = self.run([sys.executable, str(CHIEF_OPS), *argv], VERB_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                checks.append(Check("verb", name, UNKNOWN, {"reason": f"timed out after {VERB_TIMEOUT_S}s"}))
                continue
            except (OSError, subprocess.SubprocessError) as exc:
                checks.append(Check("verb", name, UNKNOWN, {"reason": f"did not run: {type(exc).__name__}"}))
                continue
            ms = str(int((time.monotonic() - started) * 1000))
            if re.search(r"^(?:UNREACHABLE|UNCONFIRMED)=", out, re.MULTILINE):
                checks.append(Check("verb", name, UNKNOWN, {"ms": ms, "reason": "launcher did not answer"}))
                continue
            status, line = judge(out)
            self.verb_lines[name] = line
            checks.append(Check("verb", name, status, {"ms": ms, "exit": str(code), "line": line[:LINE_CHARS]}))
        return checks

    # --- mailbox ------------------------------------------------------------------------------

    def check_mailbox(self, stale_after: float = mailbox.CLAIM_STALE_SECONDS) -> Check:
        """`stale_after` is the launcher's claim re-offer window (the default when it cannot be read)."""
        inbox = mailbox.chief_inbox_dir()
        now_ms = mailbox._now_ms()
        try:
            pending = mailbox._event_files(inbox)
            claimed = mailbox._event_files(inbox / mailbox.CLAIMED_DIRNAME)
        except OSError as exc:
            return Check("mailbox", "events", UNKNOWN, {"reason": f"unreadable: {type(exc).__name__}"})

        def oldest(paths: List[Path]) -> Optional[int]:
            known = [s for s in (mailbox._split_event_name(p.name)[0] for p in paths) if s is not None]
            return (now_ms - min(known)) // 1000 if known else None

        old_pending, old_claimed = oldest(pending), oldest(claimed)
        detail = {"pending": str(len(pending)), "oldest_pending_s": str(old_pending if old_pending is not None else "-"),
                  "claimed": str(len(claimed)), "oldest_claimed_s": str(old_claimed if old_claimed is not None else "-")}
        if not inbox.is_dir():
            detail["note"] = "no mailbox folder yet"
        problems = []
        if old_pending is not None and old_pending > PENDING_STALE_S:
            problems.append("an event waited past the fallback waiter")
        if old_claimed is not None and old_claimed > stale_after:
            problems.append("a claim stayed unacked past the stale window")
        if problems:
            detail["reason"] = "; ".join(problems)
        return Check("mailbox", "events", FAIL if problems else PASS, detail)

    def check_doorbell(self) -> List[Check]:
        try:
            config = launcher._request(self.base_url, "/api/config")
        except urllib.error.HTTPError as exc:
            return [Check("mailbox", "doorbell", UNKNOWN, {"reason": f"launcher config HTTP {exc.code}"})]
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            return [Check("launcher", "reachable", FAIL, {"reason": f"{type(exc).__name__}"}),
                    Check("mailbox", "doorbell", UNKNOWN, {"reason": "launcher unreachable"})]
        value = config.get("chief_doorbell") if isinstance(config, dict) else None
        reachable = Check("launcher", "reachable", PASS, {})
        if not isinstance(value, bool):
            return [reachable, Check("mailbox", "doorbell", UNKNOWN, {"reason": "no boolean chief_doorbell"})]
        setting = "on" if value else "off"
        said = re.search(r"doorbell=(\w+)", self.verb_lines.get("wake", ""))
        if not said or said.group(1) == "unknown":
            return [reachable, Check("mailbox", "doorbell", UNKNOWN, {"setting": setting, "reason": "wake gave no answer"})]
        return [reachable, Check("mailbox", "doorbell", PASS if said.group(1) == setting else FAIL,
                                 {"setting": setting, "wake": said.group(1)})]

    def check_waiters(self, now: Optional[datetime] = None) -> Check:
        """Armed and expired waiter heartbeats, read the way `hooks/chief_waiter_stop.py` trusts them."""
        moment = now or datetime.now(timezone.utc)
        folder = waiter_heartbeat.waiters_dir()
        try:
            files = sorted(folder.glob("*.json")) if folder.is_dir() else []
        except OSError as exc:
            return Check("mailbox", "waiters", UNKNOWN, {"reason": f"unreadable folder: {type(exc).__name__}"})
        armed = expired = corrupt = 0
        for path in files:
            try:
                end = json.loads(path.read_text(encoding="utf-8"))["expected_end"]
                stamp = datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (OSError, ValueError, KeyError, TypeError):
                # The Stop hook never trusts a file it cannot parse, so it counts as not armed.
                corrupt += 1
                continue
            if stamp > moment:
                armed += 1
            else:
                expired += 1
        detail = {"armed": str(armed), "expired": str(expired), "corrupt": str(corrupt)}
        if corrupt:
            detail["note"] = "a corrupt heartbeat never counts as armed"
        return Check("mailbox", "waiters", PASS, detail)

    # --- logs ---------------------------------------------------------------------------------

    def check_logs(self) -> Tuple[List[Check], List[doctor_logs.Group]]:
        checks: List[Check] = []
        groups: List[doctor_logs.Group] = []
        sources = [("hooks", hooks_state.state_dir())]
        if self.launcher_dir is None:
            checks.append(Check("logs", "launcher", UNKNOWN, {"reason": self.launcher_reason or "no launcher checkout"}))
        else:
            sources.insert(0, ("launcher", self.launcher_dir / "webapp"))
        for name, folder in sources:
            try:
                found = doctor_logs.scan_logs(folder, name, datetime.now())
            except OSError as exc:
                checks.append(Check("logs", name, UNKNOWN, {"reason": f"unreadable: {type(exc).__name__}"}))
                continue
            groups += found
            failures = sum(g.count for g in found if g.severity == doctor_logs.FAIL)
            notes = sum(g.count for g in found if g.severity == doctor_logs.NOTE)
            checks.append(Check("logs", name, FAIL if failures else PASS,
                                {"failures": str(failures), "notes": str(notes), "window": "24h"}))
        return checks, groups

    # --- the run ------------------------------------------------------------------------------

    def run_all(self) -> Tuple[List[Check], List[doctor_logs.Group]]:
        checks = [self.check_commit(), self.check_venv(), self.check_link("hooks"), self.check_link("skills")]
        checks += self.check_settings()
        checks += self.check_verbs()
        checks.append(self.check_mailbox(timings.fetch(self.base_url)["chief_reoffer_minutes"].seconds))
        checks += self.check_doorbell()
        checks.append(self.check_waiters())
        log_checks, groups = self.check_logs()
        return checks + log_checks, groups


def verdict(checks: List[Check]) -> str:
    fails = sum(c.status == FAIL for c in checks)
    unknowns = sum(c.status == UNKNOWN for c in checks)
    overall = FAIL if fails else UNKNOWN if unknowns else PASS
    return f"DOCTOR={overall} fails={fails} unknowns={unknowns}"


def render(checks: List[Check], groups: List[doctor_logs.Group], redact: Callable[[str], str]) -> List[str]:
    lines = [redact(c.line()) for c in checks]
    shown = sorted(groups, key=lambda g: (g.severity != doctor_logs.FAIL, -g.count))[:LOG_GROUPS_SHOWN]
    for g in shown:
        message = g.message.replace('"', "'")
        lines.append(redact(f'LOG source={g.source} kind={g.kind} severity={g.severity} count={g.count} '
                            f'last={g.last.strftime("%Y-%m-%dT%H:%M")} msg="{message}"'))
    if len(groups) > len(shown):
        lines.append(f"LOG_GROUPS_NOT_SHOWN={len(groups) - len(shown)}")
    lines.append(verdict(checks))
    return lines


def _launcher_dir() -> Tuple[Optional[Path], str]:
    """The app-launcher checkout from the fleet registry, or ``(None, why)``."""
    try:
        return rails.resolve_repo_path(LAUNCHER_REPO), ""
    except OSError as exc:
        return None, f"fleet registry unreadable: {type(exc).__name__}"
    except ValueError:
        return None, f"{LAUNCHER_REPO} not in the fleet registry"


def cmd_doctor(args: argparse.Namespace) -> int:
    checkout = Path(args.checkout).resolve() if args.checkout else DEFAULT_CHECKOUT
    launcher_dir, reason = _launcher_dir()
    doctor = Doctor(checkout, Path.home() / ".claude", base_url=args.base_url,
                    launcher_dir=launcher_dir, launcher_reason=reason)
    checks, groups = doctor.run_all()
    lines = render(checks, groups, Redactor.for_machine(checkout))
    print("\n".join(lines))
    return 0 if lines[-1].startswith(f"DOCTOR={PASS} ") else 1


def add_parser(sub: argparse._SubParsersAction) -> None:
    dr = sub.add_parser("doctor", help="read-only check of this machine's chief setup (KEY=value lines)")
    dr.add_argument("--checkout", default=None, help="the checkout the live install should match (default: this one)")
    dr.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    dr.set_defaults(func=cmd_doctor)
