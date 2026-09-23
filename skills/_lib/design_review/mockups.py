"""The now-vs-proposed mock-up library keyed to the rubric's `mockup` ids (fleet-config#972).

Each template is a parametrized HTML/CSS partial with a **Now** variant drawn
from the app's measurements and a **Proposed** variant drawn from the rule's
threshold. Everything on them is a placeholder — "Item name", "Tab 3",
"Action" — never captured content, never a screenshot: the report redraws
the pattern, it does not show the app.

Keys are the rubric's `mockup` field, so `rubric.validate_rubric` refuses a
rule naming a template that does not exist here, and the unit tests assert
the other direction (every template here is referenced by at least one
rule). A template declares the value keys it `needs` from the finding
(`report.finding_values`); when a run did not measure one of them the rule
renders without a mock-up and a one-line note says which value was
missing — nothing is improvised.

    render_mockup(rule, values) -> (mockup | None, note | None)
        mockup: {"now": html, "proposed": html, "caption": text}

stdlib only; the CSS these partials rely on is `CSS`, inlined by `report.py`.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

NONE = "none"
NON_TEXT_AA = 3.0  # WCAG 2.2 SC 1.4.11 Non-text Contrast — the floor COLOR-03's standard cites

Mockup = Dict[str, str]
Values = Dict[str, object]


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _num(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _int(value: object, default: int) -> int:
    return int(round(_num(value, default)))


def _fmt(value: object) -> str:
    f = _num(value, 0.0)
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


# ---- partial builders ----------------------------------------------------------

def _rows(n: int, controls_html: str = "", cls: str = "") -> str:
    row = (f'<div class="mk-row {cls}"><div class="mk-row-text"><span class="mk-title">Item name</span>'
           f'<span class="mk-meta">Secondary line</span></div>{controls_html}</div>')
    return "".join(row for _ in range(n))


def _btn(label: str, cls: str = "", style: str = "") -> str:
    st = f' style="{style}"' if style else ""
    return f'<span class="mk-btn {cls}"{st}>{_esc(label)}</span>'


def _kebab() -> str:
    return '<span class="mk-kebab" aria-hidden="true">&#8942;</span>'


def _t_type_scale(rule: dict, v: Values) -> Mockup:
    share = _num(v.get("share"), 0.0)
    now = (f'<div class="mk-card"><span class="mk-text" style="font-size:16px">Item name</span>'
           f'<span class="mk-text" style="font-size:12px">Secondary line at 12px</span>'
           f'<span class="mk-text" style="font-size:11px">Caption at 11px &middot; status &middot; time</span>'
           f'<span class="mk-note">{_esc(f"{share:.0%}")} of text runs under 14px</span></div>')
    proposed = ('<div class="mk-card"><span class="mk-text" style="font-size:16px">Item name</span>'
                '<span class="mk-text" style="font-size:14px">Secondary line at body-sm</span>'
                '<span class="mk-text" style="font-size:14px">Status &middot; last run at body-sm</span>'
                '<span class="mk-note">secondary lines at 14px; 12px caption kept for chips, badges and timestamps</span></div>')
    return {"now": now, "proposed": proposed, "caption": "Body scale: secondary lines move from caption to the 14px body-sm role."}


def _t_hit_target(rule: dict, v: Values) -> Mockup:
    hit = _int(v.get("hit_min"), 44)
    items = v.get("items") or []
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    w, h = _int(first.get("w"), 28), _int(first.get("h"), 28)
    w, h = max(12, min(w, 60)), max(12, min(h, 60))
    glyph = '<span class="mk-glyph" aria-hidden="true"></span>'
    now_btn = f'<span class="mk-icon-btn" style="width:{w}px;height:{h}px">{glyph}</span>'
    prop_btn = (f'<span class="mk-hit" style="width:{hit}px;height:{hit}px">'
                f'<span class="mk-icon-btn" style="width:{w}px;height:{h}px">{glyph}</span></span>')
    now = (f'<div class="mk-card"><div class="mk-row"><div class="mk-row-text"><span class="mk-title">Item name</span></div>'
           f'{now_btn}{now_btn}{now_btn}</div><span class="mk-note">{_esc(v["count"])} of {_esc(v["total"])} controls '
           f'under {hit}px effective (first: {w}&times;{h}px)</span></div>')
    proposed = (f'<div class="mk-card"><div class="mk-row"><div class="mk-row-text"><span class="mk-title">Item name</span></div>'
                f'{prop_btn}{prop_btn}{prop_btn}</div><span class="mk-note">same glyphs; dashed box is the {hit}&times;{hit}px '
                f'effective rect from the .hit-target expansion</span></div>')
    return {"now": now, "proposed": proposed, "caption": f"Hit targets: every control reaches {hit}px effective without growing visually."}


def _t_nav_five(rule: dict, v: Values) -> Mockup:
    n = max(1, min(_int(v.get("value"), 6), 9))
    def bar(labels: List[str], active: int = 0) -> str:
        tabs = "".join(f'<span class="mk-tab{" mk-tab-active" if i == active else ""}">{_esc(l)}</span>' for i, l in enumerate(labels))
        return f'<div class="mk-nav">{tabs}</div>'
    now = f'<div class="mk-phone">{bar([f"Tab {i + 1}" for i in range(n)])}<span class="mk-note">{n} primary destinations</span></div>'
    proposed = f'<div class="mk-phone">{bar(["Tab 1", "Tab 2", "Tab 3", "Tab 4", "More"])}<span class="mk-note">4 + More; the rest live one tap deeper</span></div>'
    return {"now": now, "proposed": proposed, "caption": "Primary navigation: three to five destinations."}


def _t_row_density(rule: dict, v: Values) -> Mockup:
    items = v.get("items") or []
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    rows = _int(first.get("rows"), 12)
    now = (f'<div class="mk-card mk-list">{_rows(6, cls="mk-row-dense")}<span class="mk-fade"></span>'
           f'<span class="mk-note">{rows} rows, no filter</span></div>')
    proposed = (f'<div class="mk-card mk-list"><div class="mk-input">&#128269; Filter rows</div>{_rows(4)}'
                f'<span class="mk-note">filter on top; rows at the 52px standard height</span></div>')
    return {"now": now, "proposed": proposed, "caption": "Long lists: a filter above the rows, rows at the standard height."}


def _t_action_row(rule: dict, v: Values) -> Mockup:
    items = v.get("items") or []
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    n = max(3, min(_int(first.get("controls"), 3), 6))
    controls = "".join(_btn(f"Action {i + 1}", "mk-btn-surface") for i in range(n))
    now = f'<div class="mk-card">{_rows(2, controls)}<span class="mk-note">{n} controls per row</span></div>'
    proposed = (f'<div class="mk-card">{_rows(2, _btn("Action", "mk-btn-tint") + _kebab())}'
                f'<span class="mk-note">one primary action; the rest in the row menu</span></div>')
    return {"now": now, "proposed": proposed, "caption": "List rows: one primary action per row, secondary actions behind a menu."}


def _t_destructive_in_menu(rule: dict, v: Values) -> Mockup:
    n = max(3, min(_int(v.get("value"), 3), 5))
    now = f'<div class="mk-card">{_rows(n, _btn("Delete", "mk-btn-danger"))}<span class="mk-note">danger on {n} rows</span></div>'
    menu = ('<span class="mk-menu"><span class="mk-menu-item">Rename</span><span class="mk-menu-item">Duplicate</span>'
            '<span class="mk-menu-item mk-menu-danger">Delete</span></span>')
    proposed = (f'<div class="mk-card">{_rows(n - 1, _kebab())}<div class="mk-row"><div class="mk-row-text">'
                f'<span class="mk-title">Item name</span><span class="mk-meta">Secondary line</span></div>{_kebab()}{menu}</div>'
                f'<span class="mk-note">delete is the last item of the row menu</span></div>')
    return {"now": now, "proposed": proposed, "caption": "Destructive actions: state, not decoration; one place per row."}


def _t_form_primary(rule: dict, v: Values) -> Mockup:
    rid = str(rule.get("id", ""))
    threshold = _num(v.get("threshold"), 48.0)
    items = v.get("items") or []
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    if rid == "TOUCH-03":
        now_h, prop_h = _int(v.get("value"), 36), _int(threshold, 48)
        now_border, prop_border, now_note, prop_note = "var(--border)", "var(--border)", f"primary action {now_h}px tall", f"primary action {prop_h}px tall"
    else:
        now_h = prop_h = 48
        ratio = v.get("ratio", first.get("ratio"))
        now_border = "color-mix(in srgb, var(--fg) 12%, transparent)"
        prop_border = "var(--fg-muted)"
        now_note = f"control boundary at {_fmt(ratio)}:1" if ratio is not None else f"control boundary under {_fmt(NON_TEXT_AA)}:1"
        prop_note = f"boundary at or above {_fmt(NON_TEXT_AA)}:1 against the surface"

    def form(h: int, border: str, note: str) -> str:
        field = f'<div class="mk-field" style="border-color:{border}"><span class="mk-meta">Field label</span></div>'
        return (f'<div class="mk-card mk-form">{field}{field}'
                f'<span class="mk-primary" style="height:{h}px">Primary action</span><span class="mk-note">{_esc(note)}</span></div>')
    return {"now": form(now_h, now_border, now_note), "proposed": form(prop_h, prop_border, prop_note),
            "caption": "Form: fields with a visible boundary and a primary action at button-primary height."}


def aa_background(fg_hex: str, bg_hex: str, need: float) -> Optional[Tuple[str, float, str]]:
    """The same background moved toward black or white, in 2% steps, until the
    pair measures `need`: `(hex, ratio, "black"|"white")`, or None if neither
    direction gets there. A computed example for the mock-up, not a token
    choice — the note says so."""
    from . import evaluate as ev  # lazy: evaluate -> rubric -> mockups would otherwise be a cycle
    fg, bg = ev.parse_color(fg_hex, {}), ev.parse_color(bg_hex, {})
    if fg is None or bg is None:
        return None
    found: List[Tuple[int, str, float, str]] = []  # (steps, hex, ratio, pole) — fewest steps wins
    for name, pole in (("black", (0.0, 0.0, 0.0, 1.0)), ("white", (255.0, 255.0, 255.0, 1.0))):
        for step in range(0, 51):
            mix = ev.composite((pole[0], pole[1], pole[2], step / 50.0), bg)
            ratio = ev.contrast(fg, mix)
            if ratio >= need:
                found.append((step, ev._hex(mix), round(ratio, 2), name))
                break
    if not found:
        return None
    _steps, hex_bg, ratio, pole = min(found)
    return hex_bg, ratio, pole


def _t_contrast_swatches(rule: dict, v: Values) -> Mockup:
    items = [i for i in (v.get("items") or []) if isinstance(i, dict) and i.get("fg") and i.get("bg")]
    threshold = _num(v.get("threshold"), 4.5)

    def row(it: dict, proposed: bool) -> str:
        need = _num(it.get("threshold"), threshold)
        bg, label = str(it["bg"]), f'{_fmt(it.get("ratio"))}:1'
        if proposed:
            fixed = aa_background(str(it["fg"]), bg, need)
            if fixed is None:
                return (f'<div class="mk-swatch"><span class="mk-swatch-text"><b>{_esc(it.get("component", "pair"))}</b> &middot; '
                        f'no background on the black/white axis reaches {_fmt(need)}:1 with {_esc(it["fg"])}; change the foreground</span></div>')
            bg, label = fixed[0], f"{_fmt(fixed[1])}:1 with the background moved toward {fixed[2]}"
        return (f'<div class="mk-swatch"><span class="mk-swatch-box" style="color:{_esc(it["fg"])};background:{_esc(bg)}">Aa Label</span>'
                f'<span class="mk-swatch-text"><b>{_esc(it.get("component", "pair"))}</b> &middot; {_esc(it["fg"])} on '
                f'{_esc(bg)} &middot; {label} (needs {_fmt(need)}:1)</span></div>')
    now = '<div class="mk-card">' + "".join(row(i, False) for i in items) + "</div>"
    proposed = ('<div class="mk-card">' + "".join(row(i, True) for i in items)
                + '<span class="mk-note">computed examples: the nearest background on the black/white axis that measures AA; '
                  'the spec owner picks the actual token</span></div>')
    return {"now": now, "proposed": proposed, "caption": "Spec pairs: every text/background pair the spec defines meets AA."}


def _t_wide_desktop(rule: dict, v: Values) -> Mockup:
    share = _num(v.get("share"), 0.5)
    threshold = _num(v.get("threshold"), 0.6)
    inner_w = _int(v.get("inner_w"), 1440)

    def frame(pct: float, note: str) -> str:
        w = max(10, min(int(round(pct * 100)), 100))
        return (f'<div class="mk-desktop"><span class="mk-column" style="width:{w}%"><span class="mk-title">Content column</span></span>'
                f'<span class="mk-note">{_esc(note)}</span></div>')
    now = frame(share, f"{share:.0%} of a {inner_w}px viewport")
    floor = v.get("min_viewport")
    proposed = frame(threshold, f"at least {threshold:.0%} of the viewport" + (f" at {_fmt(floor)}px and wider" if floor is not None else ""))
    return {"now": now, "proposed": proposed, "caption": "Desktop measure: the content column fills the width the spec reserves for it."}


@dataclass(frozen=True)
class Template:
    id: str
    needs: Tuple[str, ...]
    build: Callable[[dict, Values], Mockup]


LIBRARY: Dict[str, Template] = {t.id: t for t in (
    Template("type-scale", ("share",), _t_type_scale),
    Template("hit-target", ("hit_min", "count", "total"), _t_hit_target),
    Template("nav-five", ("value",), _t_nav_five),
    Template("row-density", ("count",), _t_row_density),
    Template("action-row", ("count",), _t_action_row),
    Template("destructive-in-menu", ("value",), _t_destructive_in_menu),
    Template("form-primary", ("value", "threshold"), _t_form_primary),
    Template("contrast-swatches", ("items",), _t_contrast_swatches),
    Template("wide-desktop", ("share", "inner_w", "threshold"), _t_wide_desktop),
)}

MOCKUP_IDS = frozenset(LIBRARY)


def render_mockup(rule: dict, values: Values) -> Tuple[Optional[Mockup], Optional[str]]:
    """The rule's mock-up, or `(None, note)` naming why there is none."""
    mid = str(rule.get("mockup") or NONE)
    if mid == NONE:
        return None, None
    tpl = LIBRARY.get(mid)
    if tpl is None:
        return None, f"mock-up {mid!r} is not in the library"
    missing = [k for k in tpl.needs if values.get(k) is None or values.get(k) == []]
    if missing:
        return None, f"no mock-up: template {mid!r} needs {', '.join(missing)}, which this run did not measure"
    return tpl.build(rule, values), None


