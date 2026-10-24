"""Unit tests for the pure logic in skills/_lib/audit_issue.py.

No live `gh` — these exercise marker handling, title adoption, and the
keep/close decision that guarantees one audit issue per (repo, kind).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_audit_issue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import audit_issue as ai  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- marker handling ----

check(ai.marker_for("bug") == "<!-- audit-managed: kind=bug -->", "marker_for")
check(ai.has_marker("<!-- audit-managed: kind=bug -->\n\nx", "bug"), "has_marker positive")
check(not ai.has_marker("<!-- audit-managed: kind=bug -->", "stale"), "has_marker wrong kind")
check(not ai.has_marker("plain body", "bug"), "has_marker absent")
# a marker merely QUOTED later in the body (e.g. a planning issue documenting the
# format) must NOT be adopted — only a top-of-body marker identifies a managed issue.
check(not ai.has_marker("Intro prose.\n\n```\n<!-- audit-managed: kind=learning -->\n```\nmore", "learning"),
      "has_marker ignores a buried/quoted marker")
check(ai.has_marker("  \n<!-- audit-managed: kind=learning -->\n\nbody", "learning"),
      "has_marker tolerates leading blank lines before a top marker")

# ensure_marker: prepend when missing
em = ai.ensure_marker("hello", "duplication")
check(em.startswith("<!-- audit-managed: kind=duplication -->\n\nhello"), "ensure_marker prepend")
# ensure_marker: idempotent (re-stamp does not accumulate)
check(ai.ensure_marker(em, "duplication") == em, "ensure_marker idempotent")
# ensure_marker: replaces a stale marker rather than stacking
restamped = ai.ensure_marker("<!-- audit-managed: kind=bug -->\n\nhello", "stale")
check(restamped.count("audit-managed") == 1, "ensure_marker single marker")
check(restamped.startswith("<!-- audit-managed: kind=stale -->"), "ensure_marker replaced kind")
# ensure_marker: empty body
check(ai.ensure_marker("", "ledger") == "<!-- audit-managed: kind=ledger -->\n", "ensure_marker empty")
# practices kind round-trips through the marker like any other kind
pm = ai.ensure_marker("## Capabilities\n- x", "practices")
check(ai.has_marker(pm, "practices"), "practices marker round-trip")
check(not ai.has_marker(pm, "ledger"), "practices marker not ledger")

# ---- title adoption (pre-marker issues) ----

check(ai.title_matches("codebase-audit ledger", "ledger"), "title ledger")
check(ai.title_matches("audit-fleet digest state", "digest"), "title digest")
check(ai.title_matches("fleet practices ledger", "practices"), "title practices")
check(not ai.title_matches("fleet practices ledger", "ledger"), "title practices not ledger")
check(ai.title_matches("learning log — fleet", "learning"), "title learning")
check(not ai.title_matches("learning log — fleet", "practices"), "title learning not practices")
check(ai.title_matches("audit: bug findings (3 items)", "bug"), "title bucket w/ count")
check(ai.title_matches("audit: bug findings", "bug"), "title bucket no count")
check(ai.title_matches("audit: claude-md-drift findings (2 items)", "claude-md-drift"), "title hyphen kind")
check(not ai.title_matches("audit: bug findings", "stale"), "title wrong kind")
check(ai.title_matches("prompt-audit ledger", "prompt-audit"), "title prompt-audit ledger")
check("prompt-audit" in ai.KINDS and "prompt-audit" not in ai.BUCKET_KINDS,
      "prompt-audit is a managed kind, never a cleanup bucket")
check(not ai.title_matches("fix a nasty bug in parser", "bug"), "title not a managed issue")
check(not ai.title_matches("codebase-audit ledger v2", "ledger"), "title ledger strict")
check(ai.title_matches("cleanup-fleet-all deferred repos", "cleanup-deferred"),
      "title cleanup-deferred")
check(not ai.title_matches("cleanup-fleet-all deferred repos", "ledger"),
      "title cleanup-deferred not ledger")

# ---- plan: keep lowest, close the rest ----

# none present -> create
keep, close = ai.plan([{"number": 5, "title": "random", "body": "x"}], "bug")
check(keep is None and close == [], "plan none")

# single marker -> edit it, close nothing
keep, close = ai.plan([{"number": 9, "title": "anything", "body": ai.marker_for("bug")}], "bug")
check(keep == 9 and close == [], "plan single marker")

# single legacy (title only, no marker) -> adopt it
keep, close = ai.plan([{"number": 4, "title": "audit: stale findings (2 items)", "body": "no marker"}], "stale")
check(keep == 4 and close == [], "plan adopt legacy by title")

# multiple -> keep lowest, close rest (mix of marker + legacy title)
issues = [
    {"number": 21, "title": "audit: duplication findings (1 item)", "body": "legacy"},
    {"number": 20, "title": "x", "body": ai.marker_for("duplication")},
    {"number": 99, "title": "unrelated", "body": "nope"},
]
keep, close = ai.plan(issues, "duplication")
check(keep == 20 and close == [21], "plan keep lowest close rest")

# real-world local-llm-hub case: #30 + #40 both duplication -> keep 30
keep, close = ai.plan([
    {"number": 40, "title": "audit: duplication findings (3 items)", "body": "x"},
    {"number": 30, "title": "audit: duplication findings (1 item)", "body": "y"},
], "duplication")
check(keep == 30 and close == [40], "plan local-llm-hub dup")

# kinds list sanity
check("ledger" in ai.KINDS and "documentation" in ai.KINDS, "KINDS populated")
check("practices" in ai.KINDS, "KINDS has practices")
check("learning" in ai.KINDS, "KINDS has learning")
check("design-drift" in ai.KINDS, "KINDS has design-drift")
# prompt-drift (fleet-config#833) is /prompt-audit's queued cleanup bucket, filed by
# audit.py, not a /codebase-audit finding bucket — so never self-fix churn.
check("prompt-drift" in ai.KINDS and "prompt-drift" not in ai.BUCKET_KINDS,
      "KINDS has prompt-drift; BUCKET_KINDS does not")
check(ai.title_matches("audit: prompt-drift findings", "prompt-drift"), "title prompt-drift bucket")
# slop + security are finding buckets (fleet-config#361): registered kinds AND
# in BUCKET_KINDS, so their fix PRs are recognized as this repo's own self-fix
# churn (security's issue is closed on merge, re-adopted by title — see the
# BUCKET_KINDS comment).
check("slop" in ai.KINDS and "security" in ai.KINDS, "KINDS has slop + security")
check("slop" in ai.BUCKET_KINDS and "security" in ai.BUCKET_KINDS,
      "BUCKET_KINDS has slop + security")
# cleanup-deferred (fleet-config#642) is a registered kind but deliberately NOT
# a finding bucket: it tracks repos /cleanup-fleet-all could not enter, which is
# maintenance, not one of a repo's own audit findings — so it must never count
# as self-fix churn in evaluate_repo.
check("cleanup-deferred" in ai.KINDS, "KINDS has cleanup-deferred")
check("cleanup-deferred" not in ai.BUCKET_KINDS,
      "BUCKET_KINDS excludes cleanup-deferred — it is not an audit finding bucket")
check(ai.has_marker(ai.ensure_marker("## Deferred\n- website", "cleanup-deferred"),
                    "cleanup-deferred"),
      "cleanup-deferred marker round-trip")
check(ai.has_marker(ai.ensure_marker("## Findings\n- x", "slop"), "slop"),
      "slop marker round-trip")
check(ai.title_matches("audit: slop findings", "slop"), "title slop bucket")
check(ai.title_matches("audit: security findings", "security"), "title security bucket")
# design-drift round-trips through the marker + adopts its bucket title like any bucket kind
dd = ai.ensure_marker("## Findings\n- x", "design-drift")
check(ai.has_marker(dd, "design-drift"), "design-drift marker round-trip")
check(ai.title_matches("audit: design-drift findings", "design-drift"), "title design-drift bucket")
check("context-audit" in ai.KINDS, "KINDS has context-audit")
# context-audit round-trips through the marker like any other kind
ca = ai.ensure_marker("## Runs\n- x", "context-audit")
check(ai.has_marker(ca, "context-audit"), "context-audit marker round-trip")
check(not ai.has_marker(ca, "ledger"), "context-audit marker not ledger")
check(ai.title_matches("context-audit — always-on surface", "context-audit"),
      "title context-audit stable title")
check(not ai.title_matches("context-audit — always-on surface", "ledger"),
      "title context-audit not ledger")

# ---- rubric_sha / rubric_sha_of_path ----

check(ai.rubric_sha(b"hello") == ai.rubric_sha(b"hello"), "rubric_sha deterministic")
check(ai.rubric_sha(b"hello") != ai.rubric_sha(b"world"), "rubric_sha distinguishes content")

_tmp_repo = Path(tempfile.mkdtemp(prefix="audit_issue_rubric_"))
check(ai.rubric_sha_of_path(str(_tmp_repo)) == "", "rubric_sha_of_path: missing CLAUDE.md -> empty string")
(_tmp_repo / "CLAUDE.md").write_bytes(b"# rules\r\n")
check(ai.rubric_sha_of_path(str(_tmp_repo)) == ai.rubric_sha(b"# rules\r\n"),
      "rubric_sha_of_path: present CLAUDE.md -> sha256 of its raw on-disk bytes")
check(ai.rubric_sha_of_path(str(_tmp_repo)) != ai.rubric_sha(b"# rules\n"),
      "rubric_sha_of_path: hashes raw bytes, not newline-translated text (CRLF != LF)")
check(ai.rubric_sha_of_path(str(_tmp_repo)) != "", "rubric_sha_of_path: never confuses present-empty with missing")

# ---- parse_ledger ----

_ledger_full = (
    "<!-- audit-managed: kind=ledger -->\n<!-- audit-ledger -->\n"
    "last-audited-sha: abc123\nlast-audited-at: 2026-06-01\nrubric-sha: deadbeef\n"
)
check(ai.parse_ledger(_ledger_full) == {"sha": "abc123", "at": "2026-06-01", "rubric": "deadbeef"},
      "parse_ledger happy path")
check(ai.parse_ledger(_ledger_full.replace("rubric-sha: deadbeef", "rubric-sha: "))
      == {"sha": "abc123", "at": "2026-06-01", "rubric": ""},
      "parse_ledger tolerates empty rubric-sha (no CLAUDE.md)")
check(ai.parse_ledger("no ledger block here") == {"sha": None, "at": None, "rubric": None},
      "parse_ledger missing block")
check(ai.parse_ledger("<!-- audit-ledger -->\nrubric-sha: deadbeef\n")
      == {"sha": None, "at": None, "rubric": "deadbeef"},
      "parse_ledger missing sha/at fields")

# ---- bucket_issue_numbers ----

_mixed_issues = [
    {"number": 71, "title": "audit: documentation findings", "body": ai.marker_for("documentation")},
    {"number": 64, "title": "x", "body": ai.marker_for("bug")},
    {"number": 12, "title": "Fix a nasty bug in the parser", "body": "hand-filed, not audit-managed"},
    {"number": 99, "title": "unrelated", "body": "nope"},
]
_managed = ai.bucket_issue_numbers(_mixed_issues)
check(_managed == {"documentation": 71, "bug": 64}, "bucket_issue_numbers matches only marker/title, not label")
check(12 not in _managed.values(), "bucket_issue_numbers excludes hand-filed issue sharing a bucket label")

# ---- audit_only_churn ----

_prs_all_managed = [
    {"number": 1, "mergeCommit": {"oid": "sha1"}, "closingIssuesReferences": [{"number": 71}]},
    {"number": 2, "mergeCommit": {"oid": "sha2"}, "closingIssuesReferences": [{"number": 64}]},
]
check(ai.audit_only_churn(["sha1", "sha2"], _prs_all_managed, {71, 64}) is True,
      "audit_only_churn: all commits explained by managed-only PRs -> True")
check(ai.audit_only_churn(["sha1", "sha_unexplained"], _prs_all_managed, {71, 64}) is False,
      "audit_only_churn: one commit with no matching PR -> False")

_prs_hand_filed = [{"number": 3, "mergeCommit": {"oid": "sha3"}, "closingIssuesReferences": [{"number": 12}]}]
check(ai.audit_only_churn(["sha3"], _prs_hand_filed, {71, 64}) is False,
      "audit_only_churn: PR closing an unmanaged (hand-filed) issue -> False")

_prs_mixed = [{"number": 4, "mergeCommit": {"oid": "sha4"}, "closingIssuesReferences": [{"number": 71}, {"number": 12}]}]
check(ai.audit_only_churn(["sha4"], _prs_mixed, {71, 64}) is False,
      "audit_only_churn: PR closing a mix of managed + unmanaged -> False")

check(ai.audit_only_churn([], _prs_all_managed, {71, 64}) is True,
      "audit_only_churn: empty commit_shas -> True (defensive; count==0 branch handles this upstream)")

# ---- ledger_decision ----

check(ai.ledger_decision(0, "abc", "abc") == "SKIP", "ledger_decision: unchanged -> SKIP")
check(ai.ledger_decision(3, "abc", "abc") == "AUDIT", "ledger_decision: commits since, no self_fix -> AUDIT")
check(ai.ledger_decision(0, "abc", "def") == "AUDIT",
      "ledger_decision: zero commits but rubric differs (unexplained) -> AUDIT")
check(ai.ledger_decision(0, "", "") == "SKIP", "ledger_decision: both-empty rubric -> SKIP")
check(ai.ledger_decision(3, "abc", "abc", self_fix=True) == "SKIP_SELF_FIX",
      "ledger_decision: commits since + self_fix=True -> SKIP_SELF_FIX")
check(ai.ledger_decision(3, "abc", "def", self_fix=True) == "SKIP_SELF_FIX",
      "ledger_decision: self_fix=True stays SKIP_SELF_FIX even if the self-fix itself "
      "changed the rubric (e.g. a claude-md-drift fix editing CLAUDE.md) — rubric is "
      "only checked independently at zero commits")
check(ai.ledger_decision(3, "abc", "def", self_fix=False) == "AUDIT",
      "ledger_decision: commits since, rubric differs, NOT self_fix -> AUDIT")
check(ai.ledger_decision(None, "abc", "abc") == "AUDIT", "ledger_decision: unparseable count -> AUDIT")
check(ai.ledger_decision(0, None, "abc") == "AUDIT", "ledger_decision: unparseable ledger -> AUDIT")

# ---- pr_weight ----

check(ai.pr_weight("feat/12-add-thing") == 1.0, "pr_weight: feat -> 1.0")
check(ai.pr_weight("refactor/12-x") == 1.0, "pr_weight: refactor -> 1.0")
check(ai.pr_weight("perf/12-x") == 0.5, "pr_weight: perf -> 0.5")
check(ai.pr_weight("fix/12-x") == 0.3, "pr_weight: fix -> 0.3")
check(ai.pr_weight("chore/12-x") == 0.2, "pr_weight: chore -> 0.2")
check(ai.pr_weight("docs/12-x") == 0.0, "pr_weight: docs -> 0.0")
check(ai.pr_weight("test/12-x") == 0.0, "pr_weight: test -> 0.0")
check(ai.pr_weight("claude/some-agent-branch-XYZ") == 1.0,
      "pr_weight: unrecognized prefix fails open to 1.0")
check(ai.pr_weight("no-slash-branch") == 1.0, "pr_weight: no slash at all -> 1.0 (fail open)")
check(ai.pr_weight("") == 1.0, "pr_weight: empty branch -> 1.0 (fail open)")

# ---- unexplained_weighted_loc ----

def _pr(oid, additions, deletions, branch, closes=None):
    return {
        "mergeCommit": {"oid": oid},
        "additions": additions,
        "deletions": deletions,
        "headRefName": branch,
        "closingIssuesReferences": [{"number": n} for n in (closes or [])],
    }

# a self-fix-explained commit contributes nothing, regardless of type/LOC
_prs_self_fix_big = [_pr("sha1", 900, 900, "feat/12-huge-self-fix", closes=[71])]
check(ai.unexplained_weighted_loc(["sha1"], _prs_self_fix_big, {71}) == 0.0,
      "unexplained_weighted_loc: self-fix-explained commit -> 0 regardless of size/type")

# a docs-only unexplained commit contributes 0 weighted LOC (weight 0.0) even though real LOC > 0
_prs_docs = [_pr("sha2", 50, 10, "docs/13-fix-readme")]
check(ai.unexplained_weighted_loc(["sha2"], _prs_docs, {71}) == 0.0,
      "unexplained_weighted_loc: unexplained docs-only commit -> 0.0 (docs weight)")

# a fix-only unexplained commit contributes at 0.3 weight
_prs_fix = [_pr("sha3", 100, 0, "fix/14-bug")]
check(ai.unexplained_weighted_loc(["sha3"], _prs_fix, {71}) == 30.0,
      "unexplained_weighted_loc: unexplained fix commit -> 100 * 0.3 = 30.0")

# a feat commit contributes at full weight
_prs_feat = [_pr("sha4", 200, 50, "feat/15-new-thing")]
check(ai.unexplained_weighted_loc(["sha4"], _prs_feat, {71}) == 250.0,
      "unexplained_weighted_loc: unexplained feat commit -> full 250 LOC")

# mixed: self-fix (0) + docs (0) + fix (0.3x) + feat (1.0x) accumulate
_prs_mixed_sig = [
    _pr("sha1", 900, 900, "feat/12-huge-self-fix", closes=[71]),
    _pr("sha2", 50, 10, "docs/13-fix-readme"),
    _pr("sha3", 100, 0, "fix/14-bug"),
    _pr("sha4", 200, 50, "feat/15-new-thing"),
]
check(ai.unexplained_weighted_loc(["sha1", "sha2", "sha3", "sha4"], _prs_mixed_sig, {71}) == 30.0 + 250.0,
      "unexplained_weighted_loc: sums only unexplained commits' weighted LOC")

# a PR closing a mix of managed + unmanaged issues is NOT self-fix-explained -> counts fully
_prs_mixed_refs = [_pr("sha5", 40, 0, "fix/16-x", closes=[71, 999])]
check(ai.unexplained_weighted_loc(["sha5"], _prs_mixed_refs, {71}) == 12.0,
      "unexplained_weighted_loc: PR closing managed+unmanaged mix counts fully at its own weight")

# a commit with no matching PR at all (direct push) fails open to infinity
check(ai.unexplained_weighted_loc(["sha_orphan"], _prs_feat, {71}) == float("inf"),
      "unexplained_weighted_loc: no matching PR -> inf (fail open, forces AUDIT past any threshold)")

# empty commit list -> 0.0
check(ai.unexplained_weighted_loc([], _prs_feat, {71}) == 0.0,
      "unexplained_weighted_loc: no commits -> 0.0")

# ---- ledger_decision: significance threshold ----

check(ai.ledger_decision(3, "abc", "abc", self_fix=False, significance=500.0, threshold=1000.0)
      == "SKIP_BELOW_THRESHOLD",
      "ledger_decision: significance below threshold -> SKIP_BELOW_THRESHOLD")
check(ai.ledger_decision(3, "abc", "abc", self_fix=False, significance=1000.0, threshold=1000.0)
      == "AUDIT",
      "ledger_decision: significance at threshold -> AUDIT (>= crosses)")
check(ai.ledger_decision(3, "abc", "abc", self_fix=False, significance=1500.0, threshold=1000.0)
      == "AUDIT",
      "ledger_decision: significance above threshold -> AUDIT")
check(ai.ledger_decision(3, "abc", "abc", self_fix=False, significance=None) == "AUDIT",
      "ledger_decision: significance not computed (None) -> AUDIT (pre-threshold behavior preserved)")
check(ai.ledger_decision(3, "abc", "abc", self_fix=True, significance=999999.0) == "SKIP_SELF_FIX",
      "ledger_decision: self_fix=True wins regardless of significance value")
check(ai.ledger_decision(0, "abc", "abc", significance=0.0) == "SKIP",
      "ledger_decision: zero commits -> significance is irrelevant, rubric decides")

# ---- gh: retries once on a transient (5xx/timeout) failure, never on 4xx (fleet-config#506) ----

class _FakeCompleted:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_original_run = ai._run
_original_sleep = ai.time.sleep
ai.time.sleep = lambda seconds: None  # skip the real backoff delay in tests
_gh_calls = {"n": 0}


def _fake_run(_expected_stderr, _fail_times):
    def _run(args):
        _gh_calls["n"] += 1
        if _gh_calls["n"] <= _fail_times:
            return _FakeCompleted(1, stderr=_expected_stderr)
        return _FakeCompleted(0, stdout="ok-after-retry")
    return _run


_gh_calls["n"] = 0
ai._run = _fake_run("HTTP 502: Bad Gateway (https://api.github.com/graphql)", 1)
try:
    result = ai.gh(["issue", "list"])
    check(result == "ok-after-retry", "gh retries once on a transient HTTP 5xx failure and returns")
    check(_gh_calls["n"] == 2, "gh made exactly two attempts for a one-time transient failure")
finally:
    ai._run = _original_run

_gh_calls["n"] = 0
ai._run = _fake_run("i/o timeout", 1)
try:
    result = ai.gh(["issue", "list"])
    check(result == "ok-after-retry", "gh retries once on a transient timeout failure and returns")
finally:
    ai._run = _original_run

_gh_calls["n"] = 0
ai._run = _fake_run("HTTP 401: Bad credentials", 99)
try:
    try:
        ai.gh(["issue", "list"])
        raised = False
    except SystemExit:
        raised = True
    check(raised, "gh does not retry a non-transient (4xx/auth) failure")
    check(_gh_calls["n"] == 1, "gh made exactly one attempt for a non-transient failure")
finally:
    ai._run = _original_run

_gh_calls["n"] = 0
ai._run = _fake_run("HTTP 503: Service Unavailable", 99)
try:
    try:
        ai.gh(["issue", "list"])
        raised = False
    except SystemExit:
        raised = True
    check(raised, "gh gives up after one retry if the failure is still transient")
    check(_gh_calls["n"] == 2, "gh caps at exactly one retry (two attempts total)")
finally:
    ai._run = _original_run
    ai.time.sleep = _original_sleep


# ---- ledger baseline sha: squash-merge safety (fleet-config#567) ----
#
# The ledger used to record the working checkout's HEAD. On a feature branch
# that tip is destroyed by squash-merge + delete-branch, `rev-list <sha>..HEAD`
# then fails, and the repo silently drops out of every sweep via errors[].

def _git567(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


_gtmp = Path(tempfile.mkdtemp(prefix="test_audit_issue_567_"))
try:
    _up, _work = make_upstream_and_clone(_gtmp, check)

    _main_sha = _git567(_work, "rev-parse", "origin/main")

    # Check out a feature branch and commit — exactly the state /codebase-audit
    # was in when it poisoned two real ledgers.
    _git567(_work, "checkout", "-q", "-b", "feat/x")
    (_work / "f.txt").write_text("x\n", encoding="utf-8")
    _git567(_work, "add", "f.txt")
    _git567(_work, "commit", "-q", "-m", "feature")
    _feat_sha = _git567(_work, "rev-parse", "HEAD")

    check(_feat_sha != _main_sha, "fixture: feature tip differs from the default-branch tip")

    # The pre-fix write path recorded HEAD (== _feat_sha). The fix records the
    # default-branch commit, which a squash cannot destroy.
    check(ai.default_branch_sha(str(_work)) == _main_sha,
          "default_branch_sha: records the default-branch tip, not the checked-out HEAD")
    check(ai.recordable_ledger_sha(str(_work)) == _main_sha,
          "recordable_ledger_sha: returns the verified default-branch commit")

    check(ai.sha_is_on_default_branch(str(_work), _main_sha) is True,
          "sha_is_on_default_branch: the default-branch tip is reachable")
    check(ai.sha_is_on_default_branch(str(_work), _feat_sha) is False,
          "sha_is_on_default_branch: an off-branch feature tip is refused at write time")
    check(ai.sha_is_on_default_branch(str(_work), "") is False,
          "sha_is_on_default_branch: an empty sha is never recordable")

    # An unresolvable baseline is `None` — a real answer — not an exception
    # that lands the repo in an errors[] bucket nobody reads.
    check(ai.commits_since(str(_work), _main_sha) == 1,
          "commits_since: resolvable baseline -> a real count")
    check(ai.commits_since(str(_work), "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is None,
          "commits_since: a vanished baseline sha -> None, never a raise")

    # A path that is not a repo at all can produce no verifiable sha.
    check(ai.default_branch_sha(str(_gtmp / "nope")) is None,
          "default_branch_sha: unreadable repo -> None")
    check(ai.recordable_ledger_sha(str(_gtmp / "nope")) is None,
          "recordable_ledger_sha: unreadable repo -> refuses rather than guessing")

    # evaluate_repo routes an unresolvable baseline to a full audit under its
    # OWN reason, so it is never mistaken for ordinary organic change.
    _ledger_body = (
        "<!-- audit-managed: kind=ledger -->\n<!-- audit-ledger -->\n"
        "last-audited-sha: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        "last-audited-at: 2026-07-16\nrubric-sha: \n"
    )
    _orig_list_open, _orig_gh = ai._list_open, ai.gh
    try:
        ai._list_open = lambda repo: [{"number": 15, "title": "codebase-audit ledger", "body": _ledger_body}]
        ai.gh = lambda args, **kw: _ledger_body
        _out = ai.evaluate_repo("ferraroroberto/x", str(_work), dry_run=True)
    finally:
        ai._list_open, ai.gh = _orig_list_open, _orig_gh

    check(_out["decision"] == "AUDIT", "evaluate_repo: unresolvable baseline -> AUDIT, never a silent drop-out")
    check(_out["reason"] == ai.UNRESOLVABLE_BASELINE,
          "evaluate_repo: unresolvable baseline carries its own distinct reason")
    check(_out.get("baseline_sha", "").startswith("deadbeef"),
          "evaluate_repo: names the baseline sha that could not be resolved")
finally:
    shutil.rmtree(_gtmp, ignore_errors=True)


# ---- ledger marker: the tool owns the delimiter (fleet-config#566) ----
#
# An agent hand-authoring a fresh ledger writes the OPEN comment block — data
# inside the comment, so it stays hidden in rendered markdown — which looks at
# least as correct as the closed form and which the gate could not read. Three
# repos drifted this way, one of them a ledger created fresh by the very run
# that then failed to parse it, each buying a full Opus audit every week.

_CLOSED = (
    "<!-- audit-ledger -->\nlast-audited-sha: 7e31411\nlast-audited-at: 2026-08-06\n"
    "rubric-sha: 6e6ea0b\n"
)
# gitlab-to-github-migrator#6's pre-repair body, verbatim in shape.
_OPEN = (
    "<!-- audit-ledger\nlast-audited-sha: 7e31411\nlast-audited-at: 2026-08-06\n"
    "rubric-sha: 6e6ea0b\n-->\n"
)

for _label, _form in (("closed", _CLOSED), ("open-comment", _OPEN)):
    _p = ai.parse_ledger("Machine-readable ledger.\n\n" + _form)
    check(_p == {"sha": "7e31411", "at": "2026-08-06", "rubric": "6e6ea0b"},
          f"parse_ledger: reads the {_label} marker form")

check(ai.parse_ledger("no marker at all\nlast-audited-sha: 7e31411\n")["sha"] is None,
      "parse_ledger: key lines with no marker are still unparseable")

# GitHub stores bodies with CRLF; only some `gh` call shapes normalize it back.
check(ai.parse_ledger(_CLOSED.replace("\n", "\r\n")) ==
      {"sha": "7e31411", "at": "2026-08-06", "rubric": "6e6ea0b"},
      "parse_ledger: a CRLF body parses identically to an LF one")
check(ai.parse_ledger(_OPEN.replace("\n", "\r\n"))["sha"] == "7e31411",
      "parse_ledger: CRLF and the open marker form together still parse")

# render -> parse round-trips, so a body the tool wrote always reads back.
_rendered = ai.render_ledger_body("7e31411", "2026-08-06", "6e6ea0b")
check(ai.parse_ledger(_rendered) == {"sha": "7e31411", "at": "2026-08-06", "rubric": "6e6ea0b"},
      "render_ledger_body: what the tool writes always parses back")
check(ai._LEDGER_MARKER in _rendered, "render_ledger_body: emits the closed marker")
# An empty rubric-sha is a real value (repo has no CLAUDE.md), never None.
check(ai.parse_ledger(ai.render_ledger_body("7e31411", "2026-08-06", ""))["rubric"] == "",
      "render_ledger_body: an empty rubric round-trips as '', not None")

# A drifted ledger self-heals: normalizing the open form yields the closed one.
check(ai.normalize_ledger_body(_OPEN) == _rendered,
      "normalize_ledger_body: the drifted open form is rewritten closed")
check(ai.normalize_ledger_body(_rendered) == _rendered,
      "normalize_ledger_body: already-canonical bodies are unchanged (idempotent)")
try:
    ai.normalize_ledger_body("just some prose, no block at all")
    check(False, "normalize_ledger_body: refuses a body that would not parse back")
except SystemExit:
    check(True, "normalize_ledger_body: refuses a body that would not parse back")

# The generic upsert path validates + normalizes, so no caller's markdown can
# put an unreadable ledger on GitHub.
_seen: list[str] = []
_orig_upsert = ai._upsert_issue
_orig_fetch_rows = ai._fetch_snapshot_rows
try:
    # A not-recurring history, so bucket upserts stay offline and add no block
    # (recurrence stamping has its own checks, fleet-config#1513).
    _zero_row = {"run": "2026-01-01", "sha": "0", "counts": {k: 0 for k in ai.SNAPSHOT_COLUMNS.values()}}
    ai._fetch_snapshot_rows = lambda repo: ([_zero_row], None)
    ai._upsert_issue = lambda repo, kind, title, body, label, reopen=False: (
        _seen.append(body), "url"
    )[1]
    # cmd_* print the issue URL; swallow it so the harness output stays readable.
    with contextlib.redirect_stdout(io.StringIO()):
        ai.cmd_upsert("ferraroroberto/x", "ledger", "codebase-audit ledger", _OPEN, "audit-meta")
        check(_seen == [_rendered], "cmd_upsert: a ledger body is normalized before it is written")
        _seen.clear()
        try:
            ai.cmd_upsert("ferraroroberto/x", "ledger", "codebase-audit ledger", "prose only", "audit-meta")
            check(False, "cmd_upsert: refuses to write an unparseable ledger")
        except SystemExit:
            check(_seen == [], "cmd_upsert: refuses to write an unparseable ledger")
        # Non-ledger kinds are markdown, not a machine contract — passed through.
        ai.cmd_upsert("ferraroroberto/x", "bug", "audit: bug findings", "free prose", None)
    check(_seen == ["free prose"], "cmd_upsert: non-ledger bodies are passed through untouched")

    # --verify-quotes (fleet-config#960): the whole write is refused when a
    # finding's quote is not in its file; whitespace differences don't count.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _qd:
        Path(_qd, "m.py").write_bytes(b"x  =   compute(\r\n  1)\r\n")
        _good = "- [ ] **m.py:1** — slow. Quote: `x = compute( 1)`. Fix: cache.\n"
        _absent = _good + "- [ ] **m.py:2** — made up. Quote: `never there`. Fix: n/a.\n"
        _seen.clear()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ai.cmd_upsert("ferraroroberto/x", "bug", "audit: bug findings", _good, None, verify_quotes=_qd)
            check(_seen == [_good], "upsert --verify-quotes: a present, whitespace-differing quote is written")
            _seen.clear()
            try:
                ai.cmd_upsert("ferraroroberto/x", "bug", "audit: bug findings", _absent, None, verify_quotes=_qd)
                check(False, "upsert --verify-quotes: an absent quote refuses the write")
            except SystemExit as _exc:
                check(_seen == [] and "QUOTES=unverified count=1" in str(_exc),
                      "upsert --verify-quotes: an absent quote refuses the write, nothing is written")
finally:
    ai._upsert_issue = _orig_upsert
    ai._fetch_snapshot_rows = _orig_fetch_rows

# ---- cleanup-deferred: open/closed state carries the signal (fleet-config#642) ----
# A tracking issue left open saying "nothing deferred" is a zombie, so an empty
# deferred set CLOSES it and the next non-empty run must REOPEN that same issue
# rather than filing a second one. Both halves are gh plumbing, so the gh layer
# is stubbed and the recorded command sequence is what's asserted.
_gh_calls: list[list[str]] = []
_orig_gh, _orig_open, _orig_closed = ai.gh, ai._list_open, ai._list_closed
_orig_label = ai._ensure_label
try:
    ai.gh = lambda args, _retried=False: (_gh_calls.append(list(args)), "https://x/issues/7")[1]
    ai._ensure_label = lambda repo, label: None

    # No open managed issue, but a closed one exists -> reopen it, never create.
    ai._list_open = lambda repo: []
    ai._list_closed = lambda repo, kind: [
        {"number": 7, "title": "cleanup-fleet-all deferred repos", "body": ""}
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        ai.cmd_upsert("ferraroroberto/fleet-config", "cleanup-deferred",
                      "cleanup-fleet-all deferred repos", "## Deferred\n- website",
                      "chore", True)
    check(["issue", "reopen", "7", "--repo", "ferraroroberto/fleet-config"] in _gh_calls,
          "upsert --reopen: a closed cleanup-deferred issue is reopened")
    check(not any(c[:2] == ["issue", "create"] for c in _gh_calls),
          "upsert --reopen: no duplicate issue is filed when a closed one exists")
    check(any(c[:3] == ["issue", "edit", "7"] for c in _gh_calls),
          "upsert --reopen: the reopened issue is the one edited")

    # The closed lookup is filtered server-side on the kind's marker, not an
    # unfiltered recent window that slides past an old managed issue (#1303).
    _gh_calls.clear()
    _stub_gh = ai.gh
    ai.gh = lambda args, _retried=False: (_gh_calls.append(list(args)), "[]")[1]
    _orig_closed("ferraroroberto/fleet-config", "cleanup-deferred")
    ai.gh = _stub_gh
    check(_gh_calls and "--search" in _gh_calls[0]
          and "audit-managed: kind=cleanup-deferred" in _gh_calls[0][_gh_calls[0].index("--search") + 1],
          "_list_closed searches for the kind's marker instead of taking the newest N closed issues")

    # Without --reopen the closed issue is invisible -- `security` is closed on
    # fix-merge by design and must never be resurrected by an unrelated upsert.
    _gh_calls.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        ai.cmd_upsert("ferraroroberto/fleet-config", "cleanup-deferred",
                      "cleanup-fleet-all deferred repos", "body", "chore")
    check(not any(c[:2] == ["issue", "reopen"] for c in _gh_calls),
          "upsert without --reopen never reopens a closed issue (security stays closed)")
    check(any(c[:2] == ["issue", "create"] for c in _gh_calls),
          "upsert without --reopen files a new issue when none is open")

    # Empty deferred set -> close the open issue with the comment.
    _gh_calls.clear()
    ai._list_open = lambda repo: [
        {"number": 7, "title": "cleanup-fleet-all deferred repos", "body": ""}
    ]
    with contextlib.redirect_stdout(io.StringIO()) as _out:
        ai.cmd_close("ferraroroberto/fleet-config", "cleanup-deferred", "cleared on 2026-08-16")
    check(any(c[:3] == ["issue", "close", "7"] and "cleared on 2026-08-16" in c
              for c in _gh_calls),
          "close: the open cleanup-deferred issue is closed with the run's comment")
    check("CLOSED=" in _out.getvalue(),
          "close: prints which issue it closed rather than leaving the caller to assume")

    # Idempotent: nothing open is already the requested state, not an error.
    _gh_calls.clear()
    ai._list_open = lambda repo: []
    with contextlib.redirect_stdout(io.StringIO()) as _out:
        ai.cmd_close("ferraroroberto/fleet-config", "cleanup-deferred", "nothing to do")
    check(_gh_calls == [] and _out.getvalue().strip() == "CLOSED=none",
          "close: no open issue is a no-op reported as CLOSED=none, never an error")
finally:
    ai.gh, ai._list_open, ai._list_closed = _orig_gh, _orig_open, _orig_closed
    ai._ensure_label = _orig_label

check(ai.UNPARSEABLE_LEDGER in ai.BROKEN_LEDGER_REASONS and
      ai.UNRESOLVABLE_BASELINE in ai.BROKEN_LEDGER_REASONS,
      "BROKEN_LEDGER_REASONS: both unreadable-ledger reasons are flagged as broken")
check("no-ledger" not in ai.BROKEN_LEDGER_REASONS,
      "BROKEN_LEDGER_REASONS: a never-audited repo is a first audit, not drift")

# End to end: a repo whose ledger is in the drifted open form and whose baseline
# is current must now SKIP, where pre-fix it billed a full audit every week.
_gt2 = Path(tempfile.mkdtemp(prefix="test_audit_issue_566_"))
try:
    _r = _gt2 / "repo"
    _r.mkdir()
    _git567(_r, "init", "-q")
    _git567(_r, "checkout", "-q", "-b", "main")
    _git567(_r, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git567(_r, "config", "user.name", "Test")
    (_r / "README.md").write_text("hello\n", encoding="utf-8")
    _git567(_r, "add", "README.md")
    _git567(_r, "commit", "-q", "-m", "initial")
    _head = _git567(_r, "rev-parse", "HEAD")

    _drifted = (
        "<!-- audit-managed: kind=ledger -->\n"
        f"<!-- audit-ledger\nlast-audited-sha: {_head}\nlast-audited-at: 2026-08-06\n"
        "rubric-sha: \n-->\n"
    )
    _orig_list_open, _orig_gh = ai._list_open, ai.gh
    try:
        ai._list_open = lambda repo: [{"number": 6, "title": "codebase-audit ledger", "body": _drifted}]
        ai.gh = lambda args, **kw: _drifted
        _o = ai.evaluate_repo("ferraroroberto/x", str(_r), dry_run=True)
    finally:
        ai._list_open, ai.gh = _orig_list_open, _orig_gh
    check(_o["decision"] == "SKIP",
          "evaluate_repo: a drifted-but-current ledger SKIPs instead of re-billing a full audit")
finally:
    shutil.rmtree(_gt2, ignore_errors=True)


# The write path that poisoned the real ledgers was not code — it was
# /codebase-audit's step 9 prose telling the agent to record `git rev-parse
# HEAD` and to compose the block by hand. Guard the prose too, or the helpers
# above are one doc edit from moot.

_step9 = (Path(__file__).resolve().parent.parent / "skills" / "codebase-audit" / "SKILL.md").read_text(
    encoding="utf-8").split("### 9. Update the ledger", 1)[-1].split("\n### ", 1)[0]
check("git rev-parse HEAD" not in _step9,
      "codebase-audit SKILL.md step 9: never instructs recording the checkout HEAD")
check("audit_issue.py ledger-write" in _step9,
      "codebase-audit SKILL.md step 9: writes the ledger through the one helper that owns sha + marker")
check("--kind ledger" not in _step9,
      "codebase-audit SKILL.md step 9: never hand-authors a ledger body for a generic upsert")

# Same class of doc bug in /audit-fleet's step 4b (fleet-config#681): it told
# the agent "never a fixed shared name" and then gave a fixed shared name as
# the example, one clause later. Its parallel sub-agents share E:/tmp, so a
# fixed name is a race between two repos' practices-ledger writes.
_af = (Path(__file__).resolve().parent.parent / ".claude" / "skills" / "audit-fleet" / "SKILL.md").read_text(
    encoding="utf-8")
check("E:/tmp/audit-practices-ledger.md" not in _af,
      "audit-fleet SKILL.md: the practices-ledger temp-file example is not a fixed shared name")
check("audit-practices-ledger-<short-sha>.md" in _af,
      "audit-fleet SKILL.md: the example carries a per-run suffix, matching codebase-audit's convention")

# ---- _upsert_issue reclaims its NamedTemporaryFile (fleet-config#681) --------
# `_write_tmp` uses delete=False, so nothing but this function ever reclaims
# the file. One weekly /audit-fleet pass upserts a ledger plus up to eight
# bucket issues across ~39 repos, so an un-unlinked temp file is hundreds of
# orphaned .md files in %TEMP% every week, forever.

import os  # noqa: E402

_tmp_dir = Path(tempfile.gettempdir())


def _upsert_leaks(gh_stub) -> tuple[int, list[Path]]:
    before = set(_tmp_dir.glob("*.md"))
    _orig = (ai.gh, ai._run, ai._list_open)
    ai.gh = gh_stub
    ai._run = lambda *a, **k: ""
    ai._list_open = lambda *a, **k: []
    try:
        raised = 0
        try:
            ai._upsert_issue("o/r", "bug", "audit: bug findings", "body", None)
        except RuntimeError:
            raised = 1
        return raised, sorted(set(_tmp_dir.glob("*.md")) - before)
    finally:
        ai.gh, ai._run, ai._list_open = _orig


_raised, _leaked = _upsert_leaks(lambda argv: "https://example/1")
check(_raised == 0 and _leaked == [],
      f"_upsert_issue: the body temp file is unlinked on the success path (leaked: {_leaked!r})")


def _boom(argv):
    raise RuntimeError("gh exploded")


_raised, _leaked = _upsert_leaks(_boom)
check(_raised == 1 and _leaked == [],
      f"_upsert_issue: the temp file is unlinked even when `gh` raises (leaked: {_leaked!r})")

_probe = ai._write_tmp("x")
check(os.path.exists(_probe),
      "_write_tmp still produces a real file — the cleanup is the caller's job, not a no-op writer")
os.unlink(_probe)


# ---- bucket recurrence from audit snapshots (fleet-config#1513) -------------

_SNAP_HDR = ("| run | sha | dup | stale | drift | maint | slop | bug | doc | total | sec |\n"
             "|-----|-----|-----|-------|-------|-------|------|-----|-----|-------|-----|\n")


def _snap(run, dup, stale=0, drift=0, maint=0, slop=0, bug=0, doc=0, blank=False):
    total = dup + stale + drift + maint + slop + bug + doc
    gap = "\n" if blank else ""
    return (f"<!-- audit-snapshot -->\n{gap}{_SNAP_HDR}"
            f"| {run} | abc1234 | {dup} | {stale} | {drift} | {maint} | {slop} | {bug} | {doc} | {total} | 0 |\n")


_p = ai.parse_snapshot(_snap("2026-10-07", 4, stale=4, maint=2, doc=3))
check(_p is not None and _p["run"] == "2026-10-07" and _p["counts"]["duplication"] == 4
      and _p["counts"]["documentation"] == 3, "parse_snapshot: canonical table maps columns to kinds")
check(ai.parse_snapshot(_snap("2026-10-07", 1, blank=True)) is not None,
      "parse_snapshot: a blank line between marker and table still parses")
check(ai.parse_snapshot(_snap("2026-10-07", 1).replace("\n", "\r\n")) is not None,
      "parse_snapshot: CRLF body parses")
_reordered = ("<!-- audit-snapshot -->\n| sha | run | doc | bug | slop | maint | drift | stale | dup | total | sec |\n"
              "|-|-|-|-|-|-|-|-|-|-|-|\n| abc | 2026-10-07 | 9 | 0 | 0 | 0 | 0 | 0 | 5 | 14 | 0 |\n")
_pr2 = ai.parse_snapshot(_reordered)
check(_pr2 is not None and _pr2["counts"]["duplication"] == 5 and _pr2["counts"]["documentation"] == 9,
      "parse_snapshot: cells map by header name, not position")
check(ai.parse_snapshot(_snap("2026-10-07", 1).replace("| 1 |", "| x |", 1)) is None,
      "parse_snapshot: a non-integer bucket cell is malformed (None), never a guessed zero")
check(ai.parse_snapshot(_snap("2026-10-07", 1).replace("| doc ", "| docs ")) is None,
      "parse_snapshot: a missing bucket column is malformed")
check(ai.parse_snapshot(_snap("last week", 1)) is None, "parse_snapshot: a non-ISO run date is malformed")
check(not ai.is_snapshot("As noted, `<!-- audit-snapshot -->` comments carry counts.\n" + _SNAP_HDR),
      "is_snapshot: a comment that only quotes the marker is ignored")


def _c(body, created):
    return {"body": body, "created_at": created}


_rows = ai.snapshot_rows([
    _c(_snap("2026-10-15", 7, stale=4, maint=1, slop=2, bug=1, doc=2), "2026-10-15T09:00:00Z"),
    _c("unrelated self-fix comment", "2026-10-10T00:00:00Z"),
    _c(_snap("2026-10-07", 4, stale=4, maint=2, doc=3), "2026-10-07T04:00:00Z"),
])
check(len(_rows) == 2 and _rows[0]["run"] == "2026-10-07", "snapshot_rows: filters non-snapshots, orders by run")
_r = ai.bucket_recurrence(_rows)
check(_r["state"] == "ok" and _r["buckets"]["duplication"] == {"state": "recurring", "counts": [4, 7]},
      "bucket_recurrence: non-zero twice is recurring")
check(_r["buckets"]["stale"]["state"] == "recurring" and _r["buckets"]["documentation"]["state"] == "recurring",
      "bucket_recurrence: steady and shrinking non-zero counts still recur")
check(_r["buckets"]["slop"]["state"] == "not-recurring" and _r["buckets"]["claude-md-drift"]["state"] == "not-recurring",
      "bucket_recurrence: 0->2 and 0->0 are not recurring")
_r20 = ai.bucket_recurrence(ai.snapshot_rows([_c(_snap("2026-10-01", 2), "a"), _c(_snap("2026-10-08", 0), "b")]))
check(_r20["buckets"]["duplication"]["state"] == "not-recurring", "bucket_recurrence: 2->0 is not recurring")
check(set(_r["buckets"]) == set(ai.SNAPSHOT_COLUMNS.values()) and "security" not in _r["buckets"],
      "bucket_recurrence: reports all seven snapshot buckets, never security")

_one = ai.bucket_recurrence(ai.snapshot_rows([_c(_snap("2026-10-07", 4), "a")]))
check(_one["state"] == "unknown" and _one["reason"] == "insufficient-history"
      and all(b["state"] == "unknown" for b in _one["buckets"].values()),
      "bucket_recurrence: fewer than two rows is unknown, never not-recurring")
_bad = ai.bucket_recurrence(ai.snapshot_rows([
    _c(_snap("2026-10-01", 3), "2026-10-01T00:00:00Z"),
    _c(_snap("2026-10-07", 4), "2026-10-07T00:00:00Z"),
    _c(_snap("2026-10-15", 7).replace("| 7 |", "| ? |", 1), "2026-10-15T00:00:00Z"),
]))
check(_bad["state"] == "unknown" and _bad["reason"] == "malformed-row",
      "bucket_recurrence: a malformed latest row is unknown, with no fallback to older valid rows")
_same_day = ai.snapshot_rows([
    _c(_snap("2026-10-15", 0), "2026-10-15T12:00:00Z"),
    _c(_snap("2026-10-15", 5), "2026-10-15T08:00:00Z"),
])
check([r["counts"]["duplication"] for r in _same_day] == [5, 0],
      "snapshot_rows: same-day rows order by created_at")

_lines = ai.recurrence_lines(_r)
check(_lines[0] == "RECURRENCE=ok ROWS=2 LATEST=2026-10-15@abc1234 PREVIOUS=2026-10-07@abc1234"
      and "BUCKET=duplication STATE=recurring COUNTS=4,7" in _lines and len(_lines) == 8,
      "recurrence_lines: header plus all seven buckets")
_ul = ai.recurrence_lines(_one)
check(_ul[0] == "RECURRENCE=unknown REASON=insufficient-history" and "BUCKET=bug STATE=unknown" in _ul,
      "recurrence_lines: unknown window prints every bucket as unknown")

# ---- upsert stamping ----

check(ai.recurrence_at_upsert(_rows, "duplication") == {"state": "recurring", "since": "2026-10-15"},
      "recurrence_at_upsert: latest posted row non-zero + this run non-empty -> recurring")
check(ai.recurrence_at_upsert(_rows, "claude-md-drift") == {"state": "not-recurring"},
      "recurrence_at_upsert: latest posted row zero -> not-recurring")
check(ai.recurrence_at_upsert([], "bug")["state"] == "unknown", "recurrence_at_upsert: no history -> unknown")
check(ai.recurrence_at_upsert([None], "bug") == {"state": "unknown", "reason": "malformed-row"},
      "recurrence_at_upsert: malformed latest row -> unknown")

_blk = ai.render_recurrence_block({"state": "recurring", "since": "2026-10-15"})
_stamped = ai.ensure_marker(ai.apply_recurrence_block("## Findings\n- [ ] x", _blk), "duplication")
check(_stamped.startswith("<!-- audit-managed: kind=duplication -->\n\n<!-- audit-recurrence: state=recurring"),
      "recurrence block sits right after the identity marker")
check(ai.has_marker(_stamped, "duplication"), "recurrence block keeps has_marker matching")
_again = ai.ensure_marker(ai.apply_recurrence_block(_stamped, _blk), "duplication")
check(_again == _stamped, "apply_recurrence_block is idempotent")
check(ai.recurrence_state_of(_stamped) == "recurring", "recurrence_state_of reads the stamped state")
_cleared = ai.ensure_marker(ai.apply_recurrence_block(_stamped, None), "duplication")
check("audit-recurrence" not in _cleared and "## Findings" in _cleared and ai.recurrence_state_of(_cleared) is None,
      "not-recurring removes the block and keeps the body")
_unk = ai.render_recurrence_block({"state": "unknown", "reason": "gh-error"})
check(ai.recurrence_state_of(ai.ensure_marker(ai.apply_recurrence_block(_stamped, _unk), "duplication")) == "unknown"
      and "could not be established" in _unk, "unknown writes a visible state=unknown block")
_quoted = ai.ensure_marker("Prose first.\n\n<!-- audit-recurrence: state=recurring since=x -->", "bug")
check(ai.recurrence_state_of(_quoted) is None, "recurrence_state_of ignores a block that is not at the top")
check(ai.recurrence_state_of(_stamped.replace("\n", "\r\n")) == "recurring", "recurrence_state_of: CRLF body")
_bq = ai.ensure_marker(ai.apply_recurrence_block("> a user blockquote\n\nmore", _blk), "bug")
check("> a user blockquote" in ai.apply_recurrence_block(_bq, None),
      "apply_recurrence_block keeps a body blockquote separated by a blank line")


def _dry_upsert(kind, rows_or_reason) -> str:
    _orig = (ai._fetch_snapshot_rows, ai._list_open)
    ai._fetch_snapshot_rows = (lambda repo: (None, rows_or_reason)) if isinstance(rows_or_reason, str) \
        else (lambda repo: (rows_or_reason, None))
    ai._list_open = lambda repo: []
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ai.cmd_upsert("o/r", kind, f"audit: {kind} findings", "## Findings\n", None, dry_run=True)
    finally:
        ai._fetch_snapshot_rows, ai._list_open = _orig
    return buf.getvalue()


check("state=recurring since=2026-10-15" in _dry_upsert("duplication", _rows),
      "upsert --dry-run: a recurring bucket shows the block in BODY")
check("audit-recurrence" not in _dry_upsert("claude-md-drift", _rows),
      "upsert --dry-run: a not-recurring bucket carries no block")
check("state=unknown reason=gh-error" in _dry_upsert("bug", "gh-error"),
      "upsert --dry-run: an unreadable ledger stamps unknown, never silently clean")
check("audit-recurrence" not in _dry_upsert("security", _rows),
      "upsert --dry-run: security (no snapshot column) is never stamped")

# ---- structural-gate ----

_LONG = "the class is runtime-only behaviour that no static scan can see"
check(ai.structural_verdict(None, "", []) == {"state": "n/a"}, "structural_verdict: no block -> n/a")
check(ai.structural_verdict("unknown", "", [])["state"] == "unknown",
      "structural_verdict: unknown recurrence stays unknown")
check(ai.structural_verdict("recurring", "fix: x\n", ["a.py"]) == {"state": "fail", "reason": "no-declaration"},
      "structural_verdict: no declaration fails")
check(ai.structural_verdict("recurring", "Structural-check: not encodable: too hard", [])
      == {"state": "fail", "reason": "empty-reason"}, "structural_verdict: a short reason fails")

_sg = Path(tempfile.mkdtemp(prefix="test_audit_issue_1513_"))
try:
    _sup, _swork = make_upstream_and_clone(_sg, check)
    # A scaffold whose origin/main publishes one component and the conventions rules.
    (_sg / "scaf").mkdir()
    _cup, _scaf = make_upstream_and_clone(_sg / "scaf", check)
    (_cup / "scripts").mkdir()
    (_cup / ".fleet.toml").write_text('[components]\ngeometry = { src = "tests/e2e/_geometry.py" }\n', encoding="utf-8")
    (_cup / "scripts" / "conventions_ratchet.py").write_text('RULES = ("no-window", "logging-not-print")\n', encoding="utf-8")
    run_git(_cup, "add", ".", check=check)
    run_git(_cup, "commit", "-q", "-m", "catalog", check=check)
    run_git(_scaf, "fetch", "-q", "origin", check=check)

    def _sgit(*a):
        return run_git(_swork, *a, check=check)

    _sgit("checkout", "-q", "-b", "fix/1-dup")
    for _rel, _text in (("tests/test_no_dup.py", "x = 1\n"), ("tests/e2e/_geometry.py", "y = 1\n"),
                        (".fleet.toml", '[conventions_ratchet]\nenabled = true\nrules = ["no-window"]\n')):
        (_swork / _rel).parent.mkdir(parents=True, exist_ok=True)
        (_swork / _rel).write_text(_text, encoding="utf-8")
        _sgit("add", _rel)
    _CHECK = "fix: dedupe\n\nStructural-check: `tests\\test_no_dup.py`"
    _sgit("commit", "-q", "-m", _CHECK)
    _rec_body = _stamped

    def _gate(body=_rec_body, scaffold=_scaf):
        return ai.structural_gate(str(_swork), "origin/main", body, scaffold)

    def _amend(msg):
        _sgit("commit", "-q", "--amend", "-m", msg)

    _decl = {"kind": "check", "path": "tests/test_no_dup.py"}
    check(_gate() == {"state": "fail", "reason": "no-scope", **_decl},
          f"structural-gate: a check with no Structural-scope: line fails no-scope ({_gate()})")
    _amend(_CHECK + "\nStructural-scope: generalizable: NoDupBlocks")
    check(_gate() == {"state": "pass", "scope": "generalizable", "name": "no-dup-blocks", **_decl},
          f"structural-gate: a generalizable scope passes with its kebab-case name ({_gate()})")
    _amend(_CHECK + "\nStructural-scope: repo-specific: only this repo renders its own legacy report tables")
    check(_gate() == {"state": "pass", "scope": "repo-specific", **_decl}, "structural-gate: a repo-specific scope passes")
    check(_gate(scaffold=_sg / "no-such-scaffold") == {"state": "unknown", "reason": "catalog-unknown", "scope": "unknown", **_decl},
          "structural-gate: an unreadable scaffold catalog is unknown, never a pass")
    _amend("fix: dedupe\n\nStructural-check: tests/e2e/_geometry.py")
    check(_gate() == {"state": "pass", "kind": "check", "path": "tests/e2e/_geometry.py", "scope": "scaffold-owned"},
          "structural-gate: a scaffold-published component is scaffold-owned with no scope line")
    _amend("fix: dedupe\n\nStructural-check: .fleet.toml")
    check(_gate() == {"state": "pass", "kind": "check", "path": ".fleet.toml", "scope": "scaffold-owned"},
          "structural-gate: switching on an existing conventions rule is scaffold-owned")
    check(_gate(_cleared) == {"state": "n/a"}, "structural-gate: a non-recurring body is n/a")
    _amend("fix: dedupe\n\nStructural-check: tests/missing.py")
    check(_gate() == {"state": "fail", "reason": "path-not-in-diff"},
          "structural-gate: a declared path outside the diff fails")
    _amend(f"fix: dedupe\n\nStructural-check: not encodable: {_LONG}")
    check(_gate() == {"state": "pass", "kind": "not-encodable", "scope": "n/a"},
          "structural-gate: a real not-encodable reason passes and needs no scope")
    _amend("fix: dedupe\n\nStructural-check: not encodable:")
    check(_gate() == {"state": "fail", "reason": "empty-reason"}, "structural-gate: an empty reason fails")
    _amend("fix: dedupe")
    check(_gate() == {"state": "fail", "reason": "no-declaration"}, "structural-gate: no declaration fails")
    check(ai.structural_gate(str(_swork), "origin/no-such-branch", _rec_body, _scaf)
          == {"state": "unknown", "reason": "git-error"}, "structural-gate: a git failure is unknown, not fail")
    with contextlib.redirect_stdout(io.StringIO()) as _o:
        _code = ai.cmd_structural_gate(str(_swork), "origin/main", _rec_body, _scaf)
    check(_code == 1 and _o.getvalue().strip() == "STRUCTURAL=fail REASON=no-declaration",
          "cmd_structural_gate: fail prints its line and exits 1")
finally:
    shutil.rmtree(_sg, ignore_errors=True)


_h.report_and_exit("test_audit_issue")
