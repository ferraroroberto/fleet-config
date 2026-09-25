"""Value-and-time measurements for the `/e2e-audit` skill (fleet-config#1018).

`e2e_test_audit.py` answers "is the suite too big?" by counting nodes. That
misses where a gate's minutes go: app-launcher's #1215 trim cut nodes by 18%
and browser time by 3.5%, because the time is page loads and PTY spawns, and
merging tests keeps both (app-launcher#1220). This module reads what a gate
already wrote down and reports time and failure history. It never starts a
gate: a full run costs ~37 min and loads the box for everything else.

Timing source, in order (never estimated from node counts):

  1. `--log <path>` on the command line;
  2. `.fleet.toml` `[e2e] progress_log` (app-launcher: `webapp/verify-progress.log`),
     the per-test START/DONE/FAILED log app-launcher's `tests/_progress_log.py`
     writes (#534, #943);
  3. `.fleet.toml` `[e2e] junit_xml`, a JUnit XML the gate writes.

With none of them, every verdict is `unknown (no timing source)`.

`timing <repo-root> [--log <path>]` prints one JSON object:

    status        ok | unknown          reason (when unknown)
    source        {kind, path}
    run           {started, finished, wall_s, complete, exit_status}
    load          {state: quiet|loaded|unknown, scope, overlaps [..], checked [..]}
    phases        [{name, wall_s, nodes}]
    projections   {name: {nodes, seconds, mean_s}}        e2e nodes only
    buckets       [{bucket, nodes, seconds}]               e2e nodes only
    modules       [{module, seconds, nodes, page_loads, pty_refs, real_agent}]  heaviest first
    tail          {slowest_n, slowest_share, top5pct_n, top5pct_share, max_s}
    failures      [{test, nodeid, projection, date, source, when, step}]  every log on disk
    race_candidates [{test, projections, steps [..], events}]
    runtime_drift {status, measured_min, claims [{file, line, text, claimed_min, delta}]}

`load` compares this run's window with every other checkout of the repo
(`git worktree list`) that holds the same progress log: an overlapping run
marks this one `loaded`. Suites in other repos are not visible, which `scope`
says. A loaded run is reported but never used for a budget or drift verdict.

`routing <repo-root> [--prs N] [--until ISO] [--config toml] [--proposed toml]`
imports the repo's own `scripts/classify_e2e.py` (never a copy) and routes
the last N merged PRs' file lists through it: the tier distribution, which
rule labels forced `full` (every PR containing one, and PRs where it was the
only cause), the paths that forced it most, unclassified paths, and paths a
broad rule took although a later, more specific, lower-tier rule matches too
(a README under a static prefix: a free fix). `--proposed` routes the same PRs
through a candidate table and lists every PR whose tier changes.

`parallel <repo-root>` lists static signs that xdist workers would share
state (a port picked and released, fixed log names, a file every process
appends to, real-agent tests outside an `xdist_group` or serial pass; a
worker-id reference or a retry marks one `mitigated`), projects the last
serial run's per-test times onto 2/3/4/6 workers (LPT, x1.15/x1.3/x1.5 load
inflation, per test and per module), and names tests red in a parallel run
but green serially: shared state between tests, never a flake.

The `/e2e` 6b trigger (step 3): `time_budget` gives `within|over|unknown|
undeclared` for the browser leg of the latest quiet full-tier run against
`.fleet.toml` `[e2e] time_budget_s`; `growth` counts test functions since the
last audit's `write_audit_record` (machine-local hooks state); `audit_trigger`
turns nodes, time and growth into one `yes|no|unknown`.

`failures` is the raw material for the judgment layer's classing (real bug /
race / test bug / flake-load / unknown). A test whose log failures stopped at
two or more different steps is listed in `race_candidates`: the app-launcher
#1222 lesson is that such a test is a race until someone proves otherwise,
never a flake by default.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402

BUCKETS: Sequence[Tuple[str, float, float]] = (
    ("<1s", 0.0, 1.0), ("1-3s", 1.0, 3.0), ("3-6s", 3.0, 6.0), ("6-10s", 6.0, 10.0), (">=10s", 10.0, math.inf),
)
KNOWN_PROJECTIONS = ("chromium", "webkit", "firefox")
TOP_MODULES = 10
DRIFT_TOLERANCE = 0.25

_HEADER_RE = re.compile(r"run started (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2}) \+\s*[\d.,]+s\] (.*)$")
# Node ids can hold spaces (a parametrize id with a dict repr); an xdist run appends " [gw2]".
_DONE_RE = re.compile(r"^DONE\s+(.+?) \((\d+(?:\.\d+)?)s\)( \[gw\d+\])?$")
_START_RE = re.compile(r"^START (.+)$")
_FAIL_RE = re.compile(r"^(FAILED|ERROR) \((setup|call|teardown)\) (.+?)(?: \[gw\d+\])?$")
_PHASE_RE = re.compile(r"^==> phase: (.*?)(?:\.\.\.)?$")
_ROUTE_RE = re.compile(r"e2e routing: (?:tier=)?(skip|static|surface|full)\b")
_SESSION_END_RE = re.compile(r"^pytest session finished \(exit status (\d+)\)")
_EXCERPT_PREFIX = "    | "
_STEP_RE = re.compile(r"^((?:[\w.-]+[\\/])*test_\w+\.py):(\d+):")
_MINUTES_RE = re.compile(r"~?\s*(\d+(?:\.\d+)?)\s*(?:min\b|mins\b|minutes\b)", re.I)
_RUNTIME_CONTEXT_RE = re.compile(r"\b(gate|suite|e2e|verify|browser)\b", re.I)


# ---- config -----------------------------------------------------------------


def e2e_table(fleet_toml_text: Optional[str]) -> Dict[str, object]:
    """The `.fleet.toml` `[e2e]` table, or `{}` when absent or unparsable."""
    if not fleet_toml_text:
        return {}
    import tomllib
    try:
        data = tomllib.loads(fleet_toml_text)
    except tomllib.TOMLDecodeError:
        return {}
    e2e = data.get("e2e")
    return e2e if isinstance(e2e, dict) else {}


def timing_source(repo_root: Path, log: Optional[Path]) -> Tuple[Optional[str], Optional[Path], str]:
    """`(kind, path, reason)`: `progress-log` / `junit-xml`, or `(None, None, why)`."""
    if log is not None:
        path = log if log.is_absolute() else repo_root / log
        kind = "junit-xml" if path.suffix.lower() == ".xml" else "progress-log"
        return (kind, path, "--log") if path.is_file() else (None, None, f"--log {path} does not exist")
    toml = repo_root / ".fleet.toml"
    table = e2e_table(toml.read_text(encoding="utf-8", errors="replace") if toml.is_file() else None)
    for key, kind in (("progress_log", "progress-log"), ("junit_xml", "junit-xml")):
        rel = table.get(key)
        if isinstance(rel, str) and rel.strip():
            path = repo_root / rel
            if path.is_file():
                return kind, path, f"[e2e] {key}"
            return None, None, f"[e2e] {key} = {rel!r} does not exist"
    return None, None, "no timing source ([e2e] progress_log / junit_xml undeclared, no --log)"


# ---- progress-log parsing --------------------------------------------------------


def projection_of(nodeid: str) -> str:
    """The browser projection a node id runs under (`[webkit-900]` -> webkit), else `default`."""
    m = re.search(r"\[([^\]]+)\]$", nodeid)
    if m:
        head = m.group(1).split("-", 1)[0].lower()
        if head in KNOWN_PROJECTIONS:
            return head
    return "default"


def test_of(nodeid: str) -> str:
    """The node id without its parametrize id: one test across projections."""
    return re.sub(r"\[[^\]]*\]$", "", nodeid)


def split_runs(text: str) -> List[str]:
    """One chunk per `run started` header; a log without headers is one run."""
    starts = [m.start() for m in re.finditer(r"^.*run started \d{4}-\d{2}-\d{2}", text, re.M)]
    if not starts:
        return [text] if text.strip() else []
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def parse_run(text: str) -> Dict[str, object]:
    """One gate run from a progress log: nodes, phases, failures, window, completeness."""
    h = _HEADER_RE.search(text)
    day = _dt.date.fromisoformat(h.group(1)) if h else None
    started = _dt.datetime.combine(day, _dt.time.fromisoformat(h.group(2))) if h else None
    last: Optional[_dt.datetime] = started
    phases: List[Dict[str, object]] = []
    nodes: Dict[str, float] = {}
    node_phase: Dict[str, int] = {}
    open_nodes: set = set()
    failures: List[Dict[str, object]] = []
    exit_status: Optional[int] = None
    parallel = False
    routed_tier: Optional[str] = None
    current_fail: Optional[Dict[str, object]] = None
    for raw in text.splitlines():
        if raw.startswith(_EXCERPT_PREFIX):
            if current_fail is not None:
                current_fail["excerpt"].append(raw[len(_EXCERPT_PREFIX):])  # type: ignore[union-attr]
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        current_fail = None
        body = m.group(4)
        if day is not None and last is not None:
            stamp = _dt.datetime.combine(last.date(), _dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            if stamp < last - _dt.timedelta(hours=12):  # past midnight
                stamp += _dt.timedelta(days=1)
            last = stamp
        if routed_tier is None and (rm := _ROUTE_RE.search(body)):
            routed_tier = rm.group(1)
        if (pm := _PHASE_RE.match(body)):
            phases.append({"name": pm.group(1).strip(), "at": last, "nodes": 0})
        elif (sm := _START_RE.match(body)):
            open_nodes.add(sm.group(1))
        elif (dm := _DONE_RE.match(body)):
            nodes[dm.group(1)] = float(dm.group(2))
            parallel = parallel or bool(dm.group(3))
            open_nodes.discard(dm.group(1))
            if phases:
                node_phase[dm.group(1)] = len(phases) - 1
                phases[-1]["nodes"] = int(phases[-1]["nodes"]) + 1  # type: ignore[call-overload]
        elif (fm := _FAIL_RE.match(body)):
            current_fail = {"outcome": fm.group(1), "when": fm.group(2), "nodeid": fm.group(3), "excerpt": []}
            failures.append(current_fail)
        elif (em := _SESSION_END_RE.match(body)):
            exit_status = int(em.group(1)) if exit_status in (None, 0) else exit_status
    for f in failures:
        f["step"] = failure_step(f.pop("excerpt"))  # type: ignore[arg-type]
    out_phases = []
    for i, p in enumerate(phases):
        nxt = phases[i + 1]["at"] if i + 1 < len(phases) else last
        wall = (nxt - p["at"]).total_seconds() if (p["at"] and nxt) else None  # type: ignore[operator]
        out_phases.append({"name": p["name"], "wall_s": round(wall, 1) if wall is not None else None, "nodes": p["nodes"]})
    return {
        "started": started, "finished": last, "nodes": nodes, "node_phase": node_phase, "phases": out_phases,
        "failures": failures, "exit_status": exit_status, "parallel": parallel, "routed_tier": routed_tier,
        "complete": bool(nodes) and not open_nodes and exit_status is not None,
    }


def failure_step(excerpt: List[str]) -> Optional[str]:
    """Where a failure stopped: the last `test_*.py:<line>:` frame in its traceback excerpt."""
    step = None
    for line in excerpt:
        m = _STEP_RE.match(line.strip())
        if m:
            step = f"{m.group(1).replace(chr(92), '/')}:{m.group(2)}"
    return step


def parse_junit(path: Path) -> Dict[str, object]:
    """Per-node seconds and failures from a JUnit XML; no phases, no window."""
    nodes: Dict[str, float] = {}
    failures: List[Dict[str, object]] = []
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        cls = (case.get("classname") or "").replace(".", "/")
        nodeid = f"{cls}.py::{case.get('name')}" if cls else str(case.get("name"))
        nodes[nodeid] = float(case.get("time") or 0.0)
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None:
                failures.append({"outcome": tag.upper(), "when": "call", "nodeid": nodeid,
                                 "step": failure_step((el.text or "").splitlines())})
    return {"started": None, "finished": None, "nodes": nodes, "node_phase": {}, "phases": [],
            "failures": failures, "exit_status": 1 if failures else 0, "parallel": False, "routed_tier": None, "complete": bool(nodes)}


def last_complete_run(text: str) -> Optional[Dict[str, object]]:
    for chunk in reversed(split_runs(text)):
        run = parse_run(chunk)
        if run["complete"]:
            return run
    return None


# ---- measurements ------------------------------------------------------------------


def is_e2e(nodeid: str, test_dirs: Sequence[str]) -> bool:
    norm = nodeid.replace("\\", "/")
    return any(norm.startswith(d.strip("/") + "/") for d in test_dirs)


def projections(nodes: Dict[str, float]) -> Dict[str, Dict[str, object]]:
    out: Dict[str, Dict[str, object]] = {}
    for nid, s in nodes.items():
        p = out.setdefault(projection_of(nid), {"nodes": 0, "seconds": 0.0})
        p["nodes"] = int(p["nodes"]) + 1  # type: ignore[call-overload]
        p["seconds"] = float(p["seconds"]) + s  # type: ignore[arg-type]
    for p in out.values():
        p["seconds"] = round(float(p["seconds"]), 1)  # type: ignore[arg-type]
        p["mean_s"] = round(float(p["seconds"]) / int(p["nodes"]), 2) if p["nodes"] else None  # type: ignore[arg-type,call-overload]
    return out


def buckets(nodes: Dict[str, float]) -> List[Dict[str, object]]:
    out = []
    for name, lo, hi in BUCKETS:
        vals = [s for s in nodes.values() if lo <= s < hi]
        out.append({"bucket": name, "nodes": len(vals), "seconds": round(sum(vals), 1)})
    return out


def tail(nodes: Dict[str, float], slowest_n: int = 22) -> Dict[str, object]:
    vals = sorted(nodes.values(), reverse=True)
    total = sum(vals)
    if not vals or total <= 0:
        return {"slowest_n": slowest_n, "slowest_share": None, "top5pct_n": 0, "top5pct_share": None, "max_s": None}
    top5 = max(1, math.ceil(len(vals) * 0.05))
    return {"slowest_n": slowest_n, "slowest_share": round(sum(vals[:slowest_n]) / total, 3),
            "top5pct_n": top5, "top5pct_share": round(sum(vals[:top5]) / total, 3), "max_s": vals[0]}


def cost_drivers(text: str) -> Dict[str, int]:
    """What a module pays for per test, counted statically: page loads, PTY references, real-agent marks."""
    return {
        "page_loads": len(re.findall(r"\.(?:goto|reload)\(", text)),
        "pty_refs": len(re.findall(r"\bpty\b", text, re.I)),
        "real_agent": len(re.findall(r"real[_-]agent", text, re.I)),
    }


def modules(nodes: Dict[str, float], repo_root: Path, top: int = TOP_MODULES) -> List[Dict[str, object]]:
    agg: Dict[str, List[float]] = {}
    for nid, s in nodes.items():
        agg.setdefault(nid.split("::", 1)[0], []).append(s)
    ranked = sorted(agg.items(), key=lambda kv: -sum(kv[1]))[:top]
    out = []
    for mod, vals in ranked:
        path = repo_root / mod
        drivers = cost_drivers(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else {}
        out.append({"module": mod, "seconds": round(sum(vals), 1), "nodes": len(vals), **drivers})
    return out


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


def runtime_claims(text: str, file: str) -> List[Dict[str, object]]:
    """Runtime figures ("~19 min") on lines that talk about the gate or suite."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if not _RUNTIME_CONTEXT_RE.search(line):
            continue
        for m in _MINUTES_RE.finditer(line):
            out.append({"file": file, "line": i, "text": line.strip()[:160], "claimed_min": float(m.group(1))})
    return out


