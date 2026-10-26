"""Gate wall-clock of each re-vendor PR, per repo per wave, in a machine-local ledger.

A wave opens one PR per repo and runs that repo's own gate once; nothing recorded how long
that took, so the cost of propagation churn could not be weighed. Each gate run goes
through `run`, which times the command itself (the duration is observed, not reported by
the agent that ran it) and appends one line to `<hooks state>/propagate-vendored/gate-ledger.jsonl`.

A duration that could not be read is `null` and renders as `unknown`, never as 0: a gate that
could not be spawned, or a repo the wave planned that left no line at all.

CLI:

  wave-id --sha <scaffold-sha>
      Print a wave id (`<short sha>-<UTC stamp>`) to hand every repo's agent.

  run --wave <id> --repo <name> -- <gate argv...>
      Run the gate (inherits stdout/stderr), time it, append the record, and exit with the
      gate's own exit code. 0 is PASS, 3 is NOT CONFIRMED (the fleet's "could not establish
      it" code), anything else FAIL; a gate that cannot be spawned is NOT CONFIRMED with an
      unknown duration (exit 3). The last stdout line is `LEDGER=recorded ...`.

  record --wave <id> --repo <name> --result PASS|FAIL|NOT_CONFIRMED [--seconds N]
      Append a record for a gate that was timed elsewhere; omit `--seconds` when it was not.

  summary --wave <id> [--expect repoA,repoB,...] [--format text|json]
      Per-repo gate time and result for the wave plus a run total. Every `--expect` repo with
      no record is listed as unknown; a total that includes an unknown is a lower bound and
      says so. Read-only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core.hooks_state import state_dir  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

ensure_utf8_stdio()

LEDGER_DIRNAME = "propagate-vendored"
LEDGER_FILENAME = "gate-ledger.jsonl"
PASS, FAIL, NOT_CONFIRMED = "PASS", "FAIL", "NOT_CONFIRMED"
RESULTS = (PASS, FAIL, NOT_CONFIRMED)
NOT_CONFIRMED_EXIT = 3
SHORT = 7


def ledger_path() -> Path:
    return state_dir() / LEDGER_DIRNAME / LEDGER_FILENAME


def new_wave_id(scaffold_sha: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{scaffold_sha[:SHORT]}-{stamp}"


def result_for_exit(code: Optional[int]) -> str:
    if code is None:
        return NOT_CONFIRMED
    if code == 0:
        return PASS
    return NOT_CONFIRMED if code == NOT_CONFIRMED_EXIT else FAIL


def _valid_seconds(seconds: Optional[float]) -> Optional[float]:
    """A negative, non-finite or missing reading is unknown, never a duration."""
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    return round(value, 3) if 0 <= value < float("inf") else None


def append_record(wave: str, repo: str, result: str, seconds: Optional[float], path: Optional[Path] = None) -> Dict[str, object]:
    """One JSON line per gate run; a single short write so concurrent repos' agents do not interleave."""
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}, got {result!r}")
    record = {"wave": wave, "repo": repo, "result": result, "seconds": _valid_seconds(seconds),
              "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def load_wave(wave: str, path: Optional[Path] = None) -> List[Dict[str, object]]:
    """Every readable record of the wave; a torn or foreign line is skipped, not fatal."""
    target = path or ledger_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("wave") == wave and isinstance(row.get("repo"), str):
            rows.append(row)
    return rows


def summarize(rows: Sequence[Dict[str, object]], expect: Sequence[str] = ()) -> Dict[str, object]:
    """Per repo: runs, summed seconds (null if any run's duration is unknown), last result.

    A gate re-run in the same wave adds its time: the wall-clock was spent either way.
    """
    per_repo: Dict[str, Dict[str, object]] = {}
    for row in rows:
        entry = per_repo.setdefault(str(row["repo"]), {"repo": row["repo"], "runs": 0, "seconds": 0.0, "result": None})
        entry["runs"] += 1
        entry["result"] = row.get("result") if row.get("result") in RESULTS else NOT_CONFIRMED
        seconds = _valid_seconds(row.get("seconds"))
        entry["seconds"] = None if seconds is None or entry["seconds"] is None else entry["seconds"] + seconds
    for repo in expect:
        per_repo.setdefault(repo, {"repo": repo, "runs": 0, "seconds": None, "result": None})
    repos = [per_repo[name] for name in sorted(per_repo)]
    known = [r["seconds"] for r in repos if r["seconds"] is not None]
    return {"repos": repos, "total_seconds": round(sum(known), 3), "known": len(known),
            "unknown": len(repos) - len(known),
            "results": {name: sum(1 for r in repos if r["result"] == name) for name in RESULTS}}


def fmt_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    whole = int(round(seconds))
    return f"{whole // 60}m{whole % 60:02d}s" if whole >= 60 else f"{seconds:.1f}s"


def render_text(wave: str, summary: Dict[str, object]) -> str:
    lines = [f"/propagate-vendored gate time - wave {wave}", ""]
    for r in summary["repos"]:
        result = r["result"].replace("_", " ") if r["result"] else "no result recorded"
        runs = f"   ({r['runs']} runs)" if r["runs"] > 1 else ""
        lines.append(f"  {r['repo']}   {fmt_seconds(r['seconds'])}   {result}{runs}")
    if not summary["repos"]:
        lines.append("  no gate run recorded for this wave")
    total = fmt_seconds(summary["total_seconds"]) if summary["known"] else "unknown"
    bound = f" (lower bound: {summary['unknown']} repo(s) unknown)" if summary["unknown"] and summary["known"] else ""
    tally = ", ".join(f"{n} {name.replace('_', ' ')}" for name, n in summary["results"].items() if n)
    lines += ["", f"  total gate time: {total}{bound}", f"  gates: {tally or 'none recorded'}"]
    return "\n".join(lines) + "\n"


def run_gate(wave: str, repo: str, argv: Sequence[str], path: Optional[Path] = None) -> int:
    started = time.monotonic()
    try:
        code: Optional[int] = subprocess.run(list(argv), creationflags=NO_WINDOW).returncode
        seconds: Optional[float] = time.monotonic() - started
    except OSError as exc:
        sys.stderr.write(f"gate could not be spawned: {exc}\n")
        code, seconds = None, None
    record = append_record(wave, repo, result_for_exit(code), seconds, path)
    sys.stdout.write(f"LEDGER=recorded repo={repo} result={record['result']} seconds={fmt_seconds(record['seconds'])}\n")
    return NOT_CONFIRMED_EXIT if code is None else code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Gate wall-clock ledger for /propagate-vendored.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wave-id", help="print a wave id for a scaffold sha")
    w.add_argument("--sha", required=True)
    r = sub.add_parser("run", help="run a gate, time it, record it")
    r.add_argument("--wave", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("gate", nargs=argparse.REMAINDER, help="-- <gate argv...>")
    rec = sub.add_parser("record", help="record a gate timed elsewhere")
    rec.add_argument("--wave", required=True)
    rec.add_argument("--repo", required=True)
    rec.add_argument("--result", required=True, choices=RESULTS)
    rec.add_argument("--seconds", type=float, default=None)
    s = sub.add_parser("summary", help="per-repo gate time and the run total")
    s.add_argument("--wave", required=True)
    s.add_argument("--expect", default="", help="repos the wave planned, comma-separated")
    s.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    if args.cmd == "wave-id":
        sys.stdout.write(new_wave_id(args.sha) + "\n")
        return 0
    if args.cmd == "run":
        gate = args.gate[1:] if args.gate[:1] == ["--"] else args.gate
        if not gate:
            ap.error("run needs a gate command after --")
        return run_gate(args.wave, args.repo, gate)
    if args.cmd == "record":
        record = append_record(args.wave, args.repo, args.result, args.seconds)
        sys.stdout.write(f"LEDGER=recorded repo={args.repo} result={record['result']} seconds={fmt_seconds(record['seconds'])}\n")
        return 0
    expect = [name for name in (n.strip() for n in args.expect.split(",")) if name]
    summary = summarize(load_wave(args.wave), expect)
    sys.stdout.write(json.dumps(summary, indent=2) + "\n" if args.format == "json" else render_text(args.wave, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