CSS = """
.mk-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:560px){.mk-pair{grid-template-columns:1fr}}
.mk-variant{border:1px solid var(--border);border-radius:12px;padding:12px;background:var(--canvas-subtle);min-width:0}
.mk-variant>h4{margin:0 0 8px;font-size:.78rem;font-weight:600;letter-spacing:.02em;color:var(--fg-muted)}
.mk-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:8px 12px;display:flex;flex-direction:column;gap:6px;position:relative;overflow:hidden}
.mk-text{display:block;line-height:1.3}
.mk-note{display:block;font-size:.72rem;color:var(--fg-muted);margin-top:6px;font-style:italic}
.mk-row{display:flex;align-items:center;gap:6px;min-height:52px;padding:6px 0;border-top:1px solid var(--border-muted);flex-wrap:wrap}
.mk-row:first-child{border-top:0}
.mk-row-dense{min-height:30px;padding:2px 0}
.mk-row-text{display:flex;flex-direction:column;flex:1 1 90px;min-width:0}
.mk-title{font-size:.92rem;font-weight:600}
.mk-meta{font-size:.78rem;color:var(--fg-muted)}
.mk-btn{display:inline-flex;align-items:center;justify-content:center;height:30px;padding:0 10px;border-radius:8px;font-size:.78rem;font-weight:600;border:1px solid var(--border);color:var(--fg-muted);white-space:nowrap}
.mk-btn-surface{background:var(--canvas-subtle)}
.mk-btn-tint{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.mk-btn-danger{background:var(--danger);color:#fff;border-color:transparent}
.mk-kebab{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;border-radius:9999px;font-size:1.2rem;color:var(--fg-muted)}
.mk-menu{display:flex;flex-direction:column;flex-basis:100%;margin:4px 0 0 auto;max-width:160px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:4px 0;box-shadow:0 4px 12px rgba(0,0,0,.12)}
.mk-menu-item{padding:8px 12px;font-size:.85rem}
.mk-menu-danger{color:var(--danger);border-top:1px solid var(--border-muted)}
.mk-icon-btn{display:inline-flex;align-items:center;justify-content:center;background:var(--canvas-subtle);border:1px solid var(--border);border-radius:8px;flex:none}
.mk-glyph{width:14px;height:14px;border:2px solid var(--fg-muted);border-radius:3px;display:block}
.mk-hit{display:inline-flex;align-items:center;justify-content:center;outline:2px dashed var(--accent);outline-offset:-2px;border-radius:8px;flex:none}
.mk-phone{background:var(--canvas);border:1px solid var(--border);border-radius:16px;padding:40px 10px 10px}
.mk-nav{display:flex;gap:4px;background:var(--card);border:1px solid var(--border);border-radius:30px;padding:4px;overflow:hidden}
.mk-tab{flex:1 1 0;min-width:0;text-align:center;font-size:.68rem;font-weight:600;color:var(--fg-muted);padding:10px 2px;border-radius:9999px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mk-tab-active{background:var(--accent-soft);color:var(--accent)}
.mk-list .mk-fade{position:absolute;left:0;right:0;bottom:24px;height:40px;background:linear-gradient(transparent,var(--card))}
.mk-input{border:1px solid var(--border);border-radius:8px;height:36px;display:flex;align-items:center;padding:0 10px;font-size:.85rem;color:var(--fg-muted);background:var(--canvas-subtle)}
.mk-form{gap:10px}
.mk-field{border:1px solid;border-radius:8px;height:36px;display:flex;align-items:center;padding:0 10px;background:var(--canvas-subtle)}
.mk-primary{display:flex;align-items:center;justify-content:center;background:var(--accent);color:#fff;border-radius:8px;font-weight:600;font-size:.92rem}
.mk-swatch{display:flex;align-items:center;gap:10px;padding:6px 0;border-top:1px solid var(--border-muted)}
.mk-swatch:first-child{border-top:0}
.mk-swatch-box{flex:none;width:84px;height:40px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;font-weight:700;border:1px solid var(--border)}
.mk-swatch-text{font-size:.78rem;color:var(--fg-muted);min-width:0;overflow-wrap:anywhere}
.mk-desktop{background:var(--canvas);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;flex-direction:column;align-items:center;gap:8px}
.mk-column{display:block;height:80px;background:var(--accent-soft);border:1px dashed var(--accent);border-radius:8px;padding:8px;box-sizing:border-box;text-align:center}
"""
