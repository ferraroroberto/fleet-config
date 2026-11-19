"""Test-value ratchet stage.

Runs `scripts/test_value/test_value_ratchet.py` with the per-change rules (`new-test-value`,
`spawn-budget`, `flake-ledger`) against this run's live unit and spawn reports, the unit ledger and the
weekly coverage map. The reports exist only when the gate runs through `gate_evidence.py run`, which
sets their env vars; a plain `run_acceptance.py` gives the script nothing to read, and it exits 3.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason `static_ratchet_gate`
gives: exit 3 here fails as NOT CONFIRMED and never reads as a skip. It runs after every other unit but
the runtime-write probe, so the live reports hold the whole run.
"""
from __future__ import annotations

import os
from typing import Tuple

from unit_history import ledger

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "test_value" / "test_value_ratchet.py"

# A few seconds over the ledger, plus the one coverage run of new units that new-test-value may make,
# itself bounded by `coverage_timeout_s` in .fleet.toml. This only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 2000

_NOT_CONFIRMED = ("NOT CONFIRMED -- a fact could not be established (no live unit report, no ledger, "
                  "a missing or stale coverage map, no merge base), so the rules were not all checked")


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own failing state."""
    labels = {
        0: "new units show their value, spawns within budget, no flake past the ledger",
        1: "a new unit with no evidence of value, a unit's spawns above budget, or a flake past the ledger",
        2: "bad [test_value_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def ratchet_args() -> Tuple[str, ...]:
    """The machine-local inputs: the ledger, the live reports, this run's tree and the coverage map."""
    args = ["--ledger", str(ledger.ledger_path()), "--coverage-map", str(ledger.coverage_map_path())]
    for flag, env in (("--units-report", ledger.UNIT_REPORT_ENV), ("--spawns-report", ledger.SPAWN_REPORT_ENV),
                      ("--tree", ledger.TREE_ENV)):
        if os.environ.get(env):
            args += [flag, os.environ[env]]
    return tuple(args)


def _test_value_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit, ratchet_args())
