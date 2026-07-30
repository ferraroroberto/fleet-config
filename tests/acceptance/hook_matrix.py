"""Hook-payload acceptance matrix + foreign-harness shape parity (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (a) --
drive each Tier-1 hook with a synthetic Claude-shaped and Grok-shaped payload
and assert the expected exit code, including the py_syntax_check fixture
files and the Grok Build parity cases (fleet-config#491). This used to be
inline in `main()`; `run_hook_matrix()` is the same case-building + running
logic, restructured into one `_x_unit_checks()`-shaped function returning
`(failures, total)` so `tests/run_acceptance.py`'s `run_unit()` dispatch loop
folds it in exactly like every other check.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from acceptance.shared import REPO, assert_exit, run

# A synthetic Slack-token-shaped string for the secret_scan_guard cases. It is
# assembled from fragments at runtime so the literal `xoxb-` token body never
# sits in this source file — a contiguous literal would trip GitHub's push
# protection (and the very guard under test). The assembled value still matches
# secret_scan_guard's regex `xoxb-\d{6,}-\d{6,}-[A-Za-z0-9]{8,}`.
FAKE_XOXB = "-".join(("xo" + "xb", "2444556677", "8899001122", "AbCdEfGhIjKlMnOpQrStUvWx"))


def run_hook_matrix() -> Tuple[int, int]:
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

        # ---- secret_scan_guard ----
        # cwd is a non-repo tempdir so `git diff --cached` is empty and only the
        # command string is scanned (keeps the case hermetic / git-state-free).
        ("secret_scan: live xoxb- token in commit one-liner -> block",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": f'git add config.toml && git commit -m "wip: SLACK_BOT_TOKEN = {FAKE_XOXB}"'}},
         2),
        ("secret_scan: xoxb- placeholder (docs ellipsis) -> allow",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": 'git commit -m "docs: show xoxb-… placeholder in slack-workflow.md"'}},
         0),
        ("secret_scan: clean commit message -> allow",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": 'git commit -m "feat: clean message"'}},
         0),
        ("secret_scan: non-commit Bash with a token -> allow (only guards commits)",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": f'echo {FAKE_XOXB}'}},
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
        # cwd is a .venv-free dir (not REPO — this repo now ships its own .venv,
        # which would trip the bare-python rule): isolates the correct-name
        # creation form against the wrong-name rule (fleet-config#350).
        ("venv: python -m venv .venv -> allow",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": tempfile.gettempdir(), "tool_input": {"command": "python -m venv .venv"}},
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

        # ---- bash_windows_path_guard (issue #246) ----
        ("windows_path_guard: unquoted drive path -> block",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": r"ls E:\automation\fleet-config"}},
         2),
        ("windows_path_guard: forward-slash path -> allow",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": "ls E:/automation/fleet-config"}},
         0),
        ("windows_path_guard: double-quoted backslash path -> allow",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": 'echo "E:\\automation"'}},
         0),
        ("windows_path_guard: single-quoted backslash path -> allow",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": "echo 'E:\\automation'"}},
         0),
        ("windows_path_guard: backslash path inside heredoc body -> allow",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nE:\\automation\nEOF"}},
         0),
        ("windows_path_guard: same command on PowerShell -> allow (Bash-only guard)",
         "bash_windows_path_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": r"cd E:\automation"}},
         0),
        ("windows_path_guard: plain command, no drive path -> allow",
         "bash_windows_path_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
         0),

        # ---- docs_dated_filename_guard (block hook; no disk read needed) ----
        ("docs_guard: Write docs/2026-06-18-x.md -> block",
         "docs_dated_filename_guard",
         {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}},
         2),
        ("docs_guard: Write docs/architecture.md (topic name) -> allow",
         "docs_dated_filename_guard",
         {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/docs/architecture.md"}},
         0),
        ("docs_guard: dated file NOT under docs/ -> allow",
         "docs_dated_filename_guard",
         {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/src/2026-06-18-x.md"}},
         0),
        ("docs_guard: Edit (not Write) a dated docs file -> allow",
         "docs_dated_filename_guard",
         {"tool_name": "Edit", "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}},
         0),

        # ---- context_filter_hook (disabled unless env opts in) ----
        ("context_filter_hook: default off -> allow",
         "context_filter_hook",
         {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "git status --short"}},
         0),
    ]

    # ---- py_syntax_check needs real files ----
    tmp = Path(tempfile.mkdtemp(prefix="fleet-config-test-"))
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
    # fleet-config itself has no per-project slack_notify_channel in projects.toml,
    # but the [global] fallback IS now set. The hook will try to post but neither
    # SLACK_BOT_TOKEN nor a readable settings.json is in reach (both routed to
    # NO_SETTINGS_JSON above), so slack_notify returns False gracefully and the
    # hook still exits 0 without ever reaching the network.
    cases.append((
        "notify_on_idle: global channel set, missing token -> allow (graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "cwd": str(REPO), "message": "needs input"},
        0,
    ))
    # idle_prompt is now a deliberate no-op (the 💤 nag was dropped). It must exit
    # 0 without attempting a post — exercises the early-return guard.
    cases.append((
        "notify_on_idle: idle_prompt -> allow (no-op, idle nag dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "idle_prompt",
         "cwd": str(REPO), "message": "Claude is waiting for your input"},
        0,
    ))
    # permission_prompt still pings — with no token it takes the graceful-fail path.
    cases.append((
        "notify_on_idle: permission_prompt -> allow (ping attempted, graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "cwd": str(REPO), "message": "needs permission"},
        0,
    ))
    # fleet-config#443: a session_id present but NOT chief-managed (this fake id
    # can never appear in the real chief-managed.json) must fall straight through
    # to the existing human-ping path without ever invoking notify_chief's
    # subprocess/network call — exercises the new gate at exactly the boundary
    # that matters (present-but-unmanaged) with zero risk of reaching a real
    # live chief session. The genuinely chief-managed branch is covered by
    # direct unit tests on is_chief_managed/parse_chief_sid below instead —
    # deliberately never end-to-end here, since that would require a real
    # chief-managed.json entry and would let notify_chief actually shell out.
    cases.append((
        "notify_on_idle: permission_prompt with an unmanaged session_id -> allow (falls through to human ping)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "cwd": str(REPO), "message": "needs permission",
         "session_id": "fleet-config-test-fixture-sid-not-chief-managed"},
        0,
    ))
    # agent_needs_input / agent_completed (fleet-config#274) are background
    # sub-agent lifecycle events, not the parent session asking for you — a
    # deliberate no-op, same treatment as idle_prompt. Must exit 0 without
    # attempting a post.
    cases.append((
        "notify_on_idle: agent_needs_input -> allow (no-op, sub-agent noise dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "agent_needs_input",
         "cwd": str(REPO), "message": "needs input"},
        0,
    ))
    cases.append((
        "notify_on_idle: agent_completed -> allow (no-op, sub-agent noise dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "agent_completed",
         "cwd": str(REPO), "message": "Agent completed"},
        0,
    ))

    # ---- session_index: opt-in gating ----
    # A project not opted into capture must be a silent no-op (no indexer spawn).
    cases.append((
        "session_index: non-opted project (tempdir) -> no-op exit 0",
        "session_index",
        {"hook_event_name": "SessionStart", "cwd": tempfile.gettempdir()},
        0,
    ))

    # ---- chief_handover_sessionstart: cwd gating (fleet-config#442) ----
    # A session outside fleet-config is a silent no-op regardless of any
    # handover log's presence -- chief only ever runs cwd'd in fleet-config.
    cases.append((
        "chief_handover_sessionstart: non-fleet-config cwd -> no-op exit 0",
        "chief_handover_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": tempfile.gettempdir()},
        0,
    ))
    # fleet-config cwd but (almost certainly) no real handover log yet on this
    # machine -- still exit 0 either way; the content-bearing case is covered
    # by the dedicated unit-check function below with an isolated state dir.
    cases.append((
        "chief_handover_sessionstart: fleet-config cwd, no log -> exit 0",
        "chief_handover_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(REPO)},
        0,
    ))

    # ---- Grok Build payload shape, end to end (fleet-config#491) ----
    # Grok scans ~/.claude/settings.json for hooks by default, so every guard
    # here already runs inside a Grok session -- but its stdin envelope is
    # camelCase (`hookEventName`/`toolName`/`toolInput`) with lower_snake event
    # values, and its shell tool is `run_terminal_command`. Before
    # `_lib.normalize_payload()`, that mismatch made 6 of these 7 guards fire and
    # silently allow the identical dangerous command they block for Claude, while
    # still looking healthy in grok's own `/hooks` modal. These drive the real
    # hook modules with the exact envelope grok 0.2.114 emits; each one exits 0
    # instead of 2 against pre-fix code.
    def grok_bash(command: str) -> Dict[str, Any]:
        return {
            "hookEventName": "pre_tool_use",
            "toolName": "run_terminal_command",
            "toolInput": {"command": command},
            "cwd": str(REPO),
            "workspaceRoot": str(REPO),
            "sessionId": "grok-acceptance",
            "permissionMode": "default",
        }

    for label, hook, command in (
        ("AI attribution trailer", "pre_commit_no_ai_trailer",
         'git commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'),
        ("blanket python kill", "safe_kill_guard", "Stop-Process -Name python -Force"),
        ("force-push to main", "safe_kill_guard", "git push --force origin main"),
        ("--no-verify bypass", "safe_kill_guard", 'git commit --no-verify -m "x"'),
        ("venv creation as `venv`", "venv_discipline", "python -m venv venv"),
        ("native cmd.exe /c", "bash_cmdexe_syntax_guard", "cmd.exe /c dir"),
        ("unquoted Windows backslash path", "bash_windows_path_guard", "ls E:\\automation"),
    ):
        cases.append((
            f"grok shape: {label} -> block (parity with Claude shape)",
            hook,
            grok_bash(command),
            2,
        ))

    # The same guards must stay quiet on innocuous grok-shaped commands -- the
    # normalization must not turn them into blanket blockers.
    for label, hook, command in (
        ("clean commit message", "pre_commit_no_ai_trailer", 'git commit -m "feat: clean"'),
        ("ordinary status call", "safe_kill_guard", "git status"),
        ("venv python invocation", "venv_discipline", r"& .\.venv\Scripts\python.exe -V"),
    ):
        cases.append((
            f"grok shape: {label} -> allow",
            hook,
            grok_bash(command),
            0,
        ))

    # Grok collapses Edit/Write/MultiEdit into a single `search_replace` tool, so
    # the family must normalize to the one member a guard actually demands:
    # `docs_dated_filename_guard` requires `Write` exactly, and no hook requires
    # `Edit` exactly. Mapping to `Edit` would leave this guard silently inert
    # under Grok while every other edit-family hook kept working -- the same
    # class of half-fixed failure the whole issue is about.
    cases.append((
        "grok shape: dated docs/ filename via search_replace -> block",
        "docs_dated_filename_guard",
        {
            "hookEventName": "pre_tool_use",
            "toolName": "search_replace",
            "toolInput": {"file_path": str(REPO / "docs" / "2026-07-29-retro.md")},
            "cwd": str(REPO),
            "sessionId": "grok-acceptance",
        },
        2,
    ))

    failures = 0
    for name, hook, payload, expected in cases:
        code, _stdout, stderr = run(hook, payload)
        if not assert_exit(name, expected, code, stderr):
            failures += 1

    # Cleanup (moved up from main()'s end-of-run cleanup -- these tmp files
    # are only ever read by the py_syntax_check cases just run above).
    shutil.rmtree(tmp, ignore_errors=True)

    return failures, len(cases)
