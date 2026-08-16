"""Unit tests for the pure logic in skills/_lib/repo_preflight.py.

Exercises `classify_repo` against synthetic facts, `parse_worktree_list`
against real `git worktree list --porcelain` shapes, and
`partition_working_set`/`summarize` against a stubbed lookup -- this is the
test that proves fleet-config#642's acceptance criteria mechanically:

  * a skipped repo's issues are absent from `dispatch` (so no lane runs) yet
    still present, named and counted, in `skipped` (so nothing is dropped);
  * `summarize` reports repos and issues as two separate numbers, which is
    the distinction the old single footnote line collapsed;
  * an unreadable repo is `unknown` -- neither dispatched nor filed under a
    confirmed dirty tree;
  * the retry is re-runnable: the same working set through the same call
    yields a different partition once the underlying facts change, because
    the helper holds no state between calls.

Also exercises the `check`/`partition` CLI wiring with the real git calls
monkeypatched out -- no repo touched, no network.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_repo_preflight.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import repo_preflight as rp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def facts(**over) -> rp.RepoFacts:
    base = dict(
        exists=True,
        current_branch="main",
        default_branch="main",
        porcelain_empty=True,
        extra_worktrees=(),
    )
    base.update(over)
    return rp.RepoFacts(**base)


# ---- classify_repo ----

state, reason = rp.classify_repo(facts())
check(state == rp.AVAILABLE and reason == "",
      "classify_repo: clean tree on the default branch with no extra worktree -> available")

state, reason = rp.classify_repo(facts(exists=False))
check(state == rp.MISSING, "classify_repo: a path that isn't there -> missing")

state, reason = rp.classify_repo(facts(porcelain_empty=False))
check(state == rp.DIRTY and "never stashed" in reason,
      "classify_repo: dirty tree -> dirty, and the reason says the work is left alone")

state, reason = rp.classify_repo(facts(current_branch="feat/12-thing"))
check(state == rp.OFF_BRANCH and "feat/12-thing" in reason and "main" in reason,
      "classify_repo: off the default branch -> off-branch, naming both branches")

state, reason = rp.classify_repo(
    facts(extra_worktrees=("E:/automation/website-wt-9",))
)
check(state == rp.WORKTREE and "website-wt-9" in reason,
      "classify_repo: a pre-existing worktree -> worktree, naming the path")

# life-os's default branch is `master`, not `main` -- detected, never assumed.
state, _ = rp.classify_repo(facts(current_branch="master", default_branch="master"))
check(state == rp.AVAILABLE,
      "classify_repo: a repo whose default branch is `master` is available on `master`")

# Deterministic reporting when a repo fails more than one check at once.
state, _ = rp.classify_repo(
    facts(porcelain_empty=False, current_branch="feat/x",
          extra_worktrees=("E:/automation/x-wt-1",))
)
check(state == rp.DIRTY,
      "classify_repo: a repo failing several checks reports the first one, deterministically")


# ---- parse_worktree_list ----

PORCELAIN_PRIMARY_ONLY = "worktree E:/automation/website\nHEAD abc123\nbranch refs/heads/main\n"
check(rp.parse_worktree_list(PORCELAIN_PRIMARY_ONLY) == (),
      "parse_worktree_list: the primary alone is not an extra worktree")

PORCELAIN_WITH_EXTRA = (
    "worktree E:/automation/website\nHEAD abc123\nbranch refs/heads/main\n\n"
    "worktree E:/automation/website-wt-12\nHEAD def456\nbranch refs/heads/fix/12-x\n\n"
    "worktree E:/automation/website-wt-13\nHEAD 789abc\nbranch refs/heads/fix/13-y\n"
)
check(rp.parse_worktree_list(PORCELAIN_WITH_EXTRA)
      == ("E:/automation/website-wt-12", "E:/automation/website-wt-13"),
      "parse_worktree_list: every worktree past the primary, in git's own order")


# ---- partition_working_set ----

WORKING_SET = [
    {"repo": "photo-ocr", "number": 44, "bucket": "documentation"},
    {"repo": "website", "number": 12, "bucket": "documentation"},
    {"repo": "website", "number": 14, "bucket": "slop"},
    {"repo": "local-llm-hub", "number": 451, "bucket": "bug"},
]

_VERDICTS = {
    "photo-ocr": (rp.AVAILABLE, "", None),
    "website": (rp.DIRTY, "working tree not clean -- in-progress work, never stashed", None),
    "local-llm-hub": (rp.UNKNOWN, "git status --porcelain failed (exit 128)", None),
}

result = rp.partition_working_set(WORKING_SET, lambda repo: _VERDICTS[repo])

check([i["number"] for i in result["dispatch"]] == [44],
      "partition: only the available repo's issue reaches dispatch")
check([i["number"] for i in result["skipped"]] == [12, 14, 451],
      "partition: every skipped issue is still present and named -- deferred, not dropped")
check(len(result["dispatch"]) + len(result["skipped"]) == len(WORKING_SET),
      "partition: every issue lands in exactly one list, so absence from dispatch proves no lane runs")
check(all(i["repo_state"] == rp.DIRTY and "never stashed" in i["skip_reason"]
          for i in result["skipped"] if i["repo"] == "website"),
      "partition: a dirty repo drops every one of its issues across all buckets, with the reason attached")
check(next(i for i in result["skipped"] if i["repo"] == "local-llm-hub")["repo_state"] == rp.UNKNOWN,
      "partition: an unreadable repo is `unknown` -- not dispatched, and not filed as a dirty tree")

# One lookup per distinct repo, however many issues/buckets it carries.
calls: list[str] = []


def counting_lookup(repo: str):
    calls.append(repo)
    return _VERDICTS[repo]


rp.partition_working_set(WORKING_SET, counting_lookup)
check(calls == ["photo-ocr", "website", "local-llm-hub"],
      "partition: each distinct repo is resolved exactly once, however many issues it carries")

# A failed fetch is a note, never a verdict.
noted = rp.partition_working_set(
    [{"repo": "photo-ocr", "number": 44}],
    lambda repo: (rp.AVAILABLE, "", "git fetch origin failed (exit 128): could not resolve host"),
)
check(len(noted["dispatch"]) == 1 and "could not resolve host" in noted["dispatch"][0]["note"],
      "partition: an unreachable network is recorded as a note and still dispatches")


# ---- summarize ----

counts = rp.summarize(result)
check(counts["skipped_repos"] == 2 and counts["skipped_issues"] == 3,
      "summarize: repos and unprocessed issues are two separate numbers, never one 'skipped' count")
check(counts["unknown_repos"] == 1,
      "summarize: a repo whose state could not be established is counted apart from a confirmed skip")
check(counts["dispatch"] == 1, "summarize: the dispatch count matches the dispatch list")

empty = rp.summarize({"dispatch": [], "skipped": []})
check(empty == {"dispatch": 0, "skipped_repos": 0, "skipped_issues": 0, "unknown_repos": 0},
      "summarize: the zero case still produces every count, so the mandatory report line can be printed")

# fleet-config#642's fourth criterion: a run in which every repo was skipped
# has an empty dispatch list while candidates existed -- the condition the
# skill turns into SCHEDULED-RUN-FAILED rather than a clean sweep.
all_skipped = rp.partition_working_set(
    WORKING_SET, lambda repo: (rp.DIRTY, "working tree not clean", None)
)
check(all_skipped["dispatch"] == [] and len(all_skipped["skipped"]) == len(WORKING_SET),
      "partition: an all-skipped run is detectable as empty-dispatch-with-candidates")


# ---- the retry re-establishes facts, never replays a cached verdict ----

pass_number = {"n": 0}


def changing_lookup(repo: str):
    # `website` is committed and pushed between the two passes -- exactly the
    # case the end-of-run retry exists for.
    if pass_number["n"] == 0:
        return (rp.DIRTY, "working tree not clean", None)
    return (rp.AVAILABLE, "", None)


deferred = [i for i in WORKING_SET if i["repo"] == "website"]
first = rp.partition_working_set(deferred, changing_lookup)
pass_number["n"] = 1
second = rp.partition_working_set(deferred, changing_lookup)
check(first["dispatch"] == [] and len(second["dispatch"]) == 2,
      "the same deferred set re-partitions on live facts, so a repo that went clean is recovered")

pass_number["n"] = 0
third = rp.partition_working_set(
    [{"repo": "photo-ocr", "number": 44}],
    lambda repo: (rp.DIRTY, "went dirty since pre-flight", None),
)
check(third["dispatch"] == [],
      "a repo that BECAME dirty since pre-flight is not dispatched on an hours-old 'available'")


# ---- CLI wiring ----

def _run_partition_cli(issues, verdicts):
    with patch.object(rp, "check", side_effect=lambda path: verdicts[Path(path).name]):
        stdin = io.StringIO(json.dumps(issues))
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", stdin), \
             contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            rp.main(["partition"])
        return out_buf.getvalue(), err_buf.getvalue()


stdout, stderr = _run_partition_cli(WORKING_SET, _VERDICTS)
parsed = json.loads(stdout)
check(len(parsed["dispatch"]) == 1 and len(parsed["skipped"]) == 3,
      "partition CLI: stdout JSON matches the pure partition result")
check("DISPATCH=1 SKIPPED_REPOS=2 SKIPPED_ISSUES=3 UNKNOWN_REPOS=1" in stderr,
      "partition CLI: stderr summary carries all four counts")


def _run_check_cli(state, reason, note=None):
    with patch.object(rp, "check", return_value=(state, reason, note)):
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            rp.main(["check", "E:/automation/website"])
        return out_buf.getvalue()


out = _run_check_cli(rp.DIRTY, "working tree not clean")
check("STATE=dirty" in out and "REASON=working tree not clean" in out,
      "check CLI: prints STATE and REASON")
out = _run_check_cli(rp.AVAILABLE, "")
check("STATE=available" in out and "REASON=" not in out,
      "check CLI: an available repo prints no REASON line")

# A path that isn't there is `missing`, not a crash -- exercised through the
# real `check`, since that branch short-circuits before any git call.
state, reason, note = rp.check(Path("E:/automation/definitely-not-a-repo-642"))
check(state == rp.MISSING and "no such path" in reason and note is None,
      "check: a nonexistent path is `missing`, with no git call attempted")

_h.report_and_exit("test_repo_preflight")
