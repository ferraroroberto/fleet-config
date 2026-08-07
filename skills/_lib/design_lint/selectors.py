"""CSS selector analysis — splitting, compounding, and scope resolution.

The contract checks repeatedly need "which rules target this class, and what
were they scoped under" (`.settings-card .stacked` vs a bare `.stacked`). That
question is pure selector arithmetic, independent of any one contract.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .css import _BLOCK_RE


def _last_selector_line(sel_group: str) -> str:
    lines = sel_group.strip().splitlines()
    return lines[-1].strip() if lines else ""


def _split_top_level_commas(sel_line: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in sel_line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _compounds(selector: str) -> List[str]:
    return [c for c in re.split(r"\s*[>+~]\s*|\s+", selector.strip()) if c]


def _selector_hits(css_all: str, rightmost_pattern: "re.Pattern[str]"
                    ) -> List[Tuple[str, List[str], str]]:
    """`(selector, ancestor_compounds, declaration_body)` for every selector
    whose rightmost (target) compound matches `rightmost_pattern`.
    `::backdrop` pseudo-elements are excluded — they never carry the layout
    declarations these checks look for."""
    hits: List[Tuple[str, List[str], str]] = []
    for bm in _BLOCK_RE.finditer(css_all):
        sel_line = _last_selector_line(bm.group(1))
        if not sel_line or sel_line.startswith("@") or sel_line.startswith("*/"):
            continue
        for sel in _split_top_level_commas(sel_line):
            if "::backdrop" in sel:
                continue
            comps = _compounds(sel)
            if comps and rightmost_pattern.search(comps[-1]):
                hits.append((sel, comps[:-1], bm.group(2)))
    return hits


def _class_scope_status(css_all: str, class_name: str,
                        dialog_classes: frozenset = frozenset()) -> set:
    """Where a class is styled: `global` (bare/unscoped), `dialog` (an
    ancestor compound mentions "dialog" or carries a class that appears on/in
    a known editor `<dialog>` — e.g. `.detail-card .row`, where the wrapper
    class lives inside the dialog, fleet-config#342), `other` (scoped to some
    unrelated ancestor, e.g. `.settings-card .stacked` — the app-launcher#70
    bug)."""
    pat = re.compile(r"\." + re.escape(class_name) + r"\b")
    scopes: set = set()
    for _, ancestors, _body in _selector_hits(css_all, pat):
        if not ancestors:
            scopes.add("global")
        elif any("dialog" in a.lower()
                 or set(re.findall(r"\.([A-Za-z0-9_-]+)", a)) & dialog_classes
                 for a in ancestors):
            scopes.add("dialog")
        else:
            scopes.add("other")
    return scopes
