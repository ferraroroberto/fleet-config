"""Pure-logic + subprocess tests for hooks/bash_windows_path_guard.py
(fleet-config#800).

Standalone (like test_context_purge_check.py) so the guard's root-cause
attribution -- a trailing backslash immediately before a closing double-quote
escapes that quote instead of closing it, shifting quote parity for the rest
of the command -- is pinned against the exact command that surfaced the bug,
not just re-derived from the detection logic that was already correct.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_bash_windows_path_guard.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "hooks" / "bash_windows_path_guard.py"

_spec = importlib.util.spec_from_file_location("bash_windows_path_guard", HOOK_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# The exact command from fleet-config#800: the first quoted path ends
# `memory\"` -- a trailing backslash right before the closing double-quote,
# which escapes it instead of closing the string. The second, genuinely
# quoted path lands unquoted as a result and is what the detector flags.
REPRO_CMD = (
    r'ls "E:\automation\life-os\.claude\skills\general\memory\" 2>/dev/null; '
    r'echo "---"; '
    r'ls "E:\automation\life-os\.claude\skills\general\conversations\" 2>/dev/null'
)

# ---- find_broken_quote_path: pure logic ----
broken = guard.find_broken_quote_path(REPRO_CMD)
check(broken is not None, "broken-quote path detected in the repro command")
check(
    broken is not None and broken.group(0) == "E:\\automation\\life-os\\.claude\\skills\\general\\memory\\",
    "broken-quote path is the FIRST (memory) path, not the flagged (conversations) one",
)

check(
    guard.find_broken_quote_path(r'echo "E:\automation"') is None,
    "a cleanly closed double-quoted path -> no broken-quote match",
)
check(
    guard.find_broken_quote_path(r"ls E:\automation\foo") is None,
    "an unquoted path with no adjacent quote at all -> no broken-quote match",
)
check(
    guard.find_broken_quote_path(r"echo 'E:\automation\'") is None,
    "single-quoted trailing backslash is not the double-quote-escape shape -> no match",
)
check(
    guard.find_broken_quote_path('echo "E:\\\\foo\\\\"') is None,
    "an EVEN trailing backslash count (an escaped pair) closes cleanly -> no false-positive match",
)
check(
    guard.find_broken_quote_path('echo "E:\\foo\\"') is not None,
    "an ODD trailing backslash count still matches (sanity check on the parity logic)",
)

# ---- find_unsafe_drive_paths: unchanged detection still correct ----
hits = guard.find_unsafe_drive_paths(REPRO_CMD)
check(len(hits) == 1, "exactly one unsafe (unquoted) path detected in the repro command")
check(
    bool(hits) and hits[0].group(0) == "E:\\automation\\life-os\\.claude\\skills\\general\\conversations\\",
    "the unsafe hit is the second (conversations) path",
)


def _run(cmd: str) -> tuple[int, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stderr


# ---- end-to-end: the hook subprocess names the real defect ----
exit_code, stderr = _run(REPRO_CMD)
check(exit_code == 2, "repro command is still blocked (exit 2)")
check("conversations" in stderr, "message still names the flagged (symptomatic) path")
check(
    "memory" in stderr and "Root cause" in stderr,
    "message adds a distinct Root cause clause naming the actual broken path (memory), not just the symptom",
)
check(
    "escapes" in stderr and "quote" in stderr,
    "message explains the mechanism: a trailing backslash escapes the closing quote",
)

# A plain unquoted path with no broken-quote shape anywhere still gets the
# original message, with no spurious Root cause clause appended.
plain_exit, plain_stderr = _run(r"ls E:\automation\fleet-config")
check(plain_exit == 2, "plain unquoted path is still blocked (exit 2)")
check(
    "Root cause" not in plain_stderr,
    "no broken-quote shape present -> no Root cause clause appended (regression guard)",
)

# A cleanly double-quoted path is still allowed outright.
clean_exit, _ = _run(r'echo "E:\automation"')
check(clean_exit == 0, "cleanly double-quoted path -> still allowed")

_h.report_and_exit("test_bash_windows_path_guard")
