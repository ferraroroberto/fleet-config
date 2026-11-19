"""Per-unit run history of a test gate: the report contract, the ledger writer and its readers.

A test **unit** is a whole check function or a whole test file (a pytest module), never one assertion.
Every gate run appends one row per unit to a JSONL ledger, so a unit's outcome, seconds and process
spawns can be compared across runs; `test_value_ratchet.py` and `weekly_burst.py` read it.

Handoff, per gate run (`run` below does it, or a caller such as a gate-evidence helper does the same):
make a temp directory, point the gate at two files in it through `UNIT_REPORT_ENV` and
`SPAWN_REPORT_ENV`, put the sibling `spawn_probe/` folder on `PYTHONPATH` and the start tree hash in
`TREE_ENV`. The gate's unit reporter (`unit_ledger_pytest.py` for pytest, or a dispatcher calling
`begin_unit`/`end_unit`) writes one JSON line per unit; the probe writes one line per `subprocess.Popen`
any Python process in the gate's tree makes, tagged with the running unit. `record_run` folds both into
the ledger.

Ledger rows (one JSON object per line):

  kind "unit"  run_id, utc, label, wtree, unit, status (pass|fail|skip), seconds, spawns, output (fail only)
  kind "run"   run_id, utc, label, wtree, exit, seconds, spawns, units

`wtree` is the working-tree hash the run started on, or null when it could not be computed. `spawns`
counts `subprocess.Popen` calls in Python processes that honour `PYTHONPATH`; a child started with
`-I`/`-S` or a scrubbed environment is not seen, so the figure is a floor.

The ledger is machine-local state: the caller names its path, which sits in the repo's runtime state
location and is never committed.

    python scripts/unit_ledger.py run --ledger L [--label gate] [--run-id ID] [--repo .] -- <gate command...>
    python scripts/unit_ledger.py summary --ledger L [--label gate] [--runs 3] [--top 20]

`run` exits with the command's own exit code and prints `UNIT_LEDGER=recorded run_id=... units=N` or
`UNIT_LEDGER=not_recorded reason=...` last; a ledger problem never changes the gate's verdict.

stdlib only. Contract and adoption: docs/test-value-ratchet.md in project-scaffolding.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UNIT_REPORT_ENV = "FLEET_GATE_UNIT_REPORT"
SPAWN_REPORT_ENV = "FLEET_GATE_SPAWN_REPORT"
CURRENT_UNIT_ENV = "FLEET_GATE_UNIT"
# The tree hash the run started on, so a stage inside the gate can tell this run's tree from older ones.
TREE_ENV = "FLEET_GATE_WTREE"
# Comma-separated unit names: a reporter runs only these units (a red-first record, a coverage run).
ONLY_ENV = "FLEET_GATE_ONLY"
# The spawn probe's coverage half: a report file plus the root whose files are recorded.
COVERAGE_REPORT_ENV = "FLEET_GATE_COVERAGE_REPORT"
COVERAGE_ROOT_ENV = "FLEET_GATE_COVERAGE_ROOT"
UNITS_FILE = "units.jsonl"
SPAWNS_FILE = "spawns.jsonl"
PROBE_DIR = Path(__file__).resolve().parent / "spawn_probe"

PASS, FAIL, SKIP = "pass", "fail", "skip"

# A red unit keeps this much of what it printed (the tail), so one failure cannot bloat the ledger.
OUTPUT_TAIL_CHARS = 8000
GIT_TIMEOUT_S = 120
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def selected(unit: str) -> bool:
    """Is `unit` in this run's `ONLY_ENV` list? With the variable unset every unit runs."""
    only = os.environ.get(ONLY_ENV, "").strip()
    return not only or unit in {u.strip() for u in only.split(",")}


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def _rows(path: Path) -> list[dict[str, Any]]:
    """The JSON objects in a JSONL file; a missing file is empty and a half-written line is skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written trailing line must not hide the rest
        if isinstance(row, dict):
            out.append(row)
    return out


def read_report(base: Path) -> list[dict[str, Any]]:
    """A probe report: the probe writes one `<base>.<pid>` file per process, so read them all."""
    found = _rows(base)
    for part in sorted(base.parent.glob(base.name + ".*")):
        found += _rows(part)
    return found


def _append(path: Path, text: str) -> None:
    # runtime-write-exempt: the caller names the ledger or the gate's temp report, outside the code folders
    path.parent.mkdir(parents=True, exist_ok=True)
    # runtime-write-exempt: the caller names the ledger or the gate's temp report, outside the code folders
    with open(path, "a", encoding="utf-8") as fh:  # must-use-exempt: a portable stdlib module
        fh.write(text)


def record_run(run_dir: Path, ledger: Path, *, label: str, wtree: str | None, exit_code: int,
               seconds: float, run_id: str | None = None, now: str | None = None) -> int:
    """Append this run's unit rows and one run row to `ledger`; return the unit rows written (0 = none)."""
    units = _rows(run_dir / UNITS_FILE)
    if not units:
        return 0
    run_id = run_id or new_run_id()
    utc = now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    spawns: dict[str, int] = defaultdict(int)
    for row in read_report(run_dir / SPAWNS_FILE):
        spawns[str(row.get("unit") or "")] += 1
    base = {"run_id": run_id, "utc": utc, "label": label, "wtree": wtree}
    out: list[dict[str, Any]] = [
        {**base, "kind": "unit", "unit": u.get("unit"), "status": u.get("status"),
         "seconds": u.get("seconds"), "spawns": spawns.pop(str(u.get("unit")), 0),
         **({"output": str(u["output"])[-OUTPUT_TAIL_CHARS:]} if u.get("output") else {})} for u in units]
    # Spawns made outside any unit (a lock wait, the bracket) stay in the run total.
    out.append({**base, "kind": "run", "exit": exit_code, "seconds": round(seconds, 2),
                "spawns": sum(int(r["spawns"]) for r in out) + sum(spawns.values()), "units": len(units)})
    _append(ledger, "".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    return len(units)


def load(ledger: Path, run_ids: Iterable[str] | None = None, label: str | None = None) -> list[dict[str, Any]]:
    """Ledger rows, optionally narrowed to some run ids and one label."""
    wanted = set(run_ids) if run_ids is not None else None
    return [r for r in _rows(ledger)
            if (wanted is None or r.get("run_id") in wanted) and (label is None or r.get("label") == label)]


def summarize(rows: list[dict[str, Any]], top: int = 20) -> dict[str, Any]:
    """Counts and seconds over the runs in `rows`: run totals, unit count, per-unit medians, top-N share."""
    runs = [r for r in rows if r.get("kind") == "run"]
    per_unit: dict[str, list[float]] = defaultdict(list)
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


# ---------------------------------------------------------------- the reporter side


def coverage_flush(name: str) -> None:
    """Hand the probe's recorded lines to `name` (or drop them for ""); a no-op without the probe."""
    flush = getattr(sys.modules.get("sitecustomize"), "fleet_coverage_flush", None)
    if callable(flush):
        flush(name)


def begin_unit(name: str) -> None:
    """Publish `name` as the running unit, so spawns and covered lines are attributed to it."""
    os.environ[CURRENT_UNIT_ENV] = name
    coverage_flush("")  # lines run between units belong to none of them


def end_unit(name: str, status: str, seconds: float, output: str = "") -> None:
    """Close `name`: flush its covered lines and report its row when the gate asked for a unit report."""
    coverage_flush(name)
    os.environ.pop(CURRENT_UNIT_ENV, None)
    path = os.environ.get(UNIT_REPORT_ENV)
    if not path:
        return
    row: dict[str, Any] = {"unit": name, "status": status, "seconds": round(seconds, 3)}
    if status == FAIL and output:
        marker = "[... earlier output cut]\n"
        row["output"] = output if len(output) <= OUTPUT_TAIL_CHARS else marker + output[-(OUTPUT_TAIL_CHARS - len(marker)):]
    try:
        _append(Path(path), json.dumps(row) + "\n")
    except OSError:
        pass  # a reporter must not fail the gate it measures


# ---------------------------------------------------------------- the run wrapper


def _git(repo: Path, args: list[str], env: dict[str, str] | None = None) -> str | None:
    full = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", **(env or {})}  # a read must not leave index locks
    try:
        # git-wrapper-exempt: a stdlib script that ports to other repos; it sets GIT_OPTIONAL_LOCKS=0 itself
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, env=full, creationflags=_NO_WINDOW,
                              timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def tree_hash(repo: Path) -> str | None:
    """The content hash of the whole working tree (untracked, not ignored, files included), or None.

    `git add -A` + `write-tree` against a copy of the index, so the real index is never touched. The copy
    keeps the index file's mtime: git trusts an entry's stat only when it is older than the index file."""
    index = _git(repo, ["rev-parse", "--git-path", "index"])
    if not index:
        return None
    real = Path(index) if Path(index).is_absolute() else repo / index
    scratch = Path(tempfile.mkdtemp(prefix="unit_ledger_index_"))
    copy = scratch / "index"
    try:
        if real.exists():
            shutil.copy2(real, copy)
        env = {"GIT_INDEX_FILE": str(copy)}
        if _git(repo, ["add", "-A"], env) is None:
            return None
        return _git(repo, ["write-tree"], env) or None
    except OSError:
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def unit_env(run_dir: Path, wtree: str | None, base: dict[str, str] | None = None) -> dict[str, str]:
    """`base` (default: this process's environment) with the report files, start tree and probe wired in."""
    env = dict(os.environ if base is None else base)
    env.pop(TREE_ENV, None)
    if wtree:
        env[TREE_ENV] = wtree
    env[UNIT_REPORT_ENV] = str(run_dir / UNITS_FILE)
    env[SPAWN_REPORT_ENV] = str(run_dir / SPAWNS_FILE)
    env["PYTHONPATH"] = str(PROBE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def run(repo: Path, ledger: Path, label: str, argv: list[str], run_id: str | None = None) -> tuple[int, str]:
    """Run the gate command `argv` in `repo` under the reporters; `(exit code, UNIT_LEDGER=... line)`."""
    run_id = run_id or new_run_id()
    wtree = tree_hash(repo)
    run_dir = Path(tempfile.mkdtemp(prefix="unit_ledger_run_"))
    started = time.monotonic()
    sys.stdout.flush()
    try:
        # subprocess-timeout-exempt: the caller's gate command, whose length is the caller's to bound
        code = subprocess.run(argv, cwd=str(repo), env=unit_env(run_dir, wtree), creationflags=_NO_WINDOW,
                              check=False).returncode
    except OSError as exc:
        print(f"unit_ledger: could not start {argv[0]!r}: {exc}", file=sys.stderr)
        code = 127
    try:
        written = record_run(run_dir, ledger, label=label, wtree=wtree, exit_code=code,
                             seconds=time.monotonic() - started, run_id=run_id)
    except OSError as exc:
        return code, f"UNIT_LEDGER=not_recorded reason={type(exc).__name__}"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    if not written:
        return code, "UNIT_LEDGER=not_recorded reason=the command reported no unit"
    return code, f"UNIT_LEDGER=recorded run_id={run_id} units={written}"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Per-unit run history of a test gate.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run a gate command and record its units")
    run_p.add_argument("--ledger", type=Path, required=True)
    run_p.add_argument("--label", default="gate")
    run_p.add_argument("--run-id")
    run_p.add_argument("--repo", type=Path, default=Path.cwd())
    run_p.add_argument("command", nargs=argparse.REMAINDER)
    summ = sub.add_parser("summary", help="counts and seconds over the last runs of a label")
    summ.add_argument("--ledger", type=Path, required=True)
    summ.add_argument("--runs", type=int, default=3)
    summ.add_argument("--label", default="gate")
    summ.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)
    if args.cmd == "summary":
        rows = load(args.ledger, label=args.label)
        run_ids = [r["run_id"] for r in rows if r.get("kind") == "run"][-args.runs:]
        print(json.dumps(summarize([r for r in rows if r.get("run_id") in run_ids], args.top), indent=2))
        return 0 if run_ids else 1
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("unit_ledger run: give the gate command after --")
        return 2
    code, line = run(args.repo.resolve(), args.ledger, args.label, command, args.run_id)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
