"""Unit tests for tests/acceptance/gate_routing.py (fleet-config#1481): which diffs run the slow browser suite.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/e2e/test_gate_routing.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from acceptance import gate_routing as gr  # noqa: E402
from acceptance.standalone_dispatch import _STANDALONE_UNIT_CHECKS  # noqa: E402

_h = CheckHarness()
check = _h.check

SURFACE = gr.ROUTED_SURFACES["design_review_browser"]

# ---- the table points at real suites and real paths ----
labels = {label for label, _f, _w in _STANDALONE_UNIT_CHECKS}
check(set(gr.ROUTED_SURFACES) <= labels, "every routed label is a standalone_dispatch row")
check(all((REPO / p).exists() for p in SURFACE), f"every routed prefix exists on disk -- {[p for p in SURFACE if not (REPO / p).exists()]}")

# ---- matches ----
check(gr.matches("skills/_lib/design_review/walk.py", SURFACE), "a file under a directory prefix matches")
check(gr.matches("design.rubric.toml", SURFACE), "an exact file prefix matches")
check(not gr.matches("skills/_lib/design_review_extra.py", SURFACE), "a sibling that merely shares the directory name does not match")
check(not gr.matches("hooks/_lib.py", SURFACE) and not gr.matches("tests/design_review/test_walk.py", SURFACE),
      "an unrelated hook and the pure-logic suite do not match")

# ---- routed_out over a stubbed diff ----
os.environ.pop(gr.FORCE_ENV_VAR, None)
real = gr.changed_paths


def route(paths):
    gr.changed_paths = lambda: paths
    try:
        return gr.routed_out("design_review_browser")
    finally:
        gr.changed_paths = real


check(route(["hooks/_lib.py", "README.md"]) is not None, "a diff outside the surface routes the suite out")
check(route([]) is not None, "an empty diff (the default branch itself) routes it out")
check(route(["README.md", "tests/fixtures/design_review/fixture.html"]) is None, "one fixture file in the diff runs the suite")
check(route(["skills/_lib/design_review/measure.py"]) is None, "a design_review source file runs the suite")
check(route(None) is None, "a diff git could not establish runs the suite -- uncertainty never narrows")
check(gr.routed_out("some_other_suite") is None, "a suite with no routing entry always runs")
os.environ[gr.FORCE_ENV_VAR] = "1"
check(route(["README.md"]) is None, f"{gr.FORCE_ENV_VAR}=1 runs it whatever the diff")
del os.environ[gr.FORCE_ENV_VAR]

# ---- the real diff of this checkout is a list of repo-relative paths or None ----
live = gr.changed_paths()
check(live is None or all(isinstance(p, str) and not Path(p).is_absolute() for p in live), "changed_paths yields repo-relative paths or None")

_h.report_and_exit("test_gate_routing")
