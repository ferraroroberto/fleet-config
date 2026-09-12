"""Shared plumbing for every tests/acceptance/* check module (fleet-config#502).

REPO/HOOKS path resolution, the non-WindowsApps `PYTHON` interpreter probe,
the hook-subprocess `run()` / `assert_exit()` pair, `_Checker` (print+count
one OK/FAIL case -- the shared body every `_x_unit_checks()` function used to
hand-roll as a local closure), and `_subprocess_unit_check` (run a standalone
pure-logic test file as one check). Every other module in this package
depends on at least one of these; centralizing them here is what makes the
per-concern split possible without duplicating the plumbing five times.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO / "hooks"

sys.path.insert(0, str(HOOKS))
# Imported for the constant rather than re-spelled as a literal here: the env
# var name is the hooks' own contract (fleet-config#835), and a rename must
# break this import instead of silently un-scrubbing the suite's environment.
from _lib import LAUNCHER_SESSION_ID_ENV_VAR  # noqa: E402

# The exit code a standalone `tests/test_*.py` suite uses to say "I could not
# establish my facts" -- distinct from 0 (ran, all passed) and from any other
# non-zero (ran, failed). `_subprocess_unit_check` renders it as SKIP and it
# lands in neither Total nor Failed (fleet-config#679). Deliberately not 2:
# 2 is what an unhandled `SystemExit`/argparse error uses, and a crash must
# never be mistaken for an honest skip.
SKIP_EXIT = 3


def _is_windowsapps_alias(path: str) -> bool:
    return "\\windowsapps\\" in path.replace("/", "\\").lower()


def _python_for_hooks() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    candidates: list[str] = []
    if local_appdata:
        candidates.append(str(Path(local_appdata) / "Python" / "bin" / "python.exe"))
    candidates.append(sys.executable)
    for name in ("py", "python"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
    for candidate in candidates:
        if candidate and not _is_windowsapps_alias(candidate) and Path(candidate).exists():
            return candidate
    return sys.executable


# Resolve a Python interpreter that can run the hooks without hitting
# non-interactive WindowsApps aliases.
PYTHON = _python_for_hooks()

# A path that never exists on disk. notify_send._token_from_settings() reads
# ~/.claude/settings.json as a fallback when TELEGRAM_BOT_TOKEN isn't in the env --
# straight off disk via Path.home(), which on Windows resolves through the OS
# profile API and finds the real file even when a test subprocess's env dict
# omits TELEGRAM_BOT_TOKEN (and even USERPROFILE). Without this override, every
# acceptance run posted real Telegram pings to the real attention chat
# (fleet-config#<pending>).
NO_SETTINGS_JSON = str(Path(tempfile.gettempdir()) / "fleet-config-test-no-settings.json")

# The same trick for notify_send._token_from_dotenv(), which reads
# fleet-config's root .env straight off disk via DOTENV_PATH. That source was
# added after the settings.json override above and nothing here closed it, so
# the token kept resolving and every gate run posted four real pings to the
# live attention chat (fleet-config#813).
NO_DOTENV = str(Path(tempfile.gettempdir()) / "fleet-config-test-no-dotenv.env")

# Hook state (sessions-state.json and friends) must never land in the repo's
# real hooks/state/ — that directory is junctioned into ~/.claude/hooks/, so a
# fixture row written there is *live* fleet state, and #813 left a phantom
# "needs-you" row for a session id that only exists in a test payload.
# One temp dir per suite run: individual checks still override it with their
# own via extra_env, this is only the default that was missing.
TEMP_STATE_DIR = str(Path(tempfile.mkdtemp(prefix="fleet-config-acceptance-state-")))


def hook_env(extra_env: Dict[str, str] | None = None) -> Dict[str, str]:
    """The isolated environment every hook subprocess in the suite runs under.

    Closes **every** token source `notify_send._resolve_token` consults, and
    blocks the transport outright so a source added later cannot quietly
    reopen the hole. `checks_notify` asserts both halves still hold and fails
    the suite if either stops — the property this replaced was a comment.
    """
    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
    # `APP_LAUNCHER_SESSION_ID` decides whether a hook thinks it is running
    # inside a launcher-spawned (and possibly chief-dispatched) session
    # (fleet-config#835). Inheriting it would make the suite's verdicts depend
    # on *how the suite was launched* — green from a plain terminal, and from a
    # chief-dispatched worker a `block_askuserquestion_chief` that blocks and a
    # `notify_on_idle` that tries to reach the live chief. Dropped
    # unconditionally, exactly like the Telegram token; the checks that need it
    # set their own value through `extra_env`.
    env.pop(LAUNCHER_SESSION_ID_ENV_VAR, None)
    env["CLAUDE_SETTINGS_JSON_PATH"] = NO_SETTINGS_JSON
    env["FLEET_CONFIG_ENV_PATH"] = NO_DOTENV
    env["FLEET_NOTIFY_BLOCK_NETWORK"] = "1"
    # Unconditional, not setdefault: an ambient CLAUDE_HOOKS_STATE_DIR must not
    # be able to point the suite back at live state. Checks that want their own
    # dir pass it through `extra_env`, which still wins below.
    env["CLAUDE_HOOKS_STATE_DIR"] = TEMP_STATE_DIR
    if extra_env:
        env.update(extra_env)
    return env


def run(hook: str, payload: Dict[str, Any], extra_env: Dict[str, str] | None = None) -> Tuple[int, str, str]:
    env = hook_env(extra_env)
    res = subprocess.run(
        [PYTHON, str(HOOKS / f"{hook}.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    return res.returncode, res.stdout, res.stderr


def assert_exit(case: str, expected: int, got: int, stderr: str) -> bool:
    ok = got == expected
    flag = "OK   " if ok else "FAIL "
    extra = "" if ok else f" (got {got}, expected {expected})"
    print(f"{flag} {case}{extra}")
    if not ok and stderr:
        for line in stderr.strip().splitlines():
            print(f"        | {line}")
    return ok


class _Checker:
    """Print + count one OK/FAIL case -- the shared body every
    `_x_unit_checks()` function below used to hand-roll as a local `check()`
    closure plus a `nonlocal failures` counter. Centralizing it means each
    function's own `(failures, total)` return is the real count of checks it
    ran, so `main()` can sum the acceptance-matrix total at call time instead
    of the hand-maintained `_UNIT_CHECK_COUNT` constant (fleet-config#320)."""

    def __init__(self) -> None:
        self.failures = 0
        self.total = 0
        self.skipped = 0

    def __call__(self, case: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            self.failures += 1
            if detail:
                for line in detail.strip().splitlines():
                    print(f"        | {line}")

    def advisory(self, case: str, ok: bool, detail: str = "") -> None:
        """Report a check whose inputs live **outside this repo** — it may not
        gate this repo's own `main`.

        A pass counts normally (it did verify something). A failure is reported
        loudly with `detail`, but lands in `skipped`, never `failures`: nothing
        no commit here controls may turn this gate red and block every
        `/issue-finish`, `/quick`, and `/issue-yolo` until someone else's repo
        is fixed (fleet-config#562). It is also the honest state — the result
        depends on untracked sibling checkouts, so a fresh clone or another
        machine would answer differently. Never fold an unverifiable into a
        pass either: `Skipped: N` is its own column in the summary line.
        """
        if ok:
            self(case, True)
            return
        self.skipped += 1
        print(f"SKIP  {case}")
        for line in (detail or "").strip().splitlines():
            print(f"        | {line}")


def _subprocess_unit_check(label: str, test_file: str) -> Tuple[int, int, int]:
    """Run a standalone pure-logic test file as a subprocess and report it as
    one pass/fail/skip check -- the shared body every row of
    `standalone_dispatch._STANDALONE_UNIT_CHECKS` points at one focused file
    under tests/. Returns (failures, total, skipped).

    Exit codes: 0 = the suite ran and every check passed; `SKIP_EXIT` = the
    suite could not establish its facts (a missing interpreter or toolchain,
    never a real failure); anything else = failed. The third state exists
    because exit 0 was previously the only way for a suite to say "I did not
    run", which printed a bare `OK` in the gate output while verifying nothing
    (fleet-config#679). A skipped suite counts toward neither Total nor
    Failed, exactly like `_Checker.advisory`, and its stdout is echoed so the
    reason is visible.
    """
    proc = subprocess.run(
        [PYTHON, str(REPO / "tests" / test_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode == SKIP_EXIT:
        print(f"SKIP  {label}: pure-logic unit tests NOT verified")
        for line in (proc.stdout or "").strip().splitlines():
            print(f"        | {line}")
        return 0, 0, 1
    ok = proc.returncode == 0
    print(f"{'OK   ' if ok else 'FAIL '} {label}: pure-logic unit tests")
    if not ok:
        for line in (proc.stdout or "").strip().splitlines():
            print(f"        | {line}")
    return (0 if ok else 1), 1, 0
