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

# A dir holding a committed git repo (read-only object files) is removed too -- the case rmtree(ignore_errors=True) missed (#1487).
GIT_CHILD = (
    "import subprocess, sys; sys.path.insert(0, sys.argv[1])\n"
    "from throwaway_dir import throwaway_dir\n"
    "d = throwaway_dir('throwaway-dir-git-test-')\n"
    "(d / 'f.txt').write_text('x')\n"
    "for a in (['init', '-q'], ['add', '-A'], ['-c', 'core.hooksPath=' + str(d / 'no-hooks'), '-c', 'user.name=t',\n"
    "          '-c', 'user.email=t@t', 'commit', '-qm', 'init']):\n"
    "    subprocess.run(['git', '-C', str(d), *a], check=True, capture_output=True)\n"
    "print(d, flush=True)\n"
)
proc = subprocess.run([sys.executable, "-c", GIT_CHILD, str(REPO / "tests" / "_lib")],
                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
made = Path(proc.stdout.strip().splitlines()[0]) if proc.stdout.strip() else None
check(made is not None and not made.exists(), f"a dir holding a committed git repo is gone after exit -- {made} {proc.stderr[-200:]}")

# The design-review suites take their state dir from the helper, never a bare mkdtemp: the pure-logic
# suites through the shared `_suite` preamble, the browser suite on its own.
suite_src = (REPO / "tests" / "design_review" / "_suite.py").read_text(encoding="utf-8")
check('STATE = throwaway_dir("design-review-test-state-")' in suite_src, "_suite.py: STATE comes from throwaway_dir")
check("mkdtemp(prefix=\"design-review-test-state-\"" not in suite_src, "_suite.py: no bare mkdtemp for the state dir")
review_suites = sorted((REPO / "tests" / "design_review").glob("test_*.py"))
check(len(review_suites) >= 1, "the design_review folder holds at least one suite")
for path in review_suites:
    check("from _suite import" in path.read_text(encoding="utf-8"), f"{path.name}: takes its state dir from _suite")
browser_state_src = (REPO / "tests" / "test_design_review_browser.py").read_text(encoding="utf-8")
check('STATE = throwaway_dir("design-review-test-state-")' in browser_state_src, "test_design_review_browser.py: STATE comes from throwaway_dir")
check("mkdtemp(prefix=\"design-review-test-state-\"" not in browser_state_src, "test_design_review_browser.py: no bare mkdtemp for the state dir")

# The other design suites hold the same contract (fleet-config#1487), and the browser suite exits under its own name only.
# Globbed (flat and the design_lint/design_review folders), so a new design suite (and test_design_sweep_scan.py, #1489) is covered without editing this list.
for path in sorted((REPO / "tests").glob("test_design_*.py")) + sorted((REPO / "tests").glob("design_*/test_*.py")):
    check("mkdtemp" not in path.read_text(encoding="utf-8"), f"{path.name}: no bare mkdtemp")

browser_src = (REPO / "tests" / "test_design_review_browser.py").read_text(encoding="utf-8")
check(browser_src.count("report_and_exit(") == 1 and 'report_and_exit("test_design_review_browser"' in browser_src,
      "the browser suite has one report_and_exit, under its own name")

_h.report_and_exit("test_throwaway_dir")
