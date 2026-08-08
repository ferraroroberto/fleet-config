"""Unit tests for the pure logic in skills/_lib/audit_issue.py.

No live `gh` — these exercise marker handling, title adoption, and the
keep/close decision that guarantees one audit issue per (repo, kind).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_audit_issue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import audit_issue as ai  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

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
check(not ai.title_matches("fix a nasty bug in parser", "bug"), "title not a managed issue")
check(not ai.title_matches("codebase-audit ledger v2", "ledger"), "title ledger strict")

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
# slop + security are finding buckets (fleet-config#361): registered kinds AND
# in BUCKET_KINDS, so their fix PRs are recognized as this repo's own self-fix
# churn (security's issue is closed on merge, re-adopted by title — see the
# BUCKET_KINDS comment).
check("slop" in ai.KINDS and "security" in ai.KINDS, "KINDS has slop + security")
check("slop" in ai.BUCKET_KINDS and "security" in ai.BUCKET_KINDS,
      "BUCKET_KINDS has slop + security")
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
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    check(proc.returncode == 0, f"git {' '.join(args)} in {cwd} failed: {proc.stderr}")
    return proc.stdout.strip()


_gtmp = Path(tempfile.mkdtemp(prefix="test_audit_issue_567_"))
try:
    _up = _gtmp / "upstream"
    _work = _gtmp / "work"
    _up.mkdir()
    _git567(_up, "init", "-q")
    _git567(_up, "checkout", "-q", "-b", "main")
    _git567(_up, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git567(_up, "config", "user.name", "Test")
    (_up / "README.md").write_text("hello\n", encoding="utf-8")
    _git567(_up, "add", "README.md")
    _git567(_up, "commit", "-q", "-m", "initial")

    _git567(_gtmp, "clone", "-q", str(_up), str(_work))
    _git567(_work, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git567(_work, "config", "user.name", "Test")

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


# The write path that actually poisoned the two real ledgers was not code — it
# was /codebase-audit's step 9 *instructing* the agent to record `git rev-parse
# HEAD`. Guard the prose too, or the helper above is one doc edit from moot.

_step9 = (Path(__file__).resolve().parent.parent / "skills" / "codebase-audit" / "SKILL.md").read_text(
    encoding="utf-8").split("### 9. Update the ledger", 1)[-1].split("\n### ", 1)[0]
check("git rev-parse HEAD" not in _step9,
      "codebase-audit SKILL.md step 9: never instructs recording the checkout HEAD")
check("audit_issue.py ledger-sha" in _step9,
      "codebase-audit SKILL.md step 9: records the sha via the verified ledger-sha helper")

_h.report_and_exit("test_audit_issue")
