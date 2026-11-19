"""The unit-history ledger in this repo: the scaffold's `unit_ledger` plus where the ledger lives here.

The report contract, the writer, the readers and the spawn probe are project-scaffolding's vendored
`unit_ledger.py` and `spawn_probe/` beside this file (`[vendored]` in `.fleet.toml`; contract in
project-scaffolding's docs/test-value-ratchet.md). This module adds only what is this repo's own: the
ledger and the weekly coverage map live in the hooks state dir (`<state dir>/gate-unit-ledger.jsonl`,
gitignored with the rest of `hooks/state/`), and the write is refused under a code folder.

Ledger rows (one JSON object per line):

  kind "unit"  run_id, utc, label, wtree, unit, status (pass|fail|skip), seconds, spawns, output (fail only)
  kind "run"   run_id, utc, label, wtree, exit, seconds, spawns, units
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import hooks_state, runtime_files  # noqa: E402

from unit_history import unit_ledger as _core  # noqa: E402
from unit_history.unit_ledger import (  # noqa: E402,F401 -- re-exported for the gate, its reporter and the burst
    COVERAGE_REPORT_ENV,
    COVERAGE_ROOT_ENV,
    CURRENT_UNIT_ENV,
    FAIL,
    ONLY_ENV,
    OUTPUT_TAIL_CHARS,
    PASS,
    PROBE_DIR,
    SKIP,
    SPAWN_REPORT_ENV,
    SPAWNS_FILE,
    TREE_ENV,
    UNIT_REPORT_ENV,
    UNITS_FILE,
    new_run_id,
    read_report,
    selected,
    summarize,
    unit_env,
)

LEDGER_NAME = "gate-unit-ledger.jsonl"
# Built weekly by the burst from a full gate run under the probe's coverage half; read by the test-value ratchet.
COVERAGE_MAP_NAME = "test-value-coverage-map.json"


def ledger_path() -> Path:
    return hooks_state.state_dir() / LEDGER_NAME


def coverage_map_path() -> Path:
    return hooks_state.state_dir() / COVERAGE_MAP_NAME


def record_run(run_dir: Path, *, label: str, wtree: Optional[str], exit_code: int, seconds: float,
               run_id: Optional[str] = None, now: Optional[str] = None) -> int:
    """Append this run's unit rows and one run row; return the unit rows written (0 = nothing reported)."""
    return _core.record_run(run_dir, runtime_files.checked(ledger_path()), label=label, wtree=wtree,
                            exit_code=exit_code, seconds=seconds, run_id=run_id, now=now)


def load(run_ids: Optional[Iterable[str]] = None, label: Optional[str] = None) -> List[dict]:
    """Ledger rows, optionally narrowed to some run ids and one label."""
    return _core.load(ledger_path(), run_ids, label)
