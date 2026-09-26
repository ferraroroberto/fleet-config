"""Accessibility + reading-comfort contracts.

Keyboard focus, reduced motion, the desktop measure, and the effective
touch-target floor — the checks that are about reaching and perceiving the UI
rather than about any one component.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

from ..css import _BLOCK_RE
from ..selectors import _compounds, _last_selector_line, _split_top_level_commas
from ._ctx import _ContractsCtx, _evidence, _loc_at, _result


_FOCUS_VISIBLE_RULE_RE = re.compile(
    r"(?P<pre>[^{}]*?)(?P<fv>:focus-visible)(?P<post>[^{}]*)\{(?P<body>[^{}]*)\}")
_TOKENIZED_OUTLINE_RE = re.compile(r"\boutline(?:-color)?\s*:[^;}]*var\(--")
_BARE_FOCUS_VISIBLE = {":focus-visible", "*:focus-visible"}


def _check_focus_visible_ring(ctx: _ContractsCtx) -> List[dict]:
    # 1. tokenized :focus-visible ring. Every :focus-visible rule is weighed,
    # not just the first (fleet-config#994): a vendored component's scoped
    # `outline: none` sorting ahead of the global ring must not mask it. A
    # bare (global) tokenized rule is preferred as the evidence.
    css_all = ctx.css_all
    rules = list(_FOCUS_VISIBLE_RULE_RE.finditer(css_all))
    if not rules:
        return [_result("focus-visible-ring", "FAIL", "no :focus-visible rule — keyboard focus falls to the browser default (design.md v2 focus contract)")]
    tokenized = [m for m in rules if _TOKENIZED_OUTLINE_RE.search(m.group("body"))]

    def bare(m: re.Match) -> bool:
        selector = re.sub(r"/\*.*?\*/", "", m.group("pre") + m.group("fv") + m.group("post"), flags=re.S)
        return any(s.strip() in _BARE_FOCUS_VISIBLE for s in selector.split(","))

    if tokenized:
        best = next((m for m in tokenized if bare(m)), tokenized[0])
        return [_result("focus-visible-ring", "PASS", "tokenized :focus-visible outline present",
                         _loc_at(css_all, best.start("fv")))]
    return [_result("focus-visible-ring", "WARN", "a :focus-visible rule exists but its outline is not tokenized",
                     _loc_at(css_all, rules[0].start("fv")))]


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
    #     App-authored surfaces only (`files.is_third_party`,
    #     fleet-config#940): a bundled library's control chrome is sized by
    #     that library and can't be widened in the app that vendored it.
    css_own, markup_own = ctx.css_own, ctx.markup_own
    min_m = re.match(r"(\d+)px", ctx.spec_light.get("components.hit-target.min", ""))
    if not min_m:
        return [_result("hit-target", "NA", "spec declares no components.hit-target token")]
    min_px = int(min_m.group(1))

    # classes that carry a negative-inset ::before/::after hit expansion
    expansion_classes: set = set()
    for bm in _BLOCK_RE.finditer(css_own):
        sel_line = _last_selector_line(bm.group(1))
        body = bm.group(2)
        if not (re.search(r"inset:\s*-", body)
                or (re.search(r"(?<![-\w])top:\s*-", body)
                    and re.search(r"(?<![-\w])left:\s*-", body))):
            continue
        expansion_classes.update(_PSEUDO_CLASS_RE.findall(sel_line))

    markup_class_sets = [set(mm.group(1).split())
                         for mm in _CLASS_ATTR_RE.finditer(markup_own)]

    flagged: List[str] = []
    n_candidates = 0
    for bm in _BLOCK_RE.finditer(css_own):
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
            flagged.append(f"{_loc_at(css_own, bm.start())} {comps[-1]} "
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


def _check_spec_contrast(ctx: _ContractsCtx) -> List[dict]:
    # 28. the spec's own pairs clear AA — every `components.<name>` with a
    #     textColor and backgroundColor, composited over `card`, per theme,
    #     from the spec alone (fleet-config#963 found accent-on-accent-soft
    #     at 4.13 / 3.79). The same for every app, so it flags the spec, not
    #     the app. Colour maths is design_review's; imported here, at call
    #     time, because design_review itself imports design_lint.spec.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # skills/_lib, as design_review.rubric does
    from design_review.evaluate import spec_pairs

    low: List[str] = []
    measured = 0
    for theme, tokens in (("light", ctx.spec_light), ("dark", ctx.spec_dark or {})):
        for p in spec_pairs(tokens) if tokens else []:
            if p["ratio"] is None:
                continue
            measured += 1
            if p["ratio"] < p["threshold"]:
                low.append(f"{theme} {p['component']} {p['ratio']}:1 < {p['threshold']}")
    if not measured:
        return [_result("spec-contrast", "NA", "spec declares no measurable text/background pair")]
    if low:
        return [_result("spec-contrast", "WARN",
                         f"{len(low)} spec component pair(s) below AA — a spec fix "
                         "(fleet-config design.md), not an app one: " + "; ".join(low[:6]))]
    return [_result("spec-contrast", "PASS",
                     f"all {measured} spec text/background pairs clear AA in both themes")]


def _check_rendered_leg(ctx: _ContractsCtx) -> List[dict]:
    # 29. rendered leg adoption — a static PASS proves authored CSS, not the
    #     rendered box: hit-target passed on app-launcher while rendered
    #     heights were 33-39px. The shared rendered-geometry helper
    #     (project-scaffolding#157, `tests/e2e/_geometry.py`) is what measures
    #     it. Without it the rendered leg is unmeasured, and says so here
    #     rather than only in the /design-sync prose (fleet-config#969).
    if not ctx.index_files:
        return [_result("rendered-leg", "NA", "no index.html — no rendered UI to measure")]
    helper = ctx.root / "tests" / "e2e" / "_geometry.py"
    if helper.is_file():
        return [_result("rendered-leg", "PASS",
                         "rendered-geometry helper present (tests/e2e/_geometry.py) — run it; "
                         "static PASS alone is not rendered conformance")]
    return [_result("rendered-leg", "WARN",
                     "rendered leg unmeasured: no tests/e2e/_geometry.py "
                     "(project-scaffolding#157), so effective hit rectangles, overlap and "
                     "overflow are unproven whatever the static checks say")]
