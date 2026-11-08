"""Acceptance checks for `safe_kill_guard`'s host-power rule on Codex payloads.

Codex sends Claude-shaped payloads; only the invoked `.codex/hooks` entry point
marks them as Codex, and a Codex `PreToolUse` refusal is a structured deny on
stdout with exit 0, not Claude's exit 2. Each case runs the guard from a
`.codex/hooks` entry point in a temp dir that imports this tree's `hooks/`, so
the checks prove this checkout's code, not the live junction's.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

from acceptance.hook_cases.host_power import ALLOWED, REFUSED
from acceptance.shared import (
    HOOK_SPAWN_TIMEOUT_S,
    HOOKS,
    PYTHON,
    REPO,
    _Checker,
    hook_env,
)

_ENTRY = (
    "import sys\n"
    "sys.path.insert(0, {hooks!r})\n"
    "import safe_kill_guard\n"
    "safe_kill_guard.main()\n"
)


def _codex_verdict(entry: Path, tool: str, command: str) -> Tuple[int, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(REPO),
               "tool_input": {"command": command}}
    res = subprocess.run([PYTHON, str(entry)], input=json.dumps(payload), capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=HOOK_SPAWN_TIMEOUT_S, env=hook_env())
    return res.returncode, res.stdout


def _is_deny(stdout: str) -> bool:
    try:
        out = json.loads(stdout)
    except ValueError:
        return False
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _host_power_codex_unit_checks() -> Tuple[int, int]:
    """Every refused command is a Codex deny; every look-alike passes with no deny."""
    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="fleet-config-codex-"))
    try:
        entry = tmp / ".codex" / "hooks" / "safe_kill_guard.py"
        entry.parent.mkdir(parents=True)
        entry.write_text(_ENTRY.format(hooks=str(HOOKS)), encoding="utf-8")
        for tool, command in REFUSED:
            code, out = _codex_verdict(entry, tool, command)
            check(f"host_power codex: {tool} {command!r} -> deny", code == 0 and _is_deny(out), out)
        for tool, command in ALLOWED:
            code, out = _codex_verdict(entry, tool, command)
            check(f"host_power codex: {tool} {command!r} -> allow", code == 0 and not _is_deny(out), out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total
