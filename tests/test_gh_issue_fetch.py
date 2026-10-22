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
import tempfile
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

SCRATCH = Path(tempfile.mkdtemp(prefix="gh-issue-fetch-"))
FLEET_LIST = SCRATCH / "projects.toml"
FLEET_LIST.write_text(
    '[alpha]\ncwd_prefix = "X:/fleet/alpha"\n\n[bravo]\ncwd_prefix = "X:/fleet/bravo"\n\n'
    '[global]\narchitecture_ignore = [\n  "bravo",\n]\n',
    encoding="utf-8",
)
ALL_AWAKE = SCRATCH / "all-awake.toml"
ALL_AWAKE.write_text('[alpha]\ncwd_prefix = "X:/fleet/alpha"\n\n[global]\narchitecture_ignore = []\n', encoding="utf-8")


def _run_fetch(repos, fetch_one, extra, label=None):
    argv = ["fetch"] + (["--label", label] if label else []) + extra
    with patch.object(gif, "list_owner_repos", return_value=repos), \
         patch.object(gif, "fetch_repo_issues", side_effect=lambda r, lbl: fetch_one(r)):
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code = gif.main(argv)
        return out_buf.getvalue(), err_buf.getvalue(), code


def _run_fetch_cli(repos, fetch_one, label=None):
    stdout, stderr, _ = _run_fetch(repos, fetch_one, ["--projects-toml", str(ALL_AWAKE)], label)
    return stdout, stderr

stdout, stderr = _run_fetch_cli(["alpha", "bravo"], _fetch_ok)
parsed = json.loads(stdout)
check(len(parsed) == 3, "fetch CLI: stdout JSON has every aggregated issue")
check("REPOS=2 ISSUES=3 ERRORS=0" in stderr, "fetch CLI: stderr summary matches repo/issue/error counts")

stdout, stderr = _run_fetch_cli(["alpha", "bravo"], _fetch_one_fails)
parsed = json.loads(stdout)
check(len(parsed) == 1, "fetch CLI: a failing repo's issues are absent from stdout")
check("REPOS=2 ISSUES=1 ERRORS=1" in stderr, "fetch CLI: the error is counted in the summary")
check("ERROR bravo: rate limited" in stderr, "fetch CLI: the failing repo and reason are named on stderr")

# ---- dormant repos (docs/fleet-scope.md) ----

check(gif.split_dormant(["a", "b", "c"], {"b", "z"}) == (["a", "c"], ["b"]),
      "split_dormant: dormant names leave `kept` and land in `skipped`, order preserved")

# `charlie` is absent from the fleet list: an undeclared repo is still fetched.
owner_repos = ["alpha", "bravo", "charlie"]
stdout, stderr, code = _run_fetch(owner_repos, _fetch_ok, ["--projects-toml", str(FLEET_LIST)])
check(code == 0 and {i["repository"]["name"] for i in json.loads(stdout)} == {"alpha"},
      "dormant: the dormant repo's issues are omitted, the live and the undeclared repo are fetched")
check("REPOS=2 " in stderr and "SKIPPED_DORMANT=1: bravo" in stderr,
      "dormant: the summary counts the fetched repos and names the skipped one")

stdout, stderr, code = _run_fetch(owner_repos, _fetch_ok, ["--projects-toml", str(FLEET_LIST), "--include-dormant"])
check({i["repository"]["name"] for i in json.loads(stdout)} == {"alpha", "bravo"} and "SKIPPED_DORMANT" not in stderr,
      "dormant: --include-dormant restores the dormant repo and prints no skip line")

stdout, stderr, code = _run_fetch(owner_repos, _fetch_ok, ["--projects-toml", str(SCRATCH / "absent.toml")])
check(code == 2 and stdout == "" and "absent.toml is missing" in stderr,
      "dormant: a missing fleet list exits 2 with no output, never an unfiltered fetch")

_h.report_and_exit("test_gh_issue_fetch")
