"""Shared helpers for the fleet-config hooks.

Every hook in this directory:

* Reads a single JSON payload from stdin (Claude Code's hook contract).
* Returns exit code 0 to allow the action.
* Returns exit code 2 with a one-line reason on **stderr** to block the action
  (Claude sees the stderr and adjusts).
* Or returns exit code 0 with a single-line nudge on **stdout** to advise
  without blocking.

Use the helpers below so each hook stays a few dozen lines of pure rule logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for older Pythons
    import tomli as tomllib  # type: ignore[no-redef]


HOOKS_DIR = Path(__file__).resolve().parent
PROJECTS_TOML = HOOKS_DIR / "projects.toml"
PROJECTS_TOML_ENV_VAR = "CLAUDE_HOOKS_PROJECTS_TOML"

logger = logging.getLogger("fleet_hooks")


# ------------------------------------------------------- credential patterns

# The one definition of "what a live credential looks like" for this tier
# (fleet-config#561). Two independent copies used to exist — `context_filter`'s
# four-family redaction regex and `secret_scan_guard`'s one-family commit
# blocker — and the *narrower* one was the copy wired into the guard that
# actually refuses a commit. So the guard blocked a leaked Slack bot token and
# waved through an OpenAI key, a GitHub PAT, and an AWS access key id. Both now
# read from here, so extending coverage is a one-line change in one place.
#
# `\b` anchors every pattern: without them `sk-` matches inside `risk-…` and
# `gh?_` inside `highp_…`, which is tolerable for a redactor (a false positive
# just redacts a word) but not for a guard that refuses `git commit`. Verified
# against every tracked file in every repo under `E:/automation`: zero matches.
#
# Deliberately live-shaped, not prefix-shaped, so this repo's own docs — which
# legitimately carry the placeholder forms `xoxb-…` and `xoxb-<token>` — never
# trip the guard. A real token has a long secret body; the placeholders don't.
#
# The Slack pattern requires the **three** hyphen-separated groups every real
# Slack token carries (`xoxb-<team>-<bot>-<secret>`, `xoxp-`/`xoxa-` likewise),
# rather than a bare "prefix plus 16 characters". Interior groups are `+` — an
# `xoxa-` app token's second group is a single digit — but the trailing secret
# must be 8+. Without the three-group requirement a *test fixture* naming a
# plausible-looking fake token becomes uncommittable, which is how this pattern
# first blocked its own repo (fleet-config#561).
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Slack token (xox…-)", r"\bxox[baprs]-[A-Za-z0-9]+-[A-Za-z0-9]+-[A-Za-z0-9]{8,}"),
    ("API key (sk-)", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("GitHub token (gh?_)", r"\bgh[pousr]_[A-Za-z0-9_]{20,}"),
    ("AWS access key id (AKIA…)", r"\bAKIA[0-9A-Z]{16}"),
)

# The same tuple as one alternation, for redaction (`SECRET_RE.sub(...)`) and
# for "does this output look secret-bearing" tests.
SECRET_RE = re.compile("(" + "|".join(pattern for _, pattern in SECRET_PATTERNS) + ")")


def scan_for_secret(text: str) -> Optional["tuple[str, str]"]:
    """Return ``(label, pattern)`` of the first credential found in ``text``, else ``None``."""
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            return label, pattern
    return None


# ------------------------------------------------------- subprocess spawning

# Pass this as `creationflags=` on **every** subprocess spawn in this directory,
# per the global CLAUDE.md convention "Subprocess spawns must suppress the
# console window (Windows)" (fleet-config#399): a parent with no console of its
# own — pythonw, a tray app, a scheduled task, a daemon — otherwise gets a
# console window flashed on screen for each spawn. Hooks fire under exactly such
# parents, including the headless `claude -p` of every scheduled fleet job.
#
# `subprocess.CREATE_NO_WINDOW` is Windows-only; the conditional expression
# evaluates the platform test first, so the attribute is never touched on POSIX.
# The skill tier keeps its own copy in `skills/_lib/no_window.py` (the two trees
# are junctioned into the agent homes independently, and a hook must stay
# importable with nothing but its own directory on `sys.path`);
# `tests/run_acceptance.py` asserts the two agree. Never combine this with
# `DETACHED_PROCESS` — mutually exclusive (`local-llm-hub`#282).
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ------------------------------------------------------- argv text repair

# Free text that reaches a hook as a **command-line argument** has crossed the
# harness → shell → CreateProcess boundary, and on Windows that boundary is not
# UTF-8 safe end to end. Reproduced on this host (fleet-config#507): a BOM-less
# UTF-8 command handed to Windows PowerShell 5.1 is decoded with the ANSI
# codepage, so the two UTF-8 bytes of `·` (0xC2 0xB7) arrive as the two
# characters `Â·`; a further narrowing to an OEM codepage that has neither turns
# the pair into `??`, which is what landed in Slack.
#
# Two prior instances of the same class already carry fixes on adjacent paths —
# `notify_complete.gh_json` (gh stdout forced to UTF-8) and
# `slack_notify._read_text` (piped stdin forced to UTF-8). Those cover *byte*
# streams we own. This covers the argv leg, which we do not own: the only two
# defences are (a) repair the recoverable half here, and (b) never author
# non-ASCII punctuation into an argv string in the first place — skills spell the
# separator with the ASCII token instead (see `notify_complete.normalize_summary`).
_MOJIBAKE_MARKERS = ("Â", "Ã", "â€", "Å", "Ë", "Ð", "ð\x9f")


def repair_mojibake(text: Optional[str]) -> Optional[str]:
    """Undo a UTF-8-bytes-decoded-as-cp1252 round trip (``"Â·"`` → ``"·"``).

    Only rewrites text that both *looks* mojibake-encoded (carries one of the
    telltale Latin-1 lead characters) and survives the round trip cleanly, so
    genuine accented prose — where the cp1252 re-encode produces bytes that are
    not valid UTF-8 — is returned untouched. ASCII and ``None`` short-circuit.

    Irrecoverable by design: once the boundary has replaced a character with
    ``?`` the original codepoint is gone, which is why the ASCII separator token
    exists alongside this repair rather than instead of it.
    """
    if not text or text.isascii():
        return text
    if not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired


# ------------------------------------------- foreign-harness payload normalization

# Grok Build (xAI's CLI) scans `~/.claude/settings.json` for hooks by default
# (`[compat.claude] hooks = true`), so every hook in this directory already runs
# inside a Grok session — verified live against grok 0.2.114: its debug log
# reports `hooks: loaded from global source source=...settings.json count=19`
# and then actually executes them. But Grok's stdin envelope is **camelCase**
# (`hookEventName` / `toolName` / `toolInput`) where Claude Code's is snake_case,
# and its event *values* are lower_snake (`pre_tool_use`) where Claude's are
# PascalCase (`PreToolUse`). The result was a double mismatch that made the hooks
# fire and silently do nothing: 6 of 7 guards A/B-tested blocked under a Claude
# payload and allowed the identical dangerous command under a Grok one, while
# still looking healthy in `/hooks` (fleet-config#491).
#
# Normalizing here — the one entry point every hook already routes through —
# fixes all of them at once, rather than duplicating a translation in each hook
# or shipping a parallel Grok adapter per hook. A payload that is already in
# Claude shape is returned **unchanged and identical** (same object), so this is
# a strict pass-through for Claude Code and cannot alter existing behaviour.

AGENT_HINT_KEY = "_fleet_agent"
SHELL_AMBIGUOUS_KEY = "_fleet_shell_ambiguous"

# Grok exposes exactly one shell tool, which can run PowerShell *or* bash syntax
# (nothing stops it invoking `bash -c`). Claude Code splits `Bash` and
# `PowerShell` into separate tools, and `safe_kill_guard` relies on that split to
# avoid false-positiving on an `echo` of the other shell's kill string. Rather
# than guess a shell we cannot observe, normalization flags the ambiguity and the
# one guard that discriminates widens to both rule sets — per the fleet rule that
# a check which cannot establish a fact must say so rather than fold it into the
# passing state.
_SHELL_AGNOSTIC_TOOLS = {"run_terminal_command", "run_terminal_cmd"}

# Grok's lower_snake event values → Claude Code's PascalCase names.
_GROK_EVENTS = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "permission_denied": "PermissionDenied",
    "notification": "Notification",
    "stop": "Stop",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "session_end": "SessionEnd",
}

# Grok's internal tool ids → the Claude tool names every matcher and guard here
# is written against. Grok's own `matcher` aliasing goes the other way (it maps
# `Bash` → `run_terminal_command` so a Claude matcher fires); this completes the
# round trip so the hook *body* sees the name it expects.
#
# `search_replace` → `Write`, not `Edit`, and the choice is load-bearing. Grok
# collapses Claude's `Edit`/`Write`/`MultiEdit` into that one tool, so any single
# mapping loses a distinction some guard may rely on. Surveying the actual call
# sites decides it: four hooks accept the whole `{Edit, Write, MultiEdit}` family
# (`py_syntax_check`, `hub_bypass_warn`, `browser_stealth_lint`,
# `branch_before_edit_guard`), exactly one demands a specific member —
# `docs_dated_filename_guard`, which requires `Write` — and **none** requires
# `Edit`. So `Write` satisfies all five and `Edit` would silently disarm the
# dated-docs guard under Grok.
_GROK_TOOLS = {
    "run_terminal_command": "Bash",
    "run_terminal_cmd": "Bash",
    "read_file": "Read",
    "search_replace": "Write",
    "grep": "Grep",
    "list_dir": "Glob",
    "web_search": "WebSearch",
    "spawn_subagent": "Task",
}

# camelCase → snake_case for the envelope fields hooks here actually read.
# Anything else falls through the generic converter below, so a field xAI adds
# later still arrives under a predictable name instead of vanishing.
_GROK_KEYS = {
    "hookEventName": "hook_event_name",
    "sessionId": "session_id",
    "toolName": "tool_name",
    "toolInput": "tool_input",
    "toolResult": "tool_response",
    "toolUseId": "tool_use_id",
    "transcriptPath": "transcript_path",
    "workspaceRoot": "workspace_root",
    "permissionMode": "permission_mode",
    "stopHookActive": "stop_hook_active",
    "lastAssistantMessage": "last_assistant_message",
}

# Copilot CLI tool ids → Claude tool names (fleet-config#547). Unlike Codex
# and agy, Copilot's toolName truthfully names the executing shell (verified
# live on 1.0.77: toolName "powershell" ran PowerShell), so no shell-ambiguity
# marker and no platform override are needed downstream.
_COPILOT_TOOLS = {
    "powershell": "PowerShell",
    "bash": "Bash",
    "shell": "Bash",
    "sh": "Bash",
}

# Antigravity's `agy` CLI tool ids → Claude tool names (fleet-config#546).
# `run_command` is shell-agnostic like Grok's `run_terminal_command`; the live
# probe proved agy executes CommandLine under PowerShell on Windows, but that
# is the *hook body's* platform decision (see context_filter_hook), not a
# payload fact, so the map stays shell-neutral here.
_AGY_TOOLS = {
    "run_command": "Bash",
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

# The harness whose payload this process is currently handling, set by
# `normalize_payload()`. A hook is a one-payload, one-shot process, so a module
# global is the whole lifetime — this exists so `block()` can speak the calling
# harness's refusal dialect without threading the payload through the ~40
# `_lib.block(...)` call sites across the hooks directory.
_ACTIVE_AGENT: Optional[str] = None


def _camel_to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a foreign-harness hook payload into Claude Code's shape.

    Claude Code payloads (and the Pi adapter's ``{"event": ...}`` envelope) are
    returned **unchanged** — same object, no copy — so this is a no-op for every
    caller that existed before fleet-config#491. Only a payload carrying Grok's
    ``hookEventName`` key is rewritten; that key is the reliable tell, since
    Grok sends it on every event and Claude Code never does.

    The rewritten payload also carries an :data:`AGENT_HINT_KEY` entry naming
    the originating harness, so :mod:`session_state` can attribute the row to
    ``grok`` instead of silently defaulting to ``claude`` (the latent
    mis-attribution the compat shim would otherwise cause).
    """
    global _ACTIVE_AGENT

    if not isinstance(payload, dict):
        return payload

    # Antigravity's `agy` CLI (fleet-config#546): its PreToolUse payload is
    # `{"toolCall": {"name", "args": {"CommandLine", "Cwd", ...}},
    #   "conversationId", "stepIdx", ...}` — the `toolCall` envelope is the
    # reliable tell (Claude and Grok never send it; verified live against
    # agy 1.1.8). Translated here, once, same contract as the Grok branch.
    # Copilot CLI (fleet-config#547): camelCase envelope with NO event name —
    # `{"sessionId", "timestamp", "cwd", "toolName", "toolArgs": "<JSON string>"}`
    # (verified live on 1.0.77). The string-typed `toolArgs` beside `toolName`
    # is the tell: Claude sends tool_input as an object, Grok sends
    # hookEventName, agy sends toolCall. The full parsed args dict is kept in
    # tool_input because Copilot's modifiedArgs response replaces the WHOLE
    # args object — a hook that rewrites `command` must echo the other keys.
    if "toolArgs" in payload and isinstance(payload.get("toolArgs"), str) and "hookEventName" not in payload:
        _ACTIVE_AGENT = "copilot"
        try:
            parsed_args = json.loads(payload.get("toolArgs") or "{}")
        except (json.JSONDecodeError, TypeError):
            parsed_args = {}
        if not isinstance(parsed_args, dict):
            parsed_args = {}
        raw_tool = str(payload.get("toolName") or "").lower()
        out = {
            "hook_event_name": "PreToolUse",
            "session_id": payload.get("sessionId"),
            "cwd": payload.get("cwd") or "",
            "tool_name": _COPILOT_TOOLS.get(raw_tool, payload.get("toolName") or ""),
            "tool_input": parsed_args,
            AGENT_HINT_KEY: "copilot",
        }
        return out

    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        _ACTIVE_AGENT = "antigravity"
        args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        raw_tool = str(tool_call.get("name") or "")
        out = {
            "hook_event_name": "PreToolUse",
            "session_id": payload.get("conversationId"),
            "transcript_path": payload.get("transcriptPath"),
            "tool_name": _AGY_TOOLS.get(raw_tool, raw_tool),
            "tool_input": {"command": args.get("CommandLine") or ""},
            "cwd": args.get("Cwd") or "",
            AGENT_HINT_KEY: "antigravity",
        }
        if raw_tool in _AGY_TOOLS:
            out[SHELL_AMBIGUOUS_KEY] = True
        return out

    if "hookEventName" not in payload:
        return payload

    _ACTIVE_AGENT = "grok"
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        out[_GROK_KEYS.get(key) or _camel_to_snake(key)] = value

    event = str(out.get("hook_event_name") or "")
    # Grok fires a second, observe-only Stop at session end (`reason` is
    # "channel_closed"/"shutdown", not "end_turn") *after* SessionEnd has already
    # fired. Mapping that to Claude's `Stop` would resurrect the row SessionEnd
    # just deleted, stranding a dead session on the Board as `needs-you` until
    # the 24h prune. It has no Claude equivalent, so it maps to a name no hook
    # matches and stays inert. (grok docs, user-guide/10-hooks.md.)
    reason = out.get("reason")
    if event == "stop" and isinstance(reason, str) and reason and reason != "end_turn":
        out["hook_event_name"] = "StopAtSessionEnd"
    else:
        out["hook_event_name"] = _GROK_EVENTS.get(event, event)

    raw_tool = out.get("tool_name")
    if isinstance(raw_tool, str) and raw_tool:
        out["tool_name"] = _GROK_TOOLS.get(raw_tool, raw_tool)
        if raw_tool in _SHELL_AGNOSTIC_TOOLS:
            out[SHELL_AMBIGUOUS_KEY] = True

    out[AGENT_HINT_KEY] = "grok"
    return out


