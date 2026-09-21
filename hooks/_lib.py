"""Shared helpers for the fleet-config hooks.

Every hook in this directory:

* Reads a single JSON payload from stdin (Claude Code's hook contract).
* Returns exit code 0 to allow the action.
* Refuses through block(): Claude uses exit 2 + stderr; Codex PreToolUse
  uses a structured deny + exit 0; Grok adds its own structured deny.
* Or returns exit code 0 with a single-line nudge on **stdout** to advise
  without blocking.

The per-harness halves of that contract live in `harness_wire.py`; this
module re-exports them. Use the helpers below so each hook stays a few dozen lines of pure rule logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

STATE_DIR_ENV_VAR = "CLAUDE_HOOKS_STATE_DIR"

# app-launcher stamps this into every PTY/remote session it spawns
# (`src/session_host.agent_child_env`), assigning it unconditionally rather
# than inheriting, so the agent process — and every hook subprocess below it —
# carries the id of *its own* launcher session even when a launcher session
# spawns another. It is the only identifier a hook shares with the launcher:
# the harness's own `payload["session_id"]` lives in a different namespace
# entirely (fleet-config#835).
LAUNCHER_SESSION_ID_ENV_VAR = "APP_LAUNCHER_SESSION_ID"

logger = logging.getLogger("fleet_hooks")


def state_dir() -> Path:
    """The shared hooks-state base directory, resolved at call time so the
    ``CLAUDE_HOOKS_STATE_DIR`` override always wins (hermetic acceptance runs
    depend on it). Every hook that persists advisory state under
    ``~/.claude/hooks/state/`` derives its own filename from this rather than
    re-deriving the lookup itself -- eight independent copies of exactly this
    (fleet-config#817) is the failure mode a ninth caller getting it wrong
    guards against."""
    root = os.environ.get(STATE_DIR_ENV_VAR)
    return Path(root) if root else Path.home() / ".claude" / "hooks" / "state"


# An abrupt death between mkstemp and os.replace runs no Python handler, so a
# writer's own except-branch unlink never fires and the temp is stranded
# (fleet-config#816). Nothing inside the dead process can clean up, so temps
# are named after their target and expired by age by the next writer instead.
# An hour is orders of magnitude beyond a real write's duration, so the sweep
# cannot race a live writer's temp. Mirrored for skills/_lib in
# `hooks_state.py` (the tree boundary forbids importing across) (fleet-config#864).
ATOMIC_TMP_SWEEP_AFTER_SECONDS = 3600.0


def atomic_tmp_prefix(path: Path) -> str:
    """Writer-identifying ``mkstemp`` prefix for ``path``'s write temporaries.

    An orphan is then ``.<target>.<random>.tmp`` -- it names the file whose
    writer stranded it, instead of the anonymous ``tmp<8>.tmp`` that left six
    accumulated temps in ``hooks/state/`` unattributable (fleet-config#816).
    Pair with ``suffix=".tmp"``.
    """
    return f".{path.name}."


def sweep_stale_atomic_temps(path: Path) -> None:
    """Unlink ``path``'s abandoned write temporaries, by age.

    The cleanup a hard-killed writer could never do itself: see
    ``ATOMIC_TMP_SWEEP_AFTER_SECONDS``. ``hooks/state/`` is shared by several
    writers, so a name only matches when it is exactly this target's prefix
    plus one ``mkstemp`` random segment (no dots) plus ``.tmp`` -- a sibling
    target whose name merely *starts* with this one is never swept. Advisory:
    any failure leaves the orphan for the next writer rather than disturbing
    the write this sweep precedes.
    """
    pattern = re.compile(re.escape(atomic_tmp_prefix(path)) + r"[a-z0-9_]+\.tmp")
    cutoff = time.time() - ATOMIC_TMP_SWEEP_AFTER_SECONDS
    try:
        candidates = [entry for entry in path.parent.iterdir() if pattern.fullmatch(entry.name)]
    except OSError:
        return
    for stale in candidates:
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue


def launcher_session_id() -> str:
    """This session's **app-launcher** session id, or ``""`` when not launcher-spawned.

    The third reader of :data:`LAUNCHER_SESSION_ID_ENV_VAR`
    (``branch_before_edit_guard``, ``session_state``, and now the chief-managed
    lookup in ``notify_on_idle``) is where the inlined
    ``os.environ.get(..., "").strip()`` becomes a helper, so a fourth reader
    cannot quietly spell the variable — or the empty-string contract — its own
    way. Resolved at call time, like :func:`state_dir`, so an acceptance
    subprocess's env override always wins.
    """
    return os.environ.get(LAUNCHER_SESSION_ID_ENV_VAR, "").strip()


# `skills/_lib/scheduled_runner.py` stamps this into the environment of every
# scheduled child it launches, and the harness passes it on to hook
# subprocesses (probed live: scheduled Claude run -> Git Bash -> PowerShell ->
# Python, fleet-config#911). Mirrors `rate_gate.UNATTENDED_ENV`; the tree
# boundary forbids importing it, so a test holds the two spellings together.
SCHEDULED_RUN_ENV_VAR = "FLEET_SCHEDULED_RUN"


def is_scheduled_run() -> bool:
    """True inside a run launched by the scheduled runner, nobody attending.

    Such a run owns every descendant in a Windows job: a fire-and-forget child
    still alive when the provider exits turns a delivered run into exit 118 and
    is then killed by the job teardown anyway (fleet-config#911). Hooks that
    would detach background work check this and skip it. Resolved at call time,
    like :func:`state_dir`.
    """
    return os.environ.get(SCHEDULED_RUN_ENV_VAR) == "1"


# ------------------------------------------------------- credential patterns

# The one definition of "what a live credential looks like" for this tier
# (fleet-config#561). Two independent copies used to exist — `context_filter`'s
# four-family redaction regex and `secret_scan_guard`'s one-family commit
# blocker — and the *narrower* one was the copy wired into the guard that
# actually refuses a commit. So the guard blocked a leaked Telegram bot token and
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
    # Telegram bot token: `<bot_id>:<35-char secret>` (fleet-config#540). Added
    # rather than swapping out the Slack entry — this tuple is the tier's general
    # credential list, not a Slack list, and the `xoxb-` token stays live until
    # the workspace is actually decommissioned. The secret half is exactly 35
    # characters, so the length is pinned rather than `{20,}`: a looser rule
    # matches ordinary `HH:MM`-adjacent digit-colon-text runs in a log tail.
    ("Telegram bot token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
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
# the pair into `??`, which is what landed in the chat.
#
# Two prior instances of the same class already carry fixes on adjacent paths —
# `notify_complete.gh_json` (gh stdout forced to UTF-8) and
# `notify_send._read_text` (piped stdin forced to UTF-8). Those cover *byte*
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


# ------------------------------------------------------- multi-harness wire
#
# Foreign-harness payload normalization (inbound) and the per-harness
# block/warn/rewrite/allow dialects (outbound) moved to harness_wire.py
# (fleet-config#931) — re-exported here so `_lib.read_stdin_json`,
# `_lib.block`, `_lib.normalize_payload`, etc. keep working unchanged for every
# existing hook import. harness_wire.py has no dependency on `_lib`, so this
# import carries no ordering hazard. The `_ACTIVE_AGENT` / `_ACTIVE_EVENT`
# globals the emitters read live there, not here.

from harness_wire import (  # noqa: E402
    AGENT_HINT_KEY,
    SHELL_AMBIGUOUS_KEY,
    allow,
    block,
    normalize_payload,
    payload_agent,
    read_stdin_json,
    rewrite_command,
    shell_is_ambiguous,
    warn,
)


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


# --------------------------------------------------------------------- git


def git_env(base: Optional[dict] = None) -> dict:
    """``base`` (default ``os.environ``) plus ``GIT_OPTIONAL_LOCKS=0``.

    Hooks-tier copy of ``skills/_lib/git_run.git_env`` — see that function for
    the full reasoning (fleet-config#667). Short version: ``git status`` takes
    ``.git/index.lock`` only to persist a refreshed stat cache, so killing one
    mid-refresh strands a 0-byte lock that then blocks every write in that repo
    while every *read* keeps exiting 0. This tier matters most, not least:
    ``branch_before_edit_guard`` shells out to ``git`` on every Edit/Write in
    every live session fleet-wide, which is by far the highest-frequency git
    spawn this repo owns.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    """Run ``git <args>``, UTF-8 decoded with undecodable bytes replaced.

    Pass ``-C <repo>`` inside ``args`` to target a working tree — the
    convention every call site in this repo uses. ``check=False`` (the default)
    leaves the caller to inspect ``.returncode``/``.stdout``/``.stderr``.

    Deliberately a **hooks-tier copy** of ``skills/_lib/git_run.run_git``, the
    same way :data:`NO_WINDOW` duplicates ``skills/_lib/no_window`` — see that
    constant's note. ``branch_before_edit_guard`` used to reach the skills-tier
    module by inserting ``../skills/_lib`` onto ``sys.path`` at import time
    (fleet-config#564), which breaks the rule the rest of the directory states
    outright (``notify_on_idle``: the two trees are independent, so a sibling is
    reached by subprocess, never a Python import). The practical cost of the
    violation was unbounded: the import sat at module top with no guard, so a
    renamed or absent skills tree would kill a ``PreToolUse`` hook *at import
    time* — before any of its fail-open logic could run — on every Edit/Write in
    every session, fleet-wide. ``tests/acceptance/tree_boundary.py`` now fails
    on any hook that reaches across, and asserts this copy agrees behaviourally
    with the skills-tier original — including ``env=git_env()``.
    """
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
        creationflags=NO_WINDOW, env=git_env(),
    )


def run_gh(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None,
    stdin: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run ``gh <args>``, UTF-8 decoded with undecodable bytes replaced.

    The ``gh``-CLI sibling of :func:`run_git` (fleet-config#728): before this
    existed, every ``gh`` call site hand-rolled the identical
    ``capture_output=True, text=True, encoding="utf-8", errors="replace",
    creationflags=NO_WINDOW`` combination — ``text=True`` alone falls back to
    cp1252 on Windows, which raises ``UnicodeDecodeError`` on a non-ASCII issue
    title or body (fleet-config#679) — each restating the same two gotchas in
    its own comment. They had already drifted on ``timeout`` (some passed none,
    some 30s/60s/120s) before this wrapper existed to catch it; that spread is
    real call-site policy, so ``timeout`` stays a parameter here rather than a
    forced constant — only the plumbing is shared. ``stdin`` likewise stays
    optional (``None`` inherits, matching every site except
    ``chief_ops.fetch_issue_state``, which passes ``subprocess.DEVNULL``).

    Deliberately a **hooks-tier copy** of ``skills/_lib/git_run.run_gh`` — the
    same duplication `run_git` already carries between the two trees (see that
    function's docstring for why: `hooks/` must stay importable with nothing
    but its own directory on ``sys.path``).
    """
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
        creationflags=NO_WINDOW, stdin=stdin,
    )


def resolve_default_branch_ref(
    repo_path: Path,
    candidates: Sequence[str] = ("origin/main", "main", "master"),
    final_fallback: str = "main",
) -> str:
    """The repo's default branch, preferring the remote's own ``origin/HEAD``.

    On ``symbolic-ref refs/remotes/origin/HEAD`` success, returns the ref with
    the ``refs/remotes/`` prefix stripped (e.g. ``origin/main``). On failure,
    probes ``candidates`` in order via ``rev-parse --verify --quiet`` and
    returns the first that resolves; if none do (or ``candidates`` is empty),
    returns ``final_fallback``.

    Hooks-tier copy of ``skills/_lib/git_run.resolve_default_branch_ref`` — see
    :func:`run_git` for why the two trees each carry their own.
    """
    res = run_git(["-C", str(repo_path), "symbolic-ref", "refs/remotes/origin/HEAD"])
    ref = res.stdout.strip()
    if res.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)
    for cand in candidates:
        if run_git(["-C", str(repo_path), "rev-parse", "--verify", "--quiet", cand]).returncode == 0:
            return cand
    return final_fallback


# ------------------------------------------------------------------ gh CLI


def gh_json(args: Sequence[str], *, timeout: int = 20) -> Dict[str, Any]:
    """Run ``gh <args>`` and parse its JSON stdout. Returns ``{}`` on any error.

    Never raises: a missing gh, a non-zero exit, or unparseable output all yield
    an empty dict so the caller degrades to a link-less message instead of
    crashing a skill mid-run.

    Decodes gh's stdout as UTF-8 explicitly — on Windows ``text=True`` falls back
    to cp1252, which mis-decodes a UTF-8 title (em-dash — -> â€", emoji -> ðŸ§)
    before it ever reaches the chat. Mirrors ``notify_send._read_text``.

    Lives here rather than in `notify_complete` (fleet-config#561) because
    `work_summary` needed the identical helper and could not import it —
    `notify_complete` imports `work_summary`, so the obvious direction was an
    import cycle and the cycle was "resolved" by copying the body. `_lib` is
    imported by both and imports neither, so the cycle dissolves.
    """
    try:
        proc = run_gh(args, timeout=timeout)
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


# --------------------------------------------------------- Shared edit events
#
# EditTarget/EditEvent/edit_event() moved to edit_events.py (fleet-config#819)
# — re-exported here so `_lib.EditEvent`, `_lib.edit_event`, etc. keep working
# unchanged for every existing hook import. Placed after tool_name/tool_input/
# payload_agent above: edit_events.py reaches those back via `import _lib`
# inside its own function bodies (never at its module scope), which is what
# makes this reverse import safe despite `_lib` not having finished loading
# yet at the point it reaches this line.

from edit_events import EditEvent, EditTarget, edit_event  # noqa: E402


# ----------------------------------------------------------- projects.toml
#
# ProjectConfig/GlobalConfig/Registry/load_registry/detect_project/
# resolve_notify_target/resolve_board_url moved to registry.py
# (fleet-config#819) — re-exported here so `_lib.load_registry`,
# `_lib.detect_project`, etc. keep working unchanged for every existing hook
# import. registry.py has no dependency on `_lib`, so this import carries no
# ordering hazard.

from registry import (  # noqa: E402
    BOARD_URL_ENV_VAR,
    DOTENV_PATH_ENV_VAR,
    HOOKS_DIR,
    NOTIFY_CATEGORY_KEYS,
    PROJECTS_TOML,
    PROJECTS_TOML_ENV_VAR,
    GlobalConfig,
    ProjectConfig,
    Registry,
    detect_project,
    load_registry,
    resolve_board_url,
    resolve_notify_target,
)


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


# ------------------------------------------------------------ reparse points


def is_reparse_point(path: Path, *, on_error: bool) -> bool:
    """True if `path` is a Windows junction, symlink, or other reparse point.

    On Windows `Path.is_symlink()` and `os.path.islink()` both return False for
    a directory junction, so the `FILE_ATTRIBUTE_REPARSE_POINT` bit is the real
    test; `S_ISLNK` covers POSIX symlinks (and keeps this testable off
    Windows). `on_error` is what a path that cannot be stat'ed reads as — each
    caller states its own policy: the backup walker fails closed (`True`,
    "unreadable is not safe to descend"), the venv guard fails open (`False`,
    a nudge must never fire on a path it can't read). One home so the next
    reparse-point quirk is fixed once (fleet-config#928).
    """
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return on_error
    if getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    return stat.S_ISLNK(st.st_mode)
