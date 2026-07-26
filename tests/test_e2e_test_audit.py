"""Unit tests for the pure logic in skills/_lib/e2e_test_audit.py (fleet-config#406).

No live git/pytest — these exercise the CI-expectations e2e-surface parser,
the test-dir resolution fallback, near-duplicate-name clustering, size-outlier
detection, and the coverage-gap check.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_e2e_test_audit.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import e2e_test_audit as m  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- CI-expectations block + e2e-surface line -----------------------------

CLAUDE_MD = """\
# Project Instructions

## UX surface
- design spec applies: yes

## CI expectations
- Workflow `.github/workflows/e2e.yml`, advisory only.
- CI's only signal beyond the local gate is the **e2e suite**. Its e2e surface = `app/webapp/`, the session-host layer (`src/session_host.py`), `tests/e2e/`, and static assets.

## Next section
- unrelated
"""

block = m.find_ci_expectations_block(CLAUDE_MD)
check(block is not None, "CI expectations block found")
assert block is not None
check("e2e surface" in block, "block contains the e2e-surface line")
check("unrelated" not in block, "block stops at the next heading")

check(m.find_ci_expectations_block("# Title\n\n## Other\n- x\n") is None,
      "absent CI expectations block -> None")

line = next(ln for ln in block.splitlines() if "e2e surface" in ln.lower())
paths = m.extract_backtick_paths(line)
check(paths == ["app/webapp/", "src/session_host.py", "tests/e2e/"],
      "backtick paths extracted in order")

test_like = m.filter_test_like_paths(paths)
check(test_like == ["tests/e2e"], "only the test-like path survives, trailing slash stripped")

check(m.resolve_test_dirs(CLAUDE_MD) == ["tests/e2e"], "resolve_test_dirs uses the declared path")
check(m.resolve_test_dirs(None) == list(m.DEFAULT_TEST_DIRS), "no CLAUDE.md -> default test dir")
check(m.resolve_test_dirs("# no CI expectations here\n") == list(m.DEFAULT_TEST_DIRS),
      "no CI-expectations block -> default test dir")

NO_E2E_LINE = "## CI expectations\n- Workflow ci.yml, no e2e mentioned at all.\n"
check(m.resolve_test_dirs(NO_E2E_LINE) == list(m.DEFAULT_TEST_DIRS),
      "CI-expectations block with no e2e-surface line -> default test dir")

SOURCE_ONLY_LINE = "## CI expectations\n- Its e2e surface = `app/webapp/`, `src/launcher.py`.\n"
check(m.resolve_test_dirs(SOURCE_ONLY_LINE) == list(m.DEFAULT_TEST_DIRS),
      "e2e-surface line with no test-like path -> default test dir")


# ---- normalize_test_name + clustering --------------------------------------

check(m.normalize_test_name("test_board_tab_renders_correctly")
      == m.normalize_test_name("test_board_tab_renders_correctly_2"),
      "trailing digit (parametrize index) does not change the signature")
check(m.normalize_test_name("test_smoke_123") == m.normalize_test_name("test_smoke_456"),
      "embedded issue numbers are stripped")
check(m.normalize_test_name("test_a_and_b") == m.normalize_test_name("test_b_a"),
      "token order does not matter")

tests = [
    {"file": "tests/e2e/test_a.py", "name": "test_board_tab_renders"},
    {"file": "tests/e2e/test_b.py", "name": "test_board_tab_renders_2"},
    {"file": "tests/e2e/test_c.py", "name": "test_unique_thing"},
]
clusters = m.cluster_candidates(tests)
check(len(clusters) == 1, "exactly one colliding signature clustered")
check(len(clusters[0]["members"]) == 2, "the cluster has both colliding members")
clustered_names = {t["name"] for t in clusters[0]["members"]}
check("test_unique_thing" not in clustered_names, "the unique test is not in the cluster")

check(m.cluster_candidates([{"file": "a.py", "name": "test_only_one"}]) == [],
      "a single test never forms a cluster")


# ---- size_outliers ----------------------------------------------------------

FILES = [
    {"file": "tests/e2e/test_small1.py", "lines": 50},
    {"file": "tests/e2e/test_small2.py", "lines": 55},
    {"file": "tests/e2e/test_small3.py", "lines": 45},
    {"file": "tests/e2e/test_huge.py", "lines": 1200},
]
outliers = m.size_outliers(FILES)
check(len(outliers) == 1 and outliers[0]["file"] == "tests/e2e/test_huge.py",
      "only the file far above median is flagged")
check(m.size_outliers([{"file": "a.py", "lines": 10}]) == [], "too few files -> no outliers")


# ---- coverage_gaps -----------------------------------------------------------

TEXT = "tests/e2e/test_home.py test_home_tab_renders\ntests/e2e/test_settings.py test_settings_save"
check(m.coverage_gaps(["/", "/settings"], TEXT) == [], "both declared views are covered")
check(m.coverage_gaps(["/", "/settings", "/billing"], TEXT) == ["/billing"],
      "an uncovered declared view is reported as a gap")
check(m.coverage_gaps([], TEXT) == [], "no declared views -> no gaps")


# ---- target_ratio ------------------------------------------------------------

check(m.target_ratio(30, 15) == 2.0, "double the target ratio")
check(m.target_ratio(0, 15) == 0.0, "zero tests ratio")
check(m.target_ratio(10, 0) == 0.0, "zero target guarded, never divides by zero")


_h.report_and_exit("e2e_test_audit")