def payload_agent(payload: Dict[str, Any]) -> Optional[str]:
    """The harness that produced this payload, when normalization identified one."""
    hint = payload.get(AGENT_HINT_KEY)
    return hint if isinstance(hint, str) and hint else None


def shell_is_ambiguous(payload: Dict[str, Any]) -> bool:
    """True when the payload's shell tool could be running either PowerShell or
    bash, so a shell-discriminating rule must apply both sets rather than pick."""
    return payload.get(SHELL_AMBIGUOUS_KEY) is True


# --------------------------------------------------------------------------- I/O


def read_stdin_json() -> Dict[str, Any]:
    """Read the hook payload from stdin and return it as a dict.

    Returns an empty dict if stdin is empty or unparseable — that lets the
    hook short-circuit to "allow" rather than crash inside Claude's tool loop.

    A non-Claude harness's payload is translated into Claude's shape first (see
    :func:`normalize_payload`), so every hook downstream reads one vocabulary.
    """
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return normalize_payload(data)


def block(reason: str) -> "NoReturn":
    """Refuse the tool call, in whatever dialect the calling harness understands.

    Claude Code blocks on **exit 2 with the reason on stderr**, and that stays
    the contract here. Grok nominally accepts exit 2 as well, but a live
    grok 0.2.114 session showed the code arriving at its runner as ``1`` — which
    Grok treats as a *hook failure*, and hook failures fail open. The guard
    printed its refusal and the dangerous command ran anyway: the worst possible
    shape, a block that reports success while protecting nothing
    (fleet-config#491).

    Grok's documented escape hatch is that for ``PreToolUse`` a ``deny``
    decision on **stdout is honored regardless of exit code**, so a Grok-sourced
    payload also gets the JSON decision. Claude Code never reaches that branch —
    it is gated on the agent :func:`normalize_payload` identified — so Claude's
    stdout stays clean and its behaviour is byte-for-byte unchanged.
    """
    if _ACTIVE_AGENT == "grok":
        print(json.dumps({"decision": "deny", "reason": reason}), flush=True)
    print(reason, file=sys.stderr, flush=True)
    sys.exit(2)


