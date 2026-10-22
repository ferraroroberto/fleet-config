"""Shared preamble of the design_lint suites: import paths and the pass/fail harness.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`), so the
`skills/_lib` and `tests/_lib` modules resolve once, in one place.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
