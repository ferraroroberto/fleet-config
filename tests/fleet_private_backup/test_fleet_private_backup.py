"""Unit tests for the daily fleet-private backup engine (fleet-config#590).

Everything here runs against real temp trees, a real `git init`ed repo, a real
Windows junction, and real hardlinks — the three things this engine gets wrong
if it only ever meets mocks are exactly junction traversal, `st_nlink`, and what
`git ls-files --others --ignored` actually emits.

The legs are ordered modules in this folder, imported one after another: they share one temp tree and
the repo built by `selection_rules`, so each part imports what an earlier one built (see `_suite.py`).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/fleet_private_backup/test_fleet_private_backup.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import shutil

from _suite import _h, tmp

try:
    import selection_rules  # noqa: F401
    import snapshot_and_retention  # noqa: F401
    import latest_and_notes  # noqa: F401
    import runtime_data  # noqa: F401
    import runtime_selection  # noqa: F401
    import end_to_end_run  # noqa: F401
    import interrupted_and_exit_codes  # noqa: F401
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_private_backup")
