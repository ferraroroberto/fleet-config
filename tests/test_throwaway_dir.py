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

_h.report_and_exit("test_throwaway_dir")
