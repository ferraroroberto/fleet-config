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
concrete `reason` — never a pass. A screen whose app rendered another theme than the walk asked for (`theme_applied: false`, #1216)
is `unmeasured` for the rules the rubric marks `theme_dependent`, naming both themes -- never a pass and
never a failure against the wrong palette -- while every other rule still scores it.
A step screen whose target never appeared
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
    theme_mismatch_screens: [{id, requested, rendered}]   # screens whose app did not render the requested theme (#1216)
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

from . import measure, plan
from .rubric import Rubric, Rule, resolve_params, resolve_threshold

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # skills/_lib, as design_review.rubric does
from design_lint.colormath import _hex, parse_color, spec_pairs  # noqa: E402
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
    """TYPE-05: a run drawn in capitals outside the overline role, whether `text-transform` made it so or the
    characters typed in the markup already are (#1365). A literal run is a button, tab, menu item or heading label
    with no lowercase letter whose every word is not on the rule's `acronyms` allow-list (an API, a PDF, a language
    chip). A run from before the `casing` section existed, or one whose section failed, is read as `text.uppercase` alone."""
    value, items, why = _filtered_count(m, "text", "uppercase", rule, r"overline")
    if why is not None:
        return value, items, why
    labels = measure.metric_value(m, "casing.labels")
    if labels is None:
        return value, items, None
    acronyms = re.compile(str(rule.params.get("acronyms", "")))
    seen = {(i.get("sel"), i.get("text")) for i in items if isinstance(i, dict)}  # type: ignore[union-attr]
    for lab in labels:  # type: ignore[union-attr]
        if not (lab.get("literal") and lab.get("style") == "upper") or (lab.get("sel"), lab.get("text")) in seen:
            continue
        if all(acronyms.fullmatch(re.sub(r"^\W+|\W+$", "", w)) for w in str(lab.get("text", "")).split() if re.search(r"[^\W\d_]", w)):
            continue
        items.append({"sel": lab.get("sel"), "text": lab.get("text"), "why": f"typed in capitals, {lab.get('cls')}"})
    return float(len(items)), items, None


# Two styles of one label class clash when the first letter's case differs, or a sentence-case label sits beside a
# title-case one. `capitalised` (a single capitalised word) is both sentence and title case, so it clashes only
# with `lower`; `upper` is TYPE-05's, never a style here.
_CAP_FIRST = ("capitalised", "sentence", "title")
_CASING_CLASHES = ((("lower",), _CAP_FIRST), (("sentence",), ("title",)))
_CASING_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def _casing_vocab(labels: List[dict]) -> set:
    """Every word the run's labels set in lowercase, so a capitalised word is known not to be a proper noun."""
    return {w for lab in labels for w in _CASING_WORD.findall(str(lab.get("text", ""))) if w.islower()}


def _casing_style(lab: dict, vocab: set) -> Optional[str]:
    """A label's style for the TYPE-06 comparison: the measured one, except that a `title` label resting on two or fewer
    capitalised words after the first is ambiguous (`None`, not compared): a language, a brand or a product name is
    capitalised in a sentence ("Launch Docker + Langfuse", #1463). Three such words, or any word the run also writes in
    lowercase elsewhere, confirm title case."""
    style = lab.get("style")
    if style != "title":
        return str(style) if style else None
    rest = [w for w in _CASING_WORD.findall(str(lab.get("text", "")))[1:] if w[0].isupper()]
    return "title" if len(rest) >= 3 or any(w.lower() in vocab for w in rest) else None


def _casing_outliers(labels: List[dict]) -> Dict[str, Dict[str, str]]:
    """Per label class, `{outlier style: the style(s) it clashes with}` across every label recorded in the run.

    The side of a clash with fewer distinct labels is the outlier; a tie marks both sides."""
    vocab = _casing_vocab(labels)
    texts: Dict[str, Dict[str, set]] = {}
    for lab in labels:
        style, cls = _casing_style(lab, vocab), lab.get("cls")
        if style and style != "upper" and cls:
            texts.setdefault(str(cls), {}).setdefault(style, set()).add(str(lab.get("text")))
    out: Dict[str, Dict[str, str]] = {}
    for cls, styles in sorted(texts.items()):
        distinct = lambda group: len({t for st in group for t in styles.get(st, ())})  # noqa: E731
        outliers: Dict[str, str] = {}
        for left, right in _CASING_CLASHES:
            nl, nr = distinct(left), distinct(right)
            if not nl or not nr:
                continue
            for group, mine, theirs, other in ((left, nl, nr, right), (right, nr, nl, left)):
                if mine <= theirs:
                    for st in group:
                        if st in styles:
                            outliers.setdefault(st, "/".join(o for o in other if o in styles))
        if outliers:
            out[cls] = outliers
    return out


def _d_casing_mixed(m: dict, rule: Rule, ctx: dict) -> Derived:
    """TYPE-06: a label cased unlike the rest of its class anywhere in the run (#1365).

    The class is a button, tab, menu item or heading, or a stat or label element named by one class. Casing is a
    choice the app makes for the whole class, so the comparison runs across every screen of the run and each
    screen is scored on the labels of it that are the minority."""
    labels = measure.metric_value(m, "casing.labels")
    if labels is None:
        return None, [], _section_reason(m, "casing")
    outliers, vocab = ctx.get("casing_outliers") or {}, ctx.get("casing_vocab") or set()
    bad = []
    for lab in labels:  # type: ignore[union-attr]
        style = _casing_style(lab, vocab)
        if style in (outliers.get(lab.get("cls")) or {}):
            bad.append({"sel": lab.get("sel"), "text": lab.get("text"),
                        "why": f"{style} case, the rest of {lab.get('cls')} is {outliers[lab['cls']][style]}"})
    return float(len(bad)), bad, None


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

    A list-and-detail tab spans list + detail (`content_span`); a pane that
    holds two regions side by side itself (`pane_split`, #1361) is
    two-dimensional the same way, declared or not. A view the app
    declares wide (`[design] wide_views`) must actually fill the window: its
    span is held to the same share floor, with no exemption for the centred
    measure. An undeclared view keeps the measure: a column holding it (less a
    gutter each side) is exempt, a column *wider* than it with no detail pane
    docked beside it, inside or outside the pane, is a one-dimensional view
    stretched across the window (value 0.0, `arm: stretched`), and anything narrower falls to the share
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
        if cw > measure_px + 1 and span <= cw + 1 and measure.metric_value(m, "layout.pane_split") is not True:
            return 0.0, [{"content_w": cw, "content_span": span, "inner_w": iw, "arm": "stretched"}], None
    return _share(span, iw), [{"content_w": cw, "content_span": span, "inner_w": iw}], None


def _d_no_text_size_control(m: dict, rule: Rule, ctx: dict) -> Derived:
    """A11Y-02: the app carries the vendored text-size control, whether or not its viewport locks zoom (fleet-config#1211).

    The control is the fact judged. `zoom_locked` is evidence only: a viewport that cannot pinch-zoom makes a missing
    control worse, but an app that allows pinch still owes the Settings control (Roberto's decision, 2026-10-03)."""
    ctl = measure.metric_value(m, "a11y.text_size_control")
    if ctl is None:
        return None, [], _section_reason(m, "a11y")
    locked = measure.metric_value(m, "a11y.zoom_locked")
    if not ctl:
        # The control is one fact about the app, not about a screen: Settings is rendered only while open, so it
        # is absent from every other screen. Seen on any walked screen -> the app has it (#1185).
        if ctx.get("text_size_control_seen"):
            return 0.0, [{"zoom_locked": locked, "text_size_control": False, "seen_on_another_screen": True}], None
        # The vendored boot script stamped <html data-textsize> but no walked screen showed the control. When the walk
        # opened the Settings gear and still did not see it, it is missing (fails, below); when the pane was never
        # opened (no gear, or the click refused or failed) it is not established -> unmeasured, never a pass and
        # never a false fail (#1217).
        if measure.metric_value(m, "a11y.text_size_stamped") is True and ctx.get("settings_gear") != "ok":
            gear = {"absent": "no Settings gear was found to open (SETTINGS_GEAR_ABSENT)",
                    "error": "the Settings gear could not be opened"}.get(str(ctx.get("settings_gear")), "its Settings pane was not opened")
            return None, [], (f"text size: <html data-textsize> is stamped but no walked screen showed the control "
                              f"({gear}) -- declare the Settings step in [design.review] extra_steps")
    return (0.0 if ctl else 1.0), [{"zoom_locked": locked, "text_size_control": ctl}], None


def _d_pane_header_hidden(m: dict, rule: Rule, ctx: dict) -> Derived:
    vis = measure.metric_value(m, "nav.pane_header_visible")
    top = measure.metric_value(m, "nav.pane_scroll_top")
    if "nav" in measure.section_errors(m):
        return None, [], _section_reason(m, "nav")
    hidden = (vis is False) or (isinstance(top, (int, float)) and top > 0)
    return (1.0 if hidden else 0.0), [{"pane_header_visible": vis, "pane_scroll_top": top}], None


def _squash(text: object) -> str:
    return " ".join(str(text or "").split()).casefold()


def _d_home_title_is_app_name(m: dict, rule: Rule, ctx: dict) -> Derived:
    """NAV-04: the home tab's page-header title is the tab's name, never the app's (#1360).

    Only the first tab's pane is judged; any other screen is n/a. The app's name is the web manifest `name` /
    `short_name`: a manifest that is missing, unreadable or nameless, or a header with no `.home-title`, leaves the fact
    unestablished (`unmeasured`, never a pass). A single-tab app whose one tab is genuinely called the app's name is exempt."""
    if "nav" in measure.section_errors(m):
        return None, [], _section_reason(m, "nav")
    home = measure.metric_value(m, "nav.home_title")
    if not isinstance(home, dict):
        return None, [], "nav.home_title missing from this run (measured before NAV-04)"
    if not home.get("is_home"):
        return None, [], NOT_APPLICABLE
    names = {_squash(n) for n in home.get("app_names") or [] if _squash(n)}
    if home.get("manifest") != "ok" or not names:
        return None, [], f"the app's name is not established: web manifest {home.get('manifest') or 'unread'}"
    if not _squash(home.get("title")):
        return None, [], "no .home-title found in the first tab's page header"
    if _squash(home.get("tab")) in names:
        return None, [], NOT_APPLICABLE
    named = _squash(home.get("title")) in names
    return (1.0 if named else 0.0), [{"title": home.get("title"), "tab": home.get("tab"), "app_names": list(home.get("app_names") or [])}], None


def _header_contexts(screens: List[dict]) -> Dict[str, List[dict]]:
    """Per tab (`view`), the page-header context reading of every measured tab screen that carries one (#1395)."""
    out: Dict[str, List[dict]] = {}
    for s in screens:
        if not (isinstance(s, dict) and s.get("kind") == "tab" and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)):
            continue
        hc = measure.metric_value(s["metrics"], "nav.header_context")
        if isinstance(hc, dict):
            out.setdefault(str(s.get("view")), []).append({"screen": s.get("id"), "header": bool(hc.get("header")), "text": hc.get("text")})
    return out


def _d_header_context_static(m: dict, rule: Rule, ctx: dict) -> Derived:
    """NAV-05: a tab's page-header context line is the same fixed sentence in every state the walk captured (#1395).

    The line is judged across all measured screens of one tab (devices, themes, seeded states), since one screen cannot
    show that a line never changes. A fixed line passes when it is a live shape (the rule's `live` pattern: a digit,
    which covers a count and a time, or the word Build). Every screen of a run shows one data state, so a constant line
    that a script wrote could be a derived value as well as a slogan: it fails only when its text is also in the page as
    served (`static`, markup nobody wired). A header or line that is not found, fewer than two captured readings of the
    tab, or a constant script-written line leaves it `unmeasured`: a line that has not changed yet is not shown to be fixed."""
    if "nav" in measure.section_errors(m):
        return None, [], _section_reason(m, "nav")
    here = measure.metric_value(m, "nav.header_context")
    if not isinstance(here, dict):
        return None, [], "nav.header_context missing from this run (measured before NAV-05)"
    if not here.get("header"):
        return None, [], "no page header (.home-head) found on this tab"
    if not _squash(here.get("text")):
        return None, [], "the page header holds no context line (.status) to read"
    readings = [r for r in (ctx.get("header_contexts") or {}).get(str(ctx.get("view")), []) if r["header"] and _squash(r["text"])]
    if len(readings) < 2:
        return None, [], f"only {len(readings)} captured state of this tab reads a context line; one reading cannot show it never changes"
    distinct = {_squash(r["text"]) for r in readings}
    if len(distinct) > 1 or re.search(str(rule.params.get("live", r"\d")), str(here.get("text")), re.I):
        return 0.0, [], None
    if here.get("static") is None:
        return None, [], "the line is the same in every captured state, but the page source could not be read to tell markup from script"
    if not here.get("static"):
        return None, [], ("the line is the same in every captured state but is written by script: one data state cannot show "
                          "a derived value that has not changed from a fixed sentence")
    return 1.0, [{"tab": here.get("tab"), "text": here.get("text"), "states": len(readings)}], None


# One rgb step per channel of slack: the measured track is composited and rounded, the spec token is a literal.
SWITCH_ACCENT_TOLERANCE = 3.0


def _accent_colors(tokens: Dict[str, str]) -> List[tuple]:
    out = []
    for name in ("colors.accent-fill", "colors.accent"):
        rgba = parse_color(tokens.get(name, ""), tokens) if tokens.get(name) else None
        if rgba is not None:
            out.append(rgba[:3])
    return out


def _d_icon_only_unconventional(m: dict, rule: Rule, ctx: dict) -> Derived:
    """COMP-09: an icon-only control with no label beside it whose accessible name is not a conventional glyph's (#1366).

    The conventional glyphs are the rule's `conventional` pattern, matched on the control's name; a control with no
    name at all is A11Y-01's, not counted here. A run from before the metric existed is `unmeasured`.

    A row verb from the action-row glyph map (the rule's `verb_glyphs`: verb name -> glyph word, the one constant
    design.md's map is tested against, #1428) is conventional when it carries its mapped glyph. A verb name whose
    sprite glyph was read and is not a glyph of any verb the name carries still fails; a glyph that could not be read (an inline svg, an img)
    leaves the name to speak for it.
    """
    items = measure.metric_value(m, "controls.icon_only_unlabelled")
    if items is None:
        return None, [], _section_reason(m, "controls")
    conventional = re.compile(str(rule.params.get("conventional", "")), re.I)
    verb_glyphs = {str(v).lower(): [str(w).lower() for w in ([g] if isinstance(g, str) else g)]
                   for v, g in dict(rule.params.get("verb_glyphs") or {}).items()}  # type: ignore[call-overload]

    def mapped_verb(it: dict) -> bool:
        name, glyph = str(it.get("name", "")), str(it.get("glyph") or "").lower()
        wants = [g for v, g in verb_glyphs.items() if re.search(rf"\b{re.escape(v)}\b", name, re.I)]  # "Dictate (click to start)" names two
        return bool(wants) and (not glyph or any(set(w) & set(re.split(r"[^a-z0-9]+", glyph)) for w in wants))

    bad = [it for it in items if str(it.get("name", "")).strip()  # type: ignore[union-attr]
           and not conventional.search(str(it.get("name", ""))) and not mapped_verb(it)]  # type: ignore[arg-type]
    return float(len(bad)), bad, None


def _d_empty_state_no_action(m: dict, rule: Rule, ctx: dict) -> Derived:
    """Count of empty-state blocks that hold no button or link (#1367).

    A populated screen shows no block, and a block cannot be walked without a synthetic instance, so zero blocks is
    `unmeasured` with that reason, never a pass: this rule is the one that cannot be vacuous.
    """
    total, bare = measure.metric_value(m, "feedback.empty_state_count"), measure.metric_value(m, "feedback.empty_states_no_action")
    if total is None or bare is None:
        return None, [], _section_reason(m, "feedback")
    if not total:
        return None, [], "no empty-state block on this screen (walking one needs a synthetic instance)"
    return float(len(bare)), list(bare), None  # type: ignore[arg-type]


_NO_CHAT_CONFLICT = "app declares no chat surface, yet a chat transcript was reached (measured, not silenced)"


def _d_repeated_turn_header(m: dict, rule: Rule, ctx: dict) -> DerivedWithFacts:
    """COMP-18: count of runs of consecutive sibling blocks that repeat one speaker's who-when header (#1344).

    The metric only reads blocks shaped like the chat-turn contract's header (see `measure.py`), so a screen that shows
    none says nothing. A walk that never reaches a rendered transcript on any screen is `unmeasured`, never a pass: the
    rule cannot be vacuous. Once any screen in the run showed one, the screens without a turn are n/a. An app that
    declares `[design] no_chat_surface` (#1443) reads n/a on every screen without a turn, with the reason stated; a
    declared app whose walk does reach a turn is still measured, and the rule says the declaration conflicts.
    """
    runs, count, heads = (measure.metric_value(m, "layout." + k) for k in ("repeated_headers", "repeated_header_count", "turn_headers"))
    if runs is None or count is None or heads is None:
        return None, [], _section_reason(m, "layout")
    if not heads:
        if ctx.get("turn_seen"):
            conflict = f": {_NO_CHAT_CONFLICT}" if ctx.get("no_chat_surface") else ""
            return None, [], NOT_APPLICABLE + conflict
        if ctx.get("no_chat_surface_error"):
            return None, [], f"no_chat_surface: {ctx['no_chat_surface_error']}"
        if ctx.get("no_chat_surface"):
            return None, [], f"{NOT_APPLICABLE}: app declares no chat surface"
        return None, [], "no rendered chat transcript reached on this screen"
    if ctx.get("no_chat_surface"):
        return float(count), list(runs), None, {"declaration": _NO_CHAT_CONFLICT}  # type: ignore[arg-type]
    return float(count), list(runs), None  # type: ignore[arg-type]


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


def _d_toast_tinted(m: dict, rule: Rule, ctx: dict) -> Derived:
    """Count of visible toasts drawn with a green (success) tint (#1200).

    A screen with no toast says nothing about toasts, so zero is not applicable here, never a measured pass.
    """
    total, tinted = measure.metric_value(m, "feedback.toast_count"), measure.metric_value(m, "feedback.toasts_tinted")
    if total is None or tinted is None:
        return None, [], _section_reason(m, "feedback")
    if not total:
        return None, [], NOT_APPLICABLE
    return float(len(tinted)), list(tinted), None  # type: ignore[arg-type]


def _d_glance_card_growth(m: dict, rule: Rule, ctx: dict) -> Derived:
    """LAYOUT-10: px the tab's first card grew from its first paint to its height once the late reads landed (#1354).

    Negative when it only got shorter. A tab with no card under its header, a card with nothing under it in its column
    for a size change to move (a list that is the pane's last content fills without moving anything) and a card no
    longer in the pane are n/a, each with its reason. A tab whose requests never went quiet within the walk's bound, a
    tracker that could not run, and a run from before the tracker existed are `unmeasured`, never a pass.
    """
    g = m.get("glance")
    if not isinstance(g, dict):
        return None, [], "glance card not tracked on this screen"
    state = g.get("state")
    if state == "no_card":
        return None, [], f"{NOT_APPLICABLE}: no card under the page header"
    if state == "card_gone":
        return None, [], f"{NOT_APPLICABLE}: the card first drawn left the pane"
    if state == "measured" and g.get("below") is False:
        return None, [], f"{NOT_APPLICABLE}: nothing under the card for its size to move"
    if state == "not_idle":
        pending = ", ".join(str(x) for x in g.get("pending") or [])
        what = f"{pending} in flight" if pending else "the last one finished too close to the bound"
        return None, [], f"requests never went quiet within {g.get('idle_ms')} ms ({what}): late reads may still be landing"
    grew = g.get("grew_px")
    if state != "measured" or isinstance(grew, bool) or not isinstance(grew, (int, float)):
        detail = f" ({g['error']})" if g.get("error") else ""
        return None, [], f"glance card tracker {state or 'returned no state'}{detail}"
    items: List[object] = [{"sel": g.get("card"), "why": f"{g.get('first_px')}px at first paint, {g.get('settled_px')}px settled"}]
    items += [{"sel": c.get("sel"), "why": f"{c.get('from_px')} to {c.get('to_px')}px at {c.get('ms')} ms"}
              for c in g.get("changes") or [] if isinstance(c, dict)]
    return float(grew), items, None


DERIVED: Dict[str, Callable[[dict, Rule, dict], tuple]] = {
    "targets.small_share": _d_small_share,
    "text.under14_share": _d_under14_share,
    "icons.off_step_count": _d_icons_off_step,
    "text.uppercase_outside_role": _d_uppercase,
    "text.break_all_non_path": _d_break_all,
    "casing.mixed_labels": _d_casing_mixed,
    "layout.lists_unfiltered_tall": _d_lists_tall,
    "layout.content_share": _d_content_share,
    "a11y.no_text_size_control": _d_no_text_size_control,
    "nav.pane_header_hidden": _d_pane_header_hidden,
    "nav.home_title_is_app_name": _d_home_title_is_app_name,
    "nav.header_context_static": _d_header_context_static,
    "controls.switch_on_not_accent": _d_switch_on_accent,
    "controls.icon_only_unconventional": _d_icon_only_unconventional,
    "feedback.toast_tinted": _d_toast_tinted,
    "feedback.empty_state_no_action": _d_empty_state_no_action,
    "layout.repeated_turn_header": _d_repeated_turn_header,
    "glance.card_growth_px": _d_glance_card_growth,
}


# aggregate metric -> the list it summarises; empty list => the aggregate is N/A
_POPULATIONS = {
    "targets.primary_min_height": "targets.primary",
    "text.min_px": "text.runs",
}

# metrics with no list of their own borrow the sample that explains a failure
_SAMPLE_SOURCES: Dict[str, Callable[[dict], List[object]]] = {
    "text.min_px": lambda m: list(measure.metric_value(m, "text.under11") or []),
    "controls.reference_pill_variants": lambda m: list(measure.metric_value(m, "controls.reference_pill_styles") or []),
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
            if rule.theme_dependent and screen.get("theme_applied") is False:
                tally.unmeasured.append(f"{sid}: {_theme_reason(screen)}")
                continue
            value, items, why, facts = read_metric(screen["metrics"], rule,
                                                   {**ctx, "view": screen.get("view"), "theme": screen.get("theme"), "specs": specs})
            _fold(result, tally, sid, value, items, why, rule, thr, facts)

    na = f"; n/a on {tally.not_applicable}" if tally.not_applicable else ""
    na += f" ({'; '.join(tally.na_reasons)})" if tally.na_reasons else ""
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
        why_na = f": {'; '.join(tally.na_reasons)}" if tally.na_reasons else ""
        result["reason"] = f"not applicable on any of {tally.not_applicable} screen(s) (vacuous pass{why_na})"
    else:
        result["status"] = "unmeasured"
        result["reason"] = "no applicable screen in this run" + na
    return result


def _theme_reason(screen: dict) -> str:
    """Why a palette rule cannot score a screen: it rendered another theme than the walk asked for (#1216)."""
    seen = screen.get("theme_observed") if isinstance(screen.get("theme_observed"), dict) else {}
    return f"theme not applied (requested {screen.get('theme')}, rendered {seen.get('rendered') or 'another theme'})"


def theme_mismatches(screens: List[dict]) -> List[Dict[str, object]]:
    """The screens whose app did not render the requested theme; a screen the walk could not observe is not listed."""
    out: List[Dict[str, object]] = []
    for s in screens:
        if isinstance(s, dict) and s.get("status") == "ok" and s.get("theme_applied") is False:
            seen = s.get("theme_observed") if isinstance(s.get("theme_observed"), dict) else {}
            out.append({"id": str(s.get("id")), "requested": s.get("theme"), "rendered": seen.get("rendered") or None})
    return out


class _Tally:
    def __init__(self) -> None:
        self.failed: List[dict] = []
        self.unmeasured: List[str] = []
        self.passed = 0
        self.not_applicable = 0
        self.na_reasons: List[str] = []  # stated reasons behind n/a screens (a bare `n/a` has none)
        self.absent = 0  # step screens whose target never appeared (#995): neither measured nor unmeasured


def _fold(result: dict, tally: _Tally, sid: str, value: Optional[float], items: List[object],
          why: Optional[str], rule: Rule, thr: Optional[float], facts: Dict[str, object]) -> None:
    if why is not None and why.startswith(NOT_APPLICABLE):
        tally.not_applicable += 1
        stated = why[len(NOT_APPLICABLE):].lstrip(": ")
        if stated and stated not in tally.na_reasons:
            tally.na_reasons.append(stated)
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


def _settings_gear_state(screens: List[dict]) -> Optional[str]:
    """What the built-in Settings step did across the walk (#1217): `ok` when any leg opened and measured the pane,
    else `absent` (no gear button), else `error` (refused by no_go, or the click failed); `None` when it never ran."""
    mine = [s for s in screens if isinstance(s, dict) and s.get("view") == plan.SETTINGS_GEAR_VIEW]
    for state, test in (("ok", lambda s: s.get("status") == "ok"), ("absent", lambda s: s.get("status") == ABSENT),
                        ("error", lambda s: s.get("status") not in ("ok", ABSENT))):
        if any(test(s) for s in mine):
            return state
    return None


OVERLAY_SAMPLE = 8  # not-walked surfaces named in the report; `not_walked_count` keeps the true total


def overlay_coverage(screens: List[dict]) -> Dict[str, object]:
    """The overlay surfaces no walked screen showed open (#1353): walk coverage for the report, never a rule input.

    `present` is every surface key a measured screen listed, `walked` those open on at least one screen. `unknown`
    when no measured screen carries the listing (metrics captured before it existed, or the section errored), so a
    missing listing never reads as "nothing missed".
    """
    opened: Dict[str, bool] = {}
    listed = False
    for s in screens:
        if not isinstance(s, dict) or s.get("status") != "ok" or not isinstance(s.get("metrics"), dict):
            continue
        items = measure.metric_value(s["metrics"], "coverage.overlays")
        if not isinstance(items, list):
            continue
        listed = True
        for item in items:
            if isinstance(item, dict) and item.get("key"):
                key = str(item["key"])
                opened[key] = opened.get(key, False) or item.get("open") is True
    if not listed:
        return {"status": "unknown", "present": None, "walked": None, "not_walked": [], "not_walked_count": None}
    missed = sorted(k for k, was_open in opened.items() if not was_open)
    return {"status": "measured", "present": len(opened), "walked": len(opened) - len(missed),
            "not_walked": missed[:OVERLAY_SAMPLE], "not_walked_count": len(missed)}


def evaluate(doc: dict, rubric: Rubric, specs: Dict[str, Dict[str, str]],
             now: Optional[_dt.datetime] = None) -> Dict[str, object]:
    """The whole stage: one metrics document -> rule results + grades."""
    casing_labels = [lab for s in doc.get("screens") or []
                     if isinstance(s, dict) and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)
                     for lab in measure.metric_value(s["metrics"], "casing.labels") or [] if isinstance(lab, dict)]
    ctx = {"params": resolve_params(rubric, specs.get("light", {})),
           "wide_views": [str(v) for v in doc.get("wide_views") or []],
           "wide_views_error": doc.get("wide_views_error"),
           "no_chat_surface": doc.get("no_chat_surface") is True,
           "no_chat_surface_error": doc.get("no_chat_surface_error"),
           "switch_on_seen": any(
               isinstance(s, dict) and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)
               and bool(measure.metric_value(s["metrics"], "controls.switches_on"))
               for s in doc.get("screens") or []),
           "turn_seen": any(
               isinstance(s, dict) and s.get("status") == "ok" and isinstance(s.get("metrics"), dict)
               and bool(measure.metric_value(s["metrics"], "layout.turn_headers"))
               for s in doc.get("screens") or []),
           "header_contexts": _header_contexts(doc.get("screens") or []),
           "casing_outliers": _casing_outliers(casing_labels),
           "casing_vocab": _casing_vocab(casing_labels),
           "settings_gear": _settings_gear_state(doc.get("screens") or []),
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
        # `measure --e2e` (#1498): the suite's outcome rides through to the report and the ledger; a plain run has none.
        **({"e2e": doc["e2e"]} if doc.get("e2e") is not None else {}),
        "synthetic_declared": isinstance((doc.get("review") or {}).get("synthetic"), dict),
        "run_dir": doc.get("run_dir"),
        "params": ctx["params"],
        "unmeasured": doc.get("unmeasured"),
        "screens": [{k: s.get(k) for k in ("id", "device", "theme", "view", "kind", "status", "reason")}
                    for s in doc.get("screens", [])],
        "absent_screens": [str(s.get("id")) for s in doc.get("screens", []) if s.get("status") == ABSENT],
        "theme_mismatch_screens": theme_mismatches(doc.get("screens", [])),
        "overlay_coverage": overlay_coverage(doc.get("screens") or []),
        "rules": rules,
        "categories": categories,
        "overall": score_overall(categories, rubric),
    }