def runtime_drift(repo_root: Path, run: Dict[str, object], load_state: str) -> Dict[str, object]:
    """Runtime figures in CLAUDE.md / README against the run's measured spans.

    The deterministic half only: every figure on a line about the gate or
    suite, with its distance from the nearest measured span (the whole gate or
    one phase). Which span a figure means ("non-e2e ~4 min" vs "full suite ~25
    min") is the judgment layer's call; `candidate` marks a figure more than
    25% from every span. A loaded run gives no verdict.
    """
    wall = _wall_s(run)
    if wall is None:
        return {"status": "unknown", "reason": "the run's wall time is not in the log", "claims": []}
    spans = {"gate": wall / 60.0}
    for ph in run.get("phases") or []:  # type: ignore[union-attr]
        if ph.get("nodes") and ph.get("wall_s"):
            spans[str(ph["name"])[:40]] = float(ph["wall_s"]) / 60.0
    claims: List[Dict[str, object]] = []
    for name in ("CLAUDE.md", "README.md"):
        p = repo_root / name
        if p.is_file():
            claims += runtime_claims(p.read_text(encoding="utf-8", errors="replace"), name)
    for c in claims:
        claimed = float(c["claimed_min"])  # type: ignore[arg-type]
        nearest = min(spans, key=lambda k: abs(claimed - spans[k]) / spans[k] if spans[k] else math.inf)
        rel = (claimed - spans[nearest]) / spans[nearest] if spans[nearest] else None
        c["nearest_span"], c["delta"] = nearest, (round(rel, 2) if rel is not None else None)
        c["candidate"] = rel is not None and abs(rel) > DRIFT_TOLERANCE
    measured = {k: round(v, 1) for k, v in spans.items()}
    if load_state != "quiet":
        return {"status": "unknown", "reason": f"run is {load_state}; a drift verdict needs a quiet run",
                "measured_min": measured, "claims": claims}
    status = "candidates" if any(c["candidate"] for c in claims) else ("none" if claims else "no-claims")
    return {"status": status, "measured_min": measured, "claims": claims}


