"""Unit tests for the pure logic in skills/_lib/audit_issue.py.

No live `gh` — these exercise marker handling, title adoption, and the
keep/close decision that guarantees one audit issue per (repo, kind).

Run: `py tests/test_audit_issue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
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

# ---- title adoption (pre-marker issues) ----

check(ai.title_matches("codebase-audit ledger", "ledger"), "title ledger")
check(ai.title_matches("audit-fleet digest state", "digest"), "title digest")
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

if _fails:
    print("FAIL test_audit_issue:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_audit_issue: all checks pass")
