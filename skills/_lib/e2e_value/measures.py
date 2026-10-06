"""Time measurements over one run's executed e2e nodes: projections, duration buckets, the tail,
per-module cost drivers and the boot-carrying first node.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .sources import projection_of, _test_tree_files

__all__ = [
    "BUCKETS",
    "TOP_MODULES",
    "is_e2e",
    "executed_e2e",
    "projections",
    "buckets",
    "tail",
    "loaders",
    "cost_drivers",
    "shared_loaders",
    "modules",
    "BOOT_EXCESS_S",
    "first_node",
]


BUCKETS: Sequence[Tuple[str, float, float]] = (
    ("<1s", 0.0, 1.0), ("1-3s", 1.0, 3.0), ("3-6s", 3.0, 6.0), ("6-10s", 6.0, 10.0), (">=10s", 10.0, math.inf),
)
TOP_MODULES = 10


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