def _wall_s(run: Dict[str, object]) -> Optional[float]:
    a, b = run.get("started"), run.get("finished")
    return (b - a).total_seconds() if isinstance(a, _dt.datetime) and isinstance(b, _dt.datetime) else None  # type: ignore[operator]


# ---- sibling checkouts: load and history ------------------------------------------------


def sibling_logs(repo_root: Path, rel_log: Optional[str]) -> List[Path]:
    """The same progress log in every other checkout of this repo (`git worktree list`)."""
    if not rel_log:
        return []
    res = git_run.run_git(["-C", str(repo_root), "worktree", "list", "--porcelain"], timeout=30)
    if res.returncode != 0:
        return []
    here = repo_root.resolve()
    out = []
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            wt = Path(line[len("worktree "):].strip())
            if wt.resolve() != here and (wt / rel_log).is_file():
                out.append(wt / rel_log)
    return out


def load_state(run: Dict[str, object], others: List[Tuple[Path, List[Dict[str, object]]]]) -> Dict[str, object]:
    a, b = run.get("started"), run.get("finished")
    scope = "other checkouts of this repo; suites in other repos are not visible"
    if not (isinstance(a, _dt.datetime) and isinstance(b, _dt.datetime)):
        return {"state": "unknown", "reason": "the run has no start/finish time", "scope": scope, "overlaps": [], "checked": []}
    overlaps = []
    for path, runs in others:
        for r in runs:
            c, d = r.get("started"), r.get("finished")
            if isinstance(c, _dt.datetime) and isinstance(d, _dt.datetime) and c < b and a < d:
                overlaps.append({"log": str(path), "started": c.isoformat(), "finished": d.isoformat()})
    return {"state": "loaded" if overlaps else "quiet", "scope": scope, "overlaps": overlaps,
            "checked": [str(p) for p, _ in others]}


