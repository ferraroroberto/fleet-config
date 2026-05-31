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
    # Strip SLACK_BOT_TOKEN so a hook that posts to Slack (notify_on_idle) takes
    # the graceful-fail path instead of firing a real ping on every test run.
    env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
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

    # ---- notify_on_idle ----
    # claude-config itself has no per-project slack_notify_channel in projects.toml,
    # but the [global] fallback IS now set. The hook will try to post but the
    # SLACK_BOT_TOKEN is not in the subprocess env, so slack_notify returns False
    # gracefully and the hook still exits 0.
    cases.append((
        "notify_on_idle: global channel set, missing token -> allow (graceful fail)",
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

    # ---- notify_on_idle mention-construction unit checks ----
    failures += _notify_mention_unit_checks()

    # ---- notify_on_idle classify / session-link / idle-suppression ----
    failures += _notify_classify_unit_checks()

    # ---- notify_complete deterministic message assembly + resolver ----
    failures += _notify_complete_unit_checks()

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"Total: {len(cases) + _UNIT_CHECK_COUNT} | Failed: {failures}")
    return 0 if failures == 0 else 1


_UNIT_CHECK_COUNT = 24


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


_NOTIFY_MENTION_COUNT = 2


def _notify_mention_unit_checks() -> int:
    """Verify mention-string construction in notify_on_idle without touching Slack."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    # With slack_notify_user set, mention prefix must be present
    g_with_user = _lib.GlobalConfig(
        never_kill_ports=(),
        slack_notify_channel="C0B76GBA0LS",
        slack_notify_user="U0B71PQEL6S",
    )
    mention_with = f"<@{g_with_user.slack_notify_user}> " if g_with_user.slack_notify_user else ""
    check(
        "notify_mention: slack_notify_user set -> mention prefix present",
        mention_with == "<@U0B71PQEL6S> ",
    )

    # Without slack_notify_user, mention prefix must be empty
    g_no_user = _lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C0B76GBA0LS")
    mention_none = f"<@{g_no_user.slack_notify_user}> " if g_no_user.slack_notify_user else ""
    check(
        "notify_mention: slack_notify_user absent -> no mention prefix",
        mention_none == "",
    )

    return failures


_NOTIFY_CLASSIFY_COUNT = 12


def _notify_classify_unit_checks() -> int:
    """Per-type icon/wording, bridge session-link parsing, and idle-after-done
    suppression — the three deterministic pieces of the notification logic."""
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402
    import slack_notify  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    # ---- classify: icon per notification_type, message passed through ----
    icon, text = notify_on_idle.classify(
        {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
    )
    check("classify: permission -> bell icon + 'awaits your input'",
          icon == "🔔" and text == "Claude awaits your input")
    icon, text = notify_on_idle.classify(
        {"notification_type": "idle_prompt", "message": "Claude is waiting for your input"}
    )
    check("classify: idle -> sleep icon + passthrough",
          icon == "💤" and "waiting" in text)
    icon, _ = notify_on_idle.classify({"message": "x"})
    check("classify: unknown type -> bell fallback", icon == "🔔")

    # ---- session_link: bridge id -> web url, local session -> None ----
    tmp = Path(tempfile.mkdtemp(prefix="notify_link_"))
    try:
        def transcript(*entries: dict) -> str:
            path = tmp / f"t{len(list(tmp.iterdir()))}.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            return str(path)

        link = notify_on_idle.session_link(transcript(
            {"type": "mode", "mode": "normal"},
            {"type": "bridge-session", "bridgeSessionId": "cse_01HNYE6TFWrUXEGcY8oUiGFr"},
        ))
        check("session_link: bridge id -> claude.ai url",
              link == "https://claude.ai/code/session_01HNYE6TFWrUXEGcY8oUiGFr")
        check("session_link: local session -> None",
              notify_on_idle.session_link(transcript({"type": "user"})) is None)
        check("session_link: missing path -> None", notify_on_idle.session_link(None) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- _is_recent_completion: suppress idle only after a fresh ✅ ----
    now = 1_000_000.0
    user = "U0B71PQEL6S"
    done = {"bot_id": "B1", "text": f"<@{user}> ✅ Done: #5 — merged", "ts": str(now - 30)}
    idle = {"bot_id": "B1", "text": f"<@{user}> 🔔 [p] needs permission", "ts": str(now - 30)}
    old_done = {"bot_id": "B1", "text": f"<@{user}> ✅ Done", "ts": str(now - 9999)}
    filed = {"bot_id": "B1", "text": f"<@{user}> 🆕 Filed #5", "ts": str(now - 10)}
    ready = {"bot_id": "B1", "text": f"<@{user}> 🚦 #5 ready", "ts": str(now - 10)}
    check("recent_completion: fresh done latest -> suppress",
          slack_notify._is_recent_completion([done], user, now, 600) is True)
    check("recent_completion: add mark latest -> suppress",
          slack_notify._is_recent_completion([filed], user, now, 600) is True)
    check("recent_completion: start mark latest -> suppress",
          slack_notify._is_recent_completion([ready], user, now, 600) is True)
    check("recent_completion: attention ping latest -> don't suppress",
          slack_notify._is_recent_completion([idle, done], user, now, 600) is False)
    check("recent_completion: stale done -> don't suppress",
          slack_notify._is_recent_completion([old_done], user, now, 600) is False)
    check("recent_completion: no bot messages -> don't suppress",
          slack_notify._is_recent_completion([], user, now, 600) is False)

    return failures


_NOTIFY_COMPLETE_COUNT = 7


def _notify_complete_unit_checks() -> int:
    """Canonical per-kind message assembly + the shared slack-target resolver."""
    sys.path.insert(0, str(HOOKS))
    import notify_complete  # noqa: E402
    import _lib  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    bm = notify_complete.build_message
    check("build: add -> filed + issue link",
          bm("add", issue="5", title="T", url="http://u") == "🆕 Filed #5 T · http://u")
    check("build: start -> ready-to-validate + summary",
          bm("start", issue="5", title="T", summary="do X") == "🚦 #5 T — ready to validate. do X")
    check("build: finish -> done + PR link",
          bm("finish", issue="5", title="T", url="http://u") == "✅ Done #5 T — PR merged · http://u")
    check("build: yolo -> shipped + PR link",
          bm("yolo", issue="5", title="T", url="http://u") == "🚀 Shipped #5 T — PR · http://u")
    check("build: batch -> passed/total",
          bm("batch", passed="2", total="3") == "🏁 Batch done: 2/3 passed — /issue-finish each branch to ship")
    check("build: finish with no url/title degrades cleanly",
          bm("finish", issue="5") == "✅ Done #5 — PR merged")

    # The shared resolver: unknown cwd -> [global] channel/user + 'claude' name.
    ch, usr, nm = _lib.resolve_slack_target(Path("E:/does/not/match/anything"))
    check("resolve_slack_target: global fallback + claude name",
          ch == "C0B76GBA0LS" and usr == "U0B71PQEL6S" and nm == "claude")

    return failures


if __name__ == "__main__":
    sys.exit(main())
