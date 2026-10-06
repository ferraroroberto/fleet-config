"""The `timing` and `failures` reports: one run's measurements put together, and the failure history.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import git_run

from .sources import (
    checkout_of,
    failure_events,
    GH_TIMEOUT_S,
    load_state,
    projection_of,
    repo_slug,
    _gather,
    _wall_s,
)
from .measures import buckets, executed_e2e, first_node, is_e2e, modules, projections, tail
from .fit import projection_fit
from .waits import app_timers, rank_waits, runtime_drift, slow_fixtures, slow_nodes, wait_sites

__all__ = [
    "race_candidates",
    "skip_reason_counts",
    "STALE_NODE_TOLERANCE",
    "stale_nodes",
    "timing",
    "text_mentions",
    "gh_mentions",
    "failures",
]


def race_candidates(failures: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Tests whose log failures stopped at two or more different steps (app-launcher#1222)."""
    by_test: Dict[str, List[Dict[str, object]]] = {}
    for f in failures:
        by_test.setdefault(str(f["test"]), []).append(f)
    out = []
    for test, evs in sorted(by_test.items()):
        steps = sorted({str(e["step"]) for e in evs if e.get("step")})
        if len(steps) >= 2:
            out.append({"test": test, "projections": sorted({str(e["projection"]) for e in evs}),
                        "steps": steps, "events": len(evs)})
    return out


