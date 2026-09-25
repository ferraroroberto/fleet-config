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
_DONE_RE = re.compile(r"^DONE\s+(.+?) \((\d+(?:\.\d+)?)s\)(?: \[gw\d+\])?$")
_START_RE = re.compile(r"^START (.+)$")
_FAIL_RE = re.compile(r"^(FAILED|ERROR) \((setup|call|teardown)\) (.+?)(?: \[gw\d+\])?$")
_PHASE_RE = re.compile(r"^==> phase: (.*?)(?:\.\.\.)?$")
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
        if (pm := _PHASE_RE.match(body)):
            phases.append({"name": pm.group(1).strip(), "at": last, "nodes": 0})
        elif (sm := _START_RE.match(body)):
            open_nodes.add(sm.group(1))
        elif (dm := _DONE_RE.match(body)):
            nodes[dm.group(1)] = float(dm.group(2))
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
        "failures": failures, "exit_status": exit_status,
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
            "failures": failures, "exit_status": 1 if failures else 0, "complete": bool(nodes)}


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
