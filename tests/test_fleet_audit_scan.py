"""Unit tests for the pure logic in skills/_lib/fleet_audit_scan.py.

No live git/gh — this exercises only `is_fleet_repo`, the one piece of
correctness-critical logic that doesn't need filesystem/network I/O.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_audit_scan.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import fleet_audit_scan as fas  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


check(fas.is_fleet_repo("https://github.com/ferraroroberto/fleet-config.git") is True,
      "is_fleet_repo: https remote -> True")
check(fas.is_fleet_repo("git@github.com:ferraroroberto/fleet-config.git") is True,
      "is_fleet_repo: ssh remote -> True")
check(fas.is_fleet_repo("https://github.com/someoneelse/fleet-config.git") is False,
      "is_fleet_repo: a fork under a different owner -> False")
check(fas.is_fleet_repo("https://github.com/other-org/unrelated.git") is False,
      "is_fleet_repo: unrelated remote -> False")
check(fas.is_fleet_repo(None) is False, "is_fleet_repo: no remote -> False")
check(fas.is_fleet_repo("") is False, "is_fleet_repo: empty remote -> False")


# ---- accounting: no repo may drop out of every bucket (fleet-config#567) ----

_balanced = {
    "to_audit": [{"repo": "a"}], "unchanged": ["b", "c"], "self_fix": [],
    "below_threshold": [], "skipped": [{"repo": "d"}], "errors": [],
    "enumerated": 4,
}
check(fas.accounting(_balanced) == {
    "enumerated": 4, "bucketed": 4, "unaccounted": 0, "balanced": True,
}, "accounting: every enumerated repo bucketed -> balanced")

_dropped = dict(_balanced, enumerated=6)
_acct = fas.accounting(_dropped)
check(_acct["balanced"] is False, "accounting: a repo in no bucket -> not balanced")
check(_acct["unaccounted"] == 2, "accounting: reports how many repos vanished")

check(fas.accounting({"enumerated": 0}) == {
    "enumerated": 0, "bucketed": 0, "unaccounted": 0, "balanced": True,
}, "accounting: an empty walk is balanced, not an error")
check(set(fas.BUCKETS) == {"to_audit", "unchanged", "self_fix", "below_threshold", "skipped", "errors"},
      "accounting: BUCKETS covers exactly the six sweep buckets")


# ---- broken ledgers stay distinguishable from organic change (#566/#567) ----

_scan = {"to_audit": [
    {"repo": "plain"},
    {"repo": "stale-baseline", "reason": "unresolvable-baseline", "baseline_sha": "99100ac"},
    {"repo": "drifted", "reason": "unparseable-ledger", "ledger_issue": 6},
]}
check([e["repo"] for e in fas.broken_ledgers(_scan)] == ["stale-baseline", "drifted"],
      "broken_ledgers: returns exactly the audits forced by an unreadable ledger")
check(fas.broken_ledgers({"to_audit": [{"repo": "plain"}]}) == [],
      "broken_ledgers: organic-change audits are not reported as broken")
check(fas.broken_ledgers({}) == [], "broken_ledgers: an empty scan is empty, not an error")

_h.report_and_exit("test_fleet_audit_scan")
