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


# ---- fenced code blocks are not declarations (#602) -------------------------
#
# project-scaffolding documents the `## CI expectations` template verbatim
# inside a fence. Matching it made the audit resolve test_dirs to the
# template's bracketed placeholder and report a confident 0 files for a repo
# with five -- a wrong answer indistinguishable from "no tests".

check(m.fenced_mask(["a", "```", "b", "```", "c"]) == [False, True, True, True, False],
      "fenced_mask: delimiters and body masked, surrounding prose is not")
check(m.fenced_mask(["````md", "```", "x", "```", "````", "after"])
      == [True, True, True, True, True, False],
      "fenced_mask: a longer opening fence is not closed by a shorter inner one")
check(m.fenced_mask(["~~~", "x", "~~~", "y"]) == [True, True, True, False],
      "fenced_mask: tilde fences tracked too")
check(m.fenced_mask(["```py", "x"]) == [True, True],
      "fenced_mask: an unclosed fence masks to end of file, never re-opens prose")

DOCUMENTED_TEMPLATE = """\
# Project Instructions

## CI is advisory
Block template (fill the bracketed values):

```markdown
## CI expectations
- CI's only signal is the **e2e suite**. Its e2e surface = `[app/webapp/, tests/e2e/, static assets]`.
```

Prose after the fence.
"""
check(m.find_ci_expectations_block(DOCUMENTED_TEMPLATE) is None,
      "a fenced template example is not a declared CI-expectations block")
check(m.resolve_test_dirs(DOCUMENTED_TEMPLATE) == list(m.DEFAULT_TEST_DIRS),
      "documented-only template falls back to tests/e2e, never the placeholder text")
check(not any("[" in d for d in m.resolve_test_dirs(DOCUMENTED_TEMPLATE)),
      "bracketed placeholder text never escapes as a resolved test dir")

REAL_BLOCK_WITH_EXAMPLE = """\
## CI expectations
- Its e2e surface = `app/webapp/`, `tests/e2e/`.
- Example of a different heading, quoted:

```markdown
## Some other heading
```

- Still part of the CI expectations block.

## Next section
- unrelated
"""
blk = m.find_ci_expectations_block(REAL_BLOCK_WITH_EXAMPLE)
assert blk is not None
check("Still part of the CI expectations block." in blk,
      "a real block is not truncated at a `## ` line quoted inside a fence")
check("unrelated" not in blk, "a real block still stops at the next genuine heading")
check(m.resolve_test_dirs(REAL_BLOCK_WITH_EXAMPLE) == ["tests/e2e"],
      "a genuinely declared block is still honoured")


# ---- split_resolved_dirs: "nowhere to look" is not "no tests" (#602) --------

_here = Path(__file__).resolve().parent
existing, missing = m.split_resolved_dirs(_here.parent, ["tests", "does/not/exist"])
check(existing == ["tests"] and missing == ["does/not/exist"],
      "split_resolved_dirs: separates real dirs from ones absent on disk")
check(m.split_resolved_dirs(_here.parent, ["nope/at/all"]) == ([], ["nope/at/all"]),
      "split_resolved_dirs: nothing resolves -> empty existing, so test_dirs_resolved is False")


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


# ---- parametrize-matrix redundancy, the name-clustering blind spot (#602) ---
#
# Modelled on project-scaffolding's pre-6717f6b test_geometry_helper.py: four
# differently-named tests sweeping one shared 8-leg MATRIX, 32 collected nodes,
# which cluster_candidates reported as zero clusters. PR #219 later collapsed
# them to 8 nodes with no coverage loss.

MATRIX_SRC = '''
import pytest

MATRIX = [(360, "light"), (768, "dark")]
OTHER = [(1, 2)]


@pytest.mark.parametrize(("width", "theme"), MATRIX, ids=map(matrix_id, MATRIX))
def test_violating_min_target_fails(width, theme):
    pass


@pytest.mark.parametrize(("width", "theme"), MATRIX)
def test_violating_overlap_fails(width, theme):
    pass


@pytest.mark.parametrize(("width", "theme"), MATRIX)
def test_violating_chart_ticks_fail(width, theme):
    pass


@pytest.mark.parametrize(("width", "theme"), MATRIX)
def test_violating_overflow_fails(width, theme):
    pass


@pytest.mark.parametrize("n", OTHER)
def test_unrelated_sweep(n):
    pass


def test_plain_no_parametrize():
    pass
'''

sigs = m.parametrize_signatures(MATRIX_SRC)
check(sigs.get("test_violating_min_target_fails") == "width,theme@name:MATRIX",
      "parametrize_signatures: argnames tuple + named collection form the signature")
check(len({sigs[n] for n in sigs if n.startswith("test_violating_")}) == 1,
      "parametrize_signatures: all four violating twins share one signature")
check(sigs.get("test_unrelated_sweep") == "n@name:OTHER",
      "parametrize_signatures: a different matrix gets a different signature")
check("test_plain_no_parametrize" not in sigs,
      "parametrize_signatures: an unparametrized test is absent, not empty-signatured")
check(m.parametrize_signatures("def test_x(:\n  syntax error") == {},
      "parametrize_signatures: an unparseable file yields nothing, never raises")

matrix_file = {"file": "tests/e2e/test_geometry_helper.py", "lines": 200,
               "tests": list(sigs), "parametrized": sigs}
mc = m.matrix_candidates([matrix_file])
check(len(mc) == 1, "matrix_candidates: only the >=2-member matrix is a candidate")
check(set(mc[0]["members"]) == {"test_violating_min_target_fails", "test_violating_overlap_fails",
                                "test_violating_chart_ticks_fail", "test_violating_overflow_fails"},
      "matrix_candidates: flags exactly the four same-matrix twins")
check(mc[0]["source"] == "name:MATRIX" and mc[0]["argnames"] == "width,theme",
      "matrix_candidates: the entry names the shared collection and argnames")
check(m.cluster_candidates([{"file": matrix_file["file"], "name": n} for n in sigs]) == [],
      "the name-based detector still sees nothing here -- that is the blind spot being covered")

check(m.matrix_candidates([{"file": "a.py", "parametrized": {"test_a": "n@name:M"}}]) == [],
      "matrix_candidates: a lone parametrized test is not a candidate")
check(m.matrix_candidates([{"file": "a.py", "lines": 1, "tests": []}]) == [],
      "matrix_candidates: a file with no parametrize data is skipped, never raises")

INLINE_TWINS = '''
import pytest

@pytest.mark.parametrize("w", [1, 2, 3])
def test_alpha(w):
    pass

@pytest.mark.parametrize("w", [1, 2, 3])
def test_beta(w):
    pass
'''
inline = m.parametrize_signatures(INLINE_TWINS)
check(len(set(inline.values())) == 1,
      "parametrize_signatures: identical inline matrices collide via their literal hash")
check(len(m.matrix_candidates([{"file": "a.py", "parametrized": inline}])) == 1,
      "matrix_candidates: duplicated inline matrices are a candidate too")


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
