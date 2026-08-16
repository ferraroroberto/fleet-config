"""Unit tests for the pure logic in skills/_lib/fleet_audit_scan.py.

Mostly `is_fleet_repo`, the accounting block, and the sentinel plumbing — the
pieces that don't need filesystem/network I/O. The one exception is the
`stale_lock` bucket (fleet-config#667), which is driven end-to-end through the
real `scan()` against a real repo carrying a real planted lock: the whole point
of that bucket is that every signal a mock would provide reads healthy, so a
mocked test of it would prove nothing. `--only` keeps that walk to a single
local fixture, so it stays offline and fast.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_audit_scan.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
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
    "below_threshold": [], "skipped": [{"repo": "d"}],
    "stale_lock": [{"repo": "e"}], "errors": [],
    "enumerated": 5,
}
check(fas.accounting(_balanced) == {
    "enumerated": 5, "bucketed": 5, "unaccounted": 0, "balanced": True,
}, "accounting: every enumerated repo bucketed -> balanced")

_dropped = dict(_balanced, enumerated=7)
_acct = fas.accounting(_dropped)
check(_acct["balanced"] is False, "accounting: a repo in no bucket -> not balanced")
check(_acct["unaccounted"] == 2, "accounting: reports how many repos vanished")

check(fas.accounting({"enumerated": 0}) == {
    "enumerated": 0, "bucketed": 0, "unaccounted": 0, "balanced": True,
}, "accounting: an empty walk is balanced, not an error")
check(set(fas.BUCKETS) == {"to_audit", "unchanged", "self_fix", "below_threshold",
                           "skipped", "stale_lock", "errors"},
      "accounting: BUCKETS covers exactly the seven sweep buckets")
# A bucket the accounting doesn't count is a bucket repos vanish through --
# exactly the fleet-config#567 shape the block above exists to prevent, which
# a *new* bucket is the likeliest way to reintroduce (fleet-config#667).
check(fas.accounting({"stale_lock": [{"repo": "locked"}], "enumerated": 1})["balanced"] is True,
      "accounting: a repo held only in stale_lock is accounted for, not lost")


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


# ---- a stranded index.lock gets its own bucket (fleet-config#667) ----------
#
# End-to-end through the real `scan()`, against a real repo carrying a real
# planted 0-byte lock, because the failure being prevented is precisely that
# every *mocked* signal looks fine: `status` exits 0 and reads clean, so the
# only way to catch this is to check for the lock before believing the reads.
# `--only` keeps the walk to the one fixture, so nothing here touches network.

with tempfile.TemporaryDirectory() as _td:
    _root = Path(_td)
    _repo = _root / "locked-fixture"
    _repo.mkdir()

    def _g(*args: str) -> None:
        subprocess.run(["git", "-C", str(_repo), *args], capture_output=True, check=True)

    _g("init", "-q")
    _g("config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _g("config", "user.name", "Test")
    _g("remote", "add", "origin", "https://github.com/ferraroroberto/locked-fixture.git")
    (_repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _g("add", "a.txt")
    _g("commit", "-q", "-m", "initial")

    _lock = _repo / ".git" / "index.lock"
    _lock.write_bytes(b"")
    _old = time.time() - 15 * 24 * 3600          # the 2026-08-01 shape
    os.utime(_lock, (_old, _old))

    _res = fas.scan(str(_root), only="locked-fixture", dry_run=True)

    # `.get` deliberately, not `[...]`: against pre-fix code this must fail as
    # "the locked repo was not reported", the actual defect, rather than as a
    # KeyError about a missing dict key.
    _locked = _res.get("stale_lock", [])
    check([e["repo"] for e in _locked] == ["locked-fixture"],
          f"scan: a stranded index.lock lands in the stale_lock bucket, got {_locked!r} "
          f"(whole result: {_res!r})")
    if not _locked:
        _h.report_and_exit("test_fleet_audit_scan")
    _entry = _locked[0]
    check(_entry["verdict"] in ("stale", "stale_unconfirmed"),
          f"scan: the entry names which stale verdict it is, got {_entry['verdict']!r}")
    check(_entry["size"] == 0 and _entry["age_seconds"] > 14 * 24 * 3600,
          f"scan: the entry carries the size and age a human acts on, got {_entry!r}")
    check(_entry.get("reason"), "scan: the entry carries a human-readable reason")

    # Never folded into a settled state, and never silently repaired.
    check(_res["skipped"] == [] and _res["errors"] == [] and _res["unchanged"] == [],
          f"scan: a locked repo is NOT reported as clean/dirty/skipped/errored, got {_res!r}")
    check(_res["accounting"]["balanced"] is True,
          "scan: the new bucket is accounted for — the repo is not lost from the walk")
    check(_lock.exists(), "scan: REPORTS ONLY — the lock is never auto-deleted")

    # ...and a fresh lock is a different, non-alarming state: an in-flight git
    # operation is an established fact, not an unknown.
    os.utime(_lock, None)
    _res2 = fas.scan(str(_root), only="locked-fixture", dry_run=True)
    check(_res2.get("stale_lock") == [] and [e["repo"] for e in _res2["skipped"]] == ["locked-fixture"],
          f"scan: a fresh lock is a skip, not a stale-lock report, got {_res2!r}")
    check("index-lock" in _res2["skipped"][0]["reason"],
          f"scan: ...and the skip names the lock rather than looking like dirt, got {_res2['skipped']!r}")


_h.report_and_exit("test_fleet_audit_scan")
