"""Unit tests for skills/_lib/merge_danger/check.py.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_merge_danger.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from merge_danger import check as md  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

two = "**Merge danger:** two-way door · blast radius: local"
check(md.find_line(f"## Summary\nx\n\n{two}\n\nCloses #1") == two, "a two-way line inside a body is found")
check(md.find_line("**Merge danger:** two-way door · blast radius: hooks-live") is not None, "hyphenated radius ok")
one = "**Merge danger:** one-way door · blast radius: fleet-wide · drops a table"
check(md.find_line(one) == one, "a one-way line with a reason is found")
check(md.find_line("**Merge danger:** one-way door · blast radius: fleet-wide") is None, "one-way without a reason is refused")
check(md.find_line("## Summary\nCloses #1") is None, "a body without the line is refused")
check(md.find_line("Merge danger: two-way door · blast radius: local") is None, "an unbolded line is refused")
check(md.find_line("**Merge danger:** two-way door") is None, "a line without a radius is refused")
check(md.find_line("**Merge danger:** maybe door · blast radius: local") is None, "an unknown door is refused")

_h.report_and_exit("test_merge_danger")
