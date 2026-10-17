"""Unit tests for tests/_lib/throwaway_dir.py (fleet-config#1484): the design-review suites' temp state dir is removed at exit.

Each exit path runs in a child interpreter, because the cleanup is an `atexit` hook and only a real process exit fires it.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_throwaway_dir.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests" / "_lib"))

from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1])\n"
    "from throwaway_dir import throwaway_dir\n"
    "d = throwaway_dir('throwaway-dir-test-')\n"
    "(d / 'nested').mkdir(); (d / 'nested' / 'f.txt').write_text('x')\n"
    "print(d, flush=True)\n"
    "{ending}\n"
)

for label, ending in (
    ("a normal exit", "pass"),
    ("a failed-check SystemExit(1)", "raise SystemExit(1)"),
    ("an uncaught exception", "raise RuntimeError('boom')"),
):
    proc = subprocess.run([sys.executable, "-c", CHILD.format(ending=ending), str(REPO / "tests" / "_lib")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    made = Path(proc.stdout.strip().splitlines()[0]) if proc.stdout.strip() else None
    check(made is not None and made.name.startswith("throwaway-dir-test-"), f"{label}: the child made its dir -- {proc.stderr[-200:]}")
    check(made is not None and not made.exists(), f"{label}: the dir is gone after the process exits -- {made}")

# The two design-review suites take their state dir from the helper, never a bare mkdtemp.
for name in ("test_design_review.py", "test_design_review_browser.py"):
    src = (REPO / "tests" / name).read_text(encoding="utf-8")
    check('STATE = throwaway_dir("design-review-test-state-")' in src, f"{name}: STATE comes from throwaway_dir")
    check("mkdtemp(prefix=\"design-review-test-state-\"" not in src, f"{name}: no bare mkdtemp for the state dir")

# The other design suites hold the same contract (fleet-config#1487), and the browser suite exits under its own name only.
for name in ("test_design_judgment.py", "test_design_ledger.py", "test_design_lint.py", "test_design_report.py", "test_design_review.py"):
    src = (REPO / "tests" / name).read_text(encoding="utf-8")
    check("mkdtemp" not in src, f"{name}: no bare mkdtemp")

browser_src = (REPO / "tests" / "test_design_review_browser.py").read_text(encoding="utf-8")
check(browser_src.count("report_and_exit(") == 1 and 'report_and_exit("test_design_review_browser"' in browser_src,
      "the browser suite has one report_and_exit, under its own name")

_h.report_and_exit("test_throwaway_dir")
