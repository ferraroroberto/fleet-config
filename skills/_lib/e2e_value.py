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
    run           {started, finished, wall_s, complete, exit_status, routed_tier, slice,
                   checkout {root, linked_worktree}, skip_reasons [{reason, nodes}] | null (JUnit only),
                   nodes, e2e_nodes (executed), e2e_skipped, e2e_summed_s}
    load          {state: quiet|loaded|unknown, scope, overlaps [..], checked [..]}
    phases        [{name, wall_s, nodes}]
    projections   {name: {nodes, seconds, mean_s}}        executed e2e nodes only
    buckets       [{bucket, nodes, seconds}]               executed e2e nodes only
    modules       [{module, seconds, nodes, page_loads, shots, pty_refs, real_agent}]  heaviest first
    tail          {slowest_n, slowest_share, top5pct_n, top5pct_share, max_s}
    first_node    {nodeid, seconds, median_s, boot}        carries the session boot; boot: single-run | repeated | not-repeated
    waits         [{file, line, kind, ms, scope, text, nodes, measured_s, paid_s, ceiling}]  paid seconds first, `timeout=` ceilings and `poll-sleep`s last
    slow_nodes    [{nodeid, seconds, median_s}]            executed nodes over 10x the median and 1 s, boot node left out
    app_timers    [{file, line, kind, ms, text}]           literal 1 s+ timers in app JS (tests, vendored, minified left out)
    failures      [{test, nodeid, projection, date, source, when, step}]  every log on disk
    race_candidates [{test, projections, steps [..], events}]
    runtime_drift {status, measured_min, claims [{file, line, text, claimed_min, delta}]}

