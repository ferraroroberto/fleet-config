"""Unit tests for the pure logic in skills/_lib/gh_issue_fetch.py.

Exercises `aggregate` against an injected per-repo fetcher (no `gh` calls),
including the degrade-don't-block path where one repo's fetch raises, then
the `fetch` CLI wiring with `gh` monkeypatched out entirely -- no network
touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_gh_issue_fetch.py`  (also invoked by tests/run_acceptance.py)
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
import gh_issue_fetch as gif  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- aggregate ----

FIXTURE = {
    "alpha": [{"number": 1, "title": "a"}],
    "bravo": [{"number": 2, "title": "b"}, {"number": 3, "title": "c"}],
}


def _fetch_ok(repo):
    return FIXTURE.get(repo, [])


issues, errors = gif.aggregate(["alpha", "bravo"], _fetch_ok)
check(len(issues) == 3, "aggregate: issues from every repo are combined")
check(errors == [], "aggregate: no errors on a clean run")
check(all(i["repository"]["name"] in ("alpha", "bravo") for i in issues),
      "aggregate: each issue is stamped with its own repository.name")
check(next(i for i in issues if i["number"] == 1)["title"] == "a",
      "aggregate: original row fields survive the repository stamp")


def _fetch_one_fails(repo):
    if repo == "bravo":
        raise RuntimeError("rate limited")
    return FIXTURE.get(repo, [])


issues, errors = gif.aggregate(["alpha", "bravo", "charlie"], _fetch_one_fails)
check(len(issues) == 1 and issues[0]["repository"]["name"] == "alpha",
      "aggregate: a failing repo is excluded from issues, others still returned")
check(errors == [("bravo", "rate limited")],
      "aggregate: the failing repo is recorded with its reason, never silently dropped")

issues, errors = gif.aggregate([], _fetch_ok)
check(issues == [] and errors == [], "aggregate: no repos -> empty result, not an error")


# ---- fetch CLI wiring: gh monkeypatched out (no network) ----

def _run_fetch_cli(repos, fetch_one, label=None):
    argv = ["fetch"] + (["--label", label] if label else [])
    with patch.object(gif, "list_owner_repos", return_value=repos), \
         patch.object(gif, "fetch_repo_issues", side_effect=lambda r, lbl: fetch_one(r)):
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            gif.main(argv)
        return out_buf.getvalue(), err_buf.getvalue()

stdout, stderr = _run_fetch_cli(["alpha", "bravo"], _fetch_ok)
parsed = json.loads(stdout)
check(len(parsed) == 3, "fetch CLI: stdout JSON has every aggregated issue")
check("REPOS=2 ISSUES=3 ERRORS=0" in stderr, "fetch CLI: stderr summary matches repo/issue/error counts")

stdout, stderr = _run_fetch_cli(["alpha", "bravo"], _fetch_one_fails)
parsed = json.loads(stdout)
check(len(parsed) == 1, "fetch CLI: a failing repo's issues are absent from stdout")
check("REPOS=2 ISSUES=1 ERRORS=1" in stderr, "fetch CLI: the error is counted in the summary")
check("ERROR bravo: rate limited" in stderr, "fetch CLI: the failing repo and reason are named on stderr")

_h.report_and_exit("test_gh_issue_fetch")
