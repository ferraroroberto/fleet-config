"""Characterization + contract tests for the digest delivery post-condition.

Written **before** `/audit-fleet`'s `delivery_check.py` was refactored onto the
shared `skills/_lib/digest_delivery.py` helper (fleet-config#627), and proven
passing against the pre-refactor code first. A test written after a refactor
only proves the new code agrees with itself; this one pins the behaviour that
existed, so the refactor has something to be measured against. The observed
pre-refactor codes -- confirmed 0, stale 1, unestablished 1, undateable 1,
gh-error 1, no-ledger 1, partial-stamp 0 -- are asserted verbatim below.

What it pins, for **every** caller rather than once generically:

  1. The CLI surface each launcher actually invokes. `claude_progress.py` runs
     the post-condition as `[sys.executable, script]` with **no arguments**, so
     any caller whose strictness depended on a flag would be silently un-strict
     in the one place it matters. Each check is therefore driven bare.
  2. Every exit code, by outcome -- and specifically that a delivery which
     cannot be *established* (unreadable ledger, absent issue, undateable
     comment, missing stamp) exits non-zero. That predicate is the whole reason
     the shared helper exists: `#560` recorded `success` / exit 0 while
     delivering nothing, and the global rule says an unestablished fact is its
     own state, never folded into the passing one.
  3. That `claude_progress.py`'s `DELIVERY_NOT_CONFIRMED_EXIT_CODE` sentinel is
     *exercised* end to end rather than assumed to match. A sentinel that
     silently stops matching converts a loud failure into a green run.

`gh` is never invoked: `audit_issue`'s two read paths are patched in-process by
`tests/_lib/delivery_fixtures.py`, which is also the seam that makes the same
driver work identically before and after the refactor. Nothing in the shipped
code carries a test backdoor.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_delivery_check_contract.py
Exit 0 = all pass.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from delivery_fixtures import (  # noqa: E402
    FakeGitHub, characterize, comment, ledger_issue, load_module,
)

sys.path.insert(0, str(REPO / "skills" / "_lib"))
import audit_issue  # noqa: E402
import digest_delivery as dd  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_h = CheckHarness()
check = _h.check

NOW = dt.datetime(2026, 8, 16, 12, 0, tzinfo=dt.timezone.utc)

COMPLETE = "<!-- context-purge-digest run=20260815T010000 status=complete unreached=0 slack=posted -->"
PARTIAL = "<!-- context-purge-digest run=20260815T010000 status=partial unreached=3 slack=posted -->"
SLACK_FAILED = "<!-- context-purge-digest run=20260815T010000 status=complete unreached=0 slack=failed -->"
NO_SLACK_FIELD = "<!-- context-purge-digest run=r status=complete unreached=0 -->"


# ---------------------------------------------------------------------------
# 1. The age primitive both callers share
# ---------------------------------------------------------------------------

def _c(hours: float, body: str = "digest") -> dict:
    return comment(hours, body, now=NOW)


check(dd.newest_dated_comment([], NOW)[0] is None,
      "age: no comments at all is None -- 'nothing here to date', not age 0")
check(dd.newest_dated_comment([{"body": "x"}], NOW)[0] is None,
      "age: a comment with no createdAt is undateable, not fresh")
check(dd.newest_dated_comment([{"createdAt": "not-a-date"}], NOW)[0] is None,
      "age: an unparseable date is undateable, not fresh")
check(abs(dd.newest_dated_comment([_c(3), _c(50)], NOW)[0] - 3.0) < 0.01,
      "age: the newest comment wins when several are dated")

_age, _cm = dd.newest_dated_comment([_c(3, "newer"), _c(50, "older")], NOW)
check(_cm is not None and _cm["body"] == "newer",
      "age: newest_dated_comment returns the comment the age belongs to")
check(dd.newest_dated_comment([], NOW) == (None, None),
      "age: no dated comment yields (None, None), not a synthetic blank")


# ---------------------------------------------------------------------------
# 2. classify -- three outcomes, each its own state
# ---------------------------------------------------------------------------

fresh = dd.classify([_c(2)], NOW, 12.0)
check(fresh.confirmed is True and fresh.state == "confirmed",
      "classify: a comment inside the window confirms delivery")
check(fresh.exit_code == 0, "classify: a confirmed verdict exits 0")

stale = dd.classify([_c(30)], NOW, 12.0)
check(stale.confirmed is False and stale.state == "not-delivered",
      "classify: a comment older than the window is 'not delivered'")
check("30.0h" in stale.detail, "classify: the not-delivered reason quotes the age it saw")
check(stale.exit_code == 1, "classify: an unconfirmed verdict exits non-zero")

none = dd.classify([], NOW, 12.0)
check(none.confirmed is False and none.state == "unestablished",
      "classify: no dated comment is 'unestablished' -- distinct from 'not delivered'")
check(stale.state != none.state,
      "classify: 'delivered nothing' and 'could not be established' are different facts")
check({fresh.confirmed, stale.confirmed, none.confirmed} == {True, False},
      "classify: only the confirmed case is truthy -- both failure states are falsy")


# ---------------------------------------------------------------------------
# 3. Stamp parsing and strict mode (#627)
# ---------------------------------------------------------------------------

check(dd.parse_stamp(COMPLETE) == {"run": "20260815T010000", "status": "complete",
                                   "unreached": "0", "slack": "posted"},
      "stamp: a well-formed stamp parses into its fields")
check(dd.parse_stamp("no stamp here") == {},
      "stamp: an absent stamp is {} -- the caller decides what that means")
check(dd.parse_stamp(COMPLETE, prefix="other-prefix") == {},
      "stamp: a different prefix does not match -- callers cannot read each other's stamps")

ok = dd.classify([_c(2, COMPLETE)], NOW, 12.0, require_complete=True)
check(ok.confirmed is True, "strict: complete + slack=posted inside the window confirms")

part = dd.classify([_c(2, PARTIAL)], NOW, 12.0, require_complete=True)
check(part.confirmed is False and part.state == "partial",
      "strict: a digest marked partial is delivered but the run must still fail (criterion 4)")
check("3 repo(s) unreached" in part.detail,
      "strict: the partial reason names how many repos went unreached")

sf = dd.classify([_c(2, SLACK_FAILED)], NOW, 12.0, require_complete=True)
check(sf.confirmed is False and sf.state == "slack-unconfirmed",
      "strict: slack=failed is its own failure -- notify_send never raises, so a "
      "half-failure must not read as success")

missing = dd.classify([_c(2, "digest with no stamp")], NOW, 12.0, require_complete=True)
check(missing.confirmed is False and missing.state == "unestablished",
      "strict: a comment with no stamp leaves status unknown -- unknown is not success")

unknown_slack = dd.classify([_c(2, NO_SLACK_FIELD)], NOW, 12.0, require_complete=True)
check(unknown_slack.confirmed is False and unknown_slack.state == "slack-unconfirmed",
      "strict: a missing slack= field is 'unknown' and is not confirmed (non-negotiable)")
check("slack=unknown" in unknown_slack.detail,
      "strict: the reason says the delivery state was unknown, not that it failed")

lenient = dd.classify([_c(2, PARTIAL)], NOW, 12.0, require_complete=False)
check(lenient.confirmed is True,
      "lenient: without require_complete the stamp is not consulted -- audit-fleet's "
      "behaviour is unchanged by context-purge's extra strictness")

# A stale comment must fail before strictness is even consulted, so a stale
# *complete* digest cannot pass on the strength of its stamp.
stale_complete = dd.classify([_c(30, COMPLETE)], NOW, 12.0, require_complete=True)
check(stale_complete.confirmed is False and stale_complete.state == "not-delivered",
      "strict: freshness is checked before the stamp -- last week's complete run "
      "does not confirm this week's delivery")


# ---------------------------------------------------------------------------
# 4. Per-caller CLI contract -- bare invocation, real exit codes
# ---------------------------------------------------------------------------

AUDIT_SCRIPT = REPO / ".claude" / "skills" / "audit-fleet" / "delivery_check.py"
PURGE_SCRIPT = REPO / ".claude" / "skills" / "context-purge" / "delivery_check.py"

audit_mod = load_module(AUDIT_SCRIPT, "audit_fleet_delivery_check")
purge_mod = load_module(PURGE_SCRIPT, "context_purge_delivery_check")

AUDIT_ISSUES = [ledger_issue(500, "audit-fleet digest state", "digest")]
PURGE_ISSUES = [ledger_issue(700,
                             "[ledger] context-purge run digests (machine-managed, not actionable)",
                             "context-purge-digest")]


# The scripts resolve "now" from the real clock, so these fixtures must too --
# anchoring them to the fixed NOW above would make the suite pass or fail on
# what time of day it ran, which is the sort of flake that gets a real failure
# dismissed as noise.
def _live(hours: float, body: str = "digest") -> dict:
    return comment(hours, body)


def _scenarios(stamp_complete: str) -> dict:
    return {
        "confirmed": lambda f: setattr(f, "comments", [_live(2, stamp_complete)]),
        "stale": lambda f: setattr(f, "comments", [_live(30, stamp_complete)]),
        "unestablished": lambda f: setattr(f, "comments", []),
        "undateable": lambda f: setattr(f, "comments", [{"body": "no createdAt"}]),
        "gh-error": lambda f: setattr(f, "raise_on_view", SystemExit("gh issue view failed (exit 1)")),
        "no-ledger": lambda f: (setattr(f, "issues", []), setattr(f, "comments", [])),
        "partial-stamp": lambda f: setattr(f, "comments", [_live(2, PARTIAL)]),
        "slack-failed": lambda f: setattr(f, "comments", [_live(2, SLACK_FAILED)]),
        "no-stamp": lambda f: setattr(f, "comments", [_live(2, "plain digest")]),
    }


# --- audit-fleet: the pre-refactor baseline, asserted verbatim --------------
audit_codes = characterize(audit_mod, audit_issue, _scenarios("digest"), issues=AUDIT_ISSUES)

AUDIT_BASELINE = {
    "confirmed": 0, "stale": 1, "unestablished": 1, "undateable": 1,
    "gh-error": 1, "no-ledger": 1, "partial-stamp": 0,
}
for label, expected in AUDIT_BASELINE.items():
    check(audit_codes[label] == expected,
          f"audit-fleet characterization: {label} exits {expected} "
          f"(pre-refactor baseline; saw {audit_codes[label]})")

check(audit_codes["unestablished"] != 0 and audit_codes["gh-error"] != 0
      and audit_codes["no-ledger"] != 0 and audit_codes["undateable"] != 0,
      "audit-fleet: every way of FAILING TO ESTABLISH delivery exits non-zero")
check(audit_codes["partial-stamp"] == 0,
      "audit-fleet: a stamp it never writes does not make it fail -- the refactor "
      "changed no behaviour of the caller that was already scheduled")
check(audit_mod.REPO == "ferraroroberto/fleet-config"
      and audit_mod.DEFAULT_MAX_AGE_HOURS == 12.0,
      "audit-fleet: the CLI surface (repo, default window) is unchanged")

# --- context-purge: strict, and strict with no flag to forget --------------
purge_codes = characterize(purge_mod, audit_issue, _scenarios(COMPLETE), issues=PURGE_ISSUES)

check(purge_codes["confirmed"] == 0,
      f"context-purge: a fresh complete+posted digest exits 0 (saw {purge_codes['confirmed']})")
for label in ("stale", "unestablished", "undateable", "gh-error", "no-ledger"):
    check(purge_codes[label] != 0,
          f"context-purge: {label} exits non-zero (saw {purge_codes[label]})")
check(purge_codes["unestablished"] != 0 and purge_codes["gh-error"] != 0,
      "context-purge: every way of FAILING TO ESTABLISH delivery exits non-zero")
check(purge_codes["partial-stamp"] != 0,
      f"context-purge: a partial run exits non-zero when invoked BARE, with no flag "
      f"to forget (saw {purge_codes['partial-stamp']})")
check(purge_codes["slack-failed"] != 0,
      f"context-purge: slack=failed exits non-zero when invoked bare "
      f"(saw {purge_codes['slack-failed']})")
check(purge_codes["no-stamp"] != 0,
      f"context-purge: a digest with no stamp is unestablished, not a pass "
      f"(saw {purge_codes['no-stamp']})")

# The two callers must disagree exactly where they are meant to, and nowhere else.
check(audit_codes["partial-stamp"] == 0 and purge_codes["partial-stamp"] != 0,
      "the two callers differ on the stamp -- and only there -- which is the whole "
      "reason strictness is a constructor argument rather than shared default")


# ---------------------------------------------------------------------------
# 5. The sentinel is exercised, not assumed
# ---------------------------------------------------------------------------

cp = load_module(REPO / "skills" / "_lib" / "claude_progress.py", "claude_progress_sentinel")

check(cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE == 121,
      "sentinel: DELIVERY_NOT_CONFIRMED_EXIT_CODE is 121")
check(len({cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE, cp.STALL_EXIT_CODE,
           cp.BACKGROUND_KILL_EXIT_CODE, cp.SELF_REPORTED_FAILURE_EXIT_CODE}) == 4,
      "sentinel: it stays distinct from stall / background-kill / self-reported")


class _Fmt:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, line: str) -> None:
        self.lines.append(line)


_tmp = Path(tempfile.mkdtemp(prefix="delivery_sentinel_"))
try:
    failing = _tmp / "failing_check.py"
    failing.write_text("import sys\nprint('no digest comment')\nsys.exit(1)\n", encoding="utf-8")
    passing = _tmp / "passing_check.py"
    passing.write_text("print('digest comment is 2.0h old')\n", encoding="utf-8")

    fmt = _Fmt()
    check(cp.run_delivery_check(str(failing), fmt) is False,
          "sentinel: a failing post-condition reports NOT confirmed")
    check(any("NOT confirmed" in line for line in fmt.lines),
          "sentinel: the unconfirmed verdict is stated in the run log")

    fmt = _Fmt()
    check(cp.run_delivery_check(str(passing), fmt) is True,
          "sentinel: a passing post-condition confirms delivery")

    # The exact mapping the launcher depends on, driven through the real code
    # path rather than restated: a clean child + an unconfirmed delivery must
    # surface as 121, which is the #560 false success made loud.
    def _map(child_code: int, script: Path) -> int:
        confirmed = cp.run_delivery_check(str(script), _Fmt())
        if not confirmed:
            return child_code if child_code != 0 else cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE
        return child_code

    check(_map(0, failing) == cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE,
          "sentinel: a child that exits 0 with delivery unconfirmed maps to 121, not 0")
    check(_map(0, passing) == 0,
          "sentinel: a clean child with a confirmed delivery stays 0")
    check(_map(124, failing) == 124,
          "sentinel: a child that already failed keeps its own code -- 121 outranks "
          "a clean exit only")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)


# Both launchers must actually wire the post-condition, or the mechanism is theory.
for label, script in (("audit-fleet", AUDIT_SCRIPT), ("context-purge", PURGE_SCRIPT)):
    bat = script.parent / "run-weekly.bat"
    text = bat.read_text(encoding="utf-8")
    check(cp.DELIVERY_CHECK_FLAG in text, f"{label}: run-weekly.bat passes {cp.DELIVERY_CHECK_FLAG}")
    check(script.name in text and script.exists(),
          f"{label}: run-weekly.bat names a delivery check script that exists")


_h.report_and_exit("test_delivery_check_contract")