The run measured is the latest completed full-tier run that executed e2e
nodes; a later backend-only run or surface slice does not displace it, and
when only a slice is on record `run.slice` says so (fleet-config#1134).
Skipped nodes are not executed nodes: they are counted apart.

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
through a candidate table and lists every PR whose tier changes; the counterfactual also re-runs `unclassified`, `shadowed` and `import_holes` against it (`holes_opened` / `holes_closed`). `import_holes`
reads the other way (task-os#287): every file the suite loads (the test dirs,
the conftest, `_*.py` plugins) or imports one level deep, routed through the
same classifier, listing those the table sends to `none`: a diff touching only
a shared fixture would run no browser suite. `gate` is
`e2e_route.gate_contract`: whether the repo's gate runs the classifier at all
and splits its multi-target output (fleet-config#1134). When the
classifier exposes `changed_selectors` and either table declares
`shared_stylesheets`, each PR's changed CSS rules per sheet (merge commit vs
its first parent) ride into both `classify()` calls, so the counterfactual
shows stylesheet narrowing, and each sheet's PRs are bucketed: owned by one
surface, unmapped selector, spans surfaces, no rule changed, unsafe, or
unreadable (fleet-config#1033). An older classifier keeps file-list routing.

`parallel <repo-root>` lists static signs that xdist workers would share
state (a port picked and released, fixed log names, a file every process
appends to, real-agent tests outside an `xdist_group` or serial pass; a
worker-id reference or a retry marks one `mitigated`), projects the last
serial run's per-test times onto 2/3/4/6 workers (LPT, x1.15/x1.3/x1.5 load
inflation, per test and per module), and names tests red in a parallel run
but green serially: shared state between tests, never a flake.

`projection_fit` (in `timing`, fleet-config#1026) splits the modules that pay for a
second browser projection into those where the engine or viewport matters
(geometry, touch, input, nav, composer, safe-area) and Chromium-only candidates.

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
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_toml  # noqa: E402
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
# A skipped node still gets its DONE line; it ran no browser (fleet-config#1134).
_SKIP_RE = re.compile(r"^SKIPPED \((?:setup|call|teardown)\) (.+?)(?: \[gw\d+\])?$")
_PHASE_RE = re.compile(r"^==> phase: (.*?)(?:\.\.\.)?$")
_ROUTE_RE = re.compile(r"e2e routing: (?:tier=)?(skip|static|surface|full)\b")
_SESSION_END_RE = re.compile(r"^pytest session finished \(exit status (\d+)\)")
_EXCERPT_PREFIX = "    | "
_STEP_RE = re.compile(r"^((?:[\w.-]+[\\/])*test_\w+\.py):(\d+):")
_MINUTES_RE = re.compile(r"~?\s*(\d+(?:\.\d+)?)\s*(?:min\b|mins\b|minutes\b)", re.I)
_RUNTIME_CONTEXT_RE = re.compile(r"\b(gate|suite|e2e|verify|browser|pytest|runtime)\b", re.I)
CLAIM_WINDOW = 80
# The words just before a figure that make it a threshold, a history or a setting, not a measured runtime.
_NOT_A_CLAIM_RE = re.compile(
    r"\b(?:exceeds?|exceeding|over|above|beyond|more than|longer than|if|investigate|budget|limit|timeout|"
    r"after|every|cadence|default|previous|prior|was|were|old|before)\b[^.;]{0,12}$|>\s*\**\s*$", re.I)


# ---- config -----------------------------------------------------------------


def e2e_table(fleet_toml_text: Optional[str]) -> Dict[str, object]:
    """The `.fleet.toml` `[e2e]` table, or `{}` when absent or unparsable."""
    return fleet_toml.table(fleet_toml.parse(fleet_toml_text), "e2e") or {}


def timing_source(repo_root: Path, log: Optional[Path]) -> Tuple[Optional[str], Optional[Path], str]:
    """`(kind, path, reason)`: `progress-log` / `junit-xml`, or `(None, None, why)`."""
    if log is not None:
        path = log if log.is_absolute() else repo_root / log
        kind = "junit-xml" if path.suffix.lower() == ".xml" else "progress-log"
        return (kind, path, "--log") if path.is_file() else (None, None, f"--log {path} does not exist")
    table = e2e_table(fleet_toml.read_text(repo_root))
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
    skipped: set = set()
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
        elif (km := _SKIP_RE.match(body)):
            skipped.add(km.group(1))
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
        "skipped": sorted(skipped), "complete": bool(nodes) and not open_nodes and exit_status is not None,
    }


def failure_step(excerpt: List[str]) -> Optional[str]:
    """Where a failure stopped: the last `test_*.py:<line>:` frame in its traceback excerpt."""
    step = None
    for line in excerpt:
        m = _STEP_RE.match(line.strip())
        if m:
            step = f"{m.group(1).replace(chr(92), '/')}:{m.group(2)}"
    return step


def checkout_of(path: Path) -> Dict[str, object]:
    """The git checkout holding `path`: its root, and whether it is a linked worktree (`.git` is a file).

    A run measured in a linked worktree can differ from the primary in what
    skips and how it boots: gitignored runtime files (certificates, `.env`)
    are not there (fleet-config#1157, photo-ocr#127). Outside any checkout
    both answers are `None`: unknown, never a guess.
    """
    here = path.resolve()
    for d in [here, *here.parents]:
        git = d / ".git"
        if git.exists():
            return {"root": str(d), "linked_worktree": git.is_file()}
    return {"root": None, "linked_worktree": None}


def parse_junit(path: Path) -> Dict[str, object]:
    """Per-node seconds and failures from a JUnit XML; no phases, no window."""
    nodes: Dict[str, float] = {}
    failures: List[Dict[str, object]] = []
    skipped: set = set()
    skip_reasons: Dict[str, str] = {}
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        cls = (case.get("classname") or "").replace(".", "/")
        nodeid = f"{cls}.py::{case.get('name')}" if cls else str(case.get("name"))
        nodes[nodeid] = float(case.get("time") or 0.0)
        sk = case.find("skipped")
        if sk is not None:
            skipped.add(nodeid)
            skip_reasons[nodeid] = (sk.get("message") or "").strip()[:120]
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None:
                failures.append({"outcome": tag.upper(), "when": "call", "nodeid": nodeid,
                                 "step": failure_step((el.text or "").splitlines())})
    return {"started": None, "finished": None, "nodes": nodes, "node_phase": {}, "phases": [],
            "failures": failures, "exit_status": 1 if failures else 0, "parallel": False, "routed_tier": None,
            "skipped": sorted(skipped), "skip_reasons": skip_reasons, "complete": bool(nodes)}


# ---- measurements ------------------------------------------------------------------


def is_e2e(nodeid: str, test_dirs: Sequence[str]) -> bool:
    norm = nodeid.replace("\\", "/")
    return any(norm.startswith(d.strip("/") + "/") for d in test_dirs)


def executed_e2e(run: Dict[str, object], test_dirs: Sequence[str]) -> Dict[str, float]:
    """The run's e2e nodes that actually ran: a skipped node costs no browser time (fleet-config#1134)."""
    skipped = set(run.get("skipped") or ())  # type: ignore[arg-type]
    return {n: s for n, s in run["nodes"].items() if is_e2e(n, test_dirs) and n not in skipped}  # type: ignore[union-attr]


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


_PAGE_LOAD_RE = re.compile(r"\.(?:goto|reload)\(")
_SHOT_RE = re.compile(r"\.screenshot\(")


def loaders(text: str, pattern: "re.Pattern[str]" = _PAGE_LOAD_RE) -> set:
    """Functions in a module whose body matches `pattern`: by default a `boot_home()` helper or an `authed_page` fixture."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not n.name.startswith("test_") and pattern.search(ast.get_source_segment(text, n) or "")}


def cost_drivers(text: str, shared_loaders: frozenset = frozenset(),
                 shared_shot_helpers: frozenset = frozenset()) -> Dict[str, int]:
    """What a module pays for per test, counted statically: page loads, screenshots, PTY references, real-agent marks.

    A page load reached through a helper (`boot_home(page)`) or a fixture
    (`def test_x(authed_page)`) counts once per test that reaches it, and the
    helper's own `goto` is not counted again: home-automation's modules that
    boot through a helper read 0 before (fleet-config#1134). `shared_loaders`
    are the conftest/helper-module loaders the module can call or request.
    `shots` counts screenshots the same way: a call to a helper that
    screenshots (`shot(page, "x")`, `shared_shot_helpers` from the conftest)
    counts once per call site, a direct `.screenshot(` once. Call sites, not
    executions: a shot in a loop is a lower bound. It is context, never a fold
    target (task-os#278: about 195 shots, a third or more of the wall time).
    """
    import ast
    drivers = {"pty_refs": len(re.findall(r"\bpty\b", text, re.I)),
               "real_agent": len(re.findall(r"real[_-]agent", text, re.I))}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"page_loads": len(_PAGE_LOAD_RE.findall(text)), "shots": len(_SHOT_RE.findall(text)), **drivers}
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def reached(pattern: "re.Pattern[str]", names: set) -> int:
        """Matches in `text`, minus those inside the named helpers, plus a call or a fixture request per use."""
        inside = sum(len(pattern.findall(ast.get_source_segment(text, n) or "")) for n in fns if n.name in names)
        calls = sum(1 for c in ast.walk(tree) if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name) and c.func.id in names)
        requests = sum(1 for fn in fns if fn.name.startswith("test_") for a in fn.args.args if a.arg in names)
        return len(pattern.findall(text)) - inside + calls + requests

    return {"page_loads": reached(_PAGE_LOAD_RE, loaders(text) | set(shared_loaders)),
            "shots": reached(_SHOT_RE, loaders(text, _SHOT_RE) | set(shared_shot_helpers)), **drivers}


def shared_loaders(repo_root: Path, test_dirs: Sequence[str],
                   pattern: "re.Pattern[str]" = _PAGE_LOAD_RE) -> frozenset:
    """Helpers matching `pattern` defined in the test tree's conftest and `_*.py` modules, callable from any test module."""
    out: set = set()
    for p in _test_tree_files(repo_root, test_dirs):
        if p.name == "conftest.py" or p.name.startswith("_"):
            out |= loaders(p.read_text(encoding="utf-8", errors="replace"), pattern)
    return frozenset(out)


def modules(nodes: Dict[str, float], repo_root: Path, test_dirs: Sequence[str] = (),
            top: int = TOP_MODULES) -> List[Dict[str, object]]:
    agg: Dict[str, List[float]] = {}
    for nid, s in nodes.items():
        agg.setdefault(nid.split("::", 1)[0], []).append(s)
    ranked = sorted(agg.items(), key=lambda kv: -sum(kv[1]))[:top]
    shared = shared_loaders(repo_root, test_dirs) if test_dirs else frozenset()
    shared_shots = shared_loaders(repo_root, test_dirs, _SHOT_RE) if test_dirs else frozenset()
    out = []
    for mod, vals in ranked:
        path = repo_root / mod
        drivers = (cost_drivers(path.read_text(encoding="utf-8", errors="replace"), shared, shared_shots)
                   if path.is_file() else {})
        out.append({"module": mod, "seconds": round(sum(vals), 1), "nodes": len(vals), **drivers})
    return out


BOOT_EXCESS_S = 1.0


def first_node(nodes: Dict[str, float], earlier: Sequence[Dict[str, float]] = ()) -> Optional[Dict[str, object]]:
    """The first executed e2e node and the suite's median: the session app boot lands on it.

    In a surface slice that node is the touched file, so a per-module
    before/after comparison must discount it (fleet-config#1134).

    `earlier` is the executed nodes of earlier completed runs in the same
    log (one checkout, so a later run is warm), newest first. `boot` says
    whether the excess is a measured cost (fleet-config#1157, photo-ocr#127:
    a 7 s first node was one cold run in a fresh worktree, under 0.3 s warm):
    `single-run` (nothing to compare: cold, unconfirmed), `repeated` (this and
    the previous run both carry 1 s or more over the median) or `not-repeated`.
    """
    if not nodes:
        return None
    nid, s = next(iter(nodes.items()))
    vals = sorted(nodes.values())
    median = vals[len(vals) // 2]
    boot = "single-run"
    if earlier:
        prev = earlier[0]
        prev_excess = next(iter(prev.values())) - sorted(prev.values())[len(prev) // 2]
        boot = "repeated" if s - median >= BOOT_EXCESS_S and prev_excess >= BOOT_EXCESS_S else "not-repeated"
    return {"nodeid": nid, "seconds": s, "median_s": median, "boot": boot}


# ---- waits: where a suite pays wall time without doing anything (fleet-config#1134) --------------------
# home-automation's biggest savings were waits, not merges: a real 15 s poll a test waited out (31 s of
# the suite, now `page.clock`) and nine copies of a fixed 750 ms page timer (16 s, now held responses).
# Merging 34 nodes saved about 10 s. A wait is found statically, so this works with no timing source.

LONG_TIMEOUT_MS = 10_000
POLL_CONSTANT_MIN_MS = 1_000
_SLEEP_MS_RE = re.compile(r"\bwait_for_timeout\(\s*([\d_]+(?:\.\d+)?)\s*\)")
_SLEEP_S_RE = re.compile(r"\b(?:time|asyncio)\.sleep\(\s*([\d_]*\.?\d+)\s*\)")
_TIMEOUT_KW_RE = re.compile(r"\btimeout\s*=\s*([\d_]+)\b")
_POLL_CONST_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*(?:_MS|_INTERVAL|POLL[A-Z0-9_]*))\s*=\s*([\d_]+)\s*(?:#.*)?$", re.M)


def _js_timer_ms(text: str, fn: str = "setTimeout") -> List[Tuple[int, int]]:
    """`(offset, ms)` for every `<fn>(cb, <ms>)`: the literal last argument of the call."""
    out = []
    for m in re.finditer(rf"\b{fn}\(", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        tail = re.search(r",\s*([\d_]+)\s*$", text[m.end():i - 1])
        if tail:
            out.append((m.start(), int(tail.group(1).replace("_", ""))))
    return out


def _scopes(text: str) -> List[Tuple[int, int, str]]:
    """`(first_line, last_line, name)` of every function and module-level assignment, innermost last."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((n.lineno, n.end_lineno or n.lineno, n.name))
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            target = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(target, ast.Name):
                out.append((n.lineno, n.end_lineno or n.lineno, target.id))
    return sorted(out, key=lambda s: (s[0], -s[1]))


def _poll_loops(text: str) -> List[Tuple[int, int]]:
    """`(first_line, last_line)` of every `while` loop that can end on its own condition.

    A loop whose test is not a bare `True`, or whose body can `break`, `return`
    or `raise`, stops as soon as the condition it waits for holds. A `for` loop
    over taps and an endless `while True` with no exit are not polls.
    """
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.While):
            continue
        endless = isinstance(n.test, ast.Constant) and n.test.value is True
        exits = any(isinstance(c, (ast.Break, ast.Return, ast.Raise)) for b in n.body for c in ast.walk(b))
        if not endless or exits:
            out.append((n.lineno, n.end_lineno or n.lineno))
    return out


def wait_sites(repo_root: Path, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Every place the test tree waits by the clock, with its milliseconds and enclosing scope.

    `sleep` (`wait_for_timeout`, `time.sleep`) is paid in full on every run,
    except one inside a condition loop: that is `poll-sleep`, which ends when
    the condition holds and costs its interval at most per check
    (facilitation-suite#165: 6 of 11 listed sleeps, the saving overstated 1.75x);
    `page-timer` (a `setTimeout` in an init script or evaluated JS) is paid
    whenever a test waits for its effect; `poll-constant` (`POLL_MS = 15_000`)
    and `long-timeout` (`timeout=` of 10 s or more) mark a test that may wait
    out a real poll or timer. Fix shapes: `page.clock` for a poll-driven test,
    a fetch held until the test releases it for a loading state.
    """
    out: List[Dict[str, object]] = []
    for p in _test_tree_files(repo_root, test_dirs):
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        scopes = _scopes(text)
        hits: List[Tuple[int, str, int]] = []
        hits += [(m.start(), "sleep", int(float(m.group(1).replace("_", "")))) for m in _SLEEP_MS_RE.finditer(text)]
        hits += [(m.start(), "sleep", int(float(m.group(1).replace("_", "")) * 1000)) for m in _SLEEP_S_RE.finditer(text)]
        hits += [(off, "page-timer", ms) for off, ms in _js_timer_ms(text)]
        hits += [(m.start(), "long-timeout", int(m.group(1).replace("_", ""))) for m in _TIMEOUT_KW_RE.finditer(text)
                 if int(m.group(1).replace("_", "")) >= LONG_TIMEOUT_MS]
        hits += [(m.start(2), "poll-constant", int(m.group(2).replace("_", ""))) for m in _POLL_CONST_RE.finditer(text)
                 if int(m.group(2).replace("_", "")) >= POLL_CONSTANT_MIN_MS]
        loops = _poll_loops(text)
        for off, kind, ms in sorted(hits):
            line = text.count("\n", 0, off) + 1
            if kind == "sleep" and any(a <= line <= b for a, b in loops):
                kind = "poll-sleep"
            inner = [name for a, b, name in scopes if a <= line <= b]
            out.append({"file": rel, "line": line, "kind": kind, "ms": ms, "scope": inner[-1] if inner else None,
                        "text": text.splitlines()[line - 1].strip()[:120]})
    return out


SLOW_NODE_FACTOR = 10
SLOW_NODE_MIN_S = 1.0
APP_TIMER_MIN_MS = 1_000
_APP_SKIP_DIRS = frozenset({"node_modules", "_vendored", "vendor", "dist", "build", ".git", ".venv", "venv", "__pycache__"})
_APP_SLEEP_RE = re.compile(r"\bsleep\(\s*([\d_]+)\s*\)")


def slow_nodes(nodes: Dict[str, float]) -> List[Dict[str, object]]:
    """Executed nodes far above the suite's median: the `find the wait` candidates (fleet-config#1157).

    A wait that lives in app source (a 1 s status poll in `poll.js`, waited out
    twice by one test) is invisible to the test-tree scan; the per-node seconds
    show it as an outlier. The first node carries the session boot (its own
    finding), so it is left out. Candidates only: read the test and the app's
    timers (`app_timers`) before pricing one.
    """
    if len(nodes) < 2:
        return []
    items = list(nodes.items())[1:]
    vals = sorted(nodes.values())
    median = vals[len(vals) // 2]
    floor = max(SLOW_NODE_FACTOR * median, SLOW_NODE_MIN_S)
    return [{"nodeid": n, "seconds": s, "median_s": median} for n, s in sorted(items, key=lambda kv: -kv[1]) if s > floor]


def app_timers(repo_root: Path, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Literal timers of 1 s or more in the app's own JavaScript: what a test may be waiting out.

    `setTimeout`/`setInterval` (last argument) and `sleep(<ms>)`, longest
    first. Tests, vendored copies, `node_modules` and minified files are left
    out. A hint to confirm by reading, never a cost: only the test's own
    stubbed endpoints decide whether the timer runs (fleet-config#1157).
    """
    skip = {str((repo_root / d.strip("/")).resolve()) for d in test_dirs}
    out: List[Dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _APP_SKIP_DIRS and str((Path(dirpath) / d).resolve()) not in skip]
        for fn in filenames:
            if not fn.endswith((".js", ".mjs")) or fn.endswith(".min.js"):
                continue
            path = Path(dirpath) / fn
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [(off, "timeout", ms) for off, ms in _js_timer_ms(text)]
            hits += [(off, "interval", ms) for off, ms in _js_timer_ms(text, "setInterval")]
            hits += [(m.start(), "sleep", int(m.group(1).replace("_", ""))) for m in _APP_SLEEP_RE.finditer(text)]
            rel = path.relative_to(repo_root).as_posix()
            for off, kind, ms in hits:
                if ms >= APP_TIMER_MIN_MS:
                    line = text.count("\n", 0, off) + 1
                    out.append({"file": rel, "line": line, "kind": kind, "ms": ms, "text": text.splitlines()[line - 1].strip()[:120]})
    return sorted(out, key=lambda t: (-int(t["ms"]), str(t["file"]), int(t["line"])))  # type: ignore[call-overload]


def rank_waits(sites: List[Dict[str, object]], nodes: Dict[str, float], repo_root: Path) -> List[Dict[str, object]]:
    """Each wait joined to the executed nodes that pay it, ranked by their measured seconds.

    A wait inside a test belongs to that test; one in a module helper or a
    module-level script belongs to every test in the module whose body (with
    the helpers it calls) names that scope; a wait in a conftest or a shared
    helper module is not attributed (`nodes: null`). `paid_s` is the fixed
    cost of a `sleep` or `page-timer` over those nodes; `measured_s` is what
    the nodes took in all, the ceiling on what removing the wait can save.
    """
    by_test: Dict[Tuple[str, str], List[float]] = {}
    for nid, s in nodes.items():
        mod, _, rest = nid.partition("::")
        by_test.setdefault((mod, test_of(rest).split("::")[-1]), []).append(s)
    texts: Dict[str, Dict[str, str]] = {}
    out = []
    for site in sites:
        mod, scope = str(site["file"]), site["scope"]
        name = mod.rsplit("/", 1)[-1]
        row = {**site, "nodes": None, "measured_s": None, "paid_s": None}
        if name.startswith("test_"):
            if mod not in texts:
                path = repo_root / mod
                texts[mod] = _test_texts(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else {}
            tests = texts[mod]
            if scope is None:
                hit = list(tests)
            elif scope in tests:
                hit = [str(scope)]
            else:
                hit = [t for t, body in tests.items() if re.search(rf"\b{re.escape(str(scope))}\b", body)]
            vals = [s for t in hit for s in by_test.get((mod, t), [])]
            row["nodes"], row["measured_s"] = len(vals), round(sum(vals), 1)
            if site["kind"] in ("sleep", "page-timer"):
                row["paid_s"] = round(int(site["ms"]) / 1000 * len(vals), 1)  # type: ignore[call-overload]
        out.append(row)
    for r in out:
        r["ceiling"] = r["kind"] in ("long-timeout", "poll-sleep")

    def cost(r: Dict[str, object]) -> float:
        """What the wait is known to cost: seconds paid, else the nodes' seconds, else its own length."""
        if r["paid_s"] is not None:
            return float(r["paid_s"])  # type: ignore[arg-type]
        if r["nodes"] is not None:
            return float(r["measured_s"])  # type: ignore[arg-type]
        return int(r["ms"]) / 1000  # type: ignore[call-overload]

    # A fixed sleep or page timer ranks by the seconds it is paid, a poll constant by its nodes' seconds. A
    # `timeout=` or a poll-loop sleep is a ceiling the test may never reach, so it ranks after every wait that is actually paid
    # (task-os#284: a story with 30 s ceilings had 1.5 s of real waits in its 20.9 s).
    return sorted(out, key=lambda r: (bool(r["ceiling"]), -cost(r)))


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
    """Runtime figures ("~19 min") that sit near a word about the gate or suite.

    A figure counts only when a gate/suite word is within `CLAIM_WINDOW`
    characters of it, and not when the words just before it make it a
    threshold ("investigate if a run exceeds ~7 min"), a history ("the
    previous ~10 min") or a setting ("default 5 min"). home-automation's
    audit took those, an iCloud `expired after 10 min` and a telemetry cadence
    as runtime claims (fleet-config#1134).
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _MINUTES_RE.finditer(line):
            near = line[max(0, m.start() - CLAIM_WINDOW):m.end() + CLAIM_WINDOW]
            if not _RUNTIME_CONTEXT_RE.search(near) or _NOT_A_CLAIM_RE.search(line[max(0, m.start() - 40):m.start()]):
                continue
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


def timing(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None) -> Dict[str, object]:
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
    return {
        "status": "ok" if e2e else "unknown",
        "reason": None if e2e else f"no executed node under {', '.join(test_dirs)} in the last completed run",
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


def merged_prs(repo_root: Path, prs: int, until: Optional[str] = None,
               with_merge_commit: bool = False) -> Tuple[Optional[List[Dict[str, object]]], str]:
    """`(prs newest first, status)`: number, mergedAt and file paths of merged PRs.

    `with_merge_commit` adds each PR's `mergeCommit` sha ("" when gh has none),
    which the stylesheet routing reads blobs from.
    """
    slug = repo_slug(repo_root)
    if slug is None:
        return None, "unknown: no GitHub origin remote"
    limit = prs + (200 if until else 0)
    res = git_run.run_gh(["pr", "list", "--state", "merged", "--limit", str(limit), "--repo", slug,
                          "--json", "number,mergedAt,files" + (",mergeCommit" if with_merge_commit else "")],
                         timeout=180, stdin=subprocess.DEVNULL)
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
    if with_merge_commit:
        for row, it in zip(out, items):
            row["mergeCommit"] = str((it.get("mergeCommit") or {}).get("oid") or "")
    return out, f"ok ({len(out)} PRs)"


def _blob(repo_root: Path, rev: str, path: str) -> Tuple[bool, Optional[str]]:
    """`(exists, text)` of `path` at `rev`: `(False, None)` only when the commit is
    in the clone and the path is not in it; `(True, None)` when it could not be read."""
    res = git_run.run_git(["-C", str(repo_root), "show", f"{rev}:{path}"], timeout=30)
    if res.returncode == 0:
        return True, res.stdout
    commit = git_run.run_git(["-C", str(repo_root), "cat-file", "-e", f"{rev}^{{commit}}"], timeout=30)
    return (commit.returncode != 0), None


def pr_sheet_changes(mod, repo_root: Path, sha: str,
                     sheets: Sequence[str]) -> Tuple[Dict[str, object], List[str]]:
    """`(changed_selectors() per declared sheet the PR touched, the sheets that could not be read)`.

    Merge commit vs its first parent, mirroring the classifier's own
    `sheet_changes_from_git`: a sheet new in the PR diffs against the empty
    text. No merge commit, an object missing from the clone, or a deleted sheet
    gives None -- the whole suite, never a guess -- and is listed as unreadable,
    so it is never counted as an unsafe CSS change.
    """
    out: Dict[str, object] = {}
    unreadable: List[str] = []
    for sheet in sheets:
        if not sha:
            out[sheet] = None
            unreadable.append(sheet)
            continue
        old_exists, old = _blob(repo_root, f"{sha}^1", sheet)
        _new_exists, new = _blob(repo_root, sha, sheet)
        if not old_exists and new is not None:
            old = ""
        if old is None or new is None:
            unreadable.append(sheet)
        out[sheet] = mod.changed_selectors(old, new)
    return out, unreadable


def sheet_bucket(sels: object, surfaces: Sequence[object]) -> str:
    """Why one PR's change to a shared sheet does or doesn't narrow (fleet-config#1033)."""
    if sels is None:
        return "unsafe"
    if not sels:
        return "no rule changed"
    owners = set()
    for sel in sels:  # type: ignore[attr-defined]
        hits = [s for s in surfaces if s.owns_selector(sel)]  # type: ignore[attr-defined]
        if not hits:
            return "unmapped selector"
        if len(hits) > 1:
            return "spans surfaces"
        owners.add(getattr(hits[0], "name", id(hits[0])))
    return "owned by one surface" if len(owners) == 1 else "spans surfaces"


def _routes_sheets(mod) -> bool:
    """Whether this classifier can narrow a shared stylesheet (project-scaffolding#289)."""
    import inspect
    if not hasattr(mod, "changed_selectors"):
        return False
    try:
        return len(inspect.signature(mod.classify).parameters) >= 3
    except (TypeError, ValueError):
        return False


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


def shadow_entry(path: str, rules: list) -> Optional[Dict[str, object]]:
    """A shadowed path's lower-tier rules plus the rule that wins once the first one stops matching.

    The first-match-wins table can route a README full because a broad prefix
    rule sits above the docs rule (app-launcher#1220 (b)). `shadowed` lists
    the later, lower-tier, more specific rules that also match; a general rule
    placed after a specific one (`tests/` after `tests/e2e/`) is the intended
    order and is not reported. Dropping the path from the first rule hands it to `hits[1]`, which can be
    a broad full rule in between rather than the shadowed one:
    home-automation's vendored READMEs went to `app/webapp/`, not `*.md`
    (fleet-config#1134). `drop_safe` is true only when `hits[1]` is itself a
    shadowed rule; otherwise the fix is an explicit rule, checked with
    `classify_cmd` on the candidate table.
    """
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    hits = [r for r in rules if r.matches(path, ext)]
    if len(hits) < 2:
        return None
    first = hits[0]
    shadowed = [r for r in hits[1:] if r.tier < first.tier and _more_specific(r, first)]
    if not shadowed:
        return None
    nxt = hits[1]
    return {"shadowed": [r.label for r in shadowed], "next_rule": nxt.label, "next_tier": _tier_name(nxt.tier),
            "drop_safe": nxt in shadowed}


def _tier_name(tier: object) -> object:
    """A rule tier as the classifier names it (`FULL`), or the raw value for a plain int."""
    return getattr(tier, "name", tier)


def classify_cmd(paths: Sequence[str]) -> str:
    """The classifier invocation that routes exactly these paths: a proposed rule's check, in seconds."""
    return "python scripts/classify_e2e.py " + " ".join(paths)


def _repo_file(repo_root: Path, dotted: str) -> Optional[str]:
    """The repo-relative `.py` a dotted module name resolves to (`a/b.py` or `a/b/__init__.py`), or None."""
    rel = dotted.replace(".", "/")
    for cand in (f"{rel}.py", f"{rel}/__init__.py"):
        if (repo_root / cand).is_file():
            return cand
    return None


def _read_files(parsed, repo_root: Path) -> set:
    """Repo files (not `.py`) a parsed module names as a repo-relative path: `'a/b.json'` or `ROOT / 'a' / 'b.json'`.

    Only a path that exists under the repo counts, so a stray string or a path
    built from a variable is skipped, never guessed (facilitation-suite#165).
    """
    import ast

    def parts(n) -> List[str]:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return parts(n.left) + parts(n.right)
        return [n.value] if isinstance(n, ast.Constant) and isinstance(n.value, str) else []

    cands = set()
    for n in ast.walk(parsed):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            cands.add(n.value)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            cands.add("/".join(p.strip("/") for p in parts(n)))
    root = repo_root.resolve()
    out = set()
    for c in cands:
        c = c.replace("\\", "/")
        if not c or len(c) > 200 or "\n" in c or c.startswith("/") or ".." in c.split("/") or c.endswith(".py"):
            continue
        if PureWindowsPath(c).anchor:  # `C:/Windows/Fonts/x.ttf`: `root / c` would drop the root and leave the repo
            continue
        p = (root / c).resolve()
        if "." in c.rsplit("/", 1)[-1] and p.is_file() and root in p.parents:
            out.add(p.relative_to(root).as_posix())
    return out


def import_holes(repo_root: Path, mod, config, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Files the e2e suite loads or imports that the routing table sends to `none` (task-os#287).

    A diff touching only such a file runs no browser suite, though 12 of
    task-os's e2e modules imported `tests/fixtures/*.py` and `tests/conftest.py`.
    The `routing` scan lists paths from merged PRs, so a file nobody changed
    lately never showed. This reads the other direction: every module under
    the test dirs, the conftest and the `_*.py` plugins (`kind: loaded`), plus
    every repo file those modules import (`kind: imported`, `imported_by`
    counting the importers), plus every non-Python repo file those modules name
    as a repo-relative path (`kind: read`, `imported_by` counting the readers:
    facilitation-suite's conftest read `config/config.sample.json` for every
    instance while the table routed `config/` to `none`), each routed through
    the repo's own classifier. One level only: a fixture's own imports are not followed. Backend source
    the suite boots (`src/*.py` routed `none`) lands here too; whether that
    is a hole or a deliberate gate-time trade is the owner's call.
    """
    import ast
    tree = _test_tree_files(repo_root, test_dirs)
    loaded = {str(p.relative_to(repo_root)).replace("\\", "/") for p in tree}
    imported: Dict[str, int] = {}
    read: set = set()
    for p in tree:
        try:
            parsed = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        seen: set = set()
        for n in ast.walk(parsed):
            names: List[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            for name in names:
                hit = _repo_file(repo_root, name)
                if hit:
                    seen.add(hit)
        files = _read_files(parsed, repo_root)
        read |= files - seen
        for hit in seen | files:
            imported[hit] = imported.get(hit, 0) + 1
    out = []
    for path in sorted(loaded | set(imported)):
        cat, label = mod._classify_one(path, config.rules)
        if getattr(cat, "name", cat) != "NONE":
            continue
        out.append({"path": path, "rule": label, "kind": "loaded" if path in loaded else "read" if path in read else "imported",
                    "imported_by": imported.get(path, 0), "check": classify_cmd([path])})
    return sorted(out, key=lambda e: (-int(e["imported_by"]), str(e["path"])))  # type: ignore[call-overload]


def _counterfactual(repo_root: Path, mod, proposed, test_dirs: Sequence[str], path: str, narrowed: List[Dict[str, object]],
                    holes: List[Dict[str, object]], unclassified: List[Dict[str, object]],
                    shadowed: List[Dict[str, object]]) -> Dict[str, object]:
    """What a candidate table changes: PR tiers, plus the findings re-run against it (facilitation-suite#165).

    `unclassified`, `shadowed` and `import_holes` are the declared table's
    findings recomputed with the candidate's rules, and `holes_opened` /
    `holes_closed` are the import-hole paths that appear or disappear, so a
    routing proposal's effect on coverage is read here rather than proved by
    swapping the file in and classifying paths by hand.
    """
    after = import_holes(repo_root, mod, proposed, test_dirs)
    before_paths = {str(h["path"]) for h in holes}
    after_paths = {str(h["path"]) for h in after}
    return {"proposed": path, "changed": narrowed, "unclassified": unclassified, "shadowed": shadowed, "import_holes": after,
            "holes_opened": sorted(after_paths - before_paths), "holes_closed": sorted(before_paths - after_paths)}


def _note_paths(files: Sequence[str], labels: Sequence[str], rules: list,
                unclassified: Dict[str, int], shadowed: Dict[str, Dict[str, object]]) -> None:
    """Tally one PR's unclassified and shadowed paths under `rules` (labels are what each path matched)."""
    for f, lab in zip(files, labels):
        if lab == "unclassified":
            unclassified[f] = unclassified.get(f, 0) + 1
        sh = shadow_entry(f.replace("\\", "/"), rules)
        if sh:
            entry = shadowed.setdefault(f, {"path": f, "took": lab, **sh, "check": classify_cmd([f]), "prs": 0})
            entry["prs"] = int(entry["prs"]) + 1  # type: ignore[call-overload]


def _unclassified_rows(unclassified: Dict[str, int]) -> List[Dict[str, object]]:
    return [{"path": p, "prs": n, "check": classify_cmd([p])} for p, n in sorted(unclassified.items(), key=lambda kv: -kv[1])]


def _shadowed_rows(shadowed: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(shadowed.values(), key=lambda e: -int(e["prs"]))  # type: ignore[arg-type,call-overload]


def routing_report(repo_root: Path, prs: int = 60, until: Optional[str] = None,
                   config_path: Optional[Path] = None, proposed_path: Optional[Path] = None,
                   pr_list: Optional[List[Dict[str, object]]] = None,
                   test_dirs: Sequence[str] = ("tests/e2e",)) -> Dict[str, object]:
    mod = load_classifier(repo_root)
    if mod is None:
        return {"status": "unknown", "reason": "no importable scripts/classify_e2e.py in the repo"}
    config = mod.load_config(config_path or (repo_root / ".fleet.toml"))
    proposed = mod.load_config(proposed_path) if proposed_path else None
    declared = sorted(set(getattr(config, "shared_stylesheets", ()) or ())
                      | set(getattr(proposed, "shared_stylesheets", ()) or ()))
    by_sheet = bool(declared) and _routes_sheets(mod)
    if pr_list is None:
        pr_list, why = merged_prs(repo_root, prs, until, with_merge_commit=by_sheet)
        if pr_list is None:
            return {"status": "unknown", "reason": f"merged PRs: {why}"}
    sheet_reasons: Dict[str, Dict[str, int]] = {}
    tiers: Dict[str, int] = {}
    full_classes: Dict[str, int] = {}
    single_cause: Dict[str, int] = {}
    full_paths: Dict[str, int] = {}
    unclassified: Dict[str, int] = {}
    shadowed: Dict[str, Dict[str, object]] = {}
    p_unclassified: Dict[str, int] = {}
    p_shadowed: Dict[str, Dict[str, object]] = {}
    narrowed: List[Dict[str, object]] = []
    rows = []
    browser_relevant = 0
    for pr in pr_list:
        files = [str(f) for f in pr["files"]]  # type: ignore[union-attr]
        slashed = {f.replace("\\", "/") for f in files}
        touched = [sheet for sheet in declared if sheet in slashed] if by_sheet else []
        changes, unreadable = (pr_sheet_changes(mod, repo_root, str(pr.get("mergeCommit") or ""), touched)
                               if touched else ({}, []))
        extra = (changes,) if by_sheet else ()
        r = mod.classify(files, config, *extra)
        for sheet, sels in changes.items():
            bucket = "unreadable" if sheet in unreadable else sheet_bucket(sels, (proposed or config).surfaces)
            counts = sheet_reasons.setdefault(sheet, {})
            counts[bucket] = counts.get(bucket, 0) + 1
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
        _note_paths(files, [lab for _, _, lab in cats], config.rules, unclassified, shadowed)
        if proposed is not None:
            _note_paths(files, [mod._classify_one(f.replace("\\", "/"), proposed.rules)[1] for f in files],
                        proposed.rules, p_unclassified, p_shadowed)
            pr_ = mod.classify(files, proposed, *extra)
            if pr_.tier != r.tier:
                narrowed.append({"pr": pr["number"], "from": r.tier, "to": pr_.tier, "surface": pr_.surface})
        rows.append({"pr": pr["number"], "tier": r.tier, "surface": r.surface})
    full_n = tiers.get("full", 0)
    holes = import_holes(repo_root, mod, config, test_dirs)
    from e2e_route import gate_contract
    gate, gate_reason, readers = gate_contract(repo_root)
    return {
        "status": "ok",
        "prs": len(pr_list),
        # Whether the gate runs this routing at all: a `not-consumed` table saves the gate nothing (fleet-config#1134).
        "gate": {"verdict": gate, "reason": gate_reason, "readers": [{"file": f, "split": sp} for f, sp in readers]},
        "config": str(config_path or (repo_root / ".fleet.toml")), "config_source": config.source,
        "tiers": {k: tiers.get(k, 0) for k in ("skip", "static", "surface", "full")},
        "browser_relevant": browser_relevant,
        "browser_relevant_full": full_n,
        "full_classes": dict(sorted(full_classes.items(), key=lambda kv: -kv[1])),
        "single_cause": dict(sorted(single_cause.items(), key=lambda kv: -kv[1])),
        "full_paths_top": [{"path": p, "prs": n} for p, n in sorted(full_paths.items(), key=lambda kv: -kv[1])[:10]],
        "unclassified": _unclassified_rows(unclassified),
        "shadowed": _shadowed_rows(shadowed),
        "import_holes": holes,
        "counterfactual": None if proposed is None else _counterfactual(
            repo_root, mod, proposed, test_dirs, str(proposed_path), narrowed, holes,
            _unclassified_rows(p_unclassified), _shadowed_rows(p_shadowed)),
        "sheet_routing": ("n/a: no shared_stylesheets declared" if not declared
                          else "n/a: classifier routes file lists only" if not by_sheet
                          else {"sheets": declared, "reasons": sheet_reasons}),
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
            e2e = executed_e2e(run, test_dirs)
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
    limit = fleet_toml.positive_int(raw)
    if limit is None:
        return None, f"invalid [e2e] time_budget_s {raw!r} ignored"
    return limit, "[e2e] time_budget_s"


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
    limit, note = time_budget_limit(fleet_toml.read_text(repo_root))
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


# ---- second-projection fit (fleet-config#1026) -------------------------------------------------------

SECOND_PROJECTIONS = ("webkit", "firefox")
# Where the engine or the viewport matters (layout, touch targets, nav, composer, safe-area):
# geometry and computed style, viewport and touch, engine branches, input, the nav and the composer.
_ENGINE_SIGNAL_RE = re.compile(
    r"bounding_box|getBoundingClientRect|effective_rect|assert_min_target|_geometry\b|"
    r"getComputedStyle|computed_style|scrollWidth|offsetWidth|clientWidth|innerWidth|set_viewport_size|viewport|"
    r"\.tap\(|touchscreen|has_touch|is_mobile|browser_name|keyboard|safe-area|safe_area|"
    r"tablist|\bnav\b|composer|compose_bar", re.I)


def _test_texts(source: str) -> Dict[str, str]:
    """Each test function's source plus the module-level helpers it calls (one level), by test name.

    What a test *does* can live in a helper (`_open_board(page)` holding the
    bounding-box check), so the helper's text counts toward the test's signal.
    An unparsable module maps every name to its whole text.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    helpers = {n.name: ast.get_source_segment(source, n) or "" for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("test_")}
    out: Dict[str, str] = {}
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]
    for fn in funcs:
        text = ast.get_source_segment(source, fn) or ""
        called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        out[fn.name] = text + "\n" + "\n".join(helpers[c] for c in sorted(called & set(helpers)))
    return out


def projection_fit(nodes: Dict[str, float], repo_root: Path) -> Dict[str, object]:
    """Tests paying for a second browser projection, split by whether the engine or viewport matters.

    Per test (its body plus the module helpers it calls), the shape of
    app-launcher#1220's option 1: a test showing a geometry, touch, input,
    engine-branch, nav or composer signal is `kept`; one showing none is a
    Chromium-only `candidate` under the fleet rule (#1026: functional tests
    run on one engine). A static read: the judgment layer confirms each
    candidate and states what moving it gives up. A module-level signal (a
    fixture setting the viewport) keeps every test in the module.
    """
    per_test: Dict[Tuple[str, str], List[float]] = {}
    for nid, s in nodes.items():
        if projection_of(nid) in SECOND_PROJECTIONS:
            mod, _, rest = nid.partition("::")
            per_test.setdefault((mod, test_of(rest).split("::")[-1]), []).append(s)
    sources: Dict[str, Tuple[str, Dict[str, str]]] = {}
    kept: Dict[str, Dict[str, object]] = {}
    cand: Dict[str, Dict[str, object]] = {}
    for (mod, name), vals in per_test.items():
        if mod not in sources:
            path = repo_root / mod
            text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            sources[mod] = (text, _test_texts(text))
        text, tests = sources[mod]
        module_level = "\n".join(l for l in text.splitlines() if not l.startswith((" ", "\t")))
        body = tests.get(name)
        signal = bool(_ENGINE_SIGNAL_RE.search(module_level)) if text else True
        if body is None:
            signal = True  # can't find the test in the file: keep it
        elif _ENGINE_SIGNAL_RE.search(body):
            signal = True
        bucket = kept if signal else cand
        row = bucket.setdefault(mod, {"module": mod, "tests": 0, "nodes": 0, "seconds": 0.0})
        row["tests"] = int(row["tests"]) + 1  # type: ignore[call-overload]
        row["nodes"] = int(row["nodes"]) + len(vals)  # type: ignore[call-overload]
        row["seconds"] = round(float(row["seconds"]) + sum(vals), 1)  # type: ignore[arg-type]
    order = lambda rows: sorted(rows.values(), key=lambda r: -float(r["seconds"]))  # noqa: E731
    return {"projections": list(SECOND_PROJECTIONS), "kept": order(kept), "candidates": order(cand),
            "kept_nodes": sum(int(r["nodes"]) for r in kept.values()),  # type: ignore[call-overload]
            "candidate_nodes": sum(int(r["nodes"]) for r in cand.values()),  # type: ignore[call-overload]
            "candidate_seconds": round(sum(float(r["seconds"]) for r in cand.values()), 1)}  # type: ignore[arg-type]
