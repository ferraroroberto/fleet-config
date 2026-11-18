"""Run the burn, load and wall-clock units the per-change gate leaves out.

Run from the repo root:
    E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_weekly_units.py

Exit 0 if every unit passes, 1 otherwise; one line per unit, then the same
`Total: N | Failed: N | Skipped: N` line as `tests/run_acceptance.py`. Like the gate it holds the
box-wide load lock in shared mode, so the CPU-burn unit's own exclusive acquire still works when
nothing else is gating, and it reports each unit to the run history. The unit list is
`tests/acceptance/weekly/units.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))

import box_lock  # noqa: E402
from acceptance.run_history.tally import Tally  # noqa: E402
from acceptance.shared import seed_fleet_list_env  # noqa: E402
from acceptance.weekly.units import WEEKLY_FUNCTION_UNITS, WEEKLY_SUITES  # noqa: E402


def main() -> int:
    try:
        with box_lock.held(box_lock.SHARED, "fleet-config-weekly",
                           report=lambda line: print(line, flush=True)):
            return _run_all()
    except box_lock.BoxLockTimeout as exc:
        print(f"UNKNOWN  box_load_lock: {exc}; the weekly units did not start, this is not a failed check")
        return box_lock.TIMEOUT_EXIT


def _run_all() -> int:
    seed_fleet_list_env()
    tally = Tally()
    for check_fn in WEEKLY_FUNCTION_UNITS:
        tally.run_unit(check_fn)
    for label, test_file, _why in WEEKLY_SUITES:
        tally.run_suite(label, test_file)
    print()
    print(f"Total: {tally.total} | Failed: {tally.failures} | Skipped: {tally.skipped}")
    return 0 if tally.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
