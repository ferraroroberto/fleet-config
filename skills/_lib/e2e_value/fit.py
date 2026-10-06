"""Second-projection fit (fleet-config#1026): which modules need a second browser engine.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from .sources import projection_of, test_of

__all__ = [
    "SECOND_PROJECTIONS",
    "projection_fit",
]


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
