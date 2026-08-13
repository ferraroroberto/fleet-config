"""Unit + wiring tests for skills/_lib/wait_for_sentinel.py.

The load-bearing property this proves: `/audit-fleet` step 3 can only be
reached after this helper returns exit 0, and exit 0 is only ever returned
when the sentinel file genuinely exists. Absence of the sentinel can never be
mistaken for readiness -- the exact gap a model-composed `Monitor` call left
open (fleet-config#609, reopened): it returned without blocking, and the
model treated that as license to end its turn and wait for a notification
that under `claude -p` never arrives.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_wait_for_sentinel.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import wait_for_sentinel as wfs  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- wait_for_sentinel(): pure logic, injected clock/sleep -- no real wait --

with tempfile.TemporaryDirectory() as _td:
    _present = Path(_td) / "already-there.json"
    _present.write_text("{}", encoding="utf-8")
    _sleeps: list[float] = []
    _found = wfs.wait_for_sentinel(
        _present, timeout_seconds=100.0, poll_interval_seconds=5.0,
        sleep=_sleeps.append, clock=lambda: 0.0,
    )
    check(_found is True, "wait_for_sentinel: an already-present file is found immediately")
    check(_sleeps == [], "wait_for_sentinel: never sleeps when the file is already there")

with tempfile.TemporaryDirectory() as _td:
    _absent = Path(_td) / "never-appears.json"
    _clock = iter([0.0, 1.0, 2.0, 3.0, 10.0])  # jumps past the 3s deadline on the 4th check
    _sleeps = []
    _found = wfs.wait_for_sentinel(
        _absent, timeout_seconds=3.0, poll_interval_seconds=5.0,
        sleep=_sleeps.append, clock=lambda: next(_clock),
    )
    check(_found is False,
          "wait_for_sentinel: a file that never appears reports not-found, not an exception")
    check(len(_sleeps) > 0, "wait_for_sentinel: it actually blocks (sleeps) while waiting")

# The poll interval is capped by the remaining budget -- never overshoots the deadline.
with tempfile.TemporaryDirectory() as _td:
    _absent = Path(_td) / "never-appears.json"
    _clock = iter([0.0, 0.5, 1.0])
    _sleeps = []
    wfs.wait_for_sentinel(
        _absent, timeout_seconds=1.0, poll_interval_seconds=5.0,
        sleep=_sleeps.append, clock=lambda: next(_clock),
    )
    check(all(s <= 1.0 for s in _sleeps),
          "wait_for_sentinel: never sleeps longer than the remaining timeout budget")

# A file that appears mid-wait is caught on the very next check, not missed.
with tempfile.TemporaryDirectory() as _td:
    _late = Path(_td) / "appears-later.json"
    _ticks = iter([0.0, 1.0, 2.0])
    _calls = [0]

    def _sleep_then_create(_seconds: float) -> None:
        _calls[0] += 1
        if _calls[0] == 1:
            _late.write_text("{}", encoding="utf-8")

    _found = wfs.wait_for_sentinel(
        _late, timeout_seconds=100.0, poll_interval_seconds=5.0,
        sleep=_sleep_then_create, clock=lambda: next(_ticks),
    )
    check(_found is True, "wait_for_sentinel: a file created mid-wait is picked up, not missed")


# ---- CLI (main): the real subprocess contract SKILL.md relies on -----------

PYTHON = sys.executable
SCRIPT = str(ROOT / "skills" / "_lib" / "wait_for_sentinel.py")

with tempfile.TemporaryDirectory() as _td:
    _present = Path(_td) / "ready.json"
    _present.write_text('{"ok": true}', encoding="utf-8")
    _proc = subprocess.run(
        [PYTHON, SCRIPT, "--path", str(_present), "--timeout-seconds", "5"],
        capture_output=True, text=True, timeout=30,
    )
    check(_proc.returncode == 0, "CLI: exit 0 when the sentinel already exists")
    check("SENTINEL-READY" in _proc.stdout, "CLI: prints the SENTINEL-READY contract line")
    check(str(_present) in _proc.stdout, "CLI: the ready line names the sentinel path")

with tempfile.TemporaryDirectory() as _td:
    _absent = Path(_td) / "missing.json"
    _started = time.monotonic()
    _proc = subprocess.run(
        [PYTHON, SCRIPT, "--path", str(_absent),
         "--timeout-seconds", "0.3", "--poll-interval-seconds", "0.1"],
        capture_output=True, text=True, timeout=30,
    )
    _elapsed = time.monotonic() - _started
    check(_proc.returncode == wfs.NOT_READY_EXIT_CODE,
          "CLI: exit 2 (never 0) when the sentinel never appears -- this is the property "
          "step 3 reachability depends on: a missing sentinel can never read as success")
    check("SENTINEL-NOT-READY" in _proc.stdout, "CLI: prints the SENTINEL-NOT-READY contract line")
    check(_elapsed < 5, f"CLI: returns promptly after its own timeout, not stuck (took {_elapsed:.1f}s)")

check(wfs.NOT_READY_EXIT_CODE == 2,
      "NOT_READY_EXIT_CODE is 2 -- must match the exit code SKILL.md's step 2 branches on")


# ---- SKILL.md wiring: the exact contract the orchestrator is told to follow -

_skill = (ROOT / ".claude" / "skills" / "audit-fleet" / "SKILL.md").read_text(encoding="utf-8")
check("wait_for_sentinel.py" in _skill,
      "SKILL.md: step 2 calls the deterministic helper, not a model-composed wait")
check("SENTINEL-READY" in _skill and "SENTINEL-NOT-READY" in _skill,
      "SKILL.md: both exit-code contract lines are named so the branch is unambiguous")
check("Never end this turn on an exit-2 result" in _skill,
      "SKILL.md: explicitly forbids treating a not-ready result as a reason to stop")
check("Cap retries at 26" in _skill,
      "SKILL.md: the retry cap is stated as a number, not left to model judgment")


_h.report_and_exit("test_wait_for_sentinel")
