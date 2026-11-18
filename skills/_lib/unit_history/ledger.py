"""The unit-history ledger: its location, the gate-side handoff contract, and the read side.

Handoff, per gate run: `gate_evidence run` makes a temp directory, points the gate at two files in
it through the env vars below, and puts `spawn_probe/` on `PYTHONPATH`. The gate
(`tests/acceptance/run_history/unit_report.py`) writes one JSON line per unit; the probe writes one line per
`subprocess.Popen` any Python process in the gate's tree makes, tagged with the running unit.
`record_run` folds both into the ledger and the caller deletes the temp directory.

Ledger rows (`<state dir>/gate-unit-ledger.jsonl`, one JSON object per line):

  kind "unit"  run_id, utc, label, wtree, unit, status (pass|fail|skip), seconds, spawns, output (fail only)
  kind "run"   run_id, utc, label, wtree, exit, seconds, spawns, units

`wtree` is the working-tree hash the run started on, or null when it could not be computed.
`spawns` counts `subprocess.Popen` calls in Python processes that honour `PYTHONPATH`; a child
started with `-I`/`-S` or a scrubbed environment is not seen, so the figure is a floor.
"""

from __future__ import annotations

import json
import statistics
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import hooks_state, runtime_files  # noqa: E402

LEDGER_NAME = "gate-unit-ledger.jsonl"
UNIT_REPORT_ENV = "FLEET_GATE_UNIT_REPORT"
SPAWN_REPORT_ENV = "FLEET_GATE_SPAWN_REPORT"
CURRENT_UNIT_ENV = "FLEET_GATE_UNIT"
UNITS_FILE = "units.jsonl"
SPAWNS_FILE = "spawns.jsonl"
PROBE_DIR = Path(__file__).resolve().parent / "spawn_probe"

PASS, FAIL, SKIP = "pass", "fail", "skip"

# A red unit keeps this much of what it printed (the tail), so one failure cannot bloat the ledger.
OUTPUT_TAIL_CHARS = 8000


def ledger_path() -> Path:
    return hooks_state.state_dir() / LEDGER_NAME


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def _read_lines(path: Path) -> List[dict]:
    rows: List[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written trailing line must not hide the rest
        if isinstance(row, dict):
            rows.append(row)
    return rows


def record_run(run_dir: Path, *, label: str, wtree: Optional[str], exit_code: int, seconds: float,
               run_id: Optional[str] = None, now: Optional[str] = None) -> int:
    """Append this run's unit rows and one run row; return the unit rows written (0 = nothing reported)."""
    units = _read_lines(run_dir / UNITS_FILE)
    if not units:
        return 0
    run_id = run_id or new_run_id()
    utc = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    spawns: Dict[str, int] = defaultdict(int)
    for row in _read_lines(run_dir / SPAWNS_FILE):
        spawns[str(row.get("unit") or "")] += 1
    base = {"run_id": run_id, "utc": utc, "label": label, "wtree": wtree}
    out = [{**base, "kind": "unit", "unit": u.get("unit"), "status": u.get("status"),
            "seconds": u.get("seconds"), "spawns": spawns.pop(str(u.get("unit")), 0),
            **({"output": str(u["output"])[-OUTPUT_TAIL_CHARS:]} if u.get("output") else {})} for u in units]
    # Spawns made outside any unit (the lock wait, the bracket) stay in the run total.
    out.append({**base, "kind": "run", "exit": exit_code, "seconds": round(seconds, 2),
                "spawns": sum(r["spawns"] for r in out) + sum(spawns.values()), "units": len(units)})
    path = ledger_path()
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
    with runtime_files.open_for_write(path, "a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    return len(units)


def load(run_ids: Optional[Iterable[str]] = None, label: Optional[str] = None) -> List[dict]:
    """Ledger rows, optionally narrowed to some run ids and one label."""
    wanted = set(run_ids) if run_ids is not None else None
    return [r for r in _read_lines(ledger_path())
            if (wanted is None or r.get("run_id") in wanted) and (label is None or r.get("label") == label)]


def summarize(rows: List[dict], top: int = 20) -> dict:
    """Counts and seconds over the runs in `rows`: run totals, unit count, per-unit medians, top-N share."""
    runs = [r for r in rows if r.get("kind") == "run"]
    per_unit: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "unit" and isinstance(r.get("seconds"), (int, float)):
            per_unit[str(r["unit"])].append(float(r["seconds"]))
    medians = sorted(((u, statistics.median(v)) for u, v in per_unit.items()), key=lambda p: -p[1])
    total_median = sum(m for _, m in medians)
    top_rows = medians[:top]
    return {
        "runs": len(runs),
        "run_seconds": [r.get("seconds") for r in runs],
        "run_seconds_median": statistics.median([r["seconds"] for r in runs]) if runs else None,
        "spawns": [r.get("spawns") for r in runs],
        "units": len(medians),
        "failed_units": sorted({str(r["unit"]) for r in rows if r.get("kind") == "unit" and r.get("status") == FAIL}),
        "top": [{"unit": u, "median_seconds": round(m, 2)} for u, m in top_rows],
        "top_share": round(sum(m for _, m in top_rows) / total_median, 3) if total_median else None,
    }
