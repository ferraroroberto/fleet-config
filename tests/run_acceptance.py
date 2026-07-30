"""Drive each hook with a sample payload and assert the expected exit code.

Run from the repo root (invoke the resolved Python path directly — a bare
``py``/``python`` is not reliably on ``PATH`` on this machine):
    E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py

Exit 0 if all cases pass, 1 otherwise. Prints a single line per case.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

REPO     = Path(__file__).resolve().parent.parent
HOOKS    = REPO / "hooks"

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

# A synthetic Slack-token-shaped string for the secret_scan_guard cases. It is
# assembled from fragments at runtime so the literal `xoxb-` token body never
# sits in this source file — a contiguous literal would trip GitHub's push
# protection (and the very guard under test). The assembled value still matches
# secret_scan_guard's regex `xoxb-\d{6,}-\d{6,}-[A-Za-z0-9]{8,}`.
FAKE_XOXB = "-".join(("xo" + "xb", "2444556677", "8899001122", "AbCdEfGhIjKlMnOpQrStUvWx"))

# A path that never exists on disk. slack_notify._token_from_settings() reads
# ~/.claude/settings.json as a fallback when SLACK_BOT_TOKEN isn't in the env —
# straight off disk via Path.home(), which on Windows resolves through the OS
# profile API and finds the real file even when a test subprocess's env dict
# omits SLACK_BOT_TOKEN (and even USERPROFILE). Without this override, every
# acceptance run posted real Slack pings to the real attention channel
# (fleet-config#<pending>).
NO_SETTINGS_JSON = str(Path(tempfile.gettempdir()) / "fleet-config-test-no-settings.json")


def run(hook: str, payload: Dict[str, Any], extra_env: Dict[str, str] | None = None) -> Tuple[int, str, str]:
    # Strip SLACK_BOT_TOKEN so a hook that posts to Slack (notify_on_idle) takes
    # the graceful-fail path instead of firing a real ping on every test run.
    env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
    env["CLAUDE_SETTINGS_JSON_PATH"] = NO_SETTINGS_JSON
    if extra_env:
        env.update(extra_env)
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
    total_checks = len(cases)
    skipped_checks = 0

    def run_unit(check_fn: Callable[[], Tuple[int, int]]) -> None:
        """Call one `_x_unit_checks()` function and fold its own
        `(failures, total)` into the running tally — the acceptance-matrix
        total is summed from real checks executed, never a hand-maintained
        constant that drifts silently when a check is added or removed
        (fleet-config#320)."""
        nonlocal failures, total_checks
        f, t = check_fn()
        failures += f
        total_checks += t

    for name, hook, payload, expected in cases:
        code, _stdout, stderr = run(hook, payload)
        if not assert_exit(name, expected, code, stderr):
            failures += 1

    # ---- context filter hook JSON + fixture eval ----
    run_unit(_context_filter_unit_checks)

    # ---- slack_notify unit checks (pure / no network) ----
    run_unit(_slack_notify_unit_checks)

    # ---- notify_on_idle mention-construction unit checks ----
    run_unit(_notify_mention_unit_checks)

    # ---- notify_on_idle classify / session-link / idle-suppression ----
    run_unit(_notify_classify_unit_checks)

    # ---- notify_on_idle Fleet-Board deep link (fleet-config#242) ----
    run_unit(_notify_board_link_unit_checks)

    # ---- notify_on_idle chief-managed routing (fleet-config#443) ----
    run_unit(_notify_chief_routing_unit_checks)

    # ---- block_askuserquestion_chief: enforce, don't just discourage (fleet-config#463) ----
    run_unit(_block_askuserquestion_chief_unit_checks)

    # ---- _lib.detect_project: worktree-sibling cwd resolution (fleet-config#471) ----
    run_unit(_lib_detect_project_unit_checks)

    # ---- chief_handover_sessionstart pure logic + end-to-end (fleet-config#442) ----
    run_unit(_chief_handover_sessionstart_unit_checks)

    # ---- session_state board-row persistence (fleet-config#91) ----
    run_unit(_session_state_unit_checks)

    # ---- session_state_codex / session_state_pi adapters (fleet-config#349) ----
    run_unit(_session_state_agent_adapter_unit_checks)

    # ---- notify_complete deterministic message assembly + resolver ----
    run_unit(_notify_complete_unit_checks)

    # ---- work_summary roll-up block + per-file table (pure, no gh) ----
    run_unit(_work_summary_unit_checks)

    # ---- Pi usage collector parses model/provider/token telemetry (pure) ----
    run_unit(_pi_usage_stats_unit_checks)

    # ---- slack category -> channel routing (issue #139) ----
    run_unit(_slack_routing_unit_checks)

    # ---- conversation_capture session-dedup logic ----
    run_unit(_conversation_capture_unit_checks)

    # ---- conversation capture/index config-driven routing + indexing ----
    run_unit(_conversation_index_unit_checks)

    # ---- restart_and_verify_webapp restart-strategy + recovery hint ----
    run_unit(_restart_webapp_unit_checks)

    # ---- gh_body_file_guard: warn-only stdout assertions ----
    run_unit(_gh_body_file_guard_unit_checks)

    # ---- bash_cmdexe_syntax_guard: block + warn assertions (#264, #385) ----
    run_unit(_bash_cmdexe_syntax_guard_unit_checks)

    # ---- Tier 2/3 hooks: docs-guard env override + warn-hook stdout (issue #158) ----
    run_unit(_tier23_hooks_unit_checks)

    # ---- branch_before_edit_guard: real temp git repos/worktrees x launcher env, target-path resolution (fleet-config#464) ----
    run_unit(_branch_before_edit_guard_unit_checks)

    # ---- audit_issue helper pure-logic tests (skills/_lib) ----
    run_unit(_audit_issue_unit_check)

    # ---- fleet_audit_scan helper pure-logic tests (skills/_lib) ----
    run_unit(_fleet_audit_scan_unit_check)

    # ---- design_sweep_scan helper pure-logic tests (skills/_lib) ----
    run_unit(_design_sweep_scan_unit_check)

    # ---- worktree_claim helper pure-logic tests (skills/_lib) ----
    run_unit(_worktree_claim_unit_check)

    # ---- active_issue helper + issue-workflow lifecycle wiring ----
    run_unit(_active_issue_unit_check)

    # ---- claude_progress stream parser + scheduled-wrapper wiring ----
    run_unit(_claude_progress_unit_check)

    # ---- ux_surface helper pure-logic tests (skills/_lib) ----
    run_unit(_ux_surface_unit_check)

    # ---- e2e_test_audit helper pure-logic tests (skills/_lib, fleet-config#406) ----
    run_unit(_e2e_test_audit_unit_check)

    # ---- html_shot helper pure-logic tests (skills/_lib, fleet-config#96) ----
    run_unit(_html_shot_unit_check)

    # ---- docs_shots_plan helper pure-logic tests (skills/_lib, fleet-config#93) ----
    run_unit(_docs_shots_plan_unit_check)

    # ---- browser_verify helper pure-logic tests (skills/_lib) ----
    run_unit(_browser_verify_unit_check)

    # ---- cert_drift helper pure-logic tests (skills/_lib) ----
    run_unit(_cert_drift_unit_check)

    # ---- context-purge check.py pure-logic tests (.claude/skills/context-purge) ----
    run_unit(_context_purge_check_unit_check)

    # ---- context-purge gate.py ledger pure-logic tests ----
    run_unit(_context_purge_gate_unit_check)

    # ---- design_lint helper pure-logic tests (skills/_lib) ----
    run_unit(_design_lint_unit_check)

    # ---- rate_gate helper pure-logic tests (skills/_lib) ----
    run_unit(_rate_gate_unit_check)

    # ---- chief_ops helper pure-logic tests (skills/_lib, fleet-config#445) ----
    run_unit(_chief_ops_unit_check)

    # ---- chief_managed helper pure-logic tests (skills/_lib, fleet-config#443) ----
    run_unit(_chief_managed_unit_check)

    # ---- dirty_tree_check helper pure-logic tests (skills/_lib) ----
    run_unit(_dirty_tree_check_unit_check)

    # ---- git_run helper pure-logic tests (skills/_lib, fleet-config#485) ----
    run_unit(_git_run_unit_check)

    # ---- foreign-harness payload normalization (fleet-config#491) ----
    run_unit(_payload_normalization_unit_check)

    # ---- deploy_coverage helper pure-logic tests (skills/_lib, fleet-config#459) ----
    run_unit(_deploy_coverage_unit_check)

    # ---- vendored_drift helper pure-logic tests (skills/_lib) ----
    run_unit(_vendored_drift_unit_check)

    # ---- sota-watch watchlist.py pure-logic tests (.claude/skills/sota-watch) ----
    run_unit(_watchlist_unit_check)

    # ---- learning-log report.py pure helpers (.claude/skills/learning-log) ----
    run_unit(_learning_log_unit_checks)

    # ---- system-map: fleet ↔ data ↔ doc coverage (architecture/) ----
    run_unit(_system_map_coverage_check)

    # ---- system-map: per-repo .fleet.toml aggregation + anti-staleness ----
    run_unit(_fleet_toml_check)

    # ---- system-map: Mermaid companion render (render_mermaid.py) freshness ----
    run_unit(_mermaid_check)

    # ---- system-map: week-over-week 'what changed' diff (whatchanged.py) ----
    run_unit(_system_map_whatchanged_check)

    # ---- config-map: introspected config.data.js freshness + whatchanged ----
    run_unit(_config_map_check)

    # ---- Codex hook wiring: direct Python commands with bounded timeouts ----
    run_unit(_codex_hooks_config_check)

    # ---- settings: live ~/.claude/settings.json ⊇ template hook wiring ----
    # Not run_unit: this check has a third state (skipped, when the live file
    # is absent) that must never fold into total_checks/failures (fleet-config#501).
    _stsc_f, _stsc_t, _stsc_s = _settings_template_sync_check()
    failures += _stsc_f
    total_checks += _stsc_t
    skipped_checks += _stsc_s

    # ---- Windows console suppression on every runtime spawn (#399 / #412) ----
    run_unit(_no_window_unit_check)

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"Total: {total_checks} | Failed: {failures} | Skipped: {skipped_checks}")
    return 0 if failures == 0 else 1


