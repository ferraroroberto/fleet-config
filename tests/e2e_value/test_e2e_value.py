"""Unit tests for the skills/_lib/e2e_value/ package, /e2e-audit's time and failure layer (fleet-config#1018).

Synthetic progress logs only, in the shape app-launcher's tests/_progress_log.py
writes (#534, #943, #1231). No gate is started and no GitHub call is made.

The sections are ordered modules in this folder, imported one after another: every call goes through one
proxy (`v`) whose record the last part compares with the golden, so the order is part of the pin.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/e2e_value/test_e2e_value.py`  (also invoked by tests/run_acceptance.py)
"""

from _suite import _h

import parsing_and_measures  # noqa: F401
import routing_report  # noqa: F401
import stylesheet_routing  # noqa: F401
import parallel_and_budget  # noqa: F401
import slimming_lessons  # noqa: F401
import audit_callers  # noqa: F401
import call_golden  # noqa: F401

_h.report_and_exit("test_e2e_value")