def failure_events(logs: List[Tuple[Path, List[Dict[str, object]]]]) -> List[Dict[str, object]]:
    events = []
    for path, runs in logs:
        for r in runs:
            day = r["started"].date().isoformat() if isinstance(r.get("started"), _dt.datetime) else None  # type: ignore[union-attr]
            for f in r["failures"]:  # type: ignore[union-attr]
                nid = str(f["nodeid"])
                events.append({"test": test_of(nid), "nodeid": nid, "projection": projection_of(nid), "date": day,
                               "source": str(path), "when": f["when"], "step": f.get("step")})
    return events




# ---- the subcommands -------------------------------------------------------------------------


def _gather(repo_root: Path, log: Optional[Path]) -> Dict[str, object]:
    """Resolve the source; parse every run in it and in the other checkouts' copies."""
    kind, path, why = timing_source(repo_root, log)
    if kind is None or path is None:
        return {"error": why}
    source = {"kind": kind, "path": str(path), "from": why}
    if kind == "junit-xml":
        try:
            return {"source": source, "logs": [(path, [parse_junit(path)])]}
        except ET.ParseError as exc:
            return {"error": f"JUnit XML unparsable: {exc}", "source": source}
    rel = None
    try:
        rel = str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        pass
    logs = [(p, [parse_run(c) for c in split_runs(p.read_text(encoding="utf-8", errors="replace"))])
            for p in [path] + sibling_logs(repo_root, rel)]
    return {"source": source, "logs": logs}