class _Checker:
    """Print + count one OK/FAIL case — the shared body every
    `_x_unit_checks()` function below used to hand-roll as a local `check()`
    closure plus a `nonlocal failures` counter. Centralizing it means each
    function's own `(failures, total)` return is the real count of checks it
    ran, so `main()` can sum the acceptance-matrix total at call time instead
    of the hand-maintained `_UNIT_CHECK_COUNT` constant (fleet-config#320)."""

    def __init__(self) -> None:
        self.failures = 0
        self.total = 0

    def __call__(self, case: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            self.failures += 1
            if detail:
                for line in detail.strip().splitlines():
                    print(f"        | {line}")


def _subprocess_unit_check(label: str, test_file: str) -> Tuple[int, int]:
    """Run a standalone pure-logic test file as a subprocess and report it as
    one pass/fail check — the shared body behind the `_x_unit_check` wrappers
    that each point it at one focused file under tests/. Returns
    (failures, total=1)."""
    proc = subprocess.run(
        [PYTHON, str(REPO / "tests" / test_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = proc.returncode == 0
    print(f"{'OK   ' if ok else 'FAIL '} {label}: pure-logic unit tests")
    if not ok:
        for line in (proc.stdout or "").strip().splitlines():
            print(f"        | {line}")
    return (0 if ok else 1), 1


def _context_filter_unit_checks() -> Tuple[int, int]:
    check = _Checker()

    payload = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }
    code, stdout, stderr = run("context_filter_hook", payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    check(
        "context_filter_hook: rewrite mode emits updatedInput",
        code == 0 and "context_filter_cli.py" in stdout and "updatedInput" in stdout,
        stdout + stderr,
    )

    rewritten_command = ""
    if code == 0 and stdout.strip():
        rewritten_command = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    check(
        "context_filter_hook: rewritten command has no raw backslash paths (fleet-config#405)",
        code == 0 and "\\" not in rewritten_command,
        rewritten_command,
    )
    check(
        "context_filter_hook: PowerShell rewrite uses the call operator (fleet-config#405)",
        rewritten_command.startswith("& "),
        rewritten_command,
    )

    bash_payload = {
        "tool_name": "Bash",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }
    code, stdout, stderr = run("context_filter_hook", bash_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    bash_rewritten = ""
    if code == 0 and stdout.strip():
        bash_rewritten = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    check(
        "context_filter_hook: Bash rewrite has no raw backslashes and no call operator (fleet-config#405)",
        code == 0 and "\\" not in bash_rewritten and not bash_rewritten.startswith("&"),
        bash_rewritten,
    )

    streaming = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "npm run dev -- --watch"},
    }
    code, stdout, stderr = run("context_filter_hook", streaming, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    check(
        "context_filter_hook: streaming command passthrough",
        code == 0 and stdout.strip() == "",
        stdout + stderr,
    )

    res = subprocess.run(
        [
            PYTHON,
            str(HOOKS / "context_filter_cli.py"),
            "eval",
            "--fixtures",
            str(REPO / "tests" / "fixtures" / "context_filter"),
            "--min-median-reduction",
            "35",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    check(
        "context_filter_eval: fixture benchmark passes",
        res.returncode == 0 and "median reduction:" in res.stdout,
        res.stdout + res.stderr,
    )

    # ---- wrapper timeout must not outlive a pipe-holding grandchild (#411) ----
    # subprocess.run(capture_output, timeout=) reacts to a timeout by killing only
    # the direct child, then collecting output with NO timeout — so a surviving
    # grandchild that inherited the pipes blocks it forever. That wedged a
    # scheduled run for eight hours. The wrapper must kill the tree and return
    # 124 well before the grandchild would have exited on its own.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "hold_pipe.py"
        probe.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        started = time.monotonic()
        timed_out = False
        try:
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool",
                    "PowerShell",
                    "--mode",
                    "shadow",
                    "--encoded",
                    encoded,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = time.monotonic() - started
        check(
            "context_filter_cli: pipe-holding grandchild does not outlive the wrapper timeout (fleet-config#411)",
            not timed_out and res.returncode == 124 and elapsed < 45,
            f"timed_out={timed_out} elapsed={elapsed:.1f}s "
            + ("" if timed_out else f"rc={res.returncode} stderr={res.stderr.strip()}"),
        )
        check(
            "context_filter_cli: timeout message names the tree kill",
            not timed_out and "process tree killed" in res.stderr,
            "" if timed_out else res.stderr.strip(),
        )

    # ---- timed-out stdout must carry an in-band truncation marker (#424) ----
    # A consumer reading stdout as the command's output (e.g. JSON from a fleet
    # sweep helper) must not be able to mistake a wrapper-timeout truncation for
    # complete output. The marker has to land on stdout itself, not just stderr.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "slow_stdout.py"
        probe.write_text(
            "import sys, time\n"
            "for i in range(30):\n"
            "    sys.stdout.write(str(i) + chr(10))\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(1)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool",
                "PowerShell",
                "--mode",
                "shadow",
                "--encoded",
                encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
            timeout=60,
        )
        check(
            "context_filter_cli: timed-out stdout carries an in-band truncation marker (fleet-config#424)",
            res.returncode == 124 and "OUTPUT TRUNCATED" in res.stdout and "0" in res.stdout,
            res.stdout.strip() + " | " + res.stderr.strip(),
        )

    # ---- skill helpers are never wrapped; ordinary commands still are ----
    # A helper shipped with a skill produces one payload the orchestrator parses
    # directly, so wrapping it risks the #424 truncation for no compression
    # upside. #427 widened the rule from skills/_lib to every skill directory —
    # the longest-running helpers live beside their own skill (fleet-health's
    # capture.py blocks 540s against the 600s cap; system-map's build_data.py
    # crawls the whole fleet). The negative rows are the point of the table:
    # widening the pattern must not swallow ordinary work.
    python_prefix = '& "E:/automation/fleet-config/.venv/Scripts/python.exe" '
    skill_dir = '"E:/automation/fleet-config/.claude/skills/'
    passthrough_cases = [
        ("skills/_lib sweep helper",
         python_prefix + '"skills/_lib/fleet_audit_scan.py" --root E:\\automation', True, 424),
        ("fleet-health capture.py",
         python_prefix + skill_dir + 'fleet-health/capture.py" --minutes 9', True, 427),
        ("system-map build_data.py",
         python_prefix + skill_dir + 'system-map/build_data.py"', True, 427),
        ("ordinary python -c", python_prefix + '-c "print(1)"', False, 427),
        ("ordinary git", "git status --short", False, 427),
    ]
    for label, command, expect_passthrough, issue in passthrough_cases:
        payload = {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": command}}
        code, stdout, stderr = run(
            "context_filter_hook", payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
        )
        verb = "passthrough, not wrapped" if expect_passthrough else "still wrapped"
        check(
            f"context_filter_hook: {label} {verb} (fleet-config#{issue})",
            code == 0 and (stdout.strip() == "") == expect_passthrough,
            stdout + stderr,
        )

    # ---- wrapper stdout re-emission survives non-cp1252 output (#426) ----
    # The wrapped child's own output is decoded as explicit UTF-8 in
    # _run_command, but context_filter_cli.py's own sys.stdout.write of that
    # output falls back to the locale codec (cp1252 here) unless reconfigured
    # to UTF-8 up front. An emoji/astral codepoint used to crash the wrapper
    # with UnicodeEncodeError instead of passing the output through.
    emoji_command = "Write-Output ([System.Char]::ConvertFromUtf32(0x1F4CA))"
    encoded = base64.b64encode(emoji_command.encode("utf-8")).decode("ascii")
    for mode in ("shadow", "rewrite"):
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool",
                "PowerShell",
                "--mode",
                mode,
                "--encoded",
                encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONUTF8": "0"},
            timeout=30,
        )
        check(
            f"context_filter_cli: {mode} mode survives non-cp1252 output (fleet-config#426)",
            res.returncode == 0 and "UnicodeEncodeError" not in res.stderr,
            res.stdout.strip() + " | " + res.stderr.strip(),
        )
    return check.failures, check.total


def _system_map_coverage_check() -> Tuple[int, int]:
    """The system map must cover exactly the fleet, and the doc must agree.

    Guards the `/system-map` single source of truth (architecture/fleet.data.js)
    against drift, mechanically:
      1. every fleet repo (projects.toml − [global] architecture_ignore) appears
         on the map;
      2. no map entry is a stale/typo'd repo absent from the fleet;
      3. every mapped repo also appears in ARCHITECTURE.md (data ↔ doc agree).
    Returns the failure count.
    """
    import json
    import tomllib

    check = _Checker()

    arch = REPO / "architecture"
    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    fleet = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    } - ignore

    # fleet.data.js holds `window.FLEET = { ...strict JSON... };` — slice the object out.
    raw = (arch / "fleet.data.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    mapped = {
        e.get("repo", e["nm"])
        for section in ("governance", "enabling", "web", "pipe")
        for e in data.get(section, [])
    }

    missing = fleet - mapped
    stale = mapped - fleet
    check(f"system_map: every fleet repo is on the map (missing: {sorted(missing) or 'none'})", not missing)
    check(f"system_map: no stale map entries (stale: {sorted(stale) or 'none'})", not stale)

    doc = (arch / "ARCHITECTURE.md").read_text(encoding="utf-8")
    doc_missing = sorted(r for r in mapped if r not in doc)
    check(f"system_map: every mapped repo is in ARCHITECTURE.md (missing: {doc_missing or 'none'})", not doc_missing)

    return check.failures, check.total


def _fleet_toml_check() -> Tuple[int, int]:
    """Per-repo `.fleet.toml` aggregation is fresh and can't silently go stale.

    Guards the self-describing map (`build_data.py`: residual + per-repo
    `.fleet.toml` → `fleet.data.js`):
      1. `fleet.data.js` is exactly what `build_data.py` regenerates — a forgotten
         regen, a hand-edit, or an un-committed `.fleet.toml` change fails loud;
      2. every repo in the residual's `_adopted` registry still carries a
         `.fleet.toml` on its committed default branch — deleting one (which would
         silently revert to the central fallback) fails loud;
      3. every present `.fleet.toml` is a valid declaration (parses, `layer` in
         the enum, required fields set).
    Returns the failure count.
    """
    import importlib.util
    import tomllib

    check = _Checker()

    bd_path = REPO / ".claude" / "skills" / "system-map" / "build_data.py"
    spec = importlib.util.spec_from_file_location("system_map_build_data", bd_path)
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)

    committed = (REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8")
    try:
        fresh = bd.regenerate() == committed
        regen_err = ""
    except Exception as exc:  # noqa: BLE001 - surface a malformed declaration cleanly
        fresh, regen_err = False, f" ({exc})"
    check(f"fleet_toml: fleet.data.js matches build_data.py output{regen_err}", fresh)

    residual = bd.load_residual()
    repos = bd.fleet_repos()
    adopted = residual.get("_adopted", [])
    missing = [r for r in adopted if r not in repos or bd.read_fleet_toml(repos[r]) is None]
    check(f"fleet_toml: every adopted repo still has a .fleet.toml (missing: {sorted(missing) or 'none'})", not missing)

    invalid = []
    for name, repo_dir in sorted(repos.items()):
        text = bd.read_fleet_toml(repo_dir)
        if text is None:
            continue
        try:
            bd.card_from_toml(name, tomllib.loads(text))
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{name}: {exc}")
    check(f"fleet_toml: every present .fleet.toml is valid (invalid: {invalid or 'none'})", not invalid)

    return check.failures, check.total


def _mermaid_check() -> Tuple[int, int]:
    """The Mermaid companion render (`render_mermaid.py`) can't silently go stale.

    Guards the text-native fleet map the same way `_fleet_toml_check` guards
    `fleet.data.js`:
      1. `system-map.mmd` is exactly what `render_mermaid.py` regenerates from
         the current `fleet.data.js` — a forgotten regen fails loud;
      2. the marked `<!-- system-map:mermaid:start -->…:end` block inside
         `global-CLAUDE.md` embeds that same flowchart body verbatim.
    Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    rm_path = REPO / ".claude" / "skills" / "system-map" / "render_mermaid.py"
    spec = importlib.util.spec_from_file_location("system_map_render_mermaid", rm_path)
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)

    data = rm.load_data((REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8"))
    rendered = rm.render(data)
    flowchart_body = rm.render_flowchart(data)

    committed = (REPO / "architecture" / "system-map.mmd").read_text(encoding="utf-8")
    check("mermaid: system-map.mmd matches render_mermaid.py output", rendered == committed)

    claude_md = (REPO / "global-CLAUDE.md").read_text(encoding="utf-8")
    check(
        "mermaid: global-CLAUDE.md fleet-map block matches the current flowchart",
        rm.CLAUDE_MD_START in claude_md and f"```mermaid\n{flowchart_body}```" in claude_md,
    )

    return check.failures, check.total


def _system_map_whatchanged_check() -> Tuple[int, int]:
    """The /system-map week-over-week diff (.claude/skills/system-map/whatchanged.py).

    Pure-logic guard on the diff that feeds the one-line Slack summary: added /
    removed repos are named, in-place edits are counted, a no-op week and a
    first run read sensibly. Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    wc_path = REPO / ".claude" / "skills" / "system-map" / "whatchanged.py"
    spec = importlib.util.spec_from_file_location("system_map_whatchanged", wc_path)
    wc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = 'window.FLEET = {"web":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],"pipe":[{"nm":"c","ds":"z"}]};'
    # add d, remove b, edit a's description, c unchanged.
    cur = 'window.FLEET = {"web":[{"nm":"a","ds":"X2"},{"nm":"d","ds":"w"}],"pipe":[{"nm":"c","ds":"z"}]};'
    diff = wc.diff_fleet(prev, cur)
    check("system_map_whatchanged: detects an added repo", diff["added"] == ["d"])
    check("system_map_whatchanged: detects a removed repo", diff["removed"] == ["b"])
    check("system_map_whatchanged: counts edited cards, ignores unchanged", diff["updated"] == ["a"])

    # repo-keyed card (display name differs from repo) is keyed by `repo`.
    repo_prev = 'window.FLEET = {"web":[{"nm":"grocery","repo":"grocery-shopping-automation","ds":"x"}]};'
    repo_cur = 'window.FLEET = {"web":[]};'
    check("system_map_whatchanged: keys cards by repo-or-nm",
          wc.diff_fleet(repo_prev, repo_cur)["removed"] == ["grocery-shopping-automation"])

    check("system_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+d, −b, 1 repo updated")
    check("system_map_whatchanged: empty diff reads 'no fleet changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no fleet changes")
    check("system_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total


def _config_map_check() -> Tuple[int, int]:
    """The /config-map data is fresh, and its week-over-week diff behaves.

    Guards the introspected config map (`.claude/skills/config-map`):
      1. `config.data.js` is exactly what `build_data.py` regenerates — a forgotten
         regen, a hand-edit, a new skill/hook, or a re-wired `install.ps1` link
         fails loud (same anti-staleness contract as `/system-map`);
      2. `whatchanged.py` pure-logic: adds/removes are named across every
         dimension (skills/hooks/matrix/conventions), edits are counted, repo
         keys collapse to a short label, and the no-op / first-run lines read
         sensibly.
    Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    cm_dir = REPO / ".claude" / "skills" / "config-map"
    bd_spec = importlib.util.spec_from_file_location("config_map_build_data", cm_dir / "build_data.py")
    bd = importlib.util.module_from_spec(bd_spec)
    bd_spec.loader.exec_module(bd)  # type: ignore[union-attr]

    committed = (REPO / "architecture" / "config.data.js").read_text(encoding="utf-8")
    try:
        fresh = bd.regenerate() == committed
        regen_err = ""
    except Exception as exc:  # noqa: BLE001
        fresh, regen_err = False, f" ({exc})"
    check(f"config_map: config.data.js matches build_data.py output{regen_err}", fresh)

    wc_spec = importlib.util.spec_from_file_location("config_map_whatchanged", cm_dir / "whatchanged.py")
    wc = importlib.util.module_from_spec(wc_spec)
    wc_spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],'
            '"hooks":[{"nm":"h1","ds":"z"}]};')
    # add skill c, remove skill b, edit a's description, hook h1 unchanged.
    cur = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"X2"},{"nm":"c","ds":"w"}],'
           '"hooks":[{"nm":"h1","ds":"z"}]};')
    diff = wc.diff_config(prev, cur)
    check("config_map_whatchanged: detects an added entry", diff["added"] == ["skill:c"])
    check("config_map_whatchanged: detects a removed entry", diff["removed"] == ["skill:b"])
    check("config_map_whatchanged: counts edited entries, ignores unchanged", diff["updated"] == ["skill:a"])
    check("config_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+c, −b, 1 updated")

    # repo-specific skills flatten to repo:<repo>/<item>; the label drops the path.
    rp = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1","j2"]}]};'
    rc = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1"]}]};'
    check("config_map_whatchanged: keys repo skills by path, labels by short name",
          wc.format_line(wc.diff_config(rp, rc)) == "−j2")

    check("config_map_whatchanged: empty diff reads 'no config changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no config changes")
    check("config_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total


def _settings_template_sync_check() -> Tuple[int, int, int]:
    """Every hook wired in settings.template.json must also be wired in the live
    ~/.claude/settings.json.

    The live file is machine-local and NOT version-controlled (it carries
    permissions + secrets), so it can silently drift from the template — a hook
    can ship in the repo yet never actually run. This guard fails loudly when a
    template-wired `(event, hook)` is missing from the live file. Direction is
    template ⊆ live only: machine-local *extra* hooks are legitimate and don't
    fail. Skips gracefully (one line, exit 0) when the live file is absent, so
    it never breaks on a machine without it. Prints exactly one line either way
    — always one check, whether skipped or run — but a skip is its own state:
    it contributes to neither Total nor Failed, only to the separate Skipped
    counter, so a run that couldn't verify the live file never reads identical
    to one that actually verified it and passed (fleet-config#461, #501).
    """
    import re

    hook_re = re.compile(r"-Hook\s+(\w+)")

    def wired(path: Path) -> set[tuple[str, str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs: set[tuple[str, str]] = set()
        for event, blocks in data.get("hooks", {}).items():
            for block in blocks:
                for hook in block.get("hooks", []):
                    m = hook_re.search(hook.get("command", ""))
                    if m:
                        pairs.add((event, m.group(1)))
        return pairs

    live_path = Path.home() / ".claude" / "settings.json"
    if not live_path.exists():
        print("SKIP  settings_sync: no live ~/.claude/settings.json (skipped)")
        return 0, 0, 1

    template = wired(REPO / "settings.template.json")
    live = wired(live_path)
    missing = sorted(template - live)
    ok = not missing
    print(f"{'OK   ' if ok else 'FAIL '} settings_sync: template hooks all wired live "
          f"(missing: {missing or 'none'})")
    return (0 if ok else 1), 1, 0


# Directories whose Python is *runtime* code — it spawns executables under a
# console-less parent (a scheduled `claude -p` job, a tray, a hook). `tests/` is
# excluded on purpose: the acceptance suite runs from a real console, and several
# cases assert on spawn kwargs, so forcing the flag there would be noise.
_SPAWN_SCAN_DIRS = ("hooks", "skills", ".claude/skills")
_SPAWN_ATTRS = {"run", "Popen", "call", "check_output", "check_call"}


def _spawn_sites_missing_flags() -> "list[str]":
    """Every `subprocess.<spawn>(...)` under `_SPAWN_SCAN_DIRS` that omits
    `creationflags=`, as `path:line` strings. Parsed with `ast`, so a commented-
    out or string-literal example can't produce a false positive."""
    import ast

    offenders: list[str] = []
    for rel in _SPAWN_SCAN_DIRS:
        for py in sorted((REPO / rel).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError) as exc:  # pragma: no cover - byte-compile catches these first
                offenders.append(f"{py.relative_to(REPO).as_posix()}: unparseable ({exc})")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                # `from subprocess import Popen` would evade this Attribute match;
                # `_spawn_import_style_offenders` below asserts nobody uses it.
                if not (isinstance(fn, ast.Attribute) and fn.attr in _SPAWN_ATTRS
                        and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
                    continue
                if any(kw.arg == "creationflags" for kw in node.keywords):
                    continue
                offenders.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
    return offenders


def _spawn_import_style_offenders() -> "list[str]":
    """Files under `_SPAWN_SCAN_DIRS` using `from subprocess import <spawn>`.

    That form is bare-name-called, so the AST scan above cannot see it. Keeping
    the count at zero is what makes the scan a sound gate rather than a partial
    one — hence a check of its own rather than a silently-broadened matcher."""
    import ast

    offenders: list[str] = []
    for rel in _SPAWN_SCAN_DIRS:
        for py in sorted((REPO / rel).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError):  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom) and node.module == "subprocess"
                        and any(a.name in _SPAWN_ATTRS for a in node.names)):
                    offenders.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
    return offenders


def _no_window_unit_check() -> Tuple[int, int]:
    """Windows console suppression on every runtime subprocess spawn (#412).

    The global CLAUDE.md convention ("Subprocess spawns must suppress the console
    window (Windows)", #399) is invisible at runtime on this box — an unsuppressed
    spawn only misbehaves under a *console-less* parent, which is exactly where
    nobody is watching: the scheduled `claude -p` jobs behind every
    `run-weekly.bat`. So the gate is static: parse the runtime trees and assert
    every spawn carries `creationflags`, plus assert the two tiers' `NO_WINDOW`
    definitions agree so the intentional duplication cannot drift.
    """
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(REPO / "skills" / "_lib"))
    import _lib  # noqa: E402
    import no_window  # noqa: E402

    check = _Checker()

    expected = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    check("no_window: skills/_lib NO_WINDOW == CREATE_NO_WINDOW on win32, else 0",
          no_window.NO_WINDOW == expected, f"got {no_window.NO_WINDOW!r}, want {expected!r}")
    check("no_window: hooks/_lib NO_WINDOW agrees with the skills-tier copy",
          _lib.NO_WINDOW == no_window.NO_WINDOW,
          f"hooks={_lib.NO_WINDOW!r} skills={no_window.NO_WINDOW!r}")

    import_offenders = _spawn_import_style_offenders()
    check("no_window: no runtime file uses `from subprocess import <spawn>` "
          "(would evade the scan below)",
          not import_offenders, "\n".join(import_offenders))

    offenders = _spawn_sites_missing_flags()
    check(f"no_window: every subprocess spawn in {', '.join(_SPAWN_SCAN_DIRS)} "
          "passes creationflags",
          not offenders,
          "missing creationflags=NO_WINDOW at:\n" + "\n".join(offenders))

    return check.failures, check.total


def _codex_hooks_config_check() -> Tuple[int, int]:
    """Codex hooks should run Python directly and fail fast.

    The Claude side still goes through ``run-hook.ps1`` because Claude Code runs
    settings commands through Git Bash on this Windows machine. Codex does not
    need that shim, and routing it through PowerShell caused all PreToolUse
    hooks to hang until Codex's default 600-second timeout. This check keeps the
    Codex wiring on the direct-Python path and proves the configured commands
    return promptly when driven with a minimal hook payload.
    """
    check = _Checker()

    data = json.loads((REPO / "codex-hooks.json").read_text(encoding="utf-8"))
    hook_entries = [
        hook
        for blocks in data.get("hooks", {}).values()
        for block in blocks
        for hook in block.get("hooks", [])
    ]
    commands = [str(hook.get("command", "")) for hook in hook_entries]
    timeouts = [hook.get("timeout") for hook in hook_entries]

    check(
        "codex_hooks: every hook has a <=15s timeout",
        bool(hook_entries) and all(isinstance(t, int) and 1 <= t <= 15 for t in timeouts),
        f"timeouts: {timeouts}",
    )
    check(
        "codex_hooks: commands bypass run-hook.ps1 / PowerShell",
        all("run-hook.ps1" not in c and "powershell" not in c.lower() for c in commands),
        "\n".join(commands),
    )
    check(
        "codex_hooks: commands invoke hook modules directly",
        all(re.search(r"^E:/automation/fleet-config/\.venv/Scripts/python\.exe\s+C:/Users/rober/\.codex/hooks/\w+\.py$", c) for c in commands),
        "\n".join(commands),
    )

    env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
    env["CLAUDE_SETTINGS_JSON_PATH"] = NO_SETTINGS_JSON
    smoke_failures: list[str] = []
    for command in commands:
        try:
            res = subprocess.run(
                command,
                input="{}",
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                shell=True,
            )
        except subprocess.TimeoutExpired:
            smoke_failures.append(f"{command} -> timed out")
            continue
        if res.returncode != 0:
            smoke_failures.append(
                f"{command} -> exit {res.returncode}: {(res.stderr or res.stdout).strip()}"
            )

    check(
        "codex_hooks: configured commands return promptly",
        not smoke_failures,
        "\n".join(smoke_failures),
    )

    return check.failures, check.total


def _slack_notify_unit_checks() -> Tuple[int, int]:
    """Exercise slack_notify without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

    check(
        "slack_notify: archive URL -> bare id",
        slack_notify.parse_channel("https://x.slack.com/archives/C0B76GBA0LS") == "C0B76GBA0LS",
    )
    check(
        "slack_notify: bare id passes through",
        slack_notify.parse_channel("  C0B76GBA0LS  ") == "C0B76GBA0LS",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var AND neutralize the settings.json fallback around the call, so
    # neither a real token in the dev box's env nor one in ~/.claude/settings.json
    # can trigger a post — this exercises the genuine "no token anywhere" path.
    saved = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_from_settings = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: None
    try:
        result = slack_notify.notify("test", channel="C0B76GBA0LS", token=None)
    finally:
        slack_notify._token_from_settings = saved_from_settings
        if saved is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved
    check("slack_notify: missing token -> False (graceful)", result is False)

    # The settings.json fallback resolves a token when the env var is unset —
    # this is the launcher-agnostic behaviour (#192). Stub the file reader so the
    # check is hermetic (independent of whether the dev box's settings.json has a
    # token) and confirm the resolution order: env var wins, else settings.json.
    saved_env = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_reader = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: "xoxb-from-settings"
    try:
        from_settings = slack_notify._resolve_token(None)
        os.environ[slack_notify.TOKEN_ENV_VAR] = "xoxb-from-env"
        env_wins = slack_notify._resolve_token(None)
    finally:
        slack_notify._token_from_settings = saved_reader
        os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
        if saved_env is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved_env
    check("slack_notify: settings.json fallback resolves token when env unset",
          from_settings == "xoxb-from-settings")
    check("slack_notify: env var wins over settings.json fallback",
          env_wins == "xoxb-from-env")

    return check.failures, check.total


def _notify_mention_unit_checks() -> Tuple[int, int]:
    """The single-sourced @mention decision in slack_notify (off by default).

    Mentioning now lives in exactly one place — ``slack_notify.notify()`` — via
    two pure helpers. No caller hand-assembles ``<@U…>`` anymore.
    """
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

    check("mention_prefix: enabled + user -> tag",
          slack_notify._mention_prefix("U0B71PQEL6S", True) == "<@U0B71PQEL6S> ")
    check("mention_prefix: disabled -> no tag",
          slack_notify._mention_prefix("U0B71PQEL6S", False) == "")
    check("mention_prefix: enabled but no user -> no tag",
          slack_notify._mention_prefix(None, True) == "")
    check("resolve_mention: explicit override wins",
          slack_notify._resolve_mention(True) is True
          and slack_notify._resolve_mention(False) is False)
    # None -> read the [global] slack_notify_mention toggle, which ships off.
    check("resolve_mention: None -> global toggle (off by default)",
          slack_notify._resolve_mention(None) is False)

    return check.failures, check.total


def _notify_classify_unit_checks() -> Tuple[int, int]:
    """Per-type icon/wording and bridge session-link parsing — the two
    deterministic pieces of the notification logic."""
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- classify: icon per notification_type, message passed through ----
    icon, text = notify_on_idle.classify(
        {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
    )
    check("classify: permission -> bell icon + 'awaits your input'",
          icon == "🔔" and text == "Claude Code awaits your input")
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

    return check.failures, check.total


def _notify_board_link_unit_checks() -> Tuple[int, int]:
    """Fleet-Board deep-link line (fleet-config#242): _lib.resolve_board_url's
    project-override/global-fallback/unset resolution, and notify_on_idle's
    board_link() message assembly — all against synthetic registries so
    nothing touches the real projects.toml."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # The real machine may have FLEET_BOARD_URL genuinely set (fleet-config#271
    # is meant to be configured this way) — clear it for the duration of these
    # checks so "unset"/"[global] fallback" expectations aren't at the mercy of
    # the ambient environment, then restore whatever was there.
    env_key = _lib.BOARD_URL_ENV_VAR
    old_env = os.environ.pop(env_key, None)
    try:
        # ---- resolve_board_url: unset -> None (byte-identical default behavior) ----
        unset = _lib.Registry(projects=[], globals=_lib.GlobalConfig(never_kill_ports=()))
        check("resolve_board_url: neither set -> None",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) is None)

        # ---- resolve_board_url: [global] fallback ----
        glob_only = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: [global] fallback",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://global.example:8445")

        # ---- resolve_board_url: per-project override wins ----
        proj = _lib.ProjectConfig(
            name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
            gate_trigger_globs=(), gate_cmd=None, tray_cmd=None, restart_cmd=None,
            api_version_path=None, extra={"board_url": "https://proj.example:8445"},
        )
        reg = _lib.Registry(
            projects=[proj],
            globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: per-project override wins over [global]",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")

        # ---- resolve_board_url: FLEET_BOARD_URL env var precedence (fleet-config#271) ----
        # public-repo-safe indirection: env var sits between the project override
        # and the committed [global] fallback.
        os.environ[env_key] = "https://env.example:8445"
        check("resolve_board_url: env var alone -> resolves",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) == "https://env.example:8445")
        check("resolve_board_url: env var wins over [global]",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://env.example:8445")
        check("resolve_board_url: per-project override still wins over env var",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")
        os.environ.pop(env_key, None)

        # ---- board_link: configured + session_id -> mrkdwn deep link ----
        payload = {"session_id": "abc-123", "cwd": "E:/automation/x"}
        check("board_link: configured -> Slack mrkdwn deep link",
              notify_on_idle.board_link(payload, registry=reg)
              == "📋 <https://proj.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: trailing slash on board_url is stripped ----
        trailing = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445/"),
        )
        check("board_link: trailing slash on board_url stripped",
              notify_on_idle.board_link(payload, registry=trailing)
              == "📋 <https://global.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: board_url with an existing query string merges, not concatenates (fleet-config#273) ----
        tokened = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445?token=secret"),
        )
        check("board_link: existing ?token= on board_url survives alongside ?board=",
              notify_on_idle.board_link(payload, registry=tokened)
              == "📋 <https://global.example:8445/?token=secret&board=abc-123|Open on the Board>")

        # ---- board_link: unconfigured -> None (default, current behavior unchanged) ----
        check("board_link: board_url unset -> None",
              notify_on_idle.board_link(payload, registry=unset) is None)

        # ---- board_link: missing session_id -> None, even when configured ----
        check("board_link: missing session_id -> None",
              notify_on_idle.board_link({"cwd": "E:/automation/x"}, registry=reg) is None)
    finally:
        if old_env is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old_env

    return check.failures, check.total


def _notify_chief_routing_unit_checks() -> Tuple[int, int]:
    """`is_chief_managed`/`parse_chief_sid` — the pure decision logic behind
    routing a chief-dispatched worker's blocked-on-input notification to
    chief instead of Slack (fleet-config#443).

    Deliberately does NOT exercise `notify_chief`'s live subprocess/network
    call here (or via a `run()` end-to-end hook invocation with a genuinely
    chief-managed sid): doing so would require a real `chief-managed.json`
    entry and could actually shell out to `chief_ops.py chief-sid`/`say`
    against whatever launcher happens to be listening on 127.0.0.1:8445 on
    the machine running this suite — risking a real post into a real live
    chief session as a side effect of a unit test. The two pure functions
    below are the entire decision surface; the I/O wrapper composing them is
    exercised by hand against a real launcher, the same way `chief_ops.py`'s
    own network-touching CLI commands are.
    """
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- is_chief_managed: file-based, fully isolated from the real state dir ----
    tmp = Path(tempfile.mkdtemp(prefix="chief_managed_route_"))
    try:
        target = tmp / "chief-managed.json"
        check("is_chief_managed: missing state file -> False",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)

        target.write_text(json.dumps({"sid-1": {"repo": "app-launcher", "number": 528,
                                                  "dispatched_at": "2026-07-27T12:00:00Z"}}),
                           encoding="utf-8")
        check("is_chief_managed: marked sid -> True",
              notify_on_idle.is_chief_managed("sid-1", path=target) is True)
        check("is_chief_managed: unrelated sid -> False",
              notify_on_idle.is_chief_managed("sid-2", path=target) is False)

        target.write_text("{not json", encoding="utf-8")
        check("is_chief_managed: corrupt state file -> False (no crash)",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- parse_chief_sid: pure stdout-line parsing ----
    check("parse_chief_sid: CHIEF_SID=<sid> -> the sid",
          notify_on_idle.parse_chief_sid("CHIEF_SID=abc-123\n") == "abc-123")
    check("parse_chief_sid: CHIEF_SID=none -> empty (no chief live)",
          notify_on_idle.parse_chief_sid("CHIEF_SID=none\n") == "")
    check("parse_chief_sid: no matching line -> empty",
          notify_on_idle.parse_chief_sid("some other output\n") == "")
    check("parse_chief_sid: line among other output -> still extracted",
          notify_on_idle.parse_chief_sid("noise\nCHIEF_SID=xyz-789\nmore noise\n") == "xyz-789")

    return check.failures, check.total


def _block_askuserquestion_chief_unit_checks() -> Tuple[int, int]:
    """`block_askuserquestion_chief.py` (fleet-config#463): drives the real
    hook subprocess against a temp `CLAUDE_HOOKS_STATE_DIR` carrying a
    `chief-managed.json` marker, so a managed sid's `AskUserQuestion` blocks
    (exit 2) while everything else -- an unmanaged sid, a non-`AskUserQuestion`
    tool, a missing `session_id`, and a corrupt state file -- fails open
    (exit 0), never stranding an ordinary session over a bad read.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="block_askuserquestion_"))
    try:
        marker = tmp / "chief-managed.json"
        marker.write_text(json.dumps({
            "sid-managed": {"repo": "fleet-config", "number": 463,
                             "dispatched_at": "2026-07-27T12:00:00Z"},
        }), encoding="utf-8")
        env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}

        code, _out, stderr = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid + AskUserQuestion -> block (exit 2)", code == 2)
        check("block_askuserquestion: block reason mentions the say/exchange fallback",
              "chief_ops.py say" in stderr or "say" in stderr.lower())

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-unmanaged"},
            extra_env=env,
        )
        check("block_askuserquestion: unmanaged sid -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "Bash", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid but non-AskUserQuestion tool -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion"},
            extra_env=env,
        )
        check("block_askuserquestion: missing session_id -> allow (exit 0)", code == 0)

        corrupt = tmp / "corrupt"
        corrupt.mkdir()
        corrupt_marker = corrupt / "chief-managed.json"
        corrupt_marker.write_text("{not json", encoding="utf-8")
        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env={"CLAUDE_HOOKS_STATE_DIR": str(corrupt)},
        )
        check("block_askuserquestion: corrupt state file -> fail open, allow (exit 0)", code == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _lib_detect_project_unit_checks() -> Tuple[int, int]:
    """`_lib.detect_project` resolves a `<repo>-wt-<N>` sibling worktree cwd
    (`worktree_claim.py setup-worktree`'s naming convention for every second
    session on a claimed repo) to the same project as its primary checkout
    (fleet-config#471)."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    check = _Checker()

    registry = _lib.Registry(
        projects=[
            _lib.ProjectConfig(
                name="fleet-config", cwd_prefix=Path("E:/automation/fleet-config"),
                webapp_port=None, gate_trigger_globs=(), gate_cmd=None,
                tray_cmd=None, restart_cmd=None, api_version_path=None, extra={},
            ),
            _lib.ProjectConfig(
                name="app-launcher", cwd_prefix=Path("E:/automation/app-launcher"),
                webapp_port=None, gate_trigger_globs=(), gate_cmd=None,
                tray_cmd=None, restart_cmd=None, api_version_path=None, extra={},
            ),
        ],
        globals=_lib.GlobalConfig(never_kill_ports=()),
    )

    def name_of(cwd: str) -> Any:
        project = _lib.detect_project(Path(cwd), registry)
        return project.name if project else None

    check("detect_project: primary checkout still matches",
          name_of("E:/automation/fleet-config") == "fleet-config")
    check("detect_project: sibling worktree root resolves to the primary project",
          name_of("E:/automation/fleet-config-wt-464") == "fleet-config")
    check("detect_project: nested path inside a sibling worktree still resolves",
          name_of("E:/automation/fleet-config-wt-464/hooks") == "fleet-config")
    check("detect_project: unrelated sibling worktree does not cross-match",
          name_of("E:/automation/app-launcher-wt-9") == "app-launcher")
    check("detect_project: no match for a path outside every prefix",
          name_of("E:/automation/unrelated-repo") is None)

    return check.failures, check.total


def _chief_handover_sessionstart_unit_checks() -> Tuple[int, int]:
    """`build_context`/`handover_path` pure logic, plus one real end-to-end
    hook run with an isolated state dir (fleet-config#442).

    Unlike `notify_on_idle`'s chief-routing, this hook has no network or
    subprocess call at all -- a plain file read + one `print()` -- so the
    end-to-end case below carries none of that module's live-side-effect
    risk and is exercised fully via `run()`.
    """
    sys.path.insert(0, str(HOOKS))
    import chief_handover_sessionstart as chs  # noqa: E402

    check = _Checker()

    # ---- build_context: pure string assembly + tail-truncation ----
    short = chs.build_context("current batch: #442, #443 shipped.", Path("X:/log.md"))
    check("build_context: short content passes through, carries the fleet-config#442 preamble",
          "current batch: #442, #443 shipped." in short and "fleet-config#442" in short)

    log_path = Path("X:/log.md")
    long_content = "x" * (chs.MAX_INLINE_CHARS + 500)
    truncated = chs.build_context(long_content, log_path)
    check("build_context: over-ceiling content is truncated to the tail",
          truncated.count("x") <= chs.MAX_INLINE_CHARS + 20)  # + a little preamble slop, never the full length
    check("build_context: truncation points at the full-log path",
          str(log_path) in truncated)  # str(Path) renders with the platform's own separator

    # ---- handover_path: CLAUDE_HOOKS_STATE_DIR override (mirrors session_state.py) ----
    saved_env = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    try:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = "X:/fake-state-dir"
        check("handover_path: honors CLAUDE_HOOKS_STATE_DIR",
              chs.handover_path() == Path("X:/fake-state-dir") / "chief-handover.md")
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = saved_env

    # ---- end-to-end: fleet-config cwd + a real handover file -> additionalContext ----
    tmp = Path(tempfile.mkdtemp(prefix="chief_handover_e2e_"))
    try:
        (tmp / "chief-handover.md").write_text(
            "## 2026-07-27\ncurrent batch: #445 shipped, #443 in review.\n", encoding="utf-8"
        )
        code, stdout, stderr = run(
            "chief_handover_sessionstart",
            {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(REPO)},
            extra_env={"CLAUDE_HOOKS_STATE_DIR": str(tmp)},
        )
        check(f"chief_handover_sessionstart e2e: exits 0 ({stderr.strip()})", code == 0)
        check("chief_handover_sessionstart e2e: stdout carries the SessionStart hookSpecificOutput envelope",
              '"hookEventName": "SessionStart"' in stdout)
        check("chief_handover_sessionstart e2e: additionalContext carries the log content",
              "#445 shipped, #443 in review" in stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _session_state_unit_checks() -> Tuple[int, int]:
    """sessions-state.json persistence (fleet-config#91): event → status mapping,
    same-session flip, pruning, corrupt-file recovery, the notify_on_idle
    piggyback, and the live session-name lookup (fleet-config#302) — all
    against a temp CLAUDE_HOOKS_STATE_DIR / CLAUDE_SESSIONS_DIR so nothing
    touches the real ~/.claude/hooks/state or ~/.claude/sessions."""
    sys.path.insert(0, str(HOOKS))
    import session_state  # noqa: E402

    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="session_state_"))
    sessions_dir = Path(tempfile.mkdtemp(prefix="session_state_sessions_"))
    env = {
        "CLAUDE_HOOKS_STATE_DIR": str(tmp),
        "CLAUDE_SESSIONS_DIR": str(sessions_dir),
        "CLAUDE_SETTINGS_JSON_PATH": NO_SETTINGS_JSON,
        # Keep the fixture external by default even when this acceptance run
        # itself was started inside App Launcher.
        "APP_LAUNCHER_SESSION_ID": "",
        "APP_LAUNCHER_AGENT": "",
    }
    state_path = tmp / session_state.STATE_FILENAME

    def rows() -> Dict[str, Any]:
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # A live per-process registry fixture (~/.claude/sessions/<pid>.json-style):
    # one file whose sessionId matches "sid-1" (the generic-fallback case,
    # nameSource:"derived") and one unrelated file that must not match.
    (sessions_dir / "70212.json").write_text(json.dumps({
        "pid": 70212, "sessionId": "sid-1", "cwd": str(tmp), "kind": "interactive",
        "entrypoint": "cli", "name": "fleet-config-c4", "nameSource": "derived",
        "status": "busy",
    }), encoding="utf-8")
    (sessions_dir / "99999.json").write_text(json.dumps({
        "pid": 99999, "sessionId": "sid-unrelated", "name": "other-session",
    }), encoding="utf-8")
    # A malformed fixture alongside the good ones must not break the scan.
    (sessions_dir / "bad.json").write_text("{not json", encoding="utf-8")

    saved_env = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    saved_sessions_env = os.environ.get("CLAUDE_SESSIONS_DIR")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    os.environ["CLAUDE_SESSIONS_DIR"] = str(sessions_dir)
    try:
        # ---- subprocess: the two wired events, same session flips status ----
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "sid-1",
                   "transcript_path": str(tmp / "t.jsonl"), "cwd": str(tmp)}
        launcher_env = {
            **env,
            "APP_LAUNCHER_SESSION_ID": "launcher-abc",
            "APP_LAUNCHER_AGENT": "claude",
        }
        code, _out, _err = run("session_state", payload, extra_env=launcher_env)
        row = rows().get("sid-1") or {}
        check("session_state: UserPromptSubmit -> exit 0 + row 'working' with cwd",
              code == 0 and row.get("status") == "working" and row.get("cwd") == str(tmp))
        check("session_state: matching sessionId -> row carries live name + nameSource (#302)",
              row.get("name") == "fleet-config-c4" and row.get("name_source") == "derived")
        check("session_state: launcher env -> exact launcher id + agent (#345)",
              row.get("launcher_session_id") == "launcher-abc" and row.get("agent") == "claude")

        # ---- no matching sessionId in the registry -> name/name_source stay None ----
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "UserPromptSubmit", "session_id": "sid-no-match",
             "transcript_path": str(tmp / "t2.jsonl"), "cwd": str(tmp)},
            extra_env=env,
        )
        no_match_row = rows().get("sid-no-match") or {}
        check("session_state: no matching sessionId -> name/name_source omitted (None)",
              code == 0 and no_match_row.get("name") is None and no_match_row.get("name_source") is None)
        check("session_state: external Claude row -> explicit agent, no launcher id (#345)",
              no_match_row.get("agent") == "claude" and no_match_row.get("launcher_session_id") is None)

        # ---- missing sessions registry directory entirely -> still exit 0, no name ----
        missing_dir = sessions_dir / "does-not-exist"
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "UserPromptSubmit", "session_id": "sid-no-registry",
             "transcript_path": str(tmp / "t3.jsonl"), "cwd": str(tmp)},
            extra_env={**env, "CLAUDE_SESSIONS_DIR": str(missing_dir)},
        )
        no_registry_row = rows().get("sid-no-registry") or {}
        check("session_state: missing sessions registry dir -> exit 0, name omitted",
              code == 0 and no_registry_row.get("name") is None)

        code, _out, _err = run(
            "session_state", {**payload, "hook_event_name": "Stop"},
            extra_env=launcher_env,
        )
        stopped_row = rows().get("sid-1") or {}
        check("session_state: Stop flips the same session to 'needs-you'",
              code == 0 and stopped_row.get("status") == "needs-you")
        check("session_state: Stop retains exact launcher identity (#345)",
              stopped_row.get("launcher_session_id") == "launcher-abc"
              and stopped_row.get("agent") == "claude")

        rows_before_missing_sid = set(rows())
        code, _out, _err = run("session_state", {"hook_event_name": "Stop", "cwd": str(tmp)}, extra_env=env)
        check("session_state: missing session_id -> exit 0, no row added",
              code == 0 and set(rows()) == rows_before_missing_sid)

        code, _out, _err = run("session_state", {**payload, "hook_event_name": "PreToolUse"}, extra_env=env)
        check("session_state: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("sid-1") or {}).get("status") == "needs-you")

        # ---- in-process: multi-row, pruning, corrupt-file recovery ----
        rows_before_sid2 = set(rows())
        session_state.upsert("sid-2", status="working", project="p2",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: second session -> two distinct rows",
              set(rows()) == rows_before_sid2 | {"sid-2"})

        stale = rows()
        stale["sid-old"] = {"project": "old", "status": "idle", "transcript_path": None,
                            "cwd": str(tmp), "updated_at": "2020-01-01T00:00:00Z"}
        state_path.write_text(json.dumps(stale), encoding="utf-8")
        session_state.upsert("sid-2", status="needs-you", project="p2",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: >24h-old row pruned on next write", "sid-old" not in rows())

        state_path.write_text("{not json", encoding="utf-8")
        session_state.upsert("sid-3", status="working", project="p3",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: corrupt state file recovered by next upsert",
              (rows().get("sid-3") or {}).get("status") == "working")

        # ---- notify_on_idle piggyback: persists the row, ping path unchanged ----
        idle_payload = {"session_id": "sid-4", "transcript_path": str(tmp / "t.jsonl"),
                        "cwd": str(tmp), "notification_type": "permission_prompt",
                        "message": "Claude needs your permission"}
        code, _out, _err = run("notify_on_idle", idle_payload, extra_env=env)
        check("notify_on_idle: permission_prompt persists a 'needs-you' row (exit 0)",
              code == 0 and (rows().get("sid-4") or {}).get("status") == "needs-you")

        # fleet-config#354: idle_prompt is a periodic "still waiting on you" nag,
        # not a new state -- it must not downgrade an existing 'needs-you' row.
        code, _out, _err = run("notify_on_idle", {**idle_payload, "notification_type": "idle_prompt"}, extra_env=env)
        check("notify_on_idle: idle_prompt after needs-you -> row stays 'needs-you' (exit 0)",
              code == 0 and (rows().get("sid-4") or {}).get("status") == "needs-you")

        # Also true from a cold start (no prior row at all) -- idle_prompt writes nothing.
        cold_payload = {"session_id": "sid-5", "transcript_path": str(tmp / "t2.jsonl"),
                        "cwd": str(tmp), "notification_type": "idle_prompt",
                        "message": "Claude is waiting for your input"}
        code, _out, _err = run("notify_on_idle", cold_payload, extra_env=env)
        check("notify_on_idle: idle_prompt with no prior row -> exit 0, no row created",
              code == 0 and "sid-5" not in rows())

        # ---- SessionEnd (#241): deletes the row instead of leaving it to the 24h prune ----
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state: SessionEnd removes the row (exit 0)",
              code == 0 and "sid-1" not in rows())

        before = set(rows())
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "SessionEnd", "session_id": "sid-does-not-exist", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state: SessionEnd for an unknown sid -> exit 0, file untouched",
              code == 0 and set(rows()) == before)
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = saved_env
        if saved_sessions_env is None:
            os.environ.pop("CLAUDE_SESSIONS_DIR", None)
        else:
            os.environ["CLAUDE_SESSIONS_DIR"] = saved_sessions_env
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sessions_dir, ignore_errors=True)

    return check.failures, check.total


