"""The weekly burst: the burn/load/wall-clock units, the hermetic re-run and the runtime medians.

    python .claude/skills/weekly-burst/burst.py [--median-runs N] [--skip-hermetic]

Three legs, each recorded in the unit ledger under its own label so the per-unit medians can tell
them apart from the per-change gate (`gate`):

  units     `tests/run_weekly_units.py` in this working tree  (label `weekly-units`)
  hermetic  a fresh detached clone of this checkout's HEAD runs `tests/run_weekly_units.py` and the
            full `tests/run_acceptance.py`, so a unit that leans on a gitignored file, the
            checkout folder name or a `.venv` this tree happens to have goes red here
            (labels `weekly-hermetic-units`, `weekly-hermetic-gate`)
  medians   per-unit median seconds over the last N `gate` runs in the ledger

Results go to `<hooks state dir>/weekly-burst/<utc>.json` plus one log per leg, never next to the
code; the last `KEEP_RUNS` runs are kept. Exit 0 when every leg is green, 1 when any unit is red,
3 when a leg could not be established (no clone, no ledger rows): its own state, never a pass.
No model is involved, so the launcher calls this script directly rather than `claude_progress.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from fleet_core import hooks_state, runtime_files  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from git_run import run_git  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from unit_history import ledger  # noqa: E402

KEEP_RUNS = 8
GIT_TIMEOUT_S = 600
GATE_EVIDENCE = REPO / "skills" / "_lib" / "gate_evidence.py"
RED, UNKNOWN, GREEN = "red", "not-confirmed", "green"
EXIT = {GREEN: 0, RED: 1, UNKNOWN: 3}


def results_dir() -> Path:
    return runtime_files.make_dirs(hooks_state.state_dir() / "weekly-burst", parents=True, exist_ok=True)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_rows(label: str) -> List[dict]:
    return [r for r in ledger.load(label=label) if r.get("kind") == "run"]


def _failed_units(run: dict) -> List[str]:
    rows = ledger.load(run_ids=[run["run_id"]], label=run.get("label"))
    return sorted({str(r["unit"]) for r in rows if r.get("kind") == "unit" and r.get("status") == ledger.FAIL})


def _run_leg(name: str, label: str, script: str, tree: Path, log: Path) -> dict:
    """Run one test dispatcher through `gate_evidence run --units`; the verdict is its exit code
    plus the ledger row it wrote: a green exit with no row for this run is `not-confirmed`."""
    argv = [sys.executable, str(GATE_EVIDENCE), "run", "--label", label, "--units", "--repo", str(tree),
            "--", sys.executable, str(tree / "tests" / script)]
    print(f"▶ {name}: {script} in {tree}", flush=True)
    seen = {r["run_id"] for r in _run_rows(label)}
    with runtime_files.open_for_write(log, "wb") as out:
        # subprocess-timeout-exempt: foreground leg whose length is the scheduled Job's ceiling, not this script's
        code = subprocess.run(argv, cwd=str(tree), creationflags=NO_WINDOW, stdout=out, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL).returncode
    # A dispatcher that reported no units writes no ledger row; an older week's row must not stand in for it.
    fresh = [r for r in _run_rows(label) if r["run_id"] not in seen]
    run = fresh[-1] if fresh else None
    failed = _failed_units(run) if run else []
    if code != 0:
        state = RED  # a dispatcher that died before reporting any unit is red too
    else:
        state = GREEN if run else UNKNOWN  # exit 0 with no ledger row establishes nothing
    seconds = round(run["seconds"], 1) if run and isinstance(run.get("seconds"), (int, float)) else None
    print(f"{'✅' if state == GREEN else '❌'} {name}: {state} exit {code}"
          + (f" red units: {', '.join(failed)}" if failed else "") + f" · log {log.name}", flush=True)
    return {"leg": name, "label": label, "state": state, "exit": code, "seconds": seconds,
            "red_units": failed, "log": log.name}


def _fresh_clone(parent: Path) -> Optional[Path]:
    """A clone of this checkout detached at its HEAD: committed files only, no `.env`, no `.venv`."""
    head = run_git(["-C", str(REPO), "rev-parse", "HEAD"], timeout=GIT_TIMEOUT_S)
    if head.returncode != 0:
        return None
    target = parent / "clone"
    cloned = run_git(["clone", "--quiet", "--no-hardlinks", str(REPO), str(target)], timeout=GIT_TIMEOUT_S)
    if cloned.returncode != 0:
        return None
    detached = run_git(["-C", str(target), "checkout", "--quiet", "--detach", head.stdout.strip()],
                       timeout=GIT_TIMEOUT_S)
    return target if detached.returncode == 0 else None


def _make_writable_and_retry(func, path, _exc) -> None:
    """git marks pack files read-only, which Windows refuses to delete; this clone is ours alone."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        print(f"⚠️ could not remove {path}", flush=True)


