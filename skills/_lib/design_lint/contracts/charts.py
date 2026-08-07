"""Chart.js contracts (design.md Charts; home-automation#409).

A phone-width tick budget, and a second visual channel beside colour for every
multi-series chart (WCAG 2.2 Use of Color).
"""
from __future__ import annotations

import re
from typing import List

from ._ctx import _ContractsCtx, _evidence, _result


def _check_chart_tick_budget(ctx: _ContractsCtx) -> List[dict]:
    # 22. chart tick budget — Chart.js axes cap their label count
    #     (maxTicksLimit / autoSkip) with zero rotation so phone-width
    #     x-axes never collide or rotate (design.md Charts;
    #     home-automation#409).
    markup_all = ctx.markup_all
    if not re.search(r"\bnew\s+Chart\s*\(", markup_all):
        return [_result("chart-tick-budget", "NA", "no Chart.js usage found")]
    knobs = [k for k in ("maxTicksLimit", "autoSkip", "maxRotation")
             if re.search(rf"\b{k}\b", markup_all)]
    if "maxTicksLimit" not in knobs and "autoSkip" not in knobs:
        return [_result("chart-tick-budget", "WARN",
            "Chart.js used with no authored tick budget (maxTicksLimit / "
            "autoSkip) — phone-width x-axis labels collide (design.md Charts)",
            _evidence(markup_all, r"\bnew\s+Chart\s*\("))]
    detail = "tick budget authored (" + ", ".join(knobs) + ")"
    if "maxRotation" not in knobs:
        detail += "; no maxRotation — labels may rotate on narrow viewports"
    return [_result("chart-tick-budget", "PASS", detail,
                    _evidence(markup_all, r"\bmaxTicksLimit\b|\bautoSkip\b"))]


def _check_chart_noncolor_cue(ctx: _ContractsCtx) -> List[dict]:
    # 23. non-colour series cue — every colour-distinguished Chart.js series
    #     carries a second visual channel (borderDash / pointStyle / fill
    #     treatment), per WCAG 2.2 Use of Color (design.md Charts;
    #     home-automation#409).
    markup_all = ctx.markup_all
    if not re.search(r"\bnew\s+Chart\s*\(", markup_all):
        return [_result("chart-noncolor-cue", "NA", "no Chart.js usage found")]
    n_colored = len(re.findall(r"\bborderColor\b", markup_all))
    if n_colored < 2:
        return [_result("chart-noncolor-cue", "NA",
            "fewer than two colour-assigned dataset sites — single-series "
            "charts need no second channel")]
    cue_pat = r"\bborderDash\b|\bpointStyle\b|\bfill\s*:"
    cues = [k for k, p in (("borderDash", r"\bborderDash\b"),
                           ("pointStyle", r"\bpointStyle\b"),
                           ("fill", r"\bfill\s*:"))
            if re.search(p, markup_all)]
    if cues:
        return [_result("chart-noncolor-cue", "PASS",
            f"{n_colored} colour-assigned dataset sites carry non-colour "
            "cue(s): " + ", ".join(cues),
            _evidence(markup_all, cue_pat))]
    return [_result("chart-noncolor-cue", "WARN",
        f"{n_colored} colour-assigned Chart.js dataset sites with no "
        "borderDash / pointStyle / fill second channel — colour is the only "
        "series cue (design.md Charts; WCAG Use of Color)",
        _evidence(markup_all, r"\bborderColor\b"))]
