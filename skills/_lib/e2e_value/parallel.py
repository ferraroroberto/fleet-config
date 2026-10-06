"""Parallelisability (step 2): xdist blockers and a worker-count projection.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .sources import _gather, _test_tree_files
from .measures import executed_e2e

__all__ = [
    "INFLATION",
    "WORKER_COUNTS",
    "lpt",
    "projection",
    "xdist_state",
    "parallel_blockers",
    "shared_state_evidence",
    "parallel",
]


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
