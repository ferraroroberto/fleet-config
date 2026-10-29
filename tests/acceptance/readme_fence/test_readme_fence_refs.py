"""Tests for tests/acceptance/readme_fence/readme_fence_refs.py -- the
fenced-block half of the docs-history check the vendored
scripts/docs_ratchet.py deliberately skips (fleet-config#1697: the README's
`## Layout` tree is a fenced block, so an issue reference there never trips
the docs ratchet).

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/readme_fence/test_readme_fence_refs.py
(also invoked by tests/run_acceptance.py via standalone_dispatch)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "tests" / "acceptance" / "readme_fence"))
from readme_fence_refs import scan_fenced_issue_refs, scan_file  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# ---- 1. proves the scan catches the exact class of finding #1697 fixed: an
# issue reference (with provenance aside) sitting inside a fenced block.
_sample = (
    "# Doc\n\n"
    "```\n"
    "fleet-config/\n"
    "\u251c\u2500\u2500 a.py   # split from _lib.py, fleet-config#931\n"
    "```\n"
)
hits = scan_fenced_issue_refs(_sample)
check(len(hits) == 1 and hits[0][0] == 5, f"a fenced issue reference is caught by line, got {hits!r}")

# ---- 2. a reference outside any fence is not this check's job -- the
# vendored docs_ratchet already owns that half; never double-count it here.
_sample_outside = "# Doc\n\nSee fleet-config#931 for context.\n"
check(scan_fenced_issue_refs(_sample_outside) == [], "a reference outside a fence is not flagged here")

# ---- 3. a fence that never closes stops scanning at EOF rather than
# crashing -- no reference below an unclosed fence is silently missed by an
# exception instead of a result.
_sample_unclosed = "# Doc\n\n```\nfleet-config#931\n"
check(scan_fenced_issue_refs(_sample_unclosed) == [(4, "fleet-config#931")],
      "an unclosed fence still scans the lines inside it")

# ---- 4. the real README.md's fenced blocks are clean right now -- the
# actual gate this repo runs, proven against the live tree.
readme_hits = scan_file(REPO / "README.md")
check(readme_hits == [], f"README.md's fenced blocks carry no issue references, found {readme_hits!r}")

_h.report_and_exit("test_readme_fence_refs")
