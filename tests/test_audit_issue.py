"""Unit tests for the pure logic in skills/_lib/audit_issue.py.

No live `gh` — these exercise marker handling, title adoption, and the
keep/close decision that guarantees one audit issue per (repo, kind).

Run: `C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_audit_issue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import audit_issue as ai  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


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

if _fails:
    print("FAIL test_audit_issue:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_audit_issue: all checks pass")
