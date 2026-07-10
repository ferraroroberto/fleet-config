"""Unit tests for the pure logic in skills/_lib/fleet_audit_scan.py.

No live git/gh — this exercises only `is_fleet_repo`, the one piece of
correctness-critical logic that doesn't need filesystem/network I/O.

Run: `C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_fleet_audit_scan.py`
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

_h.report_and_exit("test_fleet_audit_scan")
