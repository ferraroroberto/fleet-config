"""`python -m perf_review sweep` — /perf-review across every fleet web app, one at a time (fleet-config#1686).

Each app is measured and filed by the same `measure` / `file` code a single `/perf-review` runs, so the sweep owns
only the loop: who is in scope, the box lock around each measurement, and the digest. Apps run strictly in sequence
because a second page load skews the timings of the first. A port that is down, an app that fails to measure or a
box that stays busy is reported `unmeasured`; the sweep never starts, restarts or kills anything.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import io
import json
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import box_lock  # noqa: E402
from design_review import fleet  # noqa: E402
from fleet_core import hooks_state  # noqa: E402

from . import cli, report  # noqa: E402

DEFAULT_LOCK_WAIT_MIN = 20.0
LOCK_REPO = "perf-sweep"


class _Tee(io.TextIOBase):
    """Forward to the real stdout (live progress in a Jobs pane) while keeping the text for parsing."""

    def __init__(self, out) -> None:
        self.out = out
        self.buf = io.StringIO()

    def write(self, s: str) -> int:
        self.buf.write(s)
        self.out.write(s)
        return len(s)

    def flush(self) -> None:
        self.out.flush()

    def text(self) -> str:
        return self.buf.getvalue()


def web_apps(projects_toml: Optional[Path] = None, only: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """`(in scope, dormant)`: the web apps `/design-review fleet` walks, in file order, and the ones it leaves out.

    A named `only` app runs even when dormant (docs/fleet-scope.md: a repo the user names must run).
    """
    live = [name for name, _ in fleet.fleet_targets(projects_toml)]
    dormant = fleet.dormant_targets(projects_toml)
    if only:
        return [n for n in live + dormant if n == only], []
    return live, dormant


def _tail(text: str, prefix: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    return lines[-1][len(prefix):].strip() if lines else ""


def measure_app(repo: str, args: argparse.Namespace) -> dict:
    """Measure and (unless `--no-file`) file one app; the digest row, never an exception."""
    row: dict = {"repo": repo, "state": "unmeasured", "reason": "", "fails": [], "unmeasured_checks": 0,
                 "regressed": [], "slower": [], "fixed": [], "build": None, "issue": None}
    ns = argparse.Namespace(repo=repo, url=None, python=None, duration=args.duration, min_spacing=cli.MIN_SPACING_S,
                            settle=args.settle, no_load=False)
    tee = _Tee(sys.stdout)
    try:
        with contextlib.redirect_stdout(tee):
            cli.cmd_measure(ns)
    except Exception as exc:  # one app's failure must not end the sweep
        row["reason"] = f"ERROR {type(exc).__name__}: {exc}"[:300]
        return row
    run_dir = _tail(tee.text(), "RUN_DIR=")
    if not run_dir or not (Path(run_dir) / "verdict.json").is_file():
        row["reason"] = _tail(tee.text(), "PERF=unmeasured") or "no run recorded"
        return row
    v = json.loads((Path(run_dir) / "verdict.json").read_text(encoding="utf-8"))
    d = v.get("diff", {})
    row.update(state=v["summary"]["overall"], unmeasured_checks=v["summary"]["unmeasured"],
               fails=[{"id": c["id"], "measured": c["measured"], "budget": c["budget"]}
                      for c in v["checks"] if c["status"] == "fail"],
               regressed=d.get("regressed", []), fixed=d.get("fixed", []), slower=d.get("slower", []),
               build=(v.get("deploy") or {}).get("state"))
    if not args.no_file:
        out = _Tee(sys.stdout)
        try:
            with contextlib.redirect_stdout(out):
                code = cli.cmd_file(argparse.Namespace(repo=repo, url=None, run=run_dir, slug=None, apply=True))
            row["issue"] = (out.text().strip().splitlines() or ["filed"])[-1] if code == 0 else f"not filed: {out.text().strip()[-200:]}"
        except (Exception, SystemExit) as exc:  # a gh failure must not lose the measurement
            row["issue"] = f"not filed: {type(exc).__name__}: {exc}"[:240]
    return row


def run_one(repo: str, args: argparse.Namespace, measure: Callable[[str, argparse.Namespace], dict] = measure_app) -> dict:
    """One app under an exclusive box lock, so no gate loads the machine while it is timed."""
    try:
        with box_lock.held(box_lock.EXCLUSIVE, LOCK_REPO, timeout_minutes=args.lock_wait):
            return measure(repo, args)
    except box_lock.BoxLockTimeout as exc:
        return {"repo": repo, "state": "unmeasured", "reason": f"BOX_BUSY {exc}"[:300], "fails": [],
                "unmeasured_checks": 0, "regressed": [], "slower": [], "fixed": [], "build": None, "issue": None}


def _cell(row: dict) -> Tuple[str, str]:
    """`(over budget, moved since last run)` cells for one row."""
    over = "; ".join(f"{f['id']} {report._fmt(f['measured'])} > {report._fmt(f['budget'])}" for f in row["fails"])
    moved = []
    if row["regressed"]:
        moved.append("regressed: " + ", ".join(row["regressed"]))
    if row["slower"]:
        moved.append("slower p95: " + ", ".join(f"{s['path']} {report._fmt(s['from'])}->{report._fmt(s['to'])} ms" for s in row["slower"]))
    if row["fixed"]:
        moved.append("fixed: " + ", ".join(row["fixed"]))
    return over or "-", "; ".join(moved) or "-"


def render_digest(rows: List[dict], dormant: List[str], date: str) -> str:
    """The markdown digest: counts, one table row per app, the unmeasured reasons. Numbers and repo names only."""
    count = {s: sum(1 for r in rows if r["state"] == s) for s in ("pass", "over-budget", "unmeasured")}
    worse = [r for r in rows if r["regressed"] or r["slower"]]
    lines = [f"# Perf sweep {date}", "",
             f"{len(rows)} web apps: {count['pass']} pass, {count['over-budget']} over budget, {count['unmeasured']} unmeasured, "
             f"{len(worse)} slower than the last run." + (f" Dormant, not measured: {', '.join(dormant)}." if dormant else ""),
             "", "| App | Result | Over budget | Since last run | Build | Issue |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        over, moved = _cell(r)
        result = r["state"] + (f" ({r['unmeasured_checks']} checks unmeasured)" if r["state"] == "unmeasured" and r["unmeasured_checks"] else "")
        lines.append(f"| {r['repo']} | {result} | {over} | {moved} | {r['build'] or '-'} | {r['issue'] or '-'} |")
    lines.append("")
    stale = [r["repo"] for r in rows if r["build"] in ("behind", "differs")]
    if stale:
        lines += [f"Serving an older build than the checkout, so a merged fix is not live in these numbers: {', '.join(stale)}.", ""]
    down = [r for r in rows if r["state"] == "unmeasured" and r["reason"]]
    if down:
        lines += ["## Unmeasured", ""] + [f"- {r['repo']}: {r['reason']}" for r in down] + [""]
    return "\n".join(lines)


def cmd_sweep(a: argparse.Namespace) -> int:
    try:
        apps, dormant = web_apps(only=a.only)
    except (OSError, ValueError) as exc:
        print(f"SWEEP=refused detail={exc}")
        return 2
    if not apps:
        print(f"SWEEP=refused detail=no web app in scope{' named ' + a.only if a.only else ''}")
        return 2
    print(f"SWEEP plan: {len(apps)} web apps, one at a time: {', '.join(apps)}")
    rows = []
    for i, repo in enumerate(apps, 1):
        print(f"[{i}/{len(apps)}] {repo} ...")
        row = run_one(repo, a)
        rows.append(row)
        print(f"[{i}/{len(apps)}] {repo} -- {row['state']}" + (f" ({row['reason']})" if row["reason"] else ""))
    digest = render_digest(rows, dormant, _dt.date.today().isoformat())
    out = hooks_state.state_dir() / "perf-review" / f"sweep-{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%SZ}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    hooks_state.atomic_write_text(out, digest)
    print(digest)
    print(f"DIGEST_FILE={out}")
    measured = sum(1 for r in rows if not r["reason"])
    print(f"SWEEP=done measured={measured} unmeasured={len(rows) - measured}")
    return 0 if measured else cli.EXIT["unmeasured"]
