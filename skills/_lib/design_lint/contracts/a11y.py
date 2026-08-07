"""Accessibility + reading-comfort contracts.

Keyboard focus, reduced motion, the desktop measure, and the effective
touch-target floor — the checks that are about reaching and perceiving the UI
rather than about any one component.
"""
from __future__ import annotations

import re
from typing import List

from ..css import _BLOCK_RE
from ..selectors import _compounds, _last_selector_line, _split_top_level_commas
from ._ctx import _ContractsCtx, _evidence, _loc_at, _result


def _check_focus_visible_ring(ctx: _ContractsCtx) -> List[dict]:
    # 1. tokenized :focus-visible ring
    css_all = ctx.css_all
    fv = re.search(r":focus-visible[^{}]*\{([^{}]*)\}", css_all)
    if not fv:
        return [_result("focus-visible-ring", "FAIL", "no :focus-visible rule — keyboard focus falls to the browser default (design.md v2 focus contract)")]
    if "var(--" in fv.group(1) and "outline" in fv.group(1):
        return [_result("focus-visible-ring", "PASS", "tokenized :focus-visible outline present",
                         _evidence(css_all, r":focus-visible[^{}]*\{"))]
    return [_result("focus-visible-ring", "WARN", "a :focus-visible rule exists but its outline is not tokenized",
                     _evidence(css_all, r":focus-visible[^{}]*\{"))]


def _check_reduced_motion(ctx: _ContractsCtx) -> List[dict]:
    # 2. prefers-reduced-motion
    css_all = ctx.css_all
    if re.search(r"@media[^{]*prefers-reduced-motion", css_all):
        return [_result("reduced-motion", "PASS", "prefers-reduced-motion block present",
                         _evidence(css_all, r"@media[^{]*prefers-reduced-motion"))]
    return [_result("reduced-motion", "FAIL", "no prefers-reduced-motion handling (design.md v2 Motion section)")]


def _check_desktop_measure(ctx: _ContractsCtx) -> List[dict]:
    # 3. desktop measure (centered 772px column)
    css_all = ctx.css_all
    if re.search(r"max-width:\s*772px", css_all):
        return [_result("desktop-measure", "PASS", "content measure capped at the fleet 772px",
                         _evidence(css_all, r"max-width:\s*772px"))]
    near = re.search(r"max-width:\s*(6\d\d|7\d\d|8\d\d)px", css_all)
    if near:
        return [_result("desktop-measure", "WARN",
                         f"content capped at {near.group(0).split(':')[1].strip()} — spec is 772px",
                         _evidence(css_all, r"max-width:\s*(6\d\d|7\d\d|8\d\d)px"))]
    return [_result("desktop-measure", "FAIL", "no desktop content cap found — spec: centered max-width 772px")]


_PSEUDO_CLASS_RE = re.compile(r"\.([A-Za-z0-9_-]+)::?(?:before|after)\b")
_CLASS_ATTR_RE = re.compile(r'class=["\']([^"\']+)["\']')
_INTERACTIVE_COMPOUND_RE = re.compile(
    r"(^|[.#\[])(button|[\w-]*(?:btn|button|close|toggle|action|step|del)(?:[\w-]*)?)\b", re.I)


def _check_hit_target(ctx: _ContractsCtx) -> List[dict]:
    # 21. effective touch targets — every non-navigation pointer target
    #     presents >= components.hit-target.min (44px) effective (design.md
    #     Touch targets; home-automation#409). Static view only: a compact
    #     authored square (explicit sub-min width AND height on an
    #     interactive-looking selector) must pair with an invisible-expansion
    #     pseudo on the same class or a co-applied expansion utility in the
    #     markup. Effective rectangles and non-overlap are rendered facts —
    #     the browser leg (project-scaffolding#157) proves the geometry.
    css_all, markup_all = ctx.css_all, ctx.markup_all
    min_m = re.match(r"(\d+)px", ctx.spec_light.get("components.hit-target.min", ""))
    if not min_m:
        return [_result("hit-target", "NA", "spec declares no components.hit-target token")]
    min_px = int(min_m.group(1))

    # classes that carry a negative-inset ::before/::after hit expansion
    expansion_classes: set = set()
    for bm in _BLOCK_RE.finditer(css_all):
        sel_line = _last_selector_line(bm.group(1))
        body = bm.group(2)
        if not (re.search(r"inset:\s*-", body)
                or (re.search(r"(?<![-\w])top:\s*-", body)
                    and re.search(r"(?<![-\w])left:\s*-", body))):
            continue
        expansion_classes.update(_PSEUDO_CLASS_RE.findall(sel_line))

    markup_class_sets = [set(mm.group(1).split())
                         for mm in _CLASS_ATTR_RE.finditer(markup_all)]

    flagged: List[str] = []
    n_candidates = 0
    for bm in _BLOCK_RE.finditer(css_all):
        sel_line = _last_selector_line(bm.group(1))
        if sel_line.startswith("@") or "nav" in sel_line.lower():
            continue
        body = bm.group(2)
        for sel in _split_top_level_commas(sel_line):
            comps = _compounds(sel)
            if not comps or "::" in comps[-1]:
                continue
            if not _INTERACTIVE_COMPOUND_RE.search(comps[-1]):
                continue
            w_m = re.search(r"(?<![-\w])width:\s*(\d+)px", body)
            h_m = re.search(r"(?<![-\w])height:\s*(\d+)px", body)
            if not (w_m and h_m):
                continue
            n_candidates += 1
            if int(w_m.group(1)) >= min_px and int(h_m.group(1)) >= min_px:
                continue
            # mitigation: min-* floor in the same body
            minw = re.search(r"min-width:\s*(\d+)px", body)
            minh = re.search(r"min-height:\s*(\d+)px", body)
            if minw and minh and int(minw.group(1)) >= min_px and int(minh.group(1)) >= min_px:
                continue
            own_classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", comps[-1]))
            # mitigation: expansion pseudo on one of the control's own classes
            if own_classes & expansion_classes:
                continue
            # mitigation: expansion utility co-applied in the markup
            if any(cs & own_classes and cs & expansion_classes
                   for cs in markup_class_sets):
                continue
            flagged.append(f"{_loc_at(css_all, bm.start())} {comps[-1]} "
                           f"{w_m.group(1)}x{h_m.group(1)}px")
    if flagged:
        return [_result("hit-target", "WARN",
            f"{len(flagged)} compact control(s) authored below the "
            f"{min_px}px effective floor with no ::before expansion or "
            "co-applied hit-area utility (design.md Touch targets): "
            + "; ".join(flagged[:6]))]
    if n_candidates:
        return [_result("hit-target", "PASS",
            f"every fixed-size compact control reaches the {min_px}px "
            "effective floor (authored size, min-* floor, or invisible "
            "expansion) — rendered rectangles/overlap need the browser leg")]
    return [_result("hit-target", "NA",
        "no fixed-size compact pointer-target rules found")]