def _session_state_agent_adapter_unit_checks() -> Tuple[int, int]:
    """session_state_codex / session_state_pi (fleet-config#349): each
    adapter's own event->status map, the default_agent fallback when no
    launcher env is present, launcher env still winning when it is, and an
    unwired/unknown event staying a no-op — against a temp
    CLAUDE_HOOKS_STATE_DIR so nothing touches the real state file."""
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="session_state_agents_"))
    env = {
        "CLAUDE_HOOKS_STATE_DIR": str(tmp),
        "CLAUDE_SESSIONS_DIR": str(tmp / "no-sessions-dir"),
        "CLAUDE_SETTINGS_JSON_PATH": NO_SETTINGS_JSON,
        "APP_LAUNCHER_SESSION_ID": "",
        "APP_LAUNCHER_AGENT": "",
    }
    state_path = tmp / "sessions-state.json"

    def rows() -> Dict[str, Any]:
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    try:
        # ---- Codex: UserPromptSubmit -> working, default_agent applied ----
        codex_payload = {"hook_event_name": "UserPromptSubmit", "session_id": "codex-1",
                          "cwd": str(tmp), "transcript_path": None}
        code, _out, _err = run("session_state_codex", codex_payload, extra_env=env)
        row = rows().get("codex-1") or {}
        check("session_state_codex: UserPromptSubmit -> working, agent defaults to codex",
              code == 0 and row.get("status") == "working" and row.get("agent") == "codex")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "Stop"}, extra_env=env,
        )
        check("session_state_codex: Stop -> needs-you",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "PermissionRequest"}, extra_env=env,
        )
        check("session_state_codex: PermissionRequest -> needs-you",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "PreToolUse"}, extra_env=env,
        )
        check("session_state_codex: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        launcher_env = {**env, "APP_LAUNCHER_SESSION_ID": "launcher-codex", "APP_LAUNCHER_AGENT": "codex"}
        code, _out, _err = run(
            "session_state_codex",
            {**codex_payload, "hook_event_name": "UserPromptSubmit", "session_id": "codex-2"},
            extra_env=launcher_env,
        )
        codex2_row = rows().get("codex-2") or {}
        check("session_state_codex: launcher env still wins over the default_agent fallback",
              code == 0 and codex2_row.get("agent") == "codex"
              and codex2_row.get("launcher_session_id") == "launcher-codex")

        # ---- Pi: input -> working, agent_settled -> needs-you, default_agent ----
        pi_event = {"event": "input", "session_id": "pi-1", "cwd": str(tmp)}
        code, _out, _err = run("session_state_pi", pi_event, extra_env=env)
        pi_row = rows().get("pi-1") or {}
        check("session_state_pi: input -> working, agent defaults to pi",
              code == 0 and pi_row.get("status") == "working" and pi_row.get("agent") == "pi")

        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "agent_settled"}, extra_env=env,
        )
        check("session_state_pi: agent_settled -> needs-you",
              code == 0 and (rows().get("pi-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "some_unwired_event"}, extra_env=env,
        )
        check("session_state_pi: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("pi-1") or {}).get("status") == "needs-you")

        # ---- Pi: session_shutdown removes the row (the Codex adapter has no analog) ----
        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "session_shutdown"}, extra_env=env,
        )
        check("session_state_pi: session_shutdown removes the row",
              code == 0 and "pi-1" not in rows())

        before = set(rows())
        code, _out, _err = run(
            "session_state_pi", {"event": "session_shutdown", "session_id": "pi-does-not-exist", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state_pi: session_shutdown for an unknown sid -> exit 0, file untouched",
              code == 0 and set(rows()) == before)

        # Two agents in the same project stay independent rows (fleet-config#349
        # acceptance: "Two agents in one project remain independent") — same
        # cwd, distinct session ids and agent fields, neither writer clobbers
        # the other's row.
        code, _out, _err = run(
            "session_state_pi", {"event": "input", "session_id": "pi-2", "cwd": str(tmp)}, extra_env=env,
        )
        codex2_after = rows().get("codex-2") or {}
        pi2_row = rows().get("pi-2") or {}
        check("session_state: Codex and Pi rows for the same cwd stay independent",
              code == 0 and codex2_after.get("agent") == "codex" and pi2_row.get("agent") == "pi"
              and codex2_after.get("cwd") == pi2_row.get("cwd") == str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total

    return check.failures, check.total


def _gh_body_file_guard_unit_checks() -> Tuple[int, int]:
    """The warn-only nudge fires on the two payload traps and stays silent
    otherwise. Exit is always 0, so these assert on STDOUT, not the exit code:
    a nudge present (non-empty stdout) for the risky forms, empty for the safe
    ones."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("gh_body_file_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # warn-only: the hook must never block (exit non-zero) regardless of input.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    check("gh_guard: gh pr create --body with backtick -> nudge",
          bool(stdout_for('gh pr create --title x --body "see `uname -a`"')))
    check("gh_guard: gh issue comment --body with heredoc -> nudge",
          bool(stdout_for('gh issue comment 5 --body "$(cat <<EOF\nhi\nEOF\n)"')))
    check("gh_guard: PowerShell here-string through Bash -> nudge",
          bool(stdout_for("printf '%s' @'\nhello\n'@")))
    check("gh_guard: gh pr create --body-file -> silent",
          stdout_for("gh pr create --title x --body-file E:/tmp/pr-116.md") == "")
    check("gh_guard: gh issue list (read) -> silent",
          stdout_for("gh issue list --state open --limit 20") == "")
    check("gh_guard: gh pr create plain inline body (no risky construct) -> silent",
          stdout_for('gh pr create --title x --body "plain text, nothing to expand"') == "")

    return check.failures, check.total


def _bash_cmdexe_syntax_guard_unit_checks() -> Tuple[int, int]:
    """The guard blocks MSYS-mangled cmd /c, nudges cmd-only syntax, and stays
    silent on Bash-native or explicitly MSYS-safe equivalents."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("bash_cmdexe_syntax_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # These legacy syntax checks remain warn-only; cmd.exe /c is exercised
        # separately below because that caller shape is now a hard block.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart" 2>&1'}},
    )
    check("cmdexe_guard: Bash cmd.exe /c tray restart -> block with root cause",
          code == 2 and not out and "C:/" in err and "PowerShell" in err,
          out + err)

    code, _out, _err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /d /s /c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe with leading flags then /c -> block", code == 2)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe //d //c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe //c MSYS-safe spelling -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'rg -n "cmd.exe /c" skills'}},
    )
    check("cmdexe_guard: quoted search text containing cmd.exe /c -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "PowerShell", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart"'}},
    )
    check("cmdexe_guard: PowerShell cmd.exe /c -> outside Bash guard",
          code == 0 and not out and not err, out + err)

    check("cmdexe_guard: %VAR% env reference -> nudge",
          bool(stdout_for("echo %USERPROFILE%")))
    check("cmdexe_guard: dir /s -> nudge",
          bool(stdout_for("dir /s")))
    check("cmdexe_guard: del /f -> nudge",
          bool(stdout_for("del /f file.txt")))
    check("cmdexe_guard: caret line-continuation -> nudge",
          bool(stdout_for("echo hello ^\necho world")))
    check("cmdexe_guard: printf %s (bare percent, no close) -> silent",
          stdout_for('printf "%s\\n" hello') == "")
    check("cmdexe_guard: URL path with /s (no cmd builtin) -> silent",
          stdout_for("curl https://example.com/s/path") == "")
    check("cmdexe_guard: date +%Y%m%d (single-letter format run) -> silent",
          stdout_for("date +%Y%m%d") == "")
    check("cmdexe_guard: plain git log -> silent",
          stdout_for("git log --oneline") == "")

    yolo_skill = (REPO / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    yolo_skill_flat = re.sub(r"\s+", " ", yolo_skill.replace("**", ""))
    check("cmdexe_guard: issue-yolo mandates a real Windows shell for tray restart",
          "real Windows shell" in yolo_skill_flat and "cmd /c" in yolo_skill_flat)

    return check.failures, check.total


def _tier23_hooks_unit_checks() -> Tuple[int, int]:
    """The three Tier 2/3 hooks (issue #158): docs-guard env override, plus the
    two warn-only hooks whose output is on STDOUT (exit always 0), so these
    assert nudge-present / silent rather than the exit code. The warn hooks read
    the file from disk, so each case writes a real temp file first.
    """
    check = _Checker()

    # ---- docs_dated_filename_guard: env override flips block -> allow ----
    os.environ["CLAUDE_HOOKS_ALLOW_DATED_DOCS"] = "1"
    try:
        code, _out, _err = run("docs_dated_filename_guard",
                               {"tool_name": "Write",
                                "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}})
        check("docs_guard: CLAUDE_HOOKS_ALLOW_DATED_DOCS=1 -> allow (override)", code == 0)
    finally:
        os.environ.pop("CLAUDE_HOOKS_ALLOW_DATED_DOCS", None)

    tmp = Path(tempfile.mkdtemp(prefix="tier23_"))
    try:
        def nudged(hook: str, path: Path, body: str, extra_env: Dict[str, str] | None = None) -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            code, out, _err = run(hook, {"tool_name": "Write", "tool_input": {"file_path": str(path)}},
                                   extra_env=extra_env)
            return code == 0 and bool(out.strip())

        # ---- hub_bypass_warn ----
        check("hub_bypass: inline `claude -p` command string -> nudge",
              nudged("hub_bypass_warn", tmp / "wrapper.py",
                     'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n'))
        check("hub_bypass: argv-form ['claude','-p'] -> nudge",
              nudged("hub_bypass_warn", tmp / "argv.py",
                     'from subprocess import Popen\nPopen(["claude", "-p", "hi"])\n'))
        check("hub_bypass: subprocess but no claude -p -> silent",
              not nudged("hub_bypass_warn", tmp / "other.py",
                         'import subprocess\nsubprocess.run(["ls", "-la"])\n'))
        # Points hub_bypass_warn.py at a throwaway projects.toml (via
        # CLAUDE_HOOKS_PROJECTS_TOML) flagging tmp/local-llm-hub as `is_hub`,
        # so the exemption is exercised through the real cwd_prefix-match path
        # instead of a hardcoded directory-name check.
        hub_projects_toml = tmp / "hub_projects.toml"
        hub_projects_toml.write_text(
            '[hub]\ncwd_prefix = "%s"\nis_hub = true\n' % (tmp / "local-llm-hub").as_posix(),
            encoding="utf-8",
        )
        check("hub_bypass: inside a repo flagged is_hub in projects.toml -> silent",
              not nudged("hub_bypass_warn", tmp / "local-llm-hub" / "server.py",
                         'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n',
                         extra_env={"CLAUDE_HOOKS_PROJECTS_TOML": str(hub_projects_toml)}))

        # ---- browser_stealth_lint ----
        bare_launch = 'ctx = p.chromium.launch_persistent_context(user_data_dir="x")\n'
        full_launch = (
            'ctx = p.chromium.launch_persistent_context(\n'
            '    user_data_dir="x", channel="chrome",\n'
            '    ignore_default_args=["--enable-automation"],\n'
            '    args=["--disable-blink-features=AutomationControlled"],\n'
            ')\n'
            'page.add_init_script("Object.defineProperty(navigator, \'webdriver\', {get: () => undefined})")\n'
        )
        check("browser_stealth: chrome_launch.py missing markers -> nudge",
              nudged("browser_stealth_lint", tmp / "chrome_launch.py", bare_launch))
        check("browser_stealth: chrome_launch.py with all markers -> silent",
              not nudged("browser_stealth_lint", tmp / "ok_launch" / "chrome_launch.py", full_launch))
        check("browser_stealth: *_session.py with a launch missing a marker -> nudge",
              nudged("browser_stealth_lint", tmp / "x_session.py", bare_launch + 'channel="chrome"\n'))
        check("browser_stealth: watched name but no launch call -> silent",
              not nudged("browser_stealth_lint", tmp / "browser.py", "PORT = 9222\n"))
        check("browser_stealth: non-watched filename with a launch -> silent",
              not nudged("browser_stealth_lint", tmp / "helper.py", bare_launch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _branch_before_edit_guard_unit_checks() -> Tuple[int, int]:
    """branch_before_edit_guard.py: real temp git repos/worktrees on
    main/master/a feature branch, crossed with APP_LAUNCHER_SESSION_ID
    presence and the CLAUDE_HOOKS_ALLOW_MAIN_EDIT override (fleet-config#464,
    take 2). Every fixture below deliberately sets `cwd` and the edit
    `file_path`'s directory to *different* paths — the take-1 guard resolved
    the branch from `cwd` and was reverted for exactly the false positives
    that shape hides: a worktree worker judged by the primary checkout's
    branch, and a write outside any repo blocked by the session's cwd repo.
    None of these fixtures configure a git remote, so the master-branch case
    also proves `resolve_default_branch_ref`'s candidate probing (not
    `dirty_tree_check`'s `candidates=()` variant) still detects `master` as
    the protected branch with no `origin` configured. The gitignored-target
    fixtures cover take 2's own false positive (fleet-config#489) and pin the
    exemption to ignored paths only."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    check = _Checker()
    launcher_env = {"APP_LAUNCHER_SESSION_ID": "launcher-test"}

    def git_repo(branch: str) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="branch_guard_"))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (repo / "sub").mkdir(exist_ok=True)
        return repo

    def edit_payload(cwd: Path, target_dir: Path, tool: str = "Edit") -> Dict[str, Any]:
        # cwd (session dir) and the edit target's directory are deliberately
        # different paths — see the docstring above.
        return {"tool_name": tool, "cwd": str(cwd), "tool_input": {"file_path": str(target_dir / "f.py")}}

    main_repo = git_repo("main")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: main (target != cwd dir) + launcher env -> block", code == 2, err)

        # Explicit empty-string override (not just an omitted extra_env) --
        # the ambient session this suite runs under may itself carry a real
        # APP_LAUNCHER_SESSION_ID, which `run()` would otherwise pass through.
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={"APP_LAUNCHER_SESSION_ID": ""},
        )
        check("branch_guard: main + no launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={**launcher_env, "CLAUDE_HOOKS_ALLOW_MAIN_EDIT": "1"},
        )
        check("branch_guard: main + launcher env + override -> allow", code == 0)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Write"), extra_env=launcher_env
        )
        check("branch_guard: Write tool covered same as Edit -> block", code == 2, err)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="MultiEdit"), extra_env=launcher_env
        )
        check("branch_guard: MultiEdit tool covered same as Edit -> block", code == 2, err)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Bash"), extra_env=launcher_env
        )
        check("branch_guard: Bash tool_name -> allow (only guards Edit/Write/MultiEdit)", code == 0)

        # ---- take-1's actual bug: a worktree worker judged by the primary's branch ----
        worktree = main_repo.parent / f"{main_repo.name}-wt-1"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feat/464-x", str(worktree)],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        try:
            # cwd is the PRIMARY repo (still on main) -- the exact shape that
            # broke take 1. file_path targets the worktree, on its own branch.
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, worktree), extra_env=launcher_env
            )
            check("branch_guard: worktree target on feature branch, cwd=primary(main) -> allow", code == 0)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "-f", str(worktree)],
                cwd=main_repo, check=False, creationflags=_lib.NO_WINDOW,
            )

        # ---- take-2's own bug (fleet-config#489): a gitignored target *inside*
        # the repo, on the default branch. Both live repros are covered: a
        # single-file rule (life-os's `.active-skill`) and a directory rule
        # (fleet-config's `hooks/state/`, reached by the chief through a
        # junction). Neither file exists on disk -- `check-ignore` matches the
        # pathname, which is what makes a creating `Write` resolve correctly.
        (main_repo / ".gitignore").write_text(".active-skill\nstate/\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "-m", "ignore rules"],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (main_repo / "state").mkdir(exist_ok=True)

        def payload_for(target: Path) -> Dict[str, Any]:
            return {"tool_name": "Write", "cwd": str(main_repo), "tool_input": {"file_path": str(target)}}

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / ".active-skill"), extra_env=launcher_env
        )
        check("branch_guard: gitignored file target on main + launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state" / "chief-handover.md"),
            extra_env=launcher_env,
        )
        check("branch_guard: target under a gitignored directory rule -> allow", code == 0)

        # The exemption is gitignored-only: an untracked, non-ignored new file
        # in the same repo can still be committed to main, so it must block.
        code, _out, err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state.py"), extra_env=launcher_env
        )
        check("branch_guard: untracked but NOT ignored target on main -> still block", code == 2, err)

        # ---- the junction shape (fleet-config#489's second live repro) ----
        # `~/.claude/hooks/` is a junction into this repo, so the chief's write
        # to its gitignored handover file arrives spelled under the junction.
        # git follows a junction for `-C` but matches the *pathname argument*
        # lexically against the worktree root, so the unresolved spelling exits
        # 128 ("is outside repository at ...") -- the fail-closed path. Only
        # the guard's `target.resolve()` keeps this case allowed.
        if sys.platform == "win32":
            link = main_repo.parent / f"{main_repo.name}-junction"
            mk = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(main_repo)],
                capture_output=True, text=True, creationflags=_lib.NO_WINDOW,
            )
            check("branch_guard: junction fixture created", mk.returncode == 0, mk.stdout + mk.stderr)
            if mk.returncode == 0:
                try:
                    code, _out, _err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "state" / "chief-handover.md"), extra_env=launcher_env,
                    )
                    check("branch_guard: gitignored target via a junction path -> allow", code == 0)

                    code, _out, err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "tracked.py"), extra_env=launcher_env,
                    )
                    check("branch_guard: non-ignored target via a junction path -> still block", code == 2, err)
                finally:
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(link)],
                        capture_output=True, creationflags=_lib.NO_WINDOW,
                    )

        # ---- take-1's other bug: a write target entirely outside any repo ----
        non_repo = Path(tempfile.mkdtemp(prefix="branch_guard_norepo_"))
        try:
            # cwd is a repo on main (e.g. chief's cwd); file_path targets a
            # plain non-git directory (e.g. E:\tmp\chief).
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, non_repo), extra_env=launcher_env
            )
            check("branch_guard: non-git target dir, cwd=repo(main) -> allow (fail open)", code == 0)
        finally:
            shutil.rmtree(non_repo, ignore_errors=True)
    finally:
        shutil.rmtree(main_repo, ignore_errors=True)

    master_repo = git_repo("master")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(master_repo, master_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: master (no origin configured) + launcher env -> block", code == 2, err)
    finally:
        shutil.rmtree(master_repo, ignore_errors=True)

    feature_repo = git_repo("feat/464-x")
    try:
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(feature_repo, feature_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: feature branch + launcher env -> allow", code == 0)
    finally:
        shutil.rmtree(feature_repo, ignore_errors=True)

    return check.failures, check.total


def _audit_issue_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/audit_issue.py's pure-logic tests as a subprocess.

    Kept standalone (not inlined here) so the helper's marker / title-adoption /
    keep-close logic is testable on its own, and reachable from the one gate.
    """
    return _subprocess_unit_check("audit_issue", "test_audit_issue.py")


def _fleet_audit_scan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/fleet_audit_scan.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue) so `is_fleet_repo` is testable on its
    own and reachable from the one gate.
    """
    return _subprocess_unit_check("fleet_audit_scan", "test_fleet_audit_scan.py")


def _design_sweep_scan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/design_sweep_scan.py's pure-logic tests as a subprocess.

    Standalone (like test_fleet_audit_scan / test_cert_drift) so the fleet-wide
    web-app gate — `classify_web_app` over synthetic trees, the FastAPI-vs-
    Streamlit disambiguation, and the reuse of design_lint's token detection —
    is testable on its own and reachable from the one gate. (fleet-config#180)
    """
    return _subprocess_unit_check("design_sweep_scan", "test_design_sweep_scan.py")


def _worktree_claim_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/worktree_claim.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue) so the claim FSM — atomic acquire, the
    worktree fallback when held, TTL stale-reclaim, and the sibling-path
    convention — is testable on its own and reachable from the one gate.
    """
    return _subprocess_unit_check("worktree_claim", "test_worktree_claim.py")


def _active_issue_unit_check() -> Tuple[int, int]:
    """Run active-issue state + workflow wiring tests as a subprocess.

    The helper's tolerant/pruned/concurrent JSON lifecycle and every workflow
    path that adds or removes a marker stay reachable from the one gate.
    """
    return _subprocess_unit_check("active_issue", "test_active_issue.py")


def _claude_progress_unit_check() -> Tuple[int, int]:
    """Run the scheduled Claude stream adapter's focused tests.

    Covers parser filtering/deduplication, child exit-code propagation, and the
    checked-in contract that every run-weekly.bat uses the shared adapter.
    """
    return _subprocess_unit_check("claude_progress", "test_claude_progress.py")


def _context_purge_check_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/context-purge/check.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue / test_ux_surface) so the purge's
    mechanical preservation rules — marked-block byte-identity and quoted
    trigger survival in SKILL.md descriptions — are testable on their own and
    reachable from the one gate. (fleet-config#287)
    """
    return _subprocess_unit_check("context_purge_check", "test_context_purge_check.py")


def _context_purge_gate_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/context-purge/gate.py's pure-logic tests as a subprocess.

    The skip-unchanged ledger's parse/render/diff core, testable without gh —
    same standalone pattern as test_context_purge_check. (fleet-config#287)
    """
    return _subprocess_unit_check("context_purge_gate", "test_context_purge_gate.py")


def _ux_surface_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/ux_surface.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue / test_worktree_claim) so the UX-gate
    trigger — `## UX surface` block parsing, brace expansion, glob→regex, and
    the diff intersection — is testable on its own and reachable from the one
    gate. (fleet-config#195)
    """
    return _subprocess_unit_check("ux_surface", "test_ux_surface.py")


def _deploy_coverage_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/deploy_coverage.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so /issue-finish's deploy-coverage gate —
    the declared-component parser (fence-skipping, the four-bullet template),
    the path-token filter, the diff-touch matcher, and the three-state
    (`yes`/`no`/`unknown`) touch decision — is testable on its own and
    reachable from the one gate. (fleet-config#459)
    """
    return _subprocess_unit_check("deploy_coverage", "test_deploy_coverage.py")


def _e2e_test_audit_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/e2e_test_audit.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the `/e2e-audit` skill's measurement
    layer — CI-expectations e2e-surface parsing, test-dir resolution, near-
    duplicate-name clustering, size-outlier and coverage-gap detection — is
    testable on its own and reachable from the one gate. (fleet-config#406)
    """
    return _subprocess_unit_check("e2e_test_audit", "test_e2e_test_audit.py")


def _html_shot_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/html_shot.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the shared headless-Chrome
    measure-then-shoot helper — URL-scheme detection, file:// URL building,
    query appending, and the DIMS-log parser — is testable on its own and
    reachable from the one gate. (fleet-config#96)
    """
    return _subprocess_unit_check("html_shot", "test_html_shot.py")


def _docs_shots_plan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/docs_shots_plan.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the `/docs-shots` discovery +
    diff-intersection layer — manifest discovery, source_globs matching, the
    unmapped-surface heuristic, and the README-marker precondition check — is
    testable on its own and reachable from the one gate. (fleet-config#93)
    """
    return _subprocess_unit_check("docs_shots_plan", "test_docs_shots_plan.py")


def _browser_verify_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/browser_verify.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the visual-gate fallback — iab-preferred
    backend selection, the browser-safety launch kwargs, the KEY_VIEWS x
    light/dark capture plan, and the distinct capability failures — is testable
    on its own and reachable from the one gate. (fleet-config#351)
    """
    return _subprocess_unit_check("browser_verify", "test_browser_verify.py")


def _cert_drift_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/cert_drift.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the tailnet-cert drift truth table —
    LAN-only stays clean, an already-migrated app stays clean, only a
    tailnet-reachable self-signed-only app trips — is testable on its own and
    reachable from the one gate. (fleet-config#210)
    """
    return _subprocess_unit_check("cert_drift", "test_cert_drift.py")


def _design_lint_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/design_lint.py's pure-logic tests as a subprocess.

    Standalone (like test_cert_drift) so /design-sync v2's deterministic
    lenses — spec frontmatter parsing, custom-prop extraction (P3/comment
    immunity), alias mapping, adoption ratios, contract checks, vendored
    byte-compare, and sibling duplicate detection — are testable on their own
    and reachable from the one gate. (fleet-config#277)
    """
    return _subprocess_unit_check("design_lint", "test_design_lint.py")


def _rate_gate_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/rate_gate.py's pure-logic tests as a subprocess.

    Standalone (like test_cert_drift) so /audit-fleet's and /cleanup-fleet's
    proactive session-rate-limit gate — OK/PAUSE/UNKNOWN decisions, staleness
    handling, and the wait-seconds computation from resets_at — is testable on
    its own, with no real rate-limits.json touched, and reachable from the one
    gate. Replaces the retired audit_retry dead-man's-switch check. (fleet-config#261)
    """
    return _subprocess_unit_check("rate_gate", "test_rate_gate.py")


def _chief_ops_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/chief_ops.py's pure-logic tests as a subprocess.

    Standalone (like test_rate_gate) so the fleet chief's deterministic
    ops helper -- repo occupancy, the alive-worker count, the three
    dispatch refusals (occupied repo, at/over worker cap, unconfirmed
    yolo), the non-loopback-host guard, and the board-digest formatting --
    is testable on its own, with no live launcher or `gh` call required,
    and reachable from the one gate. (fleet-config#445)
    """
    return _subprocess_unit_check("chief_ops", "test_chief_ops.py")


def _chief_managed_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/chief_managed.py's pure-logic tests as a subprocess.

    Standalone (like test_chief_ops) so the chief-managed session marker --
    mark/is_managed, cross-sid isolation, and the 24h TTL prune -- is
    testable on its own, with no real chief-managed.json touched, and
    reachable from the one gate. (fleet-config#443)
    """
    return _subprocess_unit_check("chief_managed", "test_chief_managed.py")


def _dirty_tree_check_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/dirty_tree_check.py's pure-logic tests as a subprocess.

    Standalone (like test_rate_gate) so the post-flight dirty-tree decision --
    merged-mode expects a clean default branch, built-mode expects the reported
    feature branch with real evidence of work -- is testable on its own, with a
    real throwaway git repo, and reachable from the one gate. (fleet-config#247)
    """
    return _subprocess_unit_check("dirty_tree_check", "test_dirty_tree_check.py")


def _git_run_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/git_run.py's pure-logic tests as a subprocess.

    Standalone (like test_dirty_tree_check) so the shared
    `resolve_default_branch_ref` helper -- symbolic-ref success, candidate
    probing, terminal fallback, and the `candidates=()` shape
    `dirty_tree_check.py` depends on -- is testable on its own, with a real
    throwaway git repo, and reachable from the one gate. (fleet-config#485)
    """
    return _subprocess_unit_check("git_run", "test_git_run.py")


def _payload_normalization_unit_check() -> Tuple[int, int]:
    """Run hooks/_lib.py's foreign-harness payload normalization tests.

    Covers the Grok camelCase -> Claude snake_case translation every hook now
    routes through, and -- the load-bearing half -- asserts a Claude-shaped
    payload is returned as the *identical object*, so a change that reaches the
    whole fleet the moment it merges cannot alter Claude behaviour.
    (fleet-config#491)
    """
    return _subprocess_unit_check("payload_normalization", "test_payload_normalization.py")


def _vendored_drift_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/vendored_drift.py's pure-logic tests as a subprocess.

    Standalone (like test_dirty_tree_check) so the /propagate-vendored
    [vendored]-manifest drift core -- manifest parsing, the hash-diff/classify
    local-drift-vs-behind-HEAD signals, and an end-to-end scan_fleet against a
    real throwaway scaffold + adopter repos -- is testable on its own and
    reachable from the one gate. (fleet-config#338)
    """
    return _subprocess_unit_check("vendored_drift", "test_vendored_drift.py")


def _watchlist_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/sota-watch/watchlist.py's pure-logic tests.

    Standalone file, same pattern as the other helpers, so the due/fresh/
    delegated cadence logic and the seed watchlist's shape are testable on
    their own and reachable from the one gate. (fleet-config#393)
    """
    return _subprocess_unit_check("watchlist", "test_watchlist.py")


def _learning_log_unit_checks() -> Tuple[int, int]:
    """The pure window / section / bucketing / stats / ledger logic of
    learning-log/gather.py.

    No gh, no sub-agents — exercises last-run-at parsing, window resolution,
    section slicing, work-type bucketing, the exact stats computation + table
    render, and the archive-growing ledger body the unattended weekly run
    depends on."""
    import datetime as dt

    sys.path.insert(0, str(REPO / ".claude" / "skills" / "learning-log"))
    import gather as ll  # noqa: E402

    check = _Checker()

    # ---- last-run-at parsing ----
    check("learning_log: parse_last_run reads the stamp",
          ll.parse_last_run("<!-- learning-log-state -->\nlast-run-at: 2026-06-12\n") == "2026-06-12")
    check("learning_log: parse_last_run absent -> None",
          ll.parse_last_run("no stamp here") is None)

    # ---- window resolution: (since, source) — arg > ledger > trailing 7d ----
    today = dt.date(2026, 6, 15)
    check("learning_log: resolve_since explicit arg wins",
          ll.resolve_since("2026-05-01", "last-run-at: 2026-06-12", today) == ("2026-05-01", "arg"))
    check("learning_log: resolve_since falls to ledger last-run-at",
          ll.resolve_since(None, "last-run-at: 2026-06-12", today) == ("2026-06-12", "ledger"))
    check("learning_log: resolve_since first run -> trailing 7d",
          ll.resolve_since(None, "", today) == ("2026-06-08", "default"))

    # ---- section slicing ----
    text = "## TL;DR\n- a\n- b\n\n## Horizon → next week\n- [ ] x\n- [ ] y\n"
    check("learning_log: slice_section bounded by next H2",
          ll.slice_section(text, "## TL;DR") == "- a\n- b")
    check("learning_log: slice_section missing header -> ''",
          ll.slice_section(text, "## Nope") == "")

    # ---- discovery bullets get dated, non-bullets dropped ----
    bullets = ll.dated_discovery_bullets("- learned X (repo#1)\n- learned Y (repo#2)\nnoise", "2026-06-15")
    check("learning_log: dated_discovery_bullets dates + drops non-bullets",
          bullets == ["- 2026-06-15: learned X (repo#1)", "- 2026-06-15: learned Y (repo#2)"])

    # ---- work-type bucketing: PR title prefix, issue label ----
    check("learning_log: pr_bucket maps feat -> Features, fix -> Bug fixes, unknown -> Other",
          ll.pr_bucket("feat(api)!: x") == "Features & enhancements"
          and ll.pr_bucket("fix: y") == "Bug fixes"
          and ll.pr_bucket("random title") == "Other")
    check("learning_log: issue_bucket maps by label, none -> Other",
          ll.issue_bucket(["bug"]) == "Bug fixes"
          and ll.issue_bucket(["enhancement"]) == "Features & enhancements"
          and ll.issue_bucket([]) == "Other")

    # ---- exact stats: per-repo + per-bucket + grand total ----
    prs = [
        {"repo": "a", "bucket": "Bug fixes", "additions": 10, "deletions": 2},
        {"repo": "a", "bucket": "Features & enhancements", "additions": 5, "deletions": 0},
        {"repo": "b", "bucket": "Bug fixes", "additions": 3, "deletions": 1},
    ]
    issues = [{"repo": "a", "bucket": "Bug fixes"}, {"repo": "b", "bucket": "Other"}]
    stats = ll.compute_stats(prs, issues)
    check("learning_log: compute_stats grand totals (PRs/issues/LOC)",
          stats["total"] == {"prs": 3, "issues": 2, "add": 18, "del": 3})
    check("learning_log: compute_stats per-repo + per-bucket counts",
          stats["repos"]["a"]["prs"] == 2 and stats["repos"]["a"]["issues"] == 1
          and stats["repos"]["a"]["add"] == 15
          and stats["buckets"]["Bug fixes"]["prs"] == 2)
    table = ll.render_stats(stats, "2026-05-01", "2026-06-15")
    check("learning_log: render_stats has TOTAL row, a repo row, and a bucket row",
          "**TOTAL**" in table and "| a |" in table and "Bug fixes" in table)

    # ---- ledger body: new stamp + horizon, new discoveries prepended, old preserved ----
    prior = ("<!-- learning-log-state -->\nlast-run-at: 2026-06-08\n\n"
             "## Horizon → next week (set 2026-06-08)\n- [ ] old item\n\n"
             "## Decision / discovery archive\n- 2026-06-08: prior learning (repo#9)\n")
    body = ll.build_ledger_body(prior, "2026-06-15",
                                "- [ ] new horizon a\n- [ ] new horizon b",
                                "- fresh learning (repo#3)")
    check("learning_log: build_ledger_body stamps new last-run-at",
          "last-run-at: 2026-06-15" in body)
    check("learning_log: build_ledger_body carries the next horizon",
          "- [ ] new horizon a" in body and "## Horizon → next week (set 2026-06-15)" in body)
    check("learning_log: build_ledger_body prepends new discovery, preserves prior archive",
          "- 2026-06-15: fresh learning (repo#3)" in body
          and "- 2026-06-08: prior learning (repo#9)" in body
          and body.index("2026-06-15: fresh") < body.index("2026-06-08: prior learning"))

    return check.failures, check.total


def _conversation_capture_unit_checks() -> Tuple[int, int]:
    """The per-session dedup logic: stable token, filename shape, and the
    supersede-prior sweep that collapses a session's many Stop captures to one."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402

    check = _Checker()

    check("session_token: last 8 alnum of a uuid-ish id",
          cc.session_token("01HNYE6TF-AbCd-1234") == "abcd1234")
    check("session_token: no id -> empty (dedup skipped)",
          cc.session_token("") == "" and cc.session_token(None) == "")
    check("capture_filename: session token only (degenerate content)",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "")
          == "2026-06-02-2020-day-today-abcd1234.md")
    check("capture_filename: both tokens -> session then signature",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "cafe9999")
          == "2026-06-02-2020-day-today-abcd1234-cafe9999.md")
    check("capture_filename: no tokens -> plain timestamped name",
          cc.capture_filename("2026-06-02-2020", "day-today", "", "")
          == "2026-06-02-2020-day-today.md")

    # content_signature is the resume-stable identity: it keys off the first real
    # user turn (copied forward verbatim on --resume), not the session id. So two
    # transcripts sharing that opening turn — but with different later turns and a
    # different session_id — hash identically; a preamble-only turn yields "".
    preamble = ("user", "Base directory for this skill: E:/automation/life-os/x")
    turn1 = ("user", "I want to record today's licenses and GPS for the ferry trip")
    orig = [preamble, turn1, ("assistant", "ok")]
    resumed = [preamble, turn1, ("assistant", "ok"),
               ("user", "and add the return ferry time"), ("assistant", "done")]
    check("content_signature: stable across resume (same first turn), non-empty",
          cc.content_signature(orig) == cc.content_signature(resumed) != "")
    check("content_signature: preamble-only turn -> empty (falls back to session token)",
          cc.content_signature([preamble]) == "")

    # conversation_slug keys off the WHOLE conversation's salient words, not the
    # opener — issue #84. A vague opening line ("tell me about your day") must not
    # decide the slug when a topic word recurs throughout the exchange.
    convo = [
        preamble,
        ("user", "Let me tell you about my day, I want to share what happened"),
        ("assistant", "Sure — how was the ferry crossing?"),
        ("user", "The ferry crossing was rough and the licenses paperwork slipped"),
        ("assistant", "Did you sort the ferry licenses after the crossing?"),
        ("user", "Yes, renewed the licenses once the ferry crossing ended"),
    ]
    slug = cc.conversation_slug(convo)
    check("conversation_slug: topic words beat the vague opener",
          "ferry" in slug and "licenses" in slug and "share" not in slug)
    check("conversation_slug: frequency ordering, ties by first appearance",
          slug == "ferry-crossing-licenses")
    check("conversation_slug: no significant words -> first-turn fallback",
          cc.conversation_slug([preamble]) == "session")
    check("conversation_slug: command tags / preamble stripped before counting",
          cc.conversation_slug([
              preamble,
              ("user", "<command-name>/journal-daily</command-name> logbook logbook entries"),
          ]) == "logbook-entries")

    # supersede_prior removes this session's earlier captures, leaves others.
    tmp = Path(tempfile.mkdtemp(prefix="cc_dedup_"))
    try:
        (tmp / "2026-06-02-2016-session-abcd1234.md").write_text("early", encoding="utf-8")
        (tmp / "2026-06-02-2018-other-abcd1234.md").write_text("mid", encoding="utf-8")
        (tmp / "2026-06-02-2020-real-deadbeef.md").write_text("other session", encoding="utf-8")
        cc.supersede_prior(tmp, "abcd1234", "")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: drops same-session files, keeps other sessions",
              remaining == ["2026-06-02-2020-real-deadbeef.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # supersede_prior on resume: the predecessor carries the SAME content
    # signature but a DIFFERENT (now-rewritten) session token, so only the
    # signature match collapses it. An unrelated conversation is left untouched.
    tmp = Path(tempfile.mkdtemp(prefix="cc_resume_"))
    try:
        (tmp / "2026-06-05-1606-licenses-and-gps-aaaa1111-cafe9999.md").write_text("v1", encoding="utf-8")
        (tmp / "2026-06-08-2105-other-topic-bbbb2222-dead8888.md").write_text("unrelated", encoding="utf-8")
        # resumed capture: new session token, same content signature.
        cc.supersede_prior(tmp, "eeee5555", "cafe9999")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: resume (new session id, same signature) drops predecessor",
              remaining == ["2026-06-08-2105-other-topic-bbbb2222-dead8888.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _conversation_index_unit_checks() -> Tuple[int, int]:
    """Config-driven capture routing + the indexer's digest/upsert/decay logic.

    Hermetic: the hub is stubbed, so no network is touched. Covers the opt-in
    gate (a non-registered project captures nothing), routing resolution, and
    the index round-trip including the preserved decay zone."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402
    import conversation_index as ci  # noqa: E402
    import hub_client  # noqa: E402

    check = _Checker()

    # ---- config resolution / opt-in gate ----
    lo = cc.resolve_capture_config({"cwd": "E:/automation/life-os"})
    check("capture_config: life-os -> skills routing",
          lo is not None and lo.routing == "skills" and lo.active_marker == ".active-skill")
    check("capture_config: non-opted project -> None",
          cc.resolve_capture_config({"cwd": "E:/automation/app-launcher"}) is None)

    # ---- conversations_dirs: flat -> one dir labelled by project ----
    flat = cc.CaptureConfig(root=Path(tempfile.gettempdir()) / "proj", routing="flat",
                            conversations_dir="conversations", skills_dir=".claude/skills",
                            active_marker=".active-skill")
    dirs = ci.conversations_dirs(flat)
    check("conversations_dirs: flat -> single dir labelled by project",
          len(dirs) == 1 and dirs[0][1] == "proj")

    # ---- index_dir: hermetic digest/upsert + decay-zone preservation ----
    saved = hub_client.complete
    ci.hub_client.complete = lambda *a, **k: "Topic: t\nDecisions: none\nOpen loops: none"
    tmp = Path(tempfile.mkdtemp(prefix="idx_unit_"))
    try:
        cap = tmp / "2026-06-10-1200-foo-aaaa1111.md"
        cap.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap, (time.time() - 600, time.time() - 600))  # settled
        n = ci.index_dir(tmp, "t")
        idx = (tmp / "index.md").read_text(encoding="utf-8")
        check("index_dir: writes one <!-- idx --> entry",
              n == 1 and "<!-- idx" in idx and "**Topic:**" in idx)
        check("index_dir: idempotent re-run -> 0", ci.index_dir(tmp, "t") == 0)
        with open(tmp / "index.md", "a", encoding="utf-8") as fh:
            fh.write("\n" + ci.DECAY_MARKER + "\n### 2026-04 · period\n- squashed\n")
        cap2 = tmp / "2026-06-11-1300-bar-bbbb2222.md"
        cap2.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap2, (time.time() - 600, time.time() - 600))
        ci.index_dir(tmp, "t")
        check("index_dir: decay zone preserved across re-index",
              "squashed" in (tmp / "index.md").read_text(encoding="utf-8"))
    finally:
        ci.hub_client.complete = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _restart_webapp_unit_checks() -> Tuple[int, int]:
    """The tray-owned restart strategy: projects.toml carries a `restart_cmd`
    for the three tray apps, and the recovery hint stays actionable and
    :8446-safe. Both are pure (no tray needed), so they're gate-testable."""
    sys.path.insert(0, str(HOOKS))
    import restart_and_verify_webapp as rw  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

    reg = _lib.load_registry()
    by_name = {p.name: p for p in reg.projects}

    check("restart_cmd: app-launcher respawns through WebappManager",
          "WebappManager" in (by_name["app-launcher"].restart_cmd or ""))
    check("restart_cmd: voice-transcriber now has webapp_port 8443 + respawn cmd",
          by_name["voice-transcriber"].webapp_port == 8443
          and "WebappManager" in (by_name["voice-transcriber"].restart_cmd or ""))
    check("restart_cmd: local-llm-hub keeps the tray_cmd path (no restart_cmd)",
          by_name["local-llm-hub"].restart_cmd is None)

    hint = rw.recovery_hint(
        "app-launcher", 8445, Path("E:/automation/app-launcher"),
        by_name["app-launcher"].restart_cmd, "tray.bat",
    )
    check("recovery_hint: leads with the manager respawn + flags it :8446-safe",
          "WebappManager" in hint and "spares :8446" in hint)
    check("recovery_hint: tray --restart present but flagged a :8446-destroying last resort",
          "tray.bat --restart" in hint and "destroys :8446" in hint)

    tray_only = rw.recovery_hint("local-llm-hub", 8000, Path("E:/automation/local-llm-hub"), None, "tray.bat")
    check("recovery_hint: no restart_cmd -> option 1 is the tray, no respawn line",
          "WebappManager" not in tray_only and "1) Full clean restart" in tray_only)

    captured = {}
    saved_popen = rw.subprocess.Popen
    rw.subprocess.Popen = lambda *a, **kw: captured.update(kw)
    try:
        rw._start_tray("tray.bat", Path("E:/automation/app-launcher"))
    finally:
        rw.subprocess.Popen = saved_popen
    flags = captured.get("creationflags", 0)
    check(
        "_start_tray: creationflags carries both CREATE_NEW_PROCESS_GROUP and "
        "CREATE_NO_WINDOW (fleet-config#409)",
        bool(flags & getattr(rw.subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        and bool(flags & getattr(rw.subprocess, "CREATE_NO_WINDOW", 0)),
    )

    return check.failures, check.total


def _notify_complete_unit_checks() -> Tuple[int, int]:
    """Canonical per-kind message assembly + the shared slack-target resolver."""
    sys.path.insert(0, str(HOOKS))
    import notify_complete  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

    bm = notify_complete.build_message
    check("build: add -> filed + issue link",
          bm("add", issue="5", title="T", url="http://u") == "🆕 Filed #5 T · http://u")
    check("build: start -> ready-to-validate + summary",
          bm("start", issue="5", title="T", summary="do X") == "🚦 #5 T — ready to validate. do X")
    check("build: start -> ready-to-validate + summary + issue link",
          bm("start", issue="5", title="T", url="http://u", summary="do X")
          == "🚦 #5 T — ready to validate. do X · http://u")
    check("build: finish -> done + PR link",
          bm("finish", issue="5", title="T", url="http://u") == "✅ Done #5 T — PR merged · http://u")
    check("build: yolo -> shipped + PR link",
          bm("yolo", issue="5", title="T", url="http://u") == "🚀 Shipped #5 T — PR · http://u")
    check("build: batch -> passed/total",
          bm("batch", passed="2", total="3") == "🏁 Batch done: 2/3 passed — /issue-finish each branch to ship")
    check("build: finish with no url/title degrades cleanly",
          bm("finish", issue="5") == "✅ Done #5 — PR merged")
    check("build: audit -> fleet audit + summary + comment link",
          bm("audit", summary="3 audited, 2 issues", url="http://gh/comment") == "📊 Fleet audit — 3 audited, 2 issues · http://gh/comment")
    check("build: audit with no url degrades cleanly",
          bm("audit", summary="0 audited") == "📊 Fleet audit — 0 audited")
    check("build: cleanup -> bucket + merged + review counts",
          bm("cleanup", summary="documentation", merged="5", review="2")
          == "🧹 Cleanup documentation: 5 merged, 2 awaiting review")
    check("build: cleanup easy-mode (0 review) drops the review clause",
          bm("cleanup", summary="documentation", merged="3", review="0")
          == "🧹 Cleanup documentation: 3 merged")
    check("build: recap -> weekly recap + summary",
          bm("recap", summary="5 skills swept, 3 proposals") == "🔄 Weekly recap — 5 skills swept, 3 proposals")
    check("build: recap with no summary degrades cleanly",
          bm("recap") == "🔄 Weekly recap")
    check("build: design -> design sweep + summary",
          bm("design", summary="8 swept · 3 drifted · 11 findings filed")
          == "🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed")
    check("build: design with no summary degrades cleanly",
          bm("design") == "🎨 Design sweep")
    check("category: design routes to the activity log, not attention",
          notify_complete.category_for("design") == "log")
    check("build: learning -> log + summary + comment link",
          bm("learning", summary="12 PRs / 8 issues · 2/3 horizon", url="http://gh/c")
          == "📓 Learning log — 12 PRs / 8 issues · 2/3 horizon · http://gh/c")
    check("build: learning with no url degrades cleanly",
          bm("learning", summary="quiet week") == "📓 Learning log — quiet week")
    check("build: finish-batch -> merged + blocked counts",
          bm("finish-batch", merged="4", blocked="1") == "🏁 Finished batch: 4 merged, 1 blocked")
    check("build: finish-batch (0 blocked) drops the blocked clause",
          bm("finish-batch", merged="5", blocked="0") == "🏁 Finished batch: 5 merged")
    check("build: security -> lock + summary + PR link",
          bm("security", issue="42", title="audit: security findings", url="http://pr", summary="auto-merged, review the diff")
          == "🔒 Security #42 audit: security findings — auto-merged, review the diff · http://pr")
    check("build: security with no summary defaults to review-the-diff",
          bm("security", issue="42", url="http://pr") == "🔒 Security #42 — review the diff · http://pr")

    # The shared resolver: unknown cwd -> [global] channel/user + 'claude' name.
    ch, usr, nm = _lib.resolve_slack_target(Path("E:/does/not/match/anything"))
    check("resolve_slack_target: global fallback + claude name",
          ch == "C0B76GBA0LS" and usr == "U0B71PQEL6S" and nm == "claude")

    # lookup(): --repo threads onto the gh invocation as `-R repo`, for both the
    # issue path and the pr-by-number path, so a cross-repo ping can't silently
    # resolve against the caller's CWD repo instead (fleet-config#497).
    captured_args = []
    saved_gh_json = notify_complete.gh_json
    notify_complete.gh_json = lambda a: (captured_args.append(a), {"title": "T", "url": "http://u"})[1]
    try:
        notify_complete.lookup("add", "496", None, repo="ferraroroberto/fleet-config")
        check("lookup: issue path threads -R <repo> onto gh issue view",
              captured_args[-1] == ["issue", "view", "496", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("add", "30", None)
        check("lookup: issue path omits -R when repo not supplied (CWD-relative, unchanged)",
              captured_args[-1] == ["issue", "view", "30", "--json", "title,url"])

        notify_complete.lookup("finish", None, "31", repo="ferraroroberto/fleet-config")
        check("lookup: pr-by-number path threads -R <repo> onto gh pr view",
              captured_args[-1] == ["pr", "view", "31", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("finish", None, None, pr_url="http://pr", repo="ferraroroberto/fleet-config")
        check("lookup: pr_url path ignores repo (absolute URL already CWD-independent)",
              captured_args[-1] == ["pr", "view", "http://pr", "--json", "title"])
    finally:
        notify_complete.gh_json = saved_gh_json

    return check.failures, check.total


def _work_summary_unit_checks() -> Tuple[int, int]:
    """The work-summary roll-up block + per-file table (hooks/work_summary.py).

    Pure / no gh: feed the formatters a synthetic ``gh pr view`` payload (an
    added/modified/renamed/deleted mix) and assert the exact rendered roll-up and
    table, the empty-bucket drop, and the no-files degrade-to-empty path that
    keeps a finish ping block-less instead of crashing."""
    sys.path.insert(0, str(HOOKS))
    import work_summary as ws  # noqa: E402

    check = _Checker()

    M = ws.MINUS  # U+2212, as the formatters emit

    # changeType -> bucket, source-agnostic (GraphQL DELETED + REST removed both deleted).
    check("work_summary: bucket_for maps add/copy->new, del/removed->deleted, else->changed",
          ws.bucket_for("ADDED") == "new" and ws.bucket_for("COPIED") == "new"
          and ws.bucket_for("DELETED") == "deleted" and ws.bucket_for("removed") == "deleted"
          and ws.bucket_for("MODIFIED") == "changed" and ws.bucket_for("RENAMED") == "changed"
          and ws.bucket_for(None) == "changed")

    # Consistent synthetic PR: 2 new (+210), 2 changed (+98 -40), 1 deleted (-7).
    data = {
        "additions": 308, "deletions": 47, "changedFiles": 5,
        "files": [
            {"path": "a_new.py", "additions": 110, "deletions": 0, "changeType": "ADDED"},
            {"path": "b_new.py", "additions": 100, "deletions": 0, "changeType": "ADDED"},
            {"path": "c_mod.py", "additions": 50, "deletions": 30, "changeType": "MODIFIED"},
            {"path": "d_ren.py", "additions": 48, "deletions": 10, "changeType": "RENAMED"},
            {"path": "e_del.py", "additions": 0, "deletions": 7, "changeType": "DELETED"},
        ],
    }
    check("work_summary: format_block renders the exact roll-up",
          ws.format_block(data) ==
          f"📊 +308 {M}47 · 5 files\n"
          f"   🆕 2 new (+210)  ✏️ 2 changed (+98 {M}40)  🗑️ 1 deleted ({M}7)")

    check("work_summary: format_table is churn-sorted with status icons",
          ws.format_table(data) ==
          "| | File | + | − |\n"
          "|---|---|--:|--:|\n"
          f"| 🆕 | `a_new.py` | +110 | {M}0 |\n"
          f"| 🆕 | `b_new.py` | +100 | {M}0 |\n"
          f"| ✏️ | `c_mod.py` | +50 | {M}30 |\n"
          f"| ✏️ | `d_ren.py` | +48 | {M}10 |\n"
          f"| 🗑️ | `e_del.py` | +0 | {M}7 |")

    # Single modified file: empty new/deleted buckets dropped, singular "1 file".
    one = {"additions": 8, "deletions": 1, "changedFiles": 1,
           "files": [{"path": "x.py", "additions": 8, "deletions": 1, "changeType": "MODIFIED"}]}
    check("work_summary: empty buckets dropped + singular 'file'",
          ws.format_block(one) == f"📊 +8 {M}1 · 1 file\n   ✏️ 1 changed (+8 {M}1)")

    # Degrade path: no files (or a {} from a failed gh call) → "" both renderings.
    check("work_summary: no files → empty block and empty table",
          ws.format_block({}) == "" and ws.format_table({}) == ""
          and ws.format_block({"files": []}) == "")

    return check.failures, check.total


def _pi_usage_stats_unit_checks() -> Tuple[int, int]:
    """Pi JSONL usage collector: model/provider + tokens, no prompt text."""
    sys.path.insert(0, str(HOOKS))
    import pi_usage_stats as pi_stats  # noqa: E402

    check = _Checker()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "sessions"
        sess_dir = root / "--E--automation-fleet-config--"
        sess_dir.mkdir(parents=True)
        path = sess_dir / "2026-06-24T09-09-16-155Z_abc.jsonl"
        path.write_text("\n".join([
            json.dumps({"type": "session", "id": "abc", "timestamp": "2026-06-24T09:09:16.155Z", "cwd": "E:\\automation\\fleet-config"}),
            json.dumps({"type": "model_change", "timestamp": "2026-06-24T09:09:17.000Z", "provider": "openai-codex", "modelId": "gpt-5.5"}),
            json.dumps({"type": "message", "timestamp": "2026-06-24T09:10:00.000Z", "message": {"role": "assistant", "provider": "openai-codex", "model": "gpt-5.5", "content": [{"type": "toolCall"}], "usage": {"input": 10, "output": 2, "cacheRead": 3, "cacheWrite": 4, "totalTokens": 19, "cost": {"total": 0.12}}}}),
        ]), encoding="utf-8")

        sessions = pi_stats.collect(root)
        summary = pi_stats.aggregate(sessions)
        row = sessions[0]
        check("pi_usage_stats: parses cwd/project/provider/model",
              len(sessions) == 1 and row.project == "fleet-config"
              and row.provider == "openai-codex" and row.model == "gpt-5.5")
        check("pi_usage_stats: aggregates token totals and tool calls",
              summary["usage"]["total"] == 19 and summary["usage"]["input"] == 10
              and row.tool_calls == 1 and summary["by_model"]["openai-codex/gpt-5.5"]["total"] == 19)
        check("pi_usage_stats: JSON rows omit prompt text",
              "content" not in row.as_dict() and "message" not in row.as_dict())

    return check.failures, check.total


def _slack_routing_unit_checks() -> Tuple[int, int]:
    """Category → channel routing (issue #139): the resolver picks the dedicated
    channel per category, falls back to the single channel when a category is
    unset, and the kind → category map sends action-needed pings to attention."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_complete  # noqa: E402

    check = _Checker()

    cwd = Path("E:/does/not/match/anything")  # global-only resolution

    # ---- category routes to its dedicated [global] channel ----
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="attention")
    check("route: attention -> #attention channel", ch == "C0BAGNEQ163")
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="log")
    check("route: log -> #log channel", ch == "C0BARRUBG03")
    # No category -> the plain channel (back-compat: existing callers unchanged).
    ch, _u, _n = _lib.resolve_slack_target(cwd)
    check("route: no category -> slack_notify_channel", ch == "C0B76GBA0LS")

    # ---- graceful degradation: category channels unset -> single-channel fallback ----
    single = _lib.Registry(
        projects=[],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_ONLY"),
    )
    ch, _u, _n = _lib.resolve_slack_target(cwd, registry=single, category="attention")
    check("route: unset category channel -> falls back to single channel", ch == "C_ONLY")

    # ---- per-project override of a category channel wins over [global] ----
    proj = _lib.ProjectConfig(
        name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
        gate_trigger_globs=(), gate_cmd=None, tray_cmd=None, restart_cmd=None,
        api_version_path=None, extra={"slack_channel_log": "C_PROJ_LOG"},
    )
    reg = _lib.Registry(
        projects=[proj],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_G",
                                  slack_channel_log="C_GLOBAL_LOG"),
    )
    ch, _u, _n = _lib.resolve_slack_target(Path("E:/automation/x"), registry=reg, category="log")
    check("route: per-project category channel overrides [global]", ch == "C_PROJ_LOG")

    # ---- kind -> category map ----
    cat = notify_complete.category_for
    check("category_for: start -> attention", cat("start") == "attention")
    check("category_for: batch -> attention", cat("batch") == "attention")
    check("category_for: security -> attention", cat("security") == "attention")
    check("category_for: cleanup with review>0 -> attention", cat("cleanup", review="2") == "attention")
    check("category_for: cleanup with review=0 -> log", cat("cleanup", review="0") == "log")
    check("category_for: log kinds -> log",
          all(cat(k) == "log" for k in ("add", "finish", "yolo", "audit", "recap", "learning", "finish-batch")))

    return check.failures, check.total


if __name__ == "__main__":
    sys.exit(main())
