"""Shared preamble of the design_review suites: import paths, the pass/fail harness and the throwaway hooks state.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`), so the
`skills/_lib` and `tests/_lib` modules resolve once, in one place.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_h = CheckHarness()
check = _h.check

# Every hooks-state write in a suite lands in a throwaway dir, never in the live one.
STATE = throwaway_dir("design-review-test-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
