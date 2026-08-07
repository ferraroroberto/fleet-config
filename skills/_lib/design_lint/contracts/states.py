"""Async lifecycle-state contract (design.md Async data & feedback).

Data surfaces declare exactly loading/ready/empty/stale/error on `data-state`
and announce changes through a `role="status"` live region. shadcn-convention
interaction states ride the same attribute and are a different channel — never
counted as lifecycle strays.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ._ctx import _ContractsCtx, _loc_at, _result


_LIFECYCLE_VOCAB = {"loading", "ready", "empty", "stale", "error"}
# shadcn-convention interaction states ride the same data-state attribute but
# are a different channel from the async lifecycle — never lifecycle strays.
_INTERACTION_STATES = {"open", "closed", "checked", "unchecked", "on", "off",
                       "active", "inactive", "expanded", "collapsed",
                       "visible", "hidden", "selected"}
# The live region can be declared in markup or set from JS — a JS-rendered
# drawer/toast is exactly the surface this contract exists for, and the
# lifecycle half of the same check already reads `dataset.state = '...'`.
# Grepping only the markup spelling made those surfaces unpassable (#416).
_ROLE_STATUS_RE = re.compile(
    r'role=["\']status["\']'
    r'|setAttribute\(\s*["\']role["\']\s*,\s*["\']status["\']\s*\)'
    r'|\.role\s*=\s*["\']status["\']',
    re.I)


def _check_async_lifecycle(ctx: _ContractsCtx) -> List[dict]:
    # 24. async lifecycle vocabulary — data surfaces declare the five-state
    #     set loading/ready/empty/stale/error on data-state and announce
    #     changes via a role="status" live region (design.md Async data &
    #     feedback; home-automation#409). Static view: literal data-state
    #     values in markup/JS/CSS; runtime-only assignments are the rendered
    #     leg's problem.
    markup_all, css_all = ctx.markup_all, ctx.css_all
    found: Dict[str, str] = {}
    for pat, blob in (
        (r'data-state=["\']([\w-]+)["\']', markup_all),
        (r'dataset\.state\s*=\s*["\']([\w-]+)["\']', markup_all),
        (r'\[data-state=["\']?([\w-]+)', css_all),
    ):
        for mm in re.finditer(pat, blob):
            found.setdefault(mm.group(1), _loc_at(blob, mm.start()))
    if not found:
        return [_result("async-lifecycle", "NA",
                        "no data-state lifecycle surface found")]
    lifecycle = sorted(v for v in found if v in _LIFECYCLE_VOCAB)
    strays = sorted(v for v in found
                    if v not in _LIFECYCLE_VOCAB and v not in _INTERACTION_STATES)
    if not lifecycle:
        return [_result("async-lifecycle", "NA",
            "data-state carries only interaction states ("
            + ", ".join(sorted(found)) + ") — the async lifecycle vocabulary "
            "(loading/ready/empty/stale/error) is not adopted")]
    if strays:
        return [_result("async-lifecycle", "WARN",
            "lifecycle data-state mixes non-canonical values — the async "
            "vocabulary is exactly loading/ready/empty/stale/error "
            "(design.md Async data & feedback): "
            + "; ".join(f"{v} at {found[v]}" for v in strays[:6]))]
    if not _ROLE_STATUS_RE.search(markup_all):
        return [_result("async-lifecycle", "WARN",
            'lifecycle states used with no role="status" live region — '
            "state changes are not announced (design.md Async data & feedback)")]
    return [_result("async-lifecycle", "PASS",
        "lifecycle vocabulary (" + ", ".join(lifecycle) + ") within the "
        'canonical five states; role="status" live region present')]
