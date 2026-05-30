"""Drive each hook with a sample payload and assert the expected exit code.

Run from the repo root:
    py tests/run_acceptance.py

Exit 0 if all cases pass, 1 otherwise. Prints a single line per case.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO     = Path(__file__).resolve().parent.parent
HOOKS    = REPO / "hooks"

# Resolve a Python interpreter that can run the hooks
PYTHON   = shutil.which("py") or shutil.which("python") or sys.executable


def run(hook: str, payload: Dict[str, Any]) -> Tuple[int, str, str]:
    res = subprocess.run(
        [PYTHON, str(HOOKS / f"{hook}.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
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


def main() -> int:
    cases: List[Tuple[str, str, Dict[str, Any], int]] = [
        # ---- pre_commit_no_ai_trailer ----
        ("pre_commit: Co-Authored-By Claude -> block",
         "pre_commit_no_ai_trailer",
         {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'}},
         2),
        ("pre_commit: Generated with Claude Code -> block",
         "pre_commit_no_ai_trailer",
         {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: x\n\n🤖 Generated with Claude Code"'}},
         2),
        ("pre_commit: clean message -> allow",
         "pre_commit_no_ai_trailer",
         {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: clean message"'}},
         0),
        ("pre_commit: non-commit Bash -> allow",
         "pre_commit_no_ai_trailer",
         {"tool_name": "Bash", "tool_input": {"command": 'git status'}},
         0),

        # ---- safe_kill_guard ----
        ("safe_kill: Stop-Process -Name python -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name python -Force"}},
         2),
        ("safe_kill: Stop-Process -Name pythonw -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name pythonw -Force"}},
         2),
        ("safe_kill: port-scoped kill on 8446 (protected) -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8446 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
         2),
        ("safe_kill: port-scoped kill on 8445 (project port) -> allow",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8445 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
         0),
        ("safe_kill: git push --force origin main -> block",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
         2),
        ("safe_kill: git push --force feature/x -> allow",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature/foo"}},
         0),
        ("safe_kill: git commit --no-verify -> block",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m hi"}},
         2),

        # ---- venv_discipline ----
        ("venv: python -m venv venv -> block",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "python -m venv venv"}},
         2),
        ("venv: python -m venv .venv -> allow",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "python -m venv .venv"}},
         0),
        ("venv: activate.ps1 -> block",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": ".\\.venv\\Scripts\\Activate.ps1"}},
         2),
        ("venv: source .venv/bin/activate -> block",
         "venv_discipline",
         {"tool_name": "Bash", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "source .venv/bin/activate"}},
         2),
        ("venv: bare python with .venv present -> block",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "python script.py"}},
         2),
        ("venv: path-scoped venv python -> allow",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "& .\\.venv\\Scripts\\python.exe -m pip install foo"}},
         0),
        ("venv: bare python with NO .venv -> allow",
         "venv_discipline",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(), "tool_input": {"command": "python --version"}},
         0),
    ]

    # ---- py_syntax_check needs real files ----
    tmp = Path(tempfile.mkdtemp(prefix="claude-config-test-"))
    broken = tmp / "broken.py"
    good   = tmp / "good.py"
    broken.write_text("def foo(:\n    pass\n", encoding="utf-8")
    good.write_text("def foo():\n    return 1\n", encoding="utf-8")

    cases.append((
        "py_syntax: broken file -> block",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(broken)}},
        2,
    ))
    cases.append((
        "py_syntax: good file -> allow",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(good)}},
        0,
    ))
    cases.append((
        "py_syntax: non-py file -> allow",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(tmp / "x.txt")}},
        0,
    ))

    # ---- notify_on_idle: opt-in, default off ----
    # No project here declares slack_notify_channel and the [global] fallback is
    # commented out, so this must be a silent no-op (exit 0) — never a live post.
    cases.append((
        "notify_on_idle: no channel configured -> allow (no-op)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "cwd": str(REPO), "message": "needs input"},
        0,
    ))

    failures = 0
    for name, hook, payload, expected in cases:
        code, _stdout, stderr = run(hook, payload)
        if not assert_exit(name, expected, code, stderr):
            failures += 1

    # ---- slack_notify unit checks (pure / no network) ----
    failures += _slack_notify_unit_checks()

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"Total: {len(cases) + _UNIT_CHECK_COUNT} | Failed: {failures}")
    return 0 if failures == 0 else 1


_UNIT_CHECK_COUNT = 3


def _slack_notify_unit_checks() -> int:
    """Exercise slack_notify without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    check(
        "slack_notify: archive URL -> bare id",
        slack_notify.parse_channel("https://x.slack.com/archives/C0B76GBA0LS") == "C0B76GBA0LS",
    )
    check(
        "slack_notify: bare id passes through",
        slack_notify.parse_channel("  C0B76GBA0LS  ") == "C0B76GBA0LS",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var around the call so a real token on the dev box can't trigger a post.
    saved = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    try:
        result = slack_notify.notify("test", channel="C0B76GBA0LS", token=None)
    finally:
        if saved is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved
    check("slack_notify: missing token -> False (graceful)", result is False)

    return failures


if __name__ == "__main__":
    sys.exit(main())
