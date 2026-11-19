"""The weekly burst: the burn/load/wall-clock units, the hermetic re-run and the runtime medians.

    python .claude/skills/weekly-burst/burst.py [--median-runs N] [--skip-hermetic]

The engine is project-scaffolding's vendored `weekly_burst.py` (`skills/_lib/unit_history/weekly_burst.py`,
`[vendored]` in `.fleet.toml`); this file says what it runs here and where it writes. Each leg runs
through `gate_evidence run --units`, so it is recorded in the unit ledger under its own label and the
per-unit medians can tell it apart from the per-change gate (`gate`):

  units     `tests/run_weekly_units.py` in this working tree  (label `weekly-units`)
  hermetic  a fresh detached clone of this checkout's HEAD runs `tests/run_weekly_units.py` and the
            full `tests/run_acceptance.py`, so a unit that leans on a gitignored file, the
            checkout folder name or a `.venv` this tree happens to have goes red here
            (labels `weekly-hermetic-units`, `weekly-hermetic-gate`)
  coverage  the hermetic gate leg runs under the spawn probe's coverage half; its report becomes the
            weekly coverage map (`<hooks state dir>/test-value-coverage-map.json`) the test-value
            ratchet's `new-test-value` rule reads
  hermetic-rule  the test-value ratchet's `hermetic` rule over the ledger: a unit red in the fresh
            clone but green in the primary checkout fails unless baselined
  medians   per-unit median seconds over the last N `gate` runs in the ledger

Results go to `<hooks state dir>/weekly-burst/<utc>.json` plus one log per leg, never next to the
code; the last `KEEP_RUNS` runs are kept. Exit 0 when every leg is green, 1 when any unit is red,
3 when a leg could not be established (no clone, no ledger rows): its own state, never a pass.
No model is involved, so the launcher calls this script directly rather than `claude_progress.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from fleet_core import hooks_state, runtime_files  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from unit_history import ledger, weekly_burst  # noqa: E402

KEEP_RUNS = weekly_burst.KEEP_RUNS
GATE_EVIDENCE = REPO / "skills" / "_lib" / "gate_evidence.py"
TEST_VALUE = REPO / "scripts" / "test_value" / "test_value_ratchet.py"
_prune = weekly_burst.prune


def results_dir() -> Path:
    return runtime_files.make_dirs(hooks_state.state_dir() / "weekly-burst", parents=True, exist_ok=True)


def _gate_evidence_leg(label: str, tree: Path, command: List[str]) -> List[str]:
    """Each leg runs through the evidence helper, which records its units in the hooks-state ledger."""
    return [sys.executable, str(GATE_EVIDENCE), "run", "--label", label, "--units", "--repo", str(tree), "--", *command]


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    burst = weekly_burst.Burst(repo=REPO, ledger=ledger.ledger_path(), coverage_map=ledger.coverage_map_path(),
                               results_dir=results_dir(), ratchet=TEST_VALUE, gate=["tests/run_acceptance.py"],
                               weekly=["tests/run_weekly_units.py"], leg_argv=_gate_evidence_leg)
    return weekly_burst.main(argv, burst)


if __name__ == "__main__":
    sys.exit(main())