def _medians(runs: int) -> dict:
    """Per-unit median seconds over the last `runs` per-change gate runs."""
    rows = ledger.load(label="gate")
    ids = [r["run_id"] for r in rows if r.get("kind") == "run"][-runs:]
    if not ids:
        return {"state": UNKNOWN, "reason": "no `gate` run in the unit ledger"}
    summary = ledger.summarize([r for r in rows if r.get("run_id") in set(ids)], top=20)
    return {"state": GREEN, "runs": summary["runs"], "run_seconds_median": summary["run_seconds_median"],
            "units": summary["units"], "top": summary["top"], "top_share": summary["top_share"]}


def _prune(folder: Path) -> None:
    summaries = sorted(folder.glob("*.json"))
    for old in summaries[:-KEEP_RUNS]:
        for stale in folder.glob(old.stem + "*"):
            stale.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Weekly burst: burn units, hermetic re-run, runtime medians.")
    parser.add_argument("--median-runs", type=int, default=5)
    parser.add_argument("--skip-hermetic", action="store_true", help="skip the fresh-clone leg (diagnosis only)")
    args = parser.parse_args(argv)

    folder = results_dir()
    stamp = _stamp()
    legs: List[dict] = [_run_leg("units", "weekly-units", "run_weekly_units.py", REPO, folder / f"{stamp}-units.log")]

    if args.skip_hermetic:
        print("⚠️ hermetic leg skipped on request: this run does not establish a fresh-clone result", flush=True)
        legs.append({"leg": "hermetic", "state": UNKNOWN, "reason": "skipped on request"})
    else:
        work = Path(tempfile.mkdtemp(prefix="weekly_burst_"))
        try:
            clone = _fresh_clone(work)
            if clone is None:
                print("❌ hermetic: the fresh clone could not be made", flush=True)
                legs.append({"leg": "hermetic", "state": UNKNOWN, "reason": "clone or detach failed"})
            else:
                legs.append(_run_leg("hermetic-units", "weekly-hermetic-units", "run_weekly_units.py", clone,
                                     folder / f"{stamp}-hermetic-units.log"))
                legs.append(_run_leg("hermetic-gate", "weekly-hermetic-gate", "run_acceptance.py", clone,
                                     folder / f"{stamp}-hermetic-gate.log"))
        finally:
            shutil.rmtree(work, onerror=_make_writable_and_retry)

    medians = _medians(args.median_runs)
    states = {leg["state"] for leg in legs} | {medians["state"]}
    verdict = RED if RED in states else UNKNOWN if UNKNOWN in states else GREEN
    summary = {"utc": stamp, "verdict": verdict, "legs": legs, "medians": medians}
    hooks_state.atomic_write_text(folder / f"{stamp}.json", json.dumps(summary, indent=2) + "\n")
    _prune(folder)
    print(f"{'✅' if verdict == GREEN else '❌'} weekly burst: {verdict} · {stamp}.json", flush=True)
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
