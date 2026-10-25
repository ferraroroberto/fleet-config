"""Shared preamble of the claude_progress suites: import paths, the adapter module and the harness.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import claude_progress as cp  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from readiness_clock import ReadinessClock  # noqa: E402

_h = CheckHarness()
check = _h.check


# fleet-config#689 made a clean exit with zero tool invocations red, so every
# fixture that asserts `exit 0` has to invoke something, as any real run does.
INIT_LINE = (
    "print(json.dumps({'type':'system','subtype':'init',"
    "'claude_code_version':'stall-fixture','model':'fixture'}), flush=True); "
)


TOOL_LINE = (
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'tool_use','id':'t1','name':'Bash','input':{}}]}}), flush=True); "
    "print(json.dumps({'type':'user','message':{'content':"
    "[{'type':'tool_result','tool_use_id':'t1'}]}}), flush=True); "
)


RESULT_LINE = "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'done'}), flush=True); "


# Kept ASCII on purpose: this string crosses the Windows argv boundary into a
# `python -c` child, where non-ASCII is not reliably preserved (fleet-config#523).
ZERO_REPORT = "Fleet audit - 0 repos evaluated.\nSCHEDULED-RUN-FAILED - the fleet sweep returned no repos"


zero_work_script = (
    "import json,sys; "
    + INIT_LINE
    + f"report = {ZERO_REPORT!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)


__all__ = ['INIT_LINE', 'RESULT_LINE', 'ROOT', 'ReadinessClock', 'TOOL_LINE', '_h', 'check', 'cp', 'zero_work_script']
