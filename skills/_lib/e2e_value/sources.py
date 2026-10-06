"""Where the measurements come from: the `[e2e]` table, the gate's progress log or JUnit XML, the
same log in sibling checkouts, the repo's GitHub slug and its test tree.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import fleet_toml
import git_run

__all__ = [
    "KNOWN_PROJECTIONS",
    "GIT_TIMEOUT_S",
    "GH_TIMEOUT_S",
    "GH_FILES_TIMEOUT_S",
    "e2e_table",
    "timing_source",
    "projection_of",
    "test_of",
    "split_runs",
    "parse_run",
    "failure_step",
    "checkout_of",
    "parse_junit",
    "sibling_logs",
    "load_state",
    "failure_events",
    "repo_slug",
]


KNOWN_PROJECTIONS = ("chromium", "webkit", "firefox")
# Subprocess timeouts (seconds), stated bounds rather than measured ones: a measurement that only reads
# git/gh must give up on a wedged call instead of hanging the audit. GIT_TIMEOUT_S bounds every local git
# read (a rev lookup or a file listing, even on a large repo, finishes far inside it); GH_TIMEOUT_S bounds a
# network `gh` list; GH_FILES_TIMEOUT_S is longer because `gh pr list --json files` returns every PR's file list.
GIT_TIMEOUT_S = 30
GH_TIMEOUT_S = 120
GH_FILES_TIMEOUT_S = 180

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


def _wall_s(run: Dict[str, object]) -> Optional[float]:
    a, b = run.get("started"), run.get("finished")
    return (b - a).total_seconds() if isinstance(a, _dt.datetime) and isinstance(b, _dt.datetime) else None  # type: ignore[operator]


# ---- sibling checkouts: load and history ------------------------------------------------


def sibling_logs(repo_root: Path, rel_log: Optional[str]) -> List[Path]:
    """The same progress log in every other checkout of this repo (`git worktree list`)."""
    if not rel_log:
        return []
    res = git_run.run_git(["-C", str(repo_root), "worktree", "list", "--porcelain"], timeout=GIT_TIMEOUT_S)
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




# ---- every run on record, the repo's slug, its test tree --------------------------------------


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


def repo_slug(repo_root: Path) -> Optional[str]:
    """`owner/repo` from the checkout's GitHub `origin` remote, else None."""
    res = git_run.run_git(["-C", str(repo_root), "remote", "get-url", "origin"], timeout=GIT_TIMEOUT_S)
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", res.stdout.strip()) if res.returncode == 0 else None
    return m.group(1) if m else None


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