def skip_reason_counts(run: Dict[str, object], skipped: Sequence[str]) -> Optional[List[Dict[str, object]]]:
    """Why the e2e nodes skipped, most common first; `None` when the source records no reason (a progress log)."""
    reasons = run.get("skip_reasons")
    if reasons is None:
        return None
    counts: Dict[str, int] = {}
    for n in skipped:
        r = str(reasons.get(n) or "no reason recorded")  # type: ignore[union-attr]
        counts[r] = counts.get(r, 0) + 1
    return [{"reason": r, "nodes": c} for r, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


STALE_NODE_TOLERANCE = 0.10


def stale_nodes(run_nodes: int, suite_nodes: Optional[int]) -> Dict[str, object]:
    """Whether a run's e2e node count still matches the suite's collected node count (fleet-config#1175).

    app-launcher's only timing log was a 612-node run from before the suite
    shrank to 485, and `timing` called it `ok`: every `paid_s` was doubled and
    `runtime_drift` measured a 29.5 min gate against a real 13.9. More than
    10% apart in either direction is `stale`; a suite count that was not
    measured (or is 0) is `unchecked`, never `fresh`.
    """
    if not suite_nodes:
        return {"status": "unchecked", "run_nodes": run_nodes, "suite_nodes": suite_nodes,
                "reason": "the suite's collected node count was not measured, so the run's age against it is unknown"}
    if abs(run_nodes - suite_nodes) / suite_nodes > STALE_NODE_TOLERANCE:
        return {"status": "stale", "run_nodes": run_nodes, "suite_nodes": suite_nodes,
                "reason": f"the run covers {run_nodes} e2e nodes and the suite collects {suite_nodes} now (more than "
                          f"{round(STALE_NODE_TOLERANCE * 100)}% apart): a fresh baseline is needed before ranking"}
    return {"status": "fresh", "run_nodes": run_nodes, "suite_nodes": suite_nodes, "reason": None}


def timing(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None,
           suite_nodes: Optional[int] = None) -> Dict[str, object]:
    g = _gather(repo_root, log)
    if "error" in g:
        return {"status": "unknown", "reason": g["error"], "source": g.get("source")}
    logs: List[Tuple[Path, List[Dict[str, object]]]] = g["logs"]  # type: ignore[assignment]
    # The latest completed run in any checkout: gates run in worktrees too, so the primary's copy goes stale.
    done = [(p, r) for p, runs in logs for r in runs if r["complete"]]
    if not done:
        return {"status": "unknown", "reason": "no completed gate run in any checkout's log", "source": g["source"]}
    # A later backend-only run or surface slice is not the suite (fleet-config#1134): prefer the latest
    # full-tier run that ran e2e nodes, as `time_budget` does, and say so when only a slice is on record.
    with_e2e = [(p, r) for p, r in done if executed_e2e(r, test_dirs)]
    full = [(p, r) for p, r in with_e2e if r.get("routed_tier") in (None, "full")]
    pool = full or with_e2e or done
    path, run = max(pool, key=lambda pr: pr[1]["finished"] or _dt.datetime.min)  # type: ignore[arg-type,return-value]
    slice_note = (None if full or not with_e2e else
                  f"no completed full-tier run on record; this run was routed {run.get('routed_tier')}, a slice of the suite")
    if g["source"]["kind"] == "junit-xml":  # type: ignore[index]
        load: Dict[str, object] = {"state": "unknown", "reason": "a JUnit XML carries no run window",
                                   "scope": "", "overlaps": [], "checked": []}
    else:
        load = load_state(run, [(p, [r for r in runs if r is not run]) for p, runs in logs])
    nodes: Dict[str, float] = run["nodes"]  # type: ignore[assignment]
    e2e = executed_e2e(run, test_dirs)
    skipped = [n for n in run.get("skipped") or () if is_e2e(n, test_dirs)]  # type: ignore[union-attr]
    # Earlier completed runs that executed e2e nodes, from the measured run's own log (one checkout), newest first.
    own = next((runs for p, runs in logs if p == path), [])
    earlier_executed = [ex for r in sorted((r for r in own if r["complete"] and r is not run and r["finished"]
                                            and run["finished"] and r["finished"] < run["finished"]),  # type: ignore[operator]
                                           key=lambda r: r["finished"], reverse=True)  # type: ignore[arg-type,return-value]
                        if (ex := executed_e2e(r, test_dirs))]
    wall = _wall_s(run)
    # A slice covers fewer nodes than the suite by design (`run.slice` says so); only a full-tier run is held to its count.
    fresh = ({"status": "slice", "run_nodes": len(e2e) + len(skipped), "suite_nodes": suite_nodes, "reason": slice_note}
             if slice_note else stale_nodes(len(e2e) + len(skipped), suite_nodes))
    status, reason = ("ok" if e2e else "unknown"), (None if e2e else f"no executed node under {', '.join(test_dirs)} in the last completed run")
    if e2e and fresh["status"] == "stale":
        status, reason = "stale", fresh["reason"]
    return {
        "status": status,
        "reason": reason,
        "freshness": fresh,
        "source": {**g["source"], "run_log": str(path)},  # type: ignore[dict-item]
        "run": {"started": run["started"].isoformat() if run.get("started") else None,  # type: ignore[union-attr]
                "finished": run["finished"].isoformat() if run.get("finished") else None,  # type: ignore[union-attr]
                "wall_s": round(wall, 1) if wall is not None else None,
                "complete": run["complete"], "exit_status": run["exit_status"],
                "routed_tier": run.get("routed_tier"), "slice": slice_note,
                "checkout": checkout_of(path),
                "skip_reasons": skip_reason_counts(run, skipped),
                "nodes": len(nodes), "e2e_nodes": len(e2e), "e2e_skipped": len(skipped),
                "e2e_summed_s": round(sum(e2e.values()), 1)},
        "load": load,
        "phases": run["phases"],
        "projections": projections(e2e),
        "buckets": buckets(e2e),
        "modules": modules(e2e, repo_root, test_dirs),
        "tail": tail(e2e),
        "first_node": first_node(e2e, earlier_executed),
        "waits": rank_waits(wait_sites(repo_root, test_dirs), e2e, repo_root),
        "slow_nodes": slow_nodes(e2e),
        "app_timers": app_timers(repo_root, test_dirs),
        "slow_fixtures": slow_fixtures(repo_root, test_dirs),
        "projection_fit": projection_fit(e2e, repo_root),
        "runtime_drift": runtime_drift(repo_root, run, str(load["state"])),
    }


_RED_WORDS_RE = re.compile(r"\b(red|reds|fail\w*|flak\w*|rerun\w*|re-run\w*|timed? ?out|race)\b", re.I)
_TEST_NAME_RE = re.compile(r"\b(test_\w+)(\[[^\]\s]+\])?")


def text_mentions(text: str) -> List[Dict[str, str]]:
    """Tests named on a line that also talks about a red, a flake, a race or a rerun."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        if not _RED_WORDS_RE.search(line):
            continue
        for m in _TEST_NAME_RE.finditer(line):
            nodeid = m.group(1) + (m.group(2) or "")
            if nodeid not in seen:
                seen.add(nodeid)
                out.append({"test": m.group(1), "nodeid": nodeid, "projection": projection_of(nodeid),
                            "line": line.strip()[:200]})
    return out


def gh_mentions(repo_root: Path, prs: int) -> Dict[str, object]:
    """Failure mentions in merged-PR bodies and `bug` issues. Free text, so a lower bound."""
    events: List[Dict[str, object]] = []
    slug = repo_slug(repo_root)
    if slug is None:
        return {"status": {"prs": "unknown: no GitHub origin remote", "bug_issues": "unknown: no GitHub origin remote"},
                "events": events}
    status: Dict[str, str] = {}
    queries = (
        ("prs", ["pr", "list", "--state", "merged", "--limit", str(prs), "--json", "number,body,mergedAt"]),
        ("bug_issues", ["issue", "list", "--label", "bug", "--state", "all", "--limit", "400",
                        "--json", "number,title,body,createdAt"]),
    )
    for label, args in queries:
        res = git_run.run_gh([*args, "--repo", slug], timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            status[label] = "unknown: " + ((res.stderr or "").strip().splitlines() or ["gh failed"])[0][:160]
            continue
        try:
            items = json.loads(res.stdout or "[]")
        except ValueError:
            status[label] = "unknown: unparsable gh output"
            continue
        status[label] = f"ok ({len(items)} read)"
        for it in items:
            src = f"{'pr' if label == 'prs' else 'issue'}#{it['number']}"
            date = str(it.get("mergedAt") or it.get("createdAt") or "")[:10] or None
            for m in text_mentions(f"{it.get('title') or ''}\n{it.get('body') or ''}"):
                events.append({**m, "date": date, "source": src})
    return {"status": status, "events": events}


def failures(repo_root: Path, log: Optional[Path] = None, prs: int = 60, use_gh: bool = True) -> Dict[str, object]:
    """Every failure event in the logs on disk and in GitHub text, plus the race candidates."""
    g = _gather(repo_root, log)
    log_events = [] if "error" in g else failure_events(g["logs"])  # type: ignore[arg-type]
    gh = gh_mentions(repo_root, prs) if use_gh else {"status": {"prs": "skipped", "bug_issues": "skipped"}, "events": []}
    by_proj: Dict[str, int] = {}
    for e in log_events:
        by_proj[str(e["projection"])] = by_proj.get(str(e["projection"]), 0) + 1
    return {
        "logs": {"status": "unknown", "reason": g["error"]} if "error" in g
                else {"status": "ok", "read": [str(p) for p, _ in g["logs"]]},  # type: ignore[union-attr]
        "gh": gh["status"],
        "log_events": log_events,
        "mentions": gh["events"],
        "log_events_by_projection": by_proj,
        "race_candidates": race_candidates(log_events),
    }
