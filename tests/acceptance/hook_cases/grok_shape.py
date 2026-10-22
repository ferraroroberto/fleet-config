"""Grok Build payload shape, end to end: the same guards driven with grok's camelCase envelope.

Grok scans ~/.claude/settings.json for hooks by default, so every guard
here already runs inside a Grok session -- but its stdin envelope is
camelCase (`hookEventName`/`toolName`/`toolInput`) with lower_snake event
values, and its shell tool is `run_terminal_command`. Before
`_lib.normalize_payload()`, that mismatch made 6 of these 7 guards fire and
silently allow the identical dangerous command they block for Claude, while
still looking healthy in grok's own `/hooks` modal. These drive the real
hook modules with the exact envelope grok 0.2.114 emits; each one exits 0
instead of 2 against pre-fix code.
"""
from __future__ import annotations

from typing import Any, Dict, List

from acceptance.hook_cases import Case
from acceptance.hook_cases.fakes import FAKE_GHP
from acceptance.shared import REPO

# (label, hook, command) for the commands every grok-shaped guard must refuse.
BLOCKED_COMMANDS = (
    ("AI attribution trailer", "pre_commit_no_ai_trailer",
     'git commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'),
    ("blanket python kill", "safe_kill_guard", "Stop-Process -Name python -Force"),
    ("force-push to main", "safe_kill_guard", "git push --force origin main"),
    ("--no-verify bypass", "safe_kill_guard", 'git commit --no-verify -m "x"'),
    ("venv creation as `venv`", "venv_discipline", "python -m venv venv"),
    ("native cmd.exe /c", "bash_cmdexe_syntax_guard", "cmd.exe /c dir"),
    ("unquoted Windows backslash path", "bash_windows_path_guard", "ls E:\\automation"),
    ("live token in an inline gh issue body", "secret_scan_guard",
     f'gh issue create --title t --body "key {FAKE_GHP}"'),
)

# The same guards must stay quiet on innocuous grok-shaped commands -- the
# normalization must not turn them into blanket blockers.
ALLOWED_COMMANDS = (
    ("clean commit message", "pre_commit_no_ai_trailer", 'git commit -m "feat: clean"'),
    ("ordinary status call", "safe_kill_guard", "git status"),
    ("venv python invocation", "venv_discipline", r"& .\.venv\Scripts\python.exe -V"),
    ("placeholder in an inline gh issue body", "secret_scan_guard",
     'gh issue create --title t --body "use xoxb-<token>"'),
)


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


def grok_cases() -> List[Case]:
    cases: List[Case] = []
    for label, hook, command in BLOCKED_COMMANDS:
        cases.append((f"grok shape: {label} -> block (parity with Claude shape)", hook, grok_bash(command), 2))
    for label, hook, command in ALLOWED_COMMANDS:
        cases.append((f"grok shape: {label} -> allow", hook, grok_bash(command), 0))

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
    return cases
