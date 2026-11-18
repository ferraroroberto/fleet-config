"""LAYOUT-11 scoring: how many open overlays on a phone screen do not fit the phone viewport.

Pure: reads one screen's `metrics.fit` (written by `walk.py` from `measure_js_fit.FIT_JS`) and returns the
`(value, evidence items, unmeasured reason)` triple `evaluate.py`'s derived metrics return. `evaluate.py` owns the
`n/a` convention, so its prefix is passed in rather than re-spelled here.

stdlib only.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

Derived = Tuple[Optional[float], List[object], Optional[str]]


def _fit_problems(root: dict) -> List[str]:
    """What is wrong with one open overlay at one phone viewport, each as a measured edge; empty when it fits."""
    out: List[str] = []
    vh = root.get("vh")
    if root.get("inside") is False:
        out.append(f"box {root.get('top')}..{root.get('bottom')} past the {vh}px viewport" if vh is not None
                   else f"box {root.get('top')}..{root.get('bottom')} outside the viewport")
    primary = root.get("primary")
    if isinstance(primary, dict):
        if primary.get("state") == "cut":
            out.append(f"{primary.get('sel')} cut off at {primary.get('top')}..{primary.get('bottom')}")
        elif primary.get("state") == "missing":
            out.append(f"declared primary {primary.get('sel')} not in the overlay")
    if isinstance(root.get("overflow"), (int, float)) and root["overflow"] > 0:
        out.append(f"scrolls sideways by {root['overflow']}px")
    return out


def violations(m: dict, ctx: dict, not_applicable: str) -> Derived:
    """LAYOUT-11: how many (overlay, viewport) pairs on a phone screen do not fit.

    A pair is a failure when the overlay's box leaves the viewport, its primary action is cut off or covered, or the
    page or the overlay scrolls sideways. A fact found is a fact whatever else is unknown. A clean count is a pass
    only when an overlay was actually read: a dialog the walk opened that rendered no box, a step declared `overlay`
    that opened none and a declared primary that is missing are `unmeasured`, never a pass. An e2e capture has no
    fit reading and a screen with no overlay open (a tab view, Select mode) has nothing to fit: both n/a.
    """
    if ctx.get("source") == "e2e":
        return None, [], f"{not_applicable}: an e2e capture is not read for fit"
    fit = m.get("fit")
    if not isinstance(fit, dict) or not isinstance(fit.get("viewports"), list):
        return None, [], "overlay fit not read on this screen"
    items: List[object] = []
    seen_roots = 0
    for view in fit["viewports"]:
        if not isinstance(view, dict):
            continue
        for root in view.get("roots") or []:
            seen_roots += 1
            label = str(root.get("id") or root.get("key"))
            problems = _fit_problems({**root, "vh": view.get("vh")})
            if problems:
                items.append({"sel": label, "why": f"{view.get('viewport')}, {ctx.get('theme')}: " + "; ".join(problems)})
    if items:
        return float(len(items)), items, None
    if not seen_roots:
        if ctx.get("kind") == "dialog":
            return None, [], "the dialog rendered no box to measure: its fit is not confirmed"
        if ctx.get("overlay"):
            return None, [], "the step opened no overlay: its fit is not confirmed"
        return None, [], f"{not_applicable}: no overlay open"
    return 0.0, [], None
