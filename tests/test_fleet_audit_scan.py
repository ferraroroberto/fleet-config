"""Unit tests for the pure logic in skills/_lib/fleet_audit_scan.py.

No live git/gh — this exercises only `is_fleet_repo`, the one piece of
correctness-critical logic that doesn't need filesystem/network I/O.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_audit_scan.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

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

# ---- sentinel publish + detached launch (fleet-config#609) -----------------

with tempfile.TemporaryDirectory() as _td:
    _tdp = Path(_td)

    # write_result: atomic publish, no leftover temp files.
    _out = _tdp / "result.json"
    fas.write_result(_out, {"hello": "world"})
    check(_out.exists(), "write_result: sentinel file exists after publish")
    check(json.loads(_out.read_text(encoding="utf-8")) == {"hello": "world"},
          "write_result: sentinel content round-trips as JSON")
    check(list(_tdp.glob("*.tmp-*")) == [], "write_result: no temp file left behind")

    # write_result: overwrites a stale sentinel from a prior run at the same path.
    fas.write_result(_out, {"hello": "again"})
    check(json.loads(_out.read_text(encoding="utf-8")) == {"hello": "again"},
          "write_result: atomic overwrite replaces a stale sentinel")

    # write_result: creates missing parent directories.
    _nested = _tdp / "nested" / "dir" / "result.json"
    fas.write_result(_nested, {"ok": True})
    check(_nested.exists(), "write_result: creates missing parent directories")

check(fas.default_out_path() != fas.default_out_path(),
      "default_out_path: two calls never collide on the same path")
check(str(fas.default_out_path()).endswith(".json"),
      "default_out_path: sentinel path is a .json file")

# run_and_publish: success path writes the sentinel and returns the scan result.
with tempfile.TemporaryDirectory() as _td:
    _out = Path(_td) / "result.json"
    _fake_result = {"to_audit": [], "enumerated": 0}
    with mock.patch.object(fas, "scan", return_value=_fake_result) as _mock_scan:
        _returned = fas.run_and_publish("E:/nowhere", None, True, _out)
    check(_mock_scan.called, "run_and_publish: calls scan() with the given args")
    check(_returned == _fake_result, "run_and_publish: returns scan()'s result")
    check(json.loads(_out.read_text(encoding="utf-8")) == _fake_result,
          "run_and_publish: publishes scan()'s result to the sentinel")

# run_and_publish: a scan() crash still publishes a sentinel (an error payload)
# instead of leaving a poller to wait forever on a file that will never appear.
with tempfile.TemporaryDirectory() as _td:
    _out = Path(_td) / "result.json"
    with mock.patch.object(fas, "scan", side_effect=RuntimeError("boom")):
        try:
            fas.run_and_publish("E:/nowhere", None, True, _out)
            check(False, "run_and_publish: re-raises the original exception")
        except RuntimeError:
            pass
    check(_out.exists(), "run_and_publish: a crash still publishes a sentinel")
    _payload = json.loads(_out.read_text(encoding="utf-8"))
    check("error" in _payload and "boom" in _payload["error"],
          "run_and_publish: the crash sentinel carries the error message")


_h.report_and_exit("test_fleet_audit_scan")