def timing(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None) -> Dict[str, object]:
    g = _gather(repo_root, log)
    if "error" in g:
        return {"status": "unknown", "reason": g["error"], "source": g.get("source")}
    logs: List[Tuple[Path, List[Dict[str, object]]]] = g["logs"]  # type: ignore[assignment]
    # The latest completed run in any checkout: gates run in worktrees too, so the primary's copy goes stale.
    done = [(p, r) for p, runs in logs for r in runs if r["complete"]]
    if not done:
        return {"status": "unknown", "reason": "no completed gate run in any checkout's log", "source": g["source"]}
    path, run = max(done, key=lambda pr: pr[1]["finished"] or _dt.datetime.min)  # type: ignore[arg-type,return-value]
    if g["source"]["kind"] == "junit-xml":  # type: ignore[index]
        load: Dict[str, object] = {"state": "unknown", "reason": "a JUnit XML carries no run window",
                                   "scope": "", "overlaps": [], "checked": []}
    else:
        load = load_state(run, [(p, [r for r in runs if r is not run]) for p, runs in logs])
    nodes: Dict[str, float] = run["nodes"]  # type: ignore[assignment]
    e2e = {n: s for n, s in nodes.items() if is_e2e(n, test_dirs)}
    wall = _wall_s(run)
    return {
        "status": "ok" if e2e else "unknown",
        "reason": None if e2e else f"no node under {', '.join(test_dirs)} in the last completed run",
        "source": {**g["source"], "run_log": str(path)},  # type: ignore[dict-item]
        "run": {"started": run["started"].isoformat() if run.get("started") else None,  # type: ignore[union-attr]
                "finished": run["finished"].isoformat() if run.get("finished") else None,  # type: ignore[union-attr]
                "wall_s": round(wall, 1) if wall is not None else None,
                "complete": run["complete"], "exit_status": run["exit_status"],
                "nodes": len(nodes), "e2e_nodes": len(e2e), "e2e_summed_s": round(sum(e2e.values()), 1)},
        "load": load,
        "phases": run["phases"],
        "projections": projections(e2e),
        "buckets": buckets(e2e),
        "modules": modules(e2e, repo_root),
        "tail": tail(e2e),
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


def repo_slug(repo_root: Path) -> Optional[str]:
    """`owner/repo` from the checkout's GitHub `origin` remote, else None."""
    res = git_run.run_git(["-C", str(repo_root), "remote", "get-url", "origin"], timeout=30)
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", res.stdout.strip()) if res.returncode == 0 else None
    return m.group(1) if m else None


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
        res = git_run.run_gh([*args, "--repo", slug], timeout=120, stdin=subprocess.DEVNULL)
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


# ---- routing report (step 2) ----------------------------------------------------------------


def load_classifier(repo_root: Path):
    """The repo's own `scripts/classify_e2e.py`, imported read-only, or None.

    Imported, never reimplemented, so the report cannot drift from what the
    gate actually does (the file is byte-verbatim from project-scaffolding).
    """
    path = repo_root / "scripts" / "classify_e2e.py"
    if not path.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"classify_e2e_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(mod)
    except Exception:  # a broken classifier is `unknown`, reported by the caller
        sys.modules.pop(spec.name, None)
        return None
    return mod if hasattr(mod, "classify") and hasattr(mod, "load_config") else None


def merged_prs(repo_root: Path, prs: int, until: Optional[str] = None) -> Tuple[Optional[List[Dict[str, object]]], str]:
    """`(prs newest first, status)`: number, mergedAt and file paths of merged PRs."""
    slug = repo_slug(repo_root)
    if slug is None:
        return None, "unknown: no GitHub origin remote"
    limit = prs + (200 if until else 0)
    res = git_run.run_gh(["pr", "list", "--state", "merged", "--limit", str(limit), "--repo", slug,
                          "--json", "number,mergedAt,files"], timeout=180, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        return None, "unknown: " + ((res.stderr or "").strip().splitlines() or ["gh failed"])[0][:160]
    try:
        items = json.loads(res.stdout or "[]")
    except ValueError:
        return None, "unknown: unparsable gh output"
    items.sort(key=lambda it: str(it.get("mergedAt") or ""), reverse=True)
    if until:
        items = [it for it in items if str(it.get("mergedAt") or "") <= until]
    items = items[:prs]
    out = [{"number": it["number"], "mergedAt": it.get("mergedAt"),
            "files": [f["path"] for f in (it.get("files") or []) if f.get("path")]} for it in items]
    return out, f"ok ({len(out)} PRs)"


def _more_specific(later, first) -> bool:
    """Whether a later rule names a path more narrowly than the one that took it.

    An exact path always does; a pure extension rule (`*.md`, no prefix) does
    against a prefix rule, since it names a file type the prefix never meant;
    between two prefixes, the longer one does.
    """
    if later.path is not None:
        return first.path is None
    if later.prefix is None:
        return bool(later.extensions) and first.prefix is not None
    return first.prefix is not None and len(later.prefix) > len(first.prefix)


def shadowed_rules(path: str, rules: list) -> List[str]:
    """Labels of later, lower-tier, more specific rules that also match a path an earlier rule took.

    The first-match-wins table can route a README full because a broad prefix
    rule sits above the docs rule (app-launcher#1220 (b)): a misroute the table
    fixes for free by reordering. A general rule placed after a specific one
    (`tests/` after `tests/e2e/`) is the intended order and is not reported.
    """
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    hits = [r for r in rules if r.matches(path, ext)]
    if len(hits) < 2:
        return []
    first = hits[0]
    return [r.label for r in hits[1:] if r.tier < first.tier and _more_specific(r, first)]


def routing_report(repo_root: Path, prs: int = 60, until: Optional[str] = None,
                   config_path: Optional[Path] = None, proposed_path: Optional[Path] = None,
                   pr_list: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
    mod = load_classifier(repo_root)
    if mod is None:
        return {"status": "unknown", "reason": "no importable scripts/classify_e2e.py in the repo"}
    config = mod.load_config(config_path or (repo_root / ".fleet.toml"))
    if pr_list is None:
        pr_list, why = merged_prs(repo_root, prs, until)
        if pr_list is None:
            return {"status": "unknown", "reason": f"merged PRs: {why}"}
    proposed = mod.load_config(proposed_path) if proposed_path else None
    tiers: Dict[str, int] = {}
    full_classes: Dict[str, int] = {}
    single_cause: Dict[str, int] = {}
    full_paths: Dict[str, int] = {}
    unclassified: Dict[str, int] = {}
    shadowed: Dict[str, Dict[str, object]] = {}
    narrowed: List[Dict[str, object]] = []
    rows = []
    browser_relevant = 0
    for pr in pr_list:
        files = [str(f) for f in pr["files"]]  # type: ignore[union-attr]
        r = mod.classify(files, config)
        tiers[r.tier] = tiers.get(r.tier, 0) + 1
        cats = [(f, *mod._classify_one(f.replace("\\", "/"), config.rules)) for f in files]
        if any(c.name != "NONE" for _, c, _ in cats):
            browser_relevant += 1
        if r.tier == "full":
            labels = sorted({lab for _, c, lab in cats if c.name == "FULL"})
            for lab in labels:
                full_classes[lab] = full_classes.get(lab, 0) + 1
            if len(labels) == 1:
                single_cause[labels[0]] = single_cause.get(labels[0], 0) + 1
            for f, c, _ in cats:
                if c.name == "FULL":
                    full_paths[f] = full_paths.get(f, 0) + 1
        for f, c, lab in cats:
            if lab == "unclassified":
                unclassified[f] = unclassified.get(f, 0) + 1
            sh = shadowed_rules(f.replace("\\", "/"), config.rules)
            if sh:
                entry = shadowed.setdefault(f, {"path": f, "took": lab, "shadowed": sh, "prs": 0})
                entry["prs"] = int(entry["prs"]) + 1  # type: ignore[call-overload]
        if proposed is not None:
            pr_ = mod.classify(files, proposed)
            if pr_.tier != r.tier:
                narrowed.append({"pr": pr["number"], "from": r.tier, "to": pr_.tier, "surface": pr_.surface})
        rows.append({"pr": pr["number"], "tier": r.tier, "surface": r.surface})
    full_n = tiers.get("full", 0)
    return {
        "status": "ok",
        "prs": len(pr_list),
        "config": str(config_path or (repo_root / ".fleet.toml")), "config_source": config.source,
        "tiers": {k: tiers.get(k, 0) for k in ("skip", "static", "surface", "full")},
        "browser_relevant": browser_relevant,
        "browser_relevant_full": full_n,
        "full_classes": dict(sorted(full_classes.items(), key=lambda kv: -kv[1])),
        "single_cause": dict(sorted(single_cause.items(), key=lambda kv: -kv[1])),
        "full_paths_top": [{"path": p, "prs": n} for p, n in sorted(full_paths.items(), key=lambda kv: -kv[1])[:10]],
        "unclassified": [{"path": p, "prs": n} for p, n in sorted(unclassified.items(), key=lambda kv: -kv[1])],
        "shadowed": sorted(shadowed.values(), key=lambda e: -int(e["prs"])),  # type: ignore[arg-type,call-overload]
        "counterfactual": None if proposed is None else {"proposed": str(proposed_path), "changed": narrowed},
        "per_pr": rows,
    }


# ---- parallelisability (step 2) -------------------------------------------------------------


INFLATION = (1.15, 1.3, 1.5)
WORKER_COUNTS = (2, 3, 4, 6)


def lpt(durations: Sequence[float], workers: int) -> float:
    """Makespan of a longest-processing-time-first schedule onto `workers` bins."""
    if workers <= 0:
        return math.inf
    bins = [0.0] * workers
    for d in sorted(durations, reverse=True):
        i = bins.index(min(bins))
        bins[i] += d
    return max(bins) if bins else 0.0


def projection(nodes: Dict[str, float]) -> Dict[str, object]:
    """Serial time, LPT makespans at 2/3/4/6 workers with load inflation, and the two floors."""
    if not nodes:
        return {"status": "unknown", "reason": "no per-test durations"}
    by_mod: Dict[str, float] = {}
    for n, s in nodes.items():
        by_mod[n.split("::", 1)[0]] = by_mod.get(n.split("::", 1)[0], 0.0) + s
    serial = sum(nodes.values())
    table = []
    for w in WORKER_COUNTS:
        load = lpt(list(nodes.values()), w)
        scope = lpt(list(by_mod.values()), w)
        table.append({"workers": w, "load_s": [round(load * f, 1) for f in INFLATION],
                      "loadscope_s": [round(scope * f, 1) for f in INFLATION]})
    heavy_mod = max(by_mod.items(), key=lambda kv: kv[1])
    slow = max(nodes.items(), key=lambda kv: kv[1])
    return {"status": "ok", "serial_s": round(serial, 1), "inflation": list(INFLATION), "table": table,
            "floor_loadscope": {"module": heavy_mod[0], "seconds": round(heavy_mod[1], 1)},
            "floor_load": {"test": slow[0], "seconds": slow[1]}}


_BIND0_RE = re.compile(r"\.bind\(\s*\(\s*[\"'][^\"']*[\"']\s*,\s*0\s*\)\s*\)")
# A retry around the port pick itself: a loop over attempts whose body re-picks a free port.
_PORT_RETRY_RE = re.compile(r"for\s+\w*attempt\w*\s+in[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*free\w*port", re.I)
_WORKER_RE = re.compile(r"PYTEST_XDIST_WORKER|worker_id\b|workerinput")
_LOG_NAME_RE = re.compile(r"[\"']([\w./-]*[\w-]+\.log)[\"']")
_APPEND_RE = re.compile(r"open\([^)]*,\s*[\"']a[\"']")
_SESSION_FIX_RE = re.compile(r"@pytest\.fixture\([^)]*scope\s*=\s*[\"']session[\"'][^)]*\)\s*\n\s*def (\w+)")
_MODULE_STATE_RE = re.compile(r"^[a-z_]\w*\s*(?::[^=]+)?=\s*(\{|\[|set\(|dict\(|list\()", re.M)
_LOAD_SENSITIVE_RE = re.compile(r"real[_-]agent", re.I)
_GROUPED_RE = re.compile(r"xdist_group|mark\.serial\b")


def _test_tree_files(repo_root: Path, test_dirs: Sequence[str]) -> List[Path]:
    """Every module under the e2e test dirs, plus the `tests/` conftest and `_*.py` plugins it loads."""
    seen: Dict[str, Path] = {}
    for d in test_dirs:
        base = repo_root / d.strip("/")
        if base.is_dir():
            for p in base.glob("**/*.py"):
                seen[str(p.resolve())] = p
    tests = repo_root / "tests"
    for p in ([tests / "conftest.py", *sorted(tests.glob("_*.py"))] if tests.is_dir() else []):
        if p.is_file() and p.name != "__init__.py":
            seen[str(p.resolve())] = p
    return sorted(seen.values())


def xdist_state(repo_root: Path) -> Dict[str, object]:
    venv = repo_root / ".venv"
    site = [venv / "Lib" / "site-packages", *sorted((venv / "lib").glob("python*/site-packages"))]
    installed: Optional[bool] = None
    if venv.is_dir():
        installed = any((s / "xdist").is_dir() for s in site)
    declared = False
    for req in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        p = repo_root / req
        if p.is_file() and re.search(r"pytest[-_]xdist", p.read_text(encoding="utf-8", errors="replace"), re.I):
            declared = True
    return {"installed": "unknown (no .venv)" if installed is None else installed, "declared": declared}


def parallel_blockers(repo_root: Path, test_dirs: Sequence[str]) -> Dict[str, object]:
    """Static signs a suite would share state between xdist workers (app-launcher#1220 (c)).

    Each entry is a candidate for the judgment layer, with the file and why.
    A worker-id reference or a retry beside the risky shape marks it
    `mitigated` rather than a blocker.
    """
    out: Dict[str, List[Dict[str, object]]] = {k: [] for k in (
        "free_port_race", "fixed_log_names", "shared_append", "load_sensitive_ungrouped",
        "session_fixtures", "module_state")}
    for p in _test_tree_files(repo_root, test_dirs):
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        worker_aware = bool(_WORKER_RE.search(text))
        if _BIND0_RE.search(text):
            out["free_port_race"].append({"file": rel, "state": "mitigated" if _PORT_RETRY_RE.search(text) else "blocker",
                                          "why": "binds port 0, releases it, then a child binds it"})
        logs = sorted(set(_LOG_NAME_RE.findall(text)))
        if logs:
            out["fixed_log_names"].append({"file": rel, "names": logs, "state": "mitigated" if worker_aware else "blocker"})
        if _APPEND_RE.search(text):
            out["shared_append"].append({"file": rel, "state": "mitigated" if worker_aware else "blocker",
                                         "why": "appends to a file every worker process would write"})
        name = p.name
        if name.startswith("test_") and _LOAD_SENSITIVE_RE.search(text):
            out["load_sensitive_ungrouped"].append({"file": rel, "state": "mitigated" if _GROUPED_RE.search(text) else "blocker",
                                                    "why": "real-agent test: needs one xdist_group or a serial pass"})
        for fx in _SESSION_FIX_RE.findall(text):
            out["session_fixtures"].append({"file": rel, "fixture": fx, "state": "info",
                                            "per_worker_tmp": "tmp_path_factory" in text})
        n = len(_MODULE_STATE_RE.findall(text))
        if n and (name == "conftest.py" or name.startswith("_")):
            out["module_state"].append({"file": rel, "count": n, "state": "info"})
    blockers = sum(1 for v in out.values() for e in v if e["state"] == "blocker")
    return {"xdist": xdist_state(repo_root), "blockers": blockers, **out}


def shared_state_evidence(logs: List[Tuple[Path, List[Dict[str, object]]]]) -> List[Dict[str, object]]:
    """Tests red in a parallel run and green in a serial one: shared state, never a flake (app-launcher#1231)."""
    red_parallel: Dict[str, List[str]] = {}
    green_serial: set = set()
    for _path, runs in logs:
        for r in runs:
            failed = {str(f["nodeid"]) for f in r["failures"]}  # type: ignore[union-attr]
            if r.get("parallel"):
                for nid in failed:
                    red_parallel.setdefault(nid, []).append(str(r.get("started")))
            else:
                green_serial.update(n for n in r["nodes"] if n not in failed)  # type: ignore[union-attr]
    return [{"nodeid": n, "parallel_reds": len(v), "green_serially": True}
            for n, v in sorted(red_parallel.items()) if n in green_serial]


def parallel(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None) -> Dict[str, object]:
    g = _gather(repo_root, log)
    proj: Dict[str, object]
    evidence: List[Dict[str, object]] = []
    if "error" in g:
        proj = {"status": "unknown", "reason": g["error"]}
    else:
        logs: List[Tuple[Path, List[Dict[str, object]]]] = g["logs"]  # type: ignore[assignment]
        # The projection needs serial durations: a parallel run's per-test times are inflated by its own load.
        serial = [r for _, runs in logs for r in runs if r["complete"] and not r.get("parallel")]
        if serial:
            run = max(serial, key=lambda r: r["finished"] or _dt.datetime.min)  # type: ignore[arg-type,return-value]
            e2e = {n: s for n, s in run["nodes"].items() if is_e2e(n, test_dirs)}  # type: ignore[union-attr]
            proj = {**projection(e2e), "run": run["started"].isoformat() if run.get("started") else None}  # type: ignore[union-attr]
        else:
            proj = {"status": "unknown", "reason": "no completed serial run in any checkout's log"}
        evidence = shared_state_evidence(logs)
    return {"static": parallel_blockers(repo_root, test_dirs), "projection": proj, "shared_state": evidence}


# ---- time budget and the growth baseline (step 3) ------------------------------------------------

GROWTH_THRESHOLD = 10


def time_budget_limit(fleet_toml_text: Optional[str]) -> Tuple[Optional[int], str]:
    """`(limit_s, note)` from `.fleet.toml` `[e2e] time_budget_s`: a positive int, else None.

    Same validation as the node budget: a bool, a string, 0 or a negative is
    ignored with a note, so a typo can never silently raise the bar.
    """
    raw = e2e_table(fleet_toml_text).get("time_budget_s")
    if raw is None:
        return None, "no [e2e] time_budget_s declared"
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None, f"invalid [e2e] time_budget_s {raw!r} ignored"
    return raw, "[e2e] time_budget_s"


def browser_leg_s(run: Dict[str, object], test_dirs: Sequence[str]) -> Optional[float]:
    """Wall seconds of the phases that ran e2e nodes (a parallel pass and a serial pass both count)."""
    node_phase: Dict[str, int] = run.get("node_phase") or {}  # type: ignore[assignment]
    phases: List[Dict[str, object]] = run.get("phases") or []  # type: ignore[assignment]
    idx = {node_phase[n] for n in run.get("nodes") or {} if is_e2e(n, test_dirs) and n in node_phase}  # type: ignore[union-attr]
    walls = [phases[i].get("wall_s") for i in sorted(idx)]
    if not idx or any(w is None for w in walls):
        return None
    return round(sum(float(w) for w in walls), 1)  # type: ignore[arg-type]


def time_budget(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None) -> Dict[str, object]:
    """`within` / `over` / `unknown` / `undeclared` for the browser leg of the latest quiet full-tier run.

    A run the log records as routed below `full` is not the suite; a run that
    overlapped another checkout's run is loaded. Neither gives a verdict, and
    neither does a missing log: all three are `unknown`, never `within`.
    """
    toml = repo_root / ".fleet.toml"
    limit, note = time_budget_limit(toml.read_text(encoding="utf-8", errors="replace") if toml.is_file() else None)
    if limit is None:
        return {"verdict": "undeclared", "seconds": None, "limit": None, "reason": note}
    g = _gather(repo_root, log)
    if "error" in g:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": str(g["error"])}
    logs: List[Tuple[Path, List[Dict[str, object]]]] = g["logs"]  # type: ignore[assignment]
    full = [(p, r) for p, runs in logs for r in runs
            if r["complete"] and r.get("routed_tier") in (None, "full") and isinstance(r.get("finished"), _dt.datetime)]
    if not full:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": "no completed full-tier run with a run window"}
    path, run = max(full, key=lambda pr: pr[1]["finished"])  # type: ignore[arg-type,return-value]
    load = load_state(run, [(p, [r for r in runs if r is not run]) for p, runs in logs])
    seconds = browser_leg_s(run, test_dirs)
    when = run["started"].isoformat() if run.get("started") else "?"  # type: ignore[union-attr]
    if load["state"] != "quiet":
        return {"verdict": "unknown", "seconds": seconds, "limit": limit, "reason": f"the latest full run ({when}) was {load['state']}"}
    if seconds is None:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": f"no e2e phase wall time in the run of {when}"}
    return {"verdict": "over" if seconds > limit else "within", "seconds": seconds, "limit": limit,
            "reason": f"browser leg {seconds:.0f} s on the quiet full run of {when} vs {limit} s ({note}); {path}"}


def audit_record_path(repo_root: Path) -> Path:
    """Where the last audit's test count lives: machine-local hooks state, one file per repo."""
    from hooks_state import state_dir
    name = (repo_slug(repo_root) or repo_root.resolve().name).replace("/", "-")
    return state_dir() / "e2e-audit" / f"{name}.json"


def read_audit_record(repo_root: Path) -> Optional[Dict[str, object]]:
    p = audit_record_path(repo_root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("raw_tests"), int) else None


def write_audit_record(repo_root: Path, raw_tests: int, node_count: Optional[int]) -> Path:
    p = audit_record_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    res = git_run.run_git(["-C", str(repo_root), "rev-parse", "--short", "HEAD"], timeout=30)
    rec = {"raw_tests": raw_tests, "node_count": node_count,
           "date": _dt.datetime.now(_dt.timezone.utc).date().isoformat(),
           "sha": res.stdout.strip() if res.returncode == 0 else None}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(p)
    return p


def growth(raw_tests: int, record: Optional[Dict[str, object]], threshold: int = GROWTH_THRESHOLD) -> Dict[str, object]:
    """Test functions gained since the last audit; `none-recorded` before the first one."""
    if record is None:
        return {"state": "none-recorded", "delta": None, "since": None, "trigger": False}
    delta = raw_tests - int(record["raw_tests"])  # type: ignore[call-overload]
    return {"state": "measured", "delta": delta, "since": record.get("date"), "trigger": delta >= threshold}


def audit_trigger(node_verdict: str, time_verdict: str, grow: Dict[str, object]) -> Tuple[str, str]:
    """`(yes|no|unknown, reason)`: whether `/e2e` 6b should run `/e2e-audit budget` (fleet-config#1018).

    Any one of: over the node budget, over the time budget, or ~10 new test
    functions since the last audit. An unmeasured leg with nothing else firing
    is `unknown`, never `no`. The open-issue check stays with the caller.
    """
    fired = []
    if node_verdict == "over":
        fired.append("over the node budget")
    if time_verdict == "over":
        fired.append("over the time budget")
    if grow.get("trigger"):
        fired.append(f"{format(int(grow['delta']), '+d')} test functions since the audit of {grow['since']}")
    if fired:
        return "yes", "; ".join(fired)
    unknown = [n for n, v in (("nodes", node_verdict), ("time", time_verdict)) if v in ("unmeasured", "unknown")]
    if unknown:
        return "unknown", f"{' and '.join(unknown)} not measured; nothing else fired"
    if grow.get("state") == "none-recorded":
        return "no", "within every declared budget; no growth baseline yet (an audit's `record` sets it)"
    return "no", f"within every declared budget; {format(int(grow['delta']), '+d')} test functions since {grow['since']}"
