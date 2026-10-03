"""Stage 3b of /design-review — metrics + rubric -> rule results and grades (fleet-config#971).

Pure: reads a `metrics.json` document and a `Rubric`, returns one JSON
document. No browser, no LLM, no git. Same inputs give byte-identical
`rules` / `categories` (only `generated_at` moves), which is what lets #974
diff two runs by rule id.

Scoring (settled here, per the issue's proposal): each category starts at
100 and loses `[penalties].<severity>` (25 / 12 / 6 / 2) once per failing
rule — never per instance — and maps to a letter through `[grades]`. The
overall score is the `[weights]`-weighted mean of the category scores. An
`unmeasured` rule neither passes nor fails; a category with one is flagged
`unmeasured: true`, as is the overall verdict.

Rule status lattice, most conservative wins:

    fail        > unmeasured > pass
    any screen  any screen   every applicable screen measured, none failed

A screen the walk could not open, a section the script could not compute,
a metric the rubric names but the script did not produce, and a target that
was not listening at all each make the affected rules `unmeasured` with the
concrete `reason` — never a pass. A step screen whose target never appeared
(`status: "absent"`, #995) is none of these: the surface does not exist in
this app state, so it is left out of every rule, named in the rule's reason
and in `absent_screens` -- and a rule left with no other screen is still
`unmeasured`, never a vacuous pass.

Output document:

    schema_version, rubric_version, target, commit, generated_at,
    metrics_generated_at, base_url, run_dir,
    synthetic_declared: bool   # the target declares [design.review.synthetic]; the report's `states not walked` note reads it (#1163)
    params:     {hit_min, primary_min, icon_steps}   # the resolved measurement floors
    screens:    [{id, device, theme, view, kind, status, reason}]
    absent_screens: [id]   # step screens whose target never appeared (#995)
    rules:      [{id, category, severity, owner, title, standard, fix_template,
                  mockup, params, status, reason, threshold: {value, source},
                  evidence: [{screen, value, items: [...], facts: {...}}],
                  measured: {screen: value}}]
    categories: {<category>: {score, grade, unmeasured, failed: [id], unmeasured_rules: [id]}}
    overall:    {score, grade, unmeasured}

`spec.*` rules evaluate the design system itself (light and dark token
files), so their `screen` keys are `spec-light` / `spec-dark`.

`params` (per rule: the rubric's `params` table; per document: the resolved
floors) and `evidence[].facts` (the numerator/denominator behind a share —
`{count, total}` for `targets.small_share`, empty otherwise) are additive
keys added for the renderer (#972): every `fix_template` placeholder must be
fillable from this document alone, never from a re-read of `metrics.json`.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import measure
from .rubric import Rubric, Rule, resolve_params, resolve_threshold

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # skills/_lib, as design_review.rubric does
from design_lint.colormath import (  # noqa: E402
    AA_LARGE,
    AA_NORMAL,
    RGBA,
    _hex,
    composite,
    contrast,
    luminance,
    parse_color,
    spec_pairs,
)
from design_lint.rules import BREAK_ALL_OK  # noqa: E402

SCHEMA_VERSION = 1
EVIDENCE_ITEMS = 8

# ---- derived metrics -------------------------------------------------------

# value, sample items, unmeasured reason — and, for a share metric, a 4th
# element: the `facts` dict whose numerator/denominator survive into the
# evidence (the renderer's `{count} of {total}`). `read_metric` normalises
# both shapes to the 4-tuple.
Derived = Tuple[Optional[float], List[object], Optional[str]]
DerivedWithFacts = Tuple[Optional[float], List[object], Optional[str], Dict[str, object]]

# A screen the rule has nothing to say about (no primary action to size, no
# text to histogram): skipped, counted separately from `unmeasured`. A rule
# that is N/A on every screen still reports `pass` — vacuously, and says so.
NOT_APPLICABLE = "n/a"
ABSENT = "absent"  # walk.py's status for a step whose target never appeared (STEP_TARGET_ABSENT, #995)


def _share(num: object, den: object) -> Optional[float]:
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)):
        return None
    return 0.0 if den == 0 else round(float(num) / float(den), 4)


def _d_small_share(m: dict, rule: Rule, ctx: dict) -> DerivedWithFacts:
    total, count = measure.metric_value(m, "targets.total"), measure.metric_value(m, "targets.small_count")
    if total is None or count is None:
        return None, [], _section_reason(m, "targets"), {}
    return _share(count, total), list(measure.metric_value(m, "targets.small") or []), None, {"count": count, "total": total}


def _d_under14_share(m: dict, rule: Rule, ctx: dict) -> Derived:
    runs, under = measure.metric_value(m, "text.runs"), measure.metric_value(m, "text.under14")
    if runs is None or under is None:
        return None, [], _section_reason(m, "text")
    if runs == 0:
        return None, [], NOT_APPLICABLE
    return _share(under, runs), [], None


def _d_icons_off_step(m: dict, rule: Rule, ctx: dict) -> Derived:
    boxes = measure.metric_value(m, "icons.boxes")
    steps = ctx.get("params", {}).get("icon_steps")
    if boxes is None:
        return None, [], _section_reason(m, "icons")
    if not steps:
        return None, [], "icons.size steps unresolved from the spec"
    tol = float(rule.params.get("tolerance_px", 1.0))
    # Which element each box is (#1020); a run from before that key has none, never an error.
    elements = measure.metric_value(m, "icons.elements") or {}
    off: List[object] = []
    for key, n in sorted(boxes.items()):
        try:
            w, h = (float(x) for x in key.split("x"))
        except ValueError:
            continue
        if not any(abs(w - s) <= tol and abs(h - s) <= tol for s in steps):
            off.append({"box": key, "count": n, "elements": list(elements.get(key) or [])})
    return float(len(off)), off, None


def _d_spec_pairs(theme_tokens: Dict[str, str]) -> Derived:
    if not theme_tokens:
        return None, [], "spec tokens unavailable"
    pairs = spec_pairs(theme_tokens)
    if not pairs:
        return None, [], "no components with textColor+backgroundColor in the spec"
    bad = [p for p in pairs if p["ratio"] is None or p["ratio"] < p["threshold"]]  # type: ignore[operator]
    return float(len(bad)), bad, None


def _filtered_count(m: dict, section: str, key: str, rule: Rule, default_allow: str) -> Derived:
    items = measure.metric_value(m, f"{section}.{key}")
    if items is None:
        return None, [], _section_reason(m, section)
    allow = re.compile(str(rule.params.get("allow", default_allow)), re.I)
    kept = [it for it in items if not allow.search(str(it.get("sel", "")))]  # type: ignore[union-attr]
    return float(len(kept)), kept, None


def _d_uppercase(m: dict, rule: Rule, ctx: dict) -> Derived:
    return _filtered_count(m, "text", "uppercase", rule, r"overline")


def _d_break_all(m: dict, rule: Rule, ctx: dict) -> Derived:
    return _filtered_count(m, "text", "break_all", rule, BREAK_ALL_OK)


def _d_lists_tall(m: dict, rule: Rule, ctx: dict) -> Derived:
    lists = measure.metric_value(m, "layout.lists")
    pane_h, inner_h = measure.metric_value(m, "layout.pane_h"), measure.metric_value(m, "layout.inner_h")
    if lists is None or pane_h is None or inner_h is None:
        return None, [], _section_reason(m, "layout")
    viewports = float(rule.params.get("viewports", 5))
    tall = isinstance(inner_h, (int, float)) and inner_h > 0 and pane_h > viewports * inner_h
    bad = [l for l in lists if not l.get("has_filter")] if tall else []  # type: ignore[union-attr]
    return float(len(bad)), bad, None


def _d_content_share(m: dict, rule: Rule, ctx: dict) -> Derived:
    """design.md's width rule (#996, #1113): width follows the shape of the view.

    A list-and-detail tab spans list + detail (`content_span`). A view the app
    declares wide (`[design] wide_views`) must actually fill the window: its
    span is held to the same share floor, with no exemption for the centred
    measure. An undeclared view keeps the measure: a column holding it (less a
    gutter each side) is exempt, a column *wider* than it with no detail pane
    docked beside it is a one-dimensional view stretched across the window
    (value 0.0, `arm: stretched`), and anything narrower falls to the share
    floor. Older metrics without `content_span` fall back to the pane width."""
    cw, iw = measure.metric_value(m, "layout.content_w"), measure.metric_value(m, "layout.inner_w")
    if cw is None or iw is None:
        return None, [], _section_reason(m, "layout")
    min_w = float(rule.params.get("min_viewport", 1100))
    if not isinstance(iw, (int, float)) or iw < min_w:
        return None, [], NOT_APPLICABLE
    if ctx.get("wide_views_error"):
        return None, [], f"wide_views: {ctx['wide_views_error']}"
    params = ctx.get("params", {})
    measure_px = float(params.get("measure") or 772)
    held = measure_px - 2 * float(params.get("gutter") or 12)
    declared_wide = ctx.get("view") in set(ctx.get("wide_views") or ())
    span = measure.metric_value(m, "layout.content_span")
    span = span if isinstance(span, (int, float)) else cw
    if not declared_wide and isinstance(cw, (int, float)):
        if held - 1 <= cw <= measure_px + 1:
            return None, [], NOT_APPLICABLE
        if cw > measure_px + 1 and span <= cw + 1:
            return 0.0, [{"content_w": cw, "content_span": span, "inner_w": iw, "arm": "stretched"}], None
    return _share(span, iw), [{"content_w": cw, "content_span": span, "inner_w": iw}], None


def _d_zoom_locked(m: dict, rule: Rule, ctx: dict) -> Derived:
    locked, ctl = measure.metric_value(m, "a11y.zoom_locked"), measure.metric_value(m, "a11y.text_size_control")
    if locked is None or ctl is None:
        return None, [], _section_reason(m, "a11y")
    if locked and not ctl:
        # The control is one fact about the app, not about a screen: Settings is rendered only while open, so it
        # is absent from every other screen. Seen on any walked screen -> the app has it (#1185).
        if ctx.get("text_size_control_seen"):
            return 0.0, [{"zoom_locked": locked, "text_size_control": False, "seen_on_another_screen": True}], None
        # The vendored boot script stamped <html data-textsize> but no walked screen showed the control: it lives
        # in a pane this walk never opened. Not established -> unmeasured, never a pass and never a false fail.
        if measure.metric_value(m, "a11y.text_size_stamped") is True:
            return None, [], ("text size: <html data-textsize> is stamped but no walked screen showed the control "
                              "(its Settings pane was not opened) -- declare the Settings step in [design.review] extra_steps")
    return (1.0 if (locked and not ctl) else 0.0), [{"zoom_locked": locked, "text_size_control": ctl}], None


def _d_pane_header_hidden(m: dict, rule: Rule, ctx: dict) -> Derived:
    vis = measure.metric_value(m, "nav.pane_header_visible")
    top = measure.metric_value(m, "nav.pane_scroll_top")
    if "nav" in measure.section_errors(m):
        return None, [], _section_reason(m, "nav")
    hidden = (vis is False) or (isinstance(top, (int, float)) and top > 0)
    return (1.0 if hidden else 0.0), [{"pane_header_visible": vis, "pane_scroll_top": top}], None


# One rgb step per channel of slack: the measured track is composited and rounded, the spec token is a literal.
SWITCH_ACCENT_TOLERANCE = 3.0


def _accent_colors(tokens: Dict[str, str]) -> List[tuple]:
    out = []
    for name in ("colors.accent-fill", "colors.accent"):
        rgba = parse_color(tokens.get(name, ""), tokens) if tokens.get(name) else None
        if rgba is not None:
            out.append(rgba[:3])
    return out


def _d_switch_on_accent(m: dict, rule: Rule, ctx: dict) -> Derived:
    """Count of on switches whose track is not the theme's accent (#1200).

    The on-colour is one fact about the app, so an off-only screen defers to an on-state seen anywhere in
    the run; a run that never saw a switch on, or could not read its track, is `unmeasured`, never a pass.
    """
    total, on = measure.metric_value(m, "controls.switch_count"), measure.metric_value(m, "controls.switches_on")
    if total is None or on is None:
        return None, [], _section_reason(m, "controls")
    if not total:
        return None, [], NOT_APPLICABLE
    if not on:
        if ctx.get("switch_on_seen"):
            return None, [], NOT_APPLICABLE
        return None, [], "switches on this screen are all off: their on-colour is not established"
    accents = _accent_colors((ctx.get("specs") or {}).get(str(ctx.get("theme")), {}))
    if not accents:
        return None, [], "spec accent token unavailable"
    bad, unread = [], 0
    for sw in on:  # type: ignore[union-attr]
        track = parse_color(str(sw.get("track") or ""), {})
        if track is None:
            unread += 1
            continue
        if not any(max(abs(track[i] - a[i]) for i in range(3)) <= SWITCH_ACCENT_TOLERANCE for a in accents):
            bad.append({"sel": sw.get("sel"), "track": sw.get("track"), "expected": [_hex(a) for a in accents]})
    if bad:
        return float(len(bad)), bad, None
    if unread:
        return None, [], f"{unread} switch(es) on, track colour not readable"
    return 0.0, [], None


def _hex(rgb: tuple) -> str:
    return "#" + "".join(f"{round(c):02x}" for c in rgb)


DERIVED: Dict[str, Callable[[dict, Rule, dict], tuple]] = {
    "targets.small_share": _d_small_share,
    "text.under14_share": _d_under14_share,
    "icons.off_step_count": _d_icons_off_step,
    "text.uppercase_outside_role": _d_uppercase,
    "text.break_all_non_path": _d_break_all,
    "layout.lists_unfiltered_tall": _d_lists_tall,
    "layout.content_share": _d_content_share,
    "a11y.zoom_locked_no_control": _d_zoom_locked,
    "nav.pane_header_hidden": _d_pane_header_hidden,
    "controls.switch_on_not_accent": _d_switch_on_accent,
}


# aggregate metric -> the list it summarises; empty list => the aggregate is N/A
_POPULATIONS = {
    "targets.primary_min_height": "targets.primary",
    "text.min_px": "text.runs",
}

# metrics with no list of their own borrow the sample that explains a failure
_SAMPLE_SOURCES: Dict[str, Callable[[dict], List[object]]] = {
    "text.min_px": lambda m: list(measure.metric_value(m, "text.under11") or []),
    "layout.overflow_x": lambda m: [{"scroll_w": measure.metric_value(m, "layout.scroll_w"),
                                     "inner_w": measure.metric_value(m, "layout.inner_w")}],
}


def _empty_population(m: dict, metric: str) -> bool:
    src = _POPULATIONS.get(metric)
    if not src:
        return False
    pop = measure.metric_value(m, src)
    return pop == [] or pop == 0


def _section_reason(m: dict, section: str) -> str:
    err = measure.section_errors(m).get(section)
    return f"section {section}: {err}" if err else f"section {section}: metric missing"


def read_metric(m: dict, rule: Rule, ctx: dict) -> DerivedWithFacts:
    """`(value, sample items, unmeasured reason, facts)` for one rule on one screen."""
    if rule.metric in DERIVED:
        out = DERIVED[rule.metric](m, rule, ctx)
        return out if len(out) == 4 else (out[0], out[1], out[2], {})  # type: ignore[return-value]
    value = measure.metric_value(m, rule.metric)
    section = rule.metric.split(".", 1)[0]
    if value is None:
        if section in measure.section_errors(m):
            return None, [], _section_reason(m, section), {}
        # A null aggregate over an empty population (`targets.primary_min_height`
        # with no primary action on the screen) is N/A, not a failure to measure.
        if _empty_population(m, rule.metric):
            return None, [], NOT_APPLICABLE, {}
        return None, [], _section_reason(m, section), {}
    items: List[object] = []
    if rule.metric.endswith("_count"):
        # `<name>_count` summarises `<name>` — or its plural (`glyph_icons`, `overlaps`).
        base = rule.metric[: -len("_count")]
        items = list(measure.metric_value(m, base) or measure.metric_value(m, base + "s") or [])
    elif rule.metric in _SAMPLE_SOURCES:
        items = _SAMPLE_SOURCES[rule.metric](m)
    elif isinstance(value, list):
        items, value = list(value), float(len(value))
    if isinstance(value, bool):
        value = 1.0 if value else 0.0
    if not isinstance(value, (int, float)):
        return None, [], f"metric {rule.metric} is not numeric", {}
    return float(value), items, None, {}


def fails(value: float, fail_when: str, threshold: Optional[float]) -> bool:
    if fail_when == "true":
        return value != 0
    if fail_when == "nonzero":
        return value != 0
    if threshold is None:
        return False
    return {
        "gt": value > threshold, "gte": value >= threshold,
        "lt": value < threshold, "lte": value <= threshold,
        "eq": value == threshold, "ne": value != threshold,
    }[fail_when]


# ---- the evaluation --------------------------------------------------------


def _applies(rule: Rule, screen: dict) -> bool:
    if rule.devices and screen.get("device") not in rule.devices:
        return False
    if rule.screens and screen.get("kind") not in rule.screens:
        return False
    return True


def evaluate_rule(rule: Rule, doc: dict, specs: Dict[str, Dict[str, str]], ctx: dict) -> Dict[str, object]:
    threshold = resolve_threshold(rule, specs.get("light", {}))
    thr = threshold["value"]
    result: Dict[str, object] = {
        "id": rule.id, "category": rule.category, "severity": rule.severity, "owner": rule.owner,
        "title": rule.title, "standard": rule.standard, "fix_template": rule.fix_template, "mockup": rule.mockup,
        "metric": rule.metric, "fail_when": rule.fail_when, "threshold": threshold, "params": dict(rule.params),
        **({"adopt": rule.adopt} if rule.adopt else {}),
        "status": "unmeasured", "reason": "", "evidence": [], "measured": {},
    }
    if doc.get("unmeasured"):
        u = doc["unmeasured"]
        result["reason"] = f"{u.get('reason', 'UNMEASURED')}: {u.get('detail', '')}".strip(": ")
        return result
    if thr is None and rule.fail_when not in ("true", "nonzero"):
        result["reason"] = f"threshold unresolved ({threshold['source']})"
        return result

    tally = _Tally()
    if rule.metric.startswith("spec."):
        legs = [("spec-light", specs.get("light", {})), ("spec-dark", specs.get("dark", {}))]
        for sid, tokens in legs:
            value, items, why = _d_spec_pairs(tokens)
            _fold(result, tally, sid, value, items, why, rule, thr, {})
    else:
        for screen in doc.get("screens", []):
            if not _applies(rule, screen):
                continue
            sid = str(screen.get("id"))
            if screen.get("status") == ABSENT:
                tally.absent += 1
                continue
            if screen.get("status") != "ok" or not isinstance(screen.get("metrics"), dict):
                tally.unmeasured.append(f"{sid}: {screen.get('reason') or 'walk error'}")
                continue
            value, items, why, facts = read_metric(screen["metrics"], rule,
                                                   {**ctx, "view": screen.get("view"), "theme": screen.get("theme"), "specs": specs})
            _fold(result, tally, sid, value, items, why, rule, thr, facts)

    na = f"; n/a on {tally.not_applicable}" if tally.not_applicable else ""
    na += f"; step target absent on {tally.absent}" if tally.absent else ""
    if tally.failed:
        result["status"] = "fail"
        unm = f"; {len(tally.unmeasured)} unmeasured" if tally.unmeasured else ""
        result["reason"] = f"{len(tally.failed)} screen(s) fail{unm}{na}"
    elif tally.unmeasured:
        result["status"] = "unmeasured"
        shown = tally.unmeasured[:4]
        more = f" (+{len(tally.unmeasured) - 4} more)" if len(tally.unmeasured) > 4 else ""
        result["reason"] = "; ".join(shown) + more
    elif tally.passed:
        result["status"] = "pass"
        result["reason"] = f"{tally.passed} screen(s) measured, none over threshold{na}"
    elif tally.not_applicable:
        result["status"] = "pass"
        result["reason"] = f"not applicable on any of {tally.not_applicable} screen(s) (vacuous pass)"
    else:
        result["status"] = "unmeasured"
        result["reason"] = "no applicable screen in this run" + na
    return result


class _Tally:
    def __init__(self) -> None:
        self.failed: List[dict] = []
        self.unmeasured: List[str] = []
        self.passed = 0
        self.not_applicable = 0
        self.absent = 0  # step screens whose target never appeared (#995): neither measured nor unmeasured


def _fold(result: dict, tally: _Tally, sid: str, value: Optional[float], items: List[object],
          why: Optional[str], rule: Rule, thr: Optional[float], facts: Dict[str, object]) -> None:
    if why == NOT_APPLICABLE:
        tally.not_applicable += 1
        return
    if why is not None or value is None:
        tally.unmeasured.append(f"{sid}: {why or 'no value'}")
        return
    result["measured"][sid] = value
    if fails(value, rule.fail_when, thr):
        ev = {"screen": sid, "value": value, "items": items[:EVIDENCE_ITEMS], "facts": dict(facts)}
        tally.failed.append(ev)
        result["evidence"].append(ev)
    else:
        tally.passed += 1


def grade_for(score: float, grades: Dict[str, float]) -> str:
    best = "F"
    best_min = -1.0
    for letter, minimum in grades.items():
        if score >= minimum and minimum > best_min:
            best, best_min = letter, minimum
    return best


def score_categories(rules: List[dict], rubric: Rubric) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for cat in rubric.categories:
        mine = [r for r in rules if r["category"] == cat]
        failed = [r["id"] for r in mine if r["status"] == "fail"]
        unmeasured = [r["id"] for r in mine if r["status"] == "unmeasured"]
        score = 100.0
        for r in mine:
            if r["status"] == "fail":
                score -= rubric.penalties.get(r["severity"], 0.0)
        score = max(0.0, round(score, 2))
        out[cat] = {"score": score, "grade": grade_for(score, rubric.grades), "unmeasured": bool(unmeasured),
                    "failed": failed, "unmeasured_rules": unmeasured}
    return out


def score_overall(categories: Dict[str, dict], rubric: Rubric) -> Dict[str, object]:
    total_w = sum(rubric.weights.get(c, 1.0) for c in categories)
    score = round(sum(v["score"] * rubric.weights.get(c, 1.0) for c, v in categories.items()) / total_w, 2) if total_w else 0.0
    return {"score": score, "grade": grade_for(score, rubric.grades),
            "unmeasured": any(v["unmeasured"] for v in categories.values())}


def evaluate(doc: dict, rubric: Rubric, specs: Dict[str, Dict[str, str]],
             now: Optional[_dt.datetime] = None) -> Dict[str, object]:
    """The whole stage: one metrics document -> rule results + grades."""
    ctx = {"params": resolve_params(rubric, specs.get("light", {})),
           "wide_views": [str(v) for v in doc.get("wide_views") or []],
           "wide_views_error": doc.get("wide_views_error"),
           "switch_on_seen": any(
               isinstance(s, dict) and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)
               and bool(measure.metric_value(s["metrics"], "controls.switches_on"))
               for s in doc.get("screens") or []),
           "text_size_control_seen": any(
               isinstance(s, dict) and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)
               and measure.metric_value(s["metrics"], "a11y.text_size_control") is True
               for s in doc.get("screens") or [])}
    rules = [evaluate_rule(rule, doc, specs, ctx) for rule in rubric.rules]
    categories = score_categories(rules, rubric)
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": rubric.version,
        "target": doc.get("target"),
        "commit": doc.get("commit"),
        "generated_at": stamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "metrics_generated_at": doc.get("generated_at"),
        "metrics_schema_version": doc.get("schema_version"),
        "metrics_rubric_version": doc.get("rubric_version"),
        "base_url": doc.get("base_url"),
        "mode": doc.get("mode") or "live",
        "synthetic_declared": isinstance((doc.get("review") or {}).get("synthetic"), dict),
        "run_dir": doc.get("run_dir"),
        "params": ctx["params"],
        "unmeasured": doc.get("unmeasured"),
        "screens": [{k: s.get(k) for k in ("id", "device", "theme", "view", "kind", "status", "reason")}
                    for s in doc.get("screens", [])],
        "absent_screens": [str(s.get("id")) for s in doc.get("screens", []) if s.get("status") == ABSENT],
        "rules": rules,
        "categories": categories,
        "overall": score_overall(categories, rubric),
    }
