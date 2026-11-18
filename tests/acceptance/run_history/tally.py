"""The gate's running Total / Failed / Skipped, shared by the per-change gate and the weekly burst.

Both dispatchers (`tests/run_acceptance.py`, `tests/run_weekly_units.py`) fold each unit's own
`(failures, total[, skipped])` into one tally and report every unit to the run history, so a moved unit
keeps its ledger row under the weekly label.
"""
from __future__ import annotations

from typing import Callable, Tuple

from acceptance import gate_routing
from acceptance.run_history import unit_report
from acceptance.shared import _subprocess_unit_check


class Tally:
    """The gate's running Total / Failed / Skipped, summed from the checks that ran."""

    def __init__(self) -> None:
        self.failures = 0
        self.total = 0
        self.skipped = 0

    def add(self, failures: int, total: int, skipped: int = 0) -> None:
        self.failures += failures
        self.total += total
        self.skipped += skipped

    def run_unit(self, check_fn: Callable[[], Tuple[int, int]]) -> None:
        """Call one check function and fold its own `(failures, total)` into
        the running tally — the acceptance-matrix total is summed from real
        checks executed, never a hand-maintained constant that drifts
        silently when a check is added or removed (fleet-config#320)."""
        with unit_report.unit(getattr(check_fn, "__name__", repr(check_fn))) as outcome:
            failures, total = check_fn()
            outcome["result"] = (failures, total, 0)
        self.add(failures, total)

    def run_unit3(self, check_fn: Callable[[], Tuple[int, int, int]]) -> None:
        """`run_unit` for the checks that also report a third state — `skipped`.

        A check that *couldn't establish* a fact (no live settings.json to
        compare against) or that *may not gate this repo* (fleet-wide freshness,
        whose inputs are sibling checkouts) must land in neither Total nor
        Failed, so a run that verified less never reads identical to one that
        verified everything (fleet-config#461, #501, #562)."""
        with unit_report.unit(getattr(check_fn, "__name__", repr(check_fn))) as outcome:
            failures, total, skipped = check_fn()
            outcome["result"] = (failures, total, skipped)
        self.add(failures, total, skipped)

    def run_suite(self, label: str, test_file: str) -> None:
        """One standalone suite file as a unit, reported under its table label."""
        with unit_report.unit(label) as outcome:
            outcome["result"] = _subprocess_unit_check(
                label, test_file, routed_out=gate_routing.routed_out(label))
        self.add(*outcome["result"])
