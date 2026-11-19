"""The weekly burst: the burn/load/wall-clock units, the hermetic re-run, the coverage map and the medians.

The per-change gate never runs a burn or load proof on purpose and never fails on wall-clock time; this
burst runs them once a week, as its own scheduled job, and records every leg in the unit ledger
(`unit_ledger.py`) under its own label so the per-unit medians can tell them apart from the per-change
gate:

  units          the weekly unit command in this working tree                  (label `weekly-units`)
  hermetic       a fresh detached clone of this checkout's HEAD runs the weekly unit command and the full
                 gate command, so a unit that leans on a gitignored file, the checkout folder name or a
                 `.venv` this tree happens to have goes red here
                 (labels `weekly-hermetic-units` and the ratchet's `hermetic_label`)
  coverage-map   the hermetic gate leg runs under the spawn probe's coverage half; its report becomes the
                 weekly coverage map the test-value ratchet's `new-test-value` rule reads
  hermetic-rule  the test-value ratchet's `hermetic` rule over the ledger
  medians        per-unit median seconds over the last N per-change gate runs in the ledger

Each leg's command runs through the run wrapper (`unit_ledger.py run`, or the caller's `leg_argv`), so
its verdict is its exit code plus the ledger row it wrote: a green exit with no row for this run is
`not-confirmed`. Results go to `<results dir>/<utc>.json` plus one log per leg, never next to the code;
the last `KEEP_RUNS` runs are kept. Exit 0 when every leg is green, 1 when any unit is red, 3 when a leg
could not be established (no clone, no ledger rows, a skipped leg): its own state, never a pass. No model
is involved, so a scheduler calls this script directly.

Opt-in, per adopter, in `.fleet.toml` (machine-local paths are arguments, never config):

    [test_value_burst]
    enabled = true
    gate    = ["-m", "pytest", "-q", "-p", "scripts.unit_ledger_pytest"]   # argv after the interpreter
    weekly  = ["-m", "pytest", "-q", "-p", "scripts.unit_ledger_pytest", "tests/weekly"]   # optional

    python scripts/weekly_burst.py --ledger L --coverage-map M --results-dir D [--median-runs 5] [--skip-hermetic]

A command's first item, when it is not an interpreter flag, is a repo-relative script, resolved inside
the tree the leg runs in. A repo with its own gate wrapper builds a `Burst` and calls `main(argv, burst)`.

stdlib only. Contract and adoption: docs/test-value-ratchet.md in project-scaffolding.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_sibling(name: str) -> ModuleType:
    """The sibling module, as a package member when imported as one, else by path."""
    if __package__:
        return importlib.import_module(f"{__package__}.{name}")
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"{name}.py is missing beside {Path(__file__).name}")
    module = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = _load_sibling("unit_ledger")

KEEP_RUNS = 8
GIT_TIMEOUT_S = 600
RATCHET_TIMEOUT_S = 600
RED, UNKNOWN, GREEN = "red", "not-confirmed", "green"
EXIT = {GREEN: 0, RED: 1, UNKNOWN: 3}
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
Leg = dict[str, Any]
#: `(label, tree, command argv)` -> the argv that runs the command under the unit reporters.
LegArgv = Callable[[str, Path, list[str]], list[str]]


@dataclass
class Burst:
    """What one burst runs and where it writes."""

    repo: Path
    ledger: Path
    coverage_map: Path
    results_dir: Path
    ratchet: Path
    gate: list[str]
    weekly: list[str] | None = None
    leg_argv: LegArgv | None = None
    gate_label: str = "gate"
    hermetic_label: str = "weekly-hermetic-gate"

    def wrap(self, label: str, tree: Path, command: list[str]) -> list[str]:
        if self.leg_argv is not None:
            return self.leg_argv(label, tree, command)
        return [sys.executable, str(Path(str(ledger.__file__))), "run", "--ledger", str(self.ledger),
                "--label", label, "--repo", str(tree), "--", *command]


def command(tree: Path, spec: list[str]) -> list[str]:
    """`spec` as a full argv run in `tree`: the interpreter, then a leading script resolved inside the tree."""
    if spec and not spec[0].startswith("-"):
        return [sys.executable, str(tree / spec[0]), *spec[1:]]
    return [sys.executable, *spec]


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_rows(burst: Burst, label: str) -> list[dict[str, Any]]:
    return [r for r in ledger.load(burst.ledger, label=label) if r.get("kind") == "run"]


def _failed_units(burst: Burst, run: dict[str, Any]) -> list[str]:
    rows = ledger.load(burst.ledger, run_ids=[run["run_id"]], label=run.get("label"))
    return sorted({str(r["unit"]) for r in rows if r.get("kind") == "unit" and r.get("status") == ledger.FAIL})


def run_leg(burst: Burst, name: str, label: str, spec: list[str], tree: Path, log: Path,
            extra_env: dict[str, str] | None = None) -> Leg:
    """Run one test command under the reporters; its exit code plus the ledger row it wrote decide."""
    argv = burst.wrap(label, tree, command(tree, spec))
    print(f"▶ {name}: {' '.join(spec)} in {tree}", flush=True)
    seen = {r["run_id"] for r in _run_rows(burst, label)}
    # runtime-write-exempt: the log sits in the caller's results directory, outside the code folders
    with open(log, "wb") as out:  # must-use-exempt: a portable stdlib script
        # subprocess-timeout-exempt: a foreground leg whose length is the scheduled job's ceiling, not this script's
        code = subprocess.run(argv, cwd=str(tree), creationflags=_NO_WINDOW, stdout=out, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env={**os.environ, **(extra_env or {})}).returncode
    # A command that reported no units writes no ledger row; an older week's row must not stand in for it.
    fresh = [r for r in _run_rows(burst, label) if r["run_id"] not in seen]
    run = fresh[-1] if fresh else None
    failed = _failed_units(burst, run) if run else []
    if code != 0:
        state = RED  # a command that died before reporting any unit is red too
    else:
        state = GREEN if run else UNKNOWN  # exit 0 with no ledger row establishes nothing
    seconds = round(run["seconds"], 1) if run and isinstance(run.get("seconds"), (int, float)) else None
    print(f"{'✅' if state == GREEN else '❌'} {name}: {state} exit {code}"
          + (f" red units: {', '.join(failed)}" if failed else "") + f" · log {log.name}", flush=True)
    return {"leg": name, "label": label, "state": state, "exit": code, "seconds": seconds,
            "red_units": failed, "log": log.name}


def ratchet_leg(burst: Burst, name: str, args: list[str], tree: Path, log: Path) -> Leg:
    """One test-value ratchet call as a leg: exit 0 green, 1 red, anything else not established."""
    print(f"▶ {name}", flush=True)
    try:
        # runtime-write-exempt: the log sits in the caller's results directory, outside the code folders
        with open(log, "wb") as out:  # must-use-exempt: a portable stdlib script
            code = subprocess.run([sys.executable, str(burst.ratchet), "--repo", str(tree), *args], cwd=str(tree),
                                  creationflags=_NO_WINDOW, stdout=out, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=RATCHET_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        code = 3
    state = GREEN if code == 0 else RED if code == 1 else UNKNOWN
    print(f"{'✅' if state == GREEN else '❌'} {name}: {state} exit {code} · log {log.name}", flush=True)
    return {"leg": name, "state": state, "exit": code, "log": log.name}


def _git(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        # git-wrapper-exempt: a stdlib script that ports to other repos; it sets GIT_OPTIONAL_LOCKS=0 itself
        return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, env=env, creationflags=_NO_WINDOW, timeout=GIT_TIMEOUT_S,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def fresh_clone(repo: Path, parent: Path) -> Path | None:
    """A clone of `repo` detached at its HEAD: committed files only, no `.env`, no `.venv`."""
    head = _git(["-C", str(repo), "rev-parse", "HEAD"])
    if head is None or head.returncode != 0:
        return None
    target = parent / "clone"
    cloned = _git(["clone", "--quiet", "--no-hardlinks", str(repo), str(target)])
    if cloned is None or cloned.returncode != 0:
        return None
    detached = _git(["-C", str(target), "checkout", "--quiet", "--detach", head.stdout.strip()])
    return target if detached is not None and detached.returncode == 0 else None


def _hermetic_legs(burst: Burst, work: Path, stamp: str) -> list[Leg]:
    """The fresh-clone legs: weekly units, the full gate under coverage, the map built from it."""
    clone = fresh_clone(burst.repo, work)
    if clone is None:
        print("❌ hermetic: the fresh clone could not be made", flush=True)
        return [{"leg": "hermetic", "state": UNKNOWN, "reason": "clone or detach failed"}]
    folder = burst.results_dir
    legs: list[Leg] = []
    if burst.weekly:
        legs.append(run_leg(burst, "hermetic-units", "weekly-hermetic-units", burst.weekly, clone,
                            folder / f"{stamp}-hermetic-units.log"))
    report = work / "coverage.jsonl"
    coverage_env = {ledger.COVERAGE_REPORT_ENV: str(report), ledger.COVERAGE_ROOT_ENV: str(clone)}
    legs.append(run_leg(burst, "hermetic-gate", burst.hermetic_label, burst.gate, clone,
                        folder / f"{stamp}-hermetic-gate.log", extra_env=coverage_env))
    if ledger.read_report(report):
        legs.append(ratchet_leg(burst, "coverage-map", ["--build-map", str(report), "--out", str(burst.coverage_map)],
                                clone, folder / f"{stamp}-coverage-map.log"))
    else:
        print("❌ coverage-map: the gate leg recorded no coverage", flush=True)
        legs.append({"leg": "coverage-map", "state": UNKNOWN, "reason": "no coverage report"})
    return legs


def _make_writable_and_retry(func: Callable[[str], object], path: str, _exc: object) -> None:
    """git marks pack files read-only, which Windows refuses to delete; this clone is ours alone."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        print(f"⚠️ could not remove {path}", flush=True)