def warn(message: str) -> "NoReturn":
    """Exit 0 with a single-line nudge on stdout → Claude sees the message but the action still runs."""
    print(message, flush=True)
    sys.exit(0)


def allow() -> "NoReturn":
    """Exit 0 silently → action proceeds, Claude sees nothing."""
    sys.exit(0)


# --------------------------------------------------------- Python resolution


def _is_windowsapps_alias(path: str) -> bool:
    return "\\windowsapps\\" in path.replace("/", "\\").lower()


def find_python_executable() -> Optional[str]:
    """Return a real Python executable, avoiding WindowsApps aliases.

    On this machine the WindowsApps ``py.exe`` / ``python.exe`` aliases can hang
    when spawned non-interactively from hooks. Hook code should use this helper
    instead of trusting PATH order.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    candidates: list[str] = []
    if local_appdata:
        candidates.extend(
            [
                str(Path(local_appdata) / "Python" / "bin" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python314" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python313" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python312" / "python.exe"),
            ]
        )
    candidates.append(sys.executable)
    for name in ("py", "python"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    for candidate in candidates:
        if candidate and not _is_windowsapps_alias(candidate) and Path(candidate).exists():
            return candidate
    return None


# ----------------------------------------------------- PowerShell resolution

# The absolute Windows PowerShell 5.1 path every hook must spell out, because
# the `pwsh` on PATH here is a 0-byte WindowsApps reparse stub that fails
# non-interactively (global CLAUDE.md, "Windows PowerShell in spawned commands").
WINDOWS_POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def powershell_exe() -> str:
    """Resolve a usable PowerShell executable, preferring Windows PowerShell 5.1.

    Probes for the absolute path first and degrades through ``shutil.which`` so a
    machine without it (a POSIX box, a trimmed Windows image) gets a best-effort
    fallback instead of a hard `FileNotFoundError`. Hoisted here from
    `context_filter_cli` (fleet-config#561): `restart_and_verify_webapp` had
    hardcoded the literal with no probe, so `/restart-webapp` hard-failed where
    the wrapper degraded — two resolutions of one fact that had drifted in
    safety.
    """
    if Path(WINDOWS_POWERSHELL).exists():
        return WINDOWS_POWERSHELL
    return shutil.which("powershell") or "powershell"


# ------------------------------------------------------------------ gh CLI


def gh_json(args: Sequence[str], *, timeout: int = 20) -> Dict[str, Any]:
    """Run ``gh <args>`` and parse its JSON stdout. Returns ``{}`` on any error.

    Never raises: a missing gh, a non-zero exit, or unparseable output all yield
    an empty dict so the caller degrades to a link-less message instead of
    crashing a skill mid-run.

    Decodes gh's stdout as UTF-8 explicitly — on Windows ``text=True`` falls back
    to cp1252, which mis-decodes a UTF-8 title (em-dash — -> â€", emoji -> ðŸ§)
    before it ever reaches Slack. Mirrors ``slack_notify._read_text``.

    Lives here rather than in `notify_complete` (fleet-config#561) because
    `work_summary` needed the identical helper and could not import it —
    `notify_complete` imports `work_summary`, so the obvious direction was an
    import cycle and the cycle was "resolved" by copying the body. `_lib` is
    imported by both and imports neither, so the cycle dissolves.
    """
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("gh call failed: %s", exc)
        return {}
    if proc.returncode != 0:
        logger.error("gh exited %s: %s", proc.returncode, (proc.stderr or "").strip()[:200])
        return {}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------- Payload extraction


def tool_name(payload: Dict[str, Any]) -> str:
    return str(payload.get("tool_name") or "")


def tool_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    ti = payload.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def cwd(payload: Dict[str, Any]) -> Path:
    """Best-effort working directory for the call.

    Claude Code sends `cwd` in the payload; fall back to the process cwd if it's
    missing.
    """
    raw = payload.get("cwd")
    if isinstance(raw, str) and raw:
        return Path(raw)
    return Path(os.getcwd())


def command_string(payload: Dict[str, Any]) -> str:
    """Pull the executed command out of a Bash/PowerShell tool_input."""
    return str(tool_input(payload).get("command") or "")


def file_path(payload: Dict[str, Any]) -> Optional[Path]:
    """Pull the file path out of an Edit/Write tool_input, if present."""
    raw = tool_input(payload).get("file_path")
    if isinstance(raw, str) and raw:
        return Path(raw)
    return None


# ----------------------------------------------------------- projects.toml


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    cwd_prefix: Path
    webapp_port: Optional[int]
    gate_trigger_globs: Sequence[str]
    gate_cmd: Optional[str]
    tray_cmd: Optional[str]
    restart_cmd: Optional[str]
    api_version_path: Optional[str]
    extra: Dict[str, Any]  # any other key from the [project] table


@dataclass(frozen=True)
class GlobalConfig:
    never_kill_ports: Sequence[int]
    slack_notify_channel: Optional[str] = None
    slack_notify_user: Optional[str] = None
    slack_notify_mention: bool = False
    # Per-category channels (issue #139). A ping carries a category — "attention"
    # ("come look": blocked / awaiting input / ready-to-validate) vs "log"
    # (activity record: filed / shipped / merged / digests). When the category's
    # channel is unset, routing falls back to `slack_notify_channel`, so a single
    # channel keeps working and the split can roll out one channel at a time.
    slack_channel_attention: Optional[str] = None
    slack_channel_log: Optional[str] = None
    # Base URL for the app-launcher Fleet Board (fleet-config#242), e.g. a
    # Tailscale address so a phone tap resolves outside the LAN. Unset by
    # default — notify_on_idle omits the board deep-link line entirely until
    # this is configured (see resolve_board_url).
    board_url: Optional[str] = None


@dataclass(frozen=True)
class Registry:
    projects: List[ProjectConfig]
    globals: GlobalConfig


def _normalize(p: str) -> str:
    return str(Path(p)).replace("\\", "/").rstrip("/").lower()


def load_registry(path: Optional[Path] = None) -> Registry:
    """Load the project registry from ``projects.toml``.

    ``CLAUDE_HOOKS_PROJECTS_TOML`` overrides the path (same pattern as
    ``slack_notify``'s ``CLAUDE_SETTINGS_JSON_PATH``) so acceptance tests can
    point this at a throwaway file with a ``cwd_prefix`` under a temp dir,
    instead of writing test fixtures into the real fleet paths.
    """
    if path is None:
        path = Path(os.environ.get(PROJECTS_TOML_ENV_VAR) or PROJECTS_TOML)
    if not path.exists():
        return Registry(projects=[], globals=GlobalConfig(never_kill_ports=()))

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    globals_table = data.pop("global", {}) if isinstance(data.get("global"), dict) else {}
    never_kill = tuple(int(p) for p in globals_table.get("never_kill_ports", []))
    slack_channel = globals_table.get("slack_notify_channel") or None
    slack_user = globals_table.get("slack_notify_user") or None
    slack_mention = bool(globals_table.get("slack_notify_mention", False))
    slack_attention = globals_table.get("slack_channel_attention") or None
    slack_log = globals_table.get("slack_channel_log") or None
    board_url = globals_table.get("board_url") or None

    projects: List[ProjectConfig] = []
    for name, table in data.items():
        if not isinstance(table, dict):
            continue
        prefix_raw = table.get("cwd_prefix")
        if not isinstance(prefix_raw, str) or not prefix_raw:
            continue
        webapp_port = table.get("webapp_port")
        projects.append(
            ProjectConfig(
                name=name,
                cwd_prefix=Path(prefix_raw),
                webapp_port=int(webapp_port) if webapp_port is not None else None,
                gate_trigger_globs=tuple(table.get("gate_trigger_globs", []) or []),
                gate_cmd=table.get("gate_cmd"),
                tray_cmd=table.get("tray_cmd"),
                restart_cmd=table.get("restart_cmd"),
                api_version_path=table.get("api_version_path"),
                extra={k: v for k, v in table.items() if k not in {
                    "cwd_prefix", "webapp_port", "gate_trigger_globs",
                    "gate_cmd", "tray_cmd", "restart_cmd", "api_version_path",
                }},
            )
        )

    return Registry(
        projects=projects,
        globals=GlobalConfig(
            never_kill_ports=never_kill,
            slack_notify_channel=slack_channel,
            slack_notify_user=slack_user,
            slack_notify_mention=slack_mention,
            slack_channel_attention=slack_attention,
            slack_channel_log=slack_log,
            board_url=board_url,
        ),
    )


_WORKTREE_SUFFIX_RE = re.compile(r"-wt-\d+$")


def _strip_worktree_suffix(normalized_path: str) -> str:
    """Strip a trailing `-wt-<N>` suffix from every path segment.

    `worktree_claim.py`'s sibling-worktree naming (`<repo>-wt-<N>`) has no
    path separator before the suffix, so it never prefix-matches the
    primary checkout's `cwd_prefix` as-is (fleet-config#471).
    """
    return "/".join(_WORKTREE_SUFFIX_RE.sub("", segment) for segment in normalized_path.split("/"))


def _match_project(cwd_norm: str, projects: List[ProjectConfig]) -> Optional[ProjectConfig]:
    best: Optional[ProjectConfig] = None
    best_len = -1
    for project in projects:
        pref_norm = _normalize(str(project.cwd_prefix))
        if cwd_norm == pref_norm or cwd_norm.startswith(pref_norm + "/"):
            if len(pref_norm) > best_len:
                best = project
                best_len = len(pref_norm)
    return best


def detect_project(cwd_path: Path, registry: Optional[Registry] = None) -> Optional[ProjectConfig]:
    """Pick the project whose `cwd_prefix` is the longest match of `cwd_path`.

    Tries the raw cwd first so a repo whose real name happens to contain a
    `-wt-<N>`-shaped segment still matches directly; only falls back to a
    worktree-suffix-stripped retry when the raw path matches nothing, so a
    `<repo>-wt-<N>` sibling worktree resolves to the same project as its
    primary checkout.
    """
    reg = registry or load_registry()
    cwd_norm = _normalize(str(cwd_path))
    match = _match_project(cwd_norm, reg.projects)
    if match is not None:
        return match
    return _match_project(_strip_worktree_suffix(cwd_norm), reg.projects)


# A ping's intent category → the projects.toml channel key that routes it
# (issue #139). Both keys are valid as a [global] entry and as a per-project
# override. An unset category channel falls back to `slack_notify_channel`.
SLACK_CATEGORY_KEYS = {
    "attention": "slack_channel_attention",
    "log": "slack_channel_log",
}


def resolve_slack_target(
    cwd_path: Path,
    registry: Optional[Registry] = None,
    *,
    category: Optional[str] = None,
) -> "tuple[Optional[str], Optional[str], str]":
    """Resolve ``(channel, user, project_name)`` for a Slack ping from ``cwd_path``.

    A project's own override wins over the ``[global]`` fallback at every level;
    ``name`` is the project key, or ``"claude"`` when ``cwd_path`` matches no
    registered project. Shared by ``notify_on_idle`` (the hook) and
    ``notify_complete`` (the skill-completion helper) so both resolve the
    channel, mention, and project name identically.

    ``category`` ("attention" / "log", issue #139) routes the ping to a
    dedicated channel: the per-category key is tried first (project override,
    then ``[global]``); when it is unset the channel **falls back to
    ``slack_notify_channel``**. That fallback is what keeps a single-channel
    setup working unchanged and lets the split roll out one channel at a time.
    """
    reg = registry or load_registry()
    project = detect_project(cwd_path, reg)

    def pick(key: str, global_value: Optional[str]) -> Optional[str]:
        return (project.extra.get(key) if project else None) or global_value

    channel: Optional[str] = None
    cat_key = SLACK_CATEGORY_KEYS.get(category) if category else None
    if cat_key:
        channel = pick(cat_key, getattr(reg.globals, cat_key, None))
    if not channel:
        channel = pick("slack_notify_channel", reg.globals.slack_notify_channel)

    user = pick("slack_notify_user", reg.globals.slack_notify_user)
    name = project.name if project else "claude"
    return channel, user, name


BOARD_URL_ENV_VAR = "FLEET_BOARD_URL"


def resolve_board_url(cwd_path: Path, registry: Optional[Registry] = None) -> Optional[str]:
    """Resolve the app-launcher Fleet Board base URL for a ``?board=<sid>`` deep
    link (fleet-config#242). Precedence: a project's own ``board_url`` override,
    then the ``FLEET_BOARD_URL`` environment variable, then the committed
    ``[global] board_url`` — ``None`` when nothing resolves, which the caller
    must treat as "omit the link line", never a guessed URL.

    The real value (a Tailscale hostname) is set via ``FLEET_BOARD_URL``, not
    ``[global] board_url``, because fleet-config is a **public** repo
    (fleet-config#271) — same reasoning as ``SLACK_BOT_TOKEN`` staying out of
    ``projects.toml``. Claude Code always injects its ``env`` block into hook
    subprocesses, so a bare env-var read is enough here (unlike
    ``slack_notify``'s extra settings.json-file fallback, needed only because
    that transport must also work from non-Claude launchers).
    """
    reg = registry or load_registry()
    project = detect_project(cwd_path, reg)
    project_value = project.extra.get("board_url") if project else None
    return project_value or os.environ.get(BOARD_URL_ENV_VAR) or reg.globals.board_url


# ------------------------------------------------------------------- .venv


def find_venv_python(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python`."""
    candidates_rel = (
        Path(".venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
    )
    for parent in [start, *start.parents]:
        for rel in candidates_rel:
            candidate = parent / rel
            if candidate.exists():
                return candidate
    return None