def medians(burst: Burst, runs: int) -> dict[str, Any]:
    """Per-unit median seconds over the last `runs` per-change gate runs."""
    rows = ledger.load(burst.ledger, label=burst.gate_label)
    ids = [r["run_id"] for r in rows if r.get("kind") == "run"][-runs:]
    if not ids:
        return {"state": UNKNOWN, "reason": f"no `{burst.gate_label}` run in the unit ledger"}
    summary = ledger.summarize([r for r in rows if r.get("run_id") in set(ids)], top=20)
    return {"state": GREEN, "runs": summary["runs"], "run_seconds_median": summary["run_seconds_median"],
            "units": summary["units"], "top": summary["top"], "top_share": summary["top_share"]}


def prune(folder: Path) -> None:
    """Keep the newest `KEEP_RUNS` summaries and the logs that share their stamp."""
    summaries = sorted(folder.glob("*.json"))
    for old in summaries[:-KEEP_RUNS]:
        for stale in folder.glob(old.stem + "*"):
            stale.unlink(missing_ok=True)


def run_burst(burst: Burst, median_runs: int = 5, skip_hermetic: bool = False) -> int:
    folder = burst.results_dir
    # runtime-write-exempt: the caller names the results folder, in its runtime state location
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    legs: list[Leg] = []
    if burst.weekly:
        legs.append(run_leg(burst, "units", "weekly-units", burst.weekly, burst.repo, folder / f"{stamp}-units.log"))
    if skip_hermetic:
        print("⚠️ hermetic leg skipped on request: this run does not establish a fresh-clone result", flush=True)
        legs.append({"leg": "hermetic", "state": UNKNOWN, "reason": "skipped on request"})
    else:
        work = Path(tempfile.mkdtemp(prefix="weekly_burst_"))
        try:
            legs += _hermetic_legs(burst, work, stamp)
        finally:
            shutil.rmtree(work, onexc=_make_writable_and_retry)
        legs.append(ratchet_leg(burst, "hermetic-rule", ["--rules", "hermetic", "--ledger", str(burst.ledger)],
                                burst.repo, folder / f"{stamp}-hermetic-rule.log"))
    times = medians(burst, median_runs)
    states = {leg["state"] for leg in legs} | {times["state"]}
    verdict = RED if RED in states else UNKNOWN if UNKNOWN in states else GREEN
    summary = {"utc": stamp, "verdict": verdict, "legs": legs, "medians": times}
    target = folder / f"{stamp}.json"
    tmp = target.with_suffix(".json.tmp")
    # runtime-write-exempt: the summary sits in the caller's results folder, outside the code folders
    tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)  # must-use-exempt: a portable stdlib script; one writer per summary, this run
    prune(folder)
    print(f"{'✅' if verdict == GREEN else '❌'} weekly burst: {verdict} · {stamp}.json", flush=True)
    return EXIT[verdict]


def _from_config(root: Path, args: argparse.Namespace) -> Burst | None:
    """The `[test_value_burst]` table as a `Burst`; None when it is absent or off. Raises ValueError."""
    try:
        table = tomllib.loads((root / ".fleet.toml").read_text(encoding="utf-8")).get("test_value_burst")
    except OSError:
        return None
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f".fleet.toml does not parse: {exc}") from exc
    if not isinstance(table, dict) or table.get("enabled") is not True:
        return None
    unknown = set(table) - {"enabled", "gate", "weekly"}
    if unknown:
        raise ValueError(f"[test_value_burst] has unknown key(s): {', '.join(sorted(unknown))}")
    specs: dict[str, list[str] | None] = {}
    for key, default in (("gate", ["-m", "pytest", "-q", "-p", "scripts.unit_ledger_pytest"]), ("weekly", None)):
        value = table.get(key, default)
        if value is not None and not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
            raise ValueError(f"[test_value_burst] {key} must be a non-empty list of strings")
        specs[key] = value
    missing = [flag for flag, value in (("--ledger", args.ledger), ("--coverage-map", args.coverage_map),
                                        ("--results-dir", args.results_dir)) if value is None]
    if missing:
        raise ValueError(f"give {', '.join(missing)}: machine-local paths are arguments, never config")
    return Burst(repo=root, ledger=args.ledger, coverage_map=args.coverage_map, results_dir=args.results_dir,
                 ratchet=args.ratchet or root / "scripts" / "test_value_ratchet.py",
                 gate=specs["gate"] or [], weekly=specs["weekly"])


def main(argv: list[str] | None = None, burst: Burst | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Weekly burst: burn units, hermetic re-run, coverage map, medians.")
    parser.add_argument("--median-runs", type=int, default=5)
    parser.add_argument("--skip-hermetic", action="store_true", help="skip the fresh-clone leg (diagnosis only)")
    if burst is None:
        parser.add_argument("--repo", type=Path, default=Path.cwd())
        parser.add_argument("--ledger", type=Path)
        parser.add_argument("--coverage-map", type=Path)
        parser.add_argument("--results-dir", type=Path)
        parser.add_argument("--ratchet", type=Path)
    args = parser.parse_args(argv)
    if burst is None:
        try:
            burst = _from_config(args.repo.resolve(), args)
        except ValueError as exc:
            print(f"weekly burst: {exc}")
            return 2
        if burst is None:
            print("weekly burst: not enabled in .fleet.toml")
            return 0
    return run_burst(burst, args.median_runs, args.skip_hermetic)


if __name__ == "__main__":
    sys.exit(main())
