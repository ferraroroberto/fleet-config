"""Stage 2 of /design-review — the in-page measurement script (fleet-config#971).

The 2026-09-21 app-launcher audit's `page.evaluate` snippet, promoted to a
library: every rendered fact the rubric needs, computed inside the page from
computed styles and bounding boxes, returned as one JSON object per screen.
Same app at the same commit gives the same object (modulo live data such as
row counts), which is where the review's predictability is won.

**Effective hit rectangles reuse project-scaffolding's canonical JS.** The
`::before`/`::after` negative-inset expansion lives in
`tests/e2e/_geometry.py` (`_EFFECTIVE_RECT_JS`, the `effective_rect` /
`assert_min_target` helpers) and is *spliced in* here (`build_script`) rather
than re-implemented — the walk (`walk.py`) loads that module from the scaffold
checkout and hands its JS over. No scaffold → hit-target metrics report
`GEOMETRY_MISSING` and stay `unmeasured`, never a pass.

Each section of the script runs under its own try/catch, so one failing
query degrades that section to `{"error": ...}` (→ `unmeasured` for the rules
reading it) without losing the rest of the screen.

Per-screen metrics object (`metrics.json` → `screens[].metrics`), all keys
present on a successful run:

    text      sizes {px: runs}, weights {w: runs}, runs, under14, under11 [..],
              min_px, low_contrast [..], low_contrast_count,
              body_font_family, break_all [..], uppercase [..], glyph_icons [..],
              raw_identifier_count, raw_identifiers [{sel, kind, text}],
              heading_smaller_than_body_count, heading_smaller_than_body [{sel, text, px, body_sel, body_px}],
              code_off_spec_count, code_off_spec [{sel, text, px, why}]
    casing    labels [{sel, cls, text, style, literal}], label_count, labels_seen
    controls  total, font_family_mismatch [..], font_family_mismatch_count, mono_code_field [..], mono_code_field_count,
              boundary_low [..], boundary_low_count, ua_styled [..],
              label_fit_bad [..], label_fit_bad_count, segmented_bad [..], segmented_bad_count, switch_count, switch_on_count, switches_on,
              icon_button_count, icon_buttons_painted [..], icon_buttons_painted_count,
              icon_only_count, icon_only_unlabelled [{sel, name, glyph}],
              reference_pill_count, reference_pill_styles [..], reference_pill_variants
    targets   total, small [..], small_count, overlaps [..], overlap_count,
              covered [..], covered_count, primary [..], primary_min_height, in_summary
    icons     boxes {"WxH": n}, elements {"WxH": [{glyph, host, label}, ..]}
    nav       primary_count, pane_scroll_top, pane_header_visible, settings_tab_count,
              home_title {is_home, manifest, app_names [..], tab, title}, header_context {header, tab, text, static}
    feedback  toast_count, toasts_tinted [..], toasts_tinted_count, empty_state_count, empty_states_no_action [{sel, text}]
    layout    control_overflow [..], control_overflow_count, overflow_x, scroll_w, inner_w, inner_h, pane_h, lists [..],
              rows_over_limit [..], danger_rows, content_w, content_span, pane_split, radii {r: n},
              open_section_count, empty_open_sections [{sel, label}], empty_open_section_count,
              multi_secondary_rows [{sel, label, lines}], multi_secondary_row_count,
              painted_row_verbs [{sel, label, via, row}], painted_row_verb_count,
              delete_row_verbs [{sel, label, row}], delete_row_verb_count,
              clipped_trailing [{sel, text, why, over_px}], clipped_trailing_count,
              clipped_trailing_excluded [{sel, text, why, over_px}] (sample of 8), clipped_trailing_excluded_count,
              turn_headers [{who, count}], repeated_headers [{sel, text, who, blocks}], repeated_header_count
    overlay   surfaces, destructive [..], destructive_bad [..], destructive_bad_count
    dialogs   count, with_commit, quiet_commit [{dialog, commit, label, filled, w, h, rival, rival_label, why}], quiet_commit_count
    clearance bars [..], hidden_rows [..], hidden_row_count
    a11y     unnamed [..], unnamed_count, zoom_locked, text_size_control, text_size_stamped
    coverage  overlays [{key, kind, open}], overlay_count -- every dialog / [popover] / [role=dialog] in the
              DOM, open or not; walk coverage for the report, never a rule input (#1353)
    inventory unmeasured (null, or why no component catalog was passed in), controls [{key, kind, component, role,
              label, family, px, weight, n, options?, group?}] (one entry per distinct rendering, `n` elements, capped at
              INVENTORY_CAP; `options` on a select or combobox is its option count -- a <select>'s, a datalist's or an
              aria-controls listbox's, absent when the DOM does not say; `group` on a segmented or range-tab control is the
              key of its parent, `~N` in document order when several share one, #1505),
              controls_count, kinds {kind: n}, components {component: n}, text [{role, runs, variants [{family, px,
              weight, runs}]}], text_count -- every visible control by kind and component and every text role by the
              fonts it renders in; read only, never a rule input yet (#1497, for #1383's cross-app comparator)
    glance   (tab screens only, added by walk.py from GLANCE_READ_JS, #1354) state (measured | no_card | not_idle |
              card_gone | stopped | unavailable | error), card, first_px, settled_px, grew_px, below,
              changes [{ms, from_px, to_px, sel}] (sample of GLANCE_CHANGE_CAP), change_count, idle, idle_ms, pending [path]
    headings  ["H2:Title@16px", ...]

Lists are capped (`CAP`) so a screen with hundreds of small controls still
serialises small; the `*_count` twin carries the true total.

stdlib only — the Playwright side is `walk.py`, executed by the *target*
repo's interpreter (this repo's venv stays stdlib-only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from design_lint.rules import GLYPH_ICON_RENDERED_RE  # noqa: E402
# walk.py, run by the target repo's interpreter, imports this file as a top-level
# module from its own folder; inside the package the halves are relative siblings.
if __package__:
    from .measure_js_core import MEASURE_JS_CORE
    from .measure_js_layout import MEASURE_JS_LAYOUT
else:
    from measure_js_core import MEASURE_JS_CORE  # noqa: E402
    from measure_js_layout import MEASURE_JS_LAYOUT  # noqa: E402

SCHEMA_VERSION = 1

# Sub-pixel tolerance on the hit-target floor: a 43.6px control that the
# layout engine rounds to 44 is not a finding (the prototype used 43.5).
HIT_TOLERANCE_PX = 0.5

INTERACTIVE_SELECTOR = (
    "button, a[href], input, select, textarea, summary, "
    "[role=button], [role=tab], [role=switch], [role=menuitem], [role=checkbox]"
)
FORM_CONTROL_SELECTOR = "button, input, select, textarea"
# COLOR-03 measures only the controls design.md gives a drawn boundary (`control-border`: input,
# select, the switch's track). A text-labelled button or tab is identified by its label, WCAG
# 1.4.11's exception, and the nav draws inactive tabs without one by design.
BOUNDARY_CONTROL_SELECTOR = (
    "input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file])"
    ":not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), "
    "select, textarea, [role=switch]"
)
GLYPH_ICON_RE = GLYPH_ICON_RENDERED_RE
# A control's label is "short" up to this many characters; a glyph at either end of a longer run is punctuation.
GLYPH_LABEL_MAX_CHARS = 24
# A text run in a control is a label up to this many characters; a longer one is prose that may wrap.
LABEL_FIT_MAX_CHARS = 40
# Sub-pixel slack on label fit and container overflow: a 0.4px rounding spill is not a finding.
FIT_TOLERANCE_PX = 1.5

# `__GEOMETRY__` is replaced by `build_script`. The script is one arrow
# function taking `params` so Playwright's `page.evaluate(script, params)`
# passes the resolved spec values in.
_MEASURE_JS = MEASURE_JS_CORE + MEASURE_JS_LAYOUT


# Which theme did the page render? The walk stamps `data-theme` and sets the context's
# color scheme, but an app that picks its theme from something else after load re-applies its own choice. Read
# the attribute as it stands now and the relative luminance of the canvas the page is painted on: `body`'s
# background composited down through its ancestors over white. `luminance` is null when a layer is not plain
# rgb() (a wide-gamut colour), so a colour space the browser will not flatten is unknown, never guessed.
RENDERED_THEME_JS = """
() => {
  const parse = c => { const m = /^rgba?\\(([^)]*)\\)$/.exec(c); if (!m) return null;
    const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const layers = []; let ok = true;
  for (let n = document.body; n && n.nodeType === 1; n = n.parentElement) {
    const raw = getComputedStyle(n).backgroundColor; if (raw === 'transparent') continue;
    const c = parse(raw); if (!c) { ok = false; break; }
    if (c[3] > 0) { layers.push(c); if (c[3] >= 0.999) break; } }
  let lum = null;
  if (ok) { let rgb = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) { const [r, g, b, a] = layers[i]; rgb = [r * a + rgb[0] * (1 - a), g * a + rgb[1] * (1 - a), b * a + rgb[2] * (1 - a)]; }
    const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    lum = Math.round((0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])) * 1000) / 1000; }
  return { attr: document.documentElement.dataset.theme || '', luminance: lum };
}
"""

# The glance card's height across its late reads. design.md's glance card contract: the first card
# under the page header never changes size as late reads land. One snapshot after a fixed settle cannot see that, so an
# init script (`walk.py` adds it to every page) samples the card every frame from the first one of the load, and
# `GLANCE_READ_JS` reads it once the tab's requests have gone quiet. It only reads: no click, scroll, focus or write.
#
# A track is one tab's pane (the visible tabpanel and the selected tab, so one pane redrawn per tab is a track per tab),
# opened on the first frame that shows it: the page load for the tab already selected, the click for any other. The
# card is the first visible element under the header that paints a surface of its own (a fill that differs from what
# is behind it, a border or a shadow), at least GLANCE_CARD_MIN_PX tall, GLANCE_CARD_MIN_SHARE of its parent wide (it
# fills its column: on a wide desktop view the card sits in one of two) and GLANCE_CARD_MIN_PANE_SHARE of the pane, and
# is not a control, a disclosure's summary, a form, a segmented control or a layer (fixed, sticky, absolute). A surface
# too narrow to be the card is a tile or a lane, and nothing inside it is the card either. Until a card is drawn the pane is
# re-scanned every GLANCE_SCAN_MS; a tab whose card appears late is baselined when it appears.
#
# `first_px` is the card's layout height (`offsetHeight`, which a transform does not change) on the first frame it is
# drawn and done entering: while a finite animation or transition runs on the card, an ancestor or a descendant, the
# baseline waits, at most GLANCE_ENTRY_MAX_MS, so a deliberate entrance is not growth. An endless one (a spinner in a
# "Reading..." row) never holds it. From then on the track follows that card, or the card redrawn in its place (the
# same tag, id and classes): a banner drawn above it, or a hidden card revealed above it, is a different card and
# never compared with it. Every frame until GLANCE_TRACK_MAX_MS after the load, then the tracker stops (`stopped`).
GLANCE_CARD_MIN_PX = 48
GLANCE_CARD_MIN_SHARE = 0.6
GLANCE_CARD_MIN_PANE_SHARE = 0.3
GLANCE_ENTRY_MAX_MS = 1500
GLANCE_SCAN_MS = 100
GLANCE_TRACK_MAX_MS = 180000
GLANCE_CHANGE_CAP = 8
_GLANCE_JS_PARAMS = {"MIN_PX": GLANCE_CARD_MIN_PX, "MIN_SHARE": GLANCE_CARD_MIN_SHARE, "MIN_PANE_SHARE": GLANCE_CARD_MIN_PANE_SHARE,
                     "ENTRY_MAX_MS": GLANCE_ENTRY_MAX_MS,
                     "SCAN_MS": GLANCE_SCAN_MS, "TRACK_MAX_MS": GLANCE_TRACK_MAX_MS, "CHANGE_CAP": GLANCE_CHANGE_CAP}
GLANCE_TRACK_JS = r"""
(() => {
  if (window.top !== window || window.__designReviewGlance) return;
  const P = __PARAMS__;
  const st = window.__designReviewGlance = { tracks: [], stopped: false, error: null };
  const HEADER = '.home-head, header, [role=banner]';
  const SKIP = 'nav, [role=tablist], [role=navigation], dialog, [popover], [role=dialog], button, a, input, select, textarea, label, form, summary, '
    + '[role=search], [role=button], [role=radiogroup], [role=toolbar], .segmented';
  const shown = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && !el.closest('[hidden]'); };
  const alpha = (c) => { const m = /rgba?\(([^)]*)\)/.exec(c); if (!m) return 0; const p = m[1].split(/[ ,\/]+/).filter(Boolean); return p.length > 3 ? +p[3] : 1; };
  const behind = (el) => { for (let e = el.parentElement; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (alpha(c) > 0.02) return c; }
    return getComputedStyle(document.documentElement).backgroundColor; };
  const surface = (el, s) => (alpha(s.backgroundColor) > 0.02 && s.backgroundColor !== behind(el))
    || ['Top', 'Right', 'Bottom', 'Left'].some(k => (parseFloat(s['border' + k + 'Width']) || 0) > 0 && s['border' + k + 'Style'] !== 'none' && alpha(s['border' + k + 'Color']) > 0.02)
    || (!!s.boxShadow && s.boxShadow !== 'none');
  const layer = (s) => /^(fixed|sticky|absolute)$/.test(s.position);
  const paneOf = () => document.querySelector('[role=tabpanel]:not([hidden])') || document.querySelector('section.pane:not([hidden])') || document.querySelector('main') || document.body;
  const tabOf = () => { const l = document.querySelector('[role=tablist]'); return l ? l.querySelector('[role=tab][aria-selected=true]') : null; };
  const sel = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + CSS.escape(el.id);
    const c = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3).map(x => CSS.escape(x)).join('.'); if (c) s += '.' + c; return s; };
  const nextOut = (w) => { while (w.parentNode()) { const n = w.nextSibling(); if (n) return n; } return null; };
  // The first card under the page header: the pane's own header, else the one visible on the page (a header above the panes).
  const firstCard = (pane) => {
    const head = [...pane.querySelectorAll(HEADER)].find(shown) || [...document.querySelectorAll(HEADER)].find(shown);
    const floor = head ? head.getBoundingClientRect().bottom - 1 : -Infinity, paneW = pane.getBoundingClientRect().width * P.MIN_PANE_SHARE;
    const wide = (el, r) => r.width >= paneW && r.width >= (el.parentElement ? el.parentElement.getBoundingClientRect().width : 0) * P.MIN_SHARE;
    const walker = document.createTreeWalker(pane, NodeFilter.SHOW_ELEMENT);
    for (let el = walker.nextNode(), n = 0; el && n < 2000; n++) {
      const s = getComputedStyle(el);
      if (el.matches(HEADER) || el.matches(SKIP) || s.display === 'none' || layer(s)) { el = walker.nextSibling() || nextOut(walker); continue; }
      const r = el.getBoundingClientRect(), painted = surface(el, s) && shown(el);
      if (painted && !wide(el, r)) { el = walker.nextSibling() || nextOut(walker); continue; }
      if (painted && r.top >= floor && el.offsetHeight >= P.MIN_PX) return el;
      el = walker.nextNode(); }
    return null; };
  // The card first drawn, or the card redrawn in its place (same tag, id and classes); null once neither is in the pane.
  const follow = (tr) => { if (tr.card && tr.card.isConnected && tr.pane.contains(tr.card) && shown(tr.card)) return tr.card;
    try { return [...tr.pane.querySelectorAll(tr.sel)].find(shown) || null; } catch (e) { return null; } };
  // Running finite animations on the card, inside it or on an ancestor: an entrance still playing.
  const entering = (card) => document.getAnimations().some(a => { const t = a.effect && a.effect.target;
    if (a.playState !== 'running' || !t || !(t.contains(card) || card.contains(t))) return false;
    return Number.isFinite(a.effect.getComputedTiming().iterations); });
  const sample = () => {
    const pane = paneOf(), tab = tabOf(), now = performance.now();
    let tr = st.tracks.find(t => t.pane === pane && t.tab === tab);
    if (!tr) { tr = { pane, tab, t0: now, seen: null, first: null, last: null, sel: null, card: null, scan: -Infinity, changes: [], change_count: 0 };
      st.tracks.push(tr); if (st.tracks.length > 24) st.tracks.shift(); }
    if (tr.first === null) {
      if (!(tr.card && tr.card.isConnected && shown(tr.card)) && now - tr.scan >= P.SCAN_MS) { tr.card = firstCard(pane); tr.scan = now; }
      if (!tr.card) return;
      if (tr.seen === null) tr.seen = now;
      if (entering(tr.card) && now - tr.seen < P.ENTRY_MAX_MS) return;
      tr.first = tr.last = tr.card.offsetHeight; tr.sel = sel(tr.card); return; }
    const card = follow(tr); if (!card) return;
    tr.card = card;
    const h = card.offsetHeight;
    if (h !== tr.last) { tr.change_count++;
      if (tr.changes.length < P.CHANGE_CAP) tr.changes.push({ ms: Math.round(now - tr.t0), from_px: tr.last, to_px: h, sel: tr.sel });
      tr.last = h; }
  };
  st.current = () => { const pane = paneOf(), tab = tabOf(); return st.tracks.find(t => t.pane === pane && t.tab === tab) || null; };
  st.follow = follow; st.shown = shown; st.layer = layer;
  const tick = () => {
    if (performance.now() > P.TRACK_MAX_MS) { st.stopped = true; return; }
    if (document.body) { try { sample(); } catch (e) { st.error = String(e && e.message || e).slice(0, 200); return; } }
    requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
})();
""".replace("__PARAMS__", json.dumps(_GLANCE_JS_PARAMS))
# The read: the current tab's track. `grew_px` is the card's height now (read afresh, not the last sample) less its
# height at first paint; a card that only got shorter is negative. `below` says whether anything visible sits under the
# card in its column, in the page's flow, for its growth to move (web.dev CLS counts a shift of what was already there,
# never content added at the end): a list card that is the last thing in its pane moves nothing as it fills.
GLANCE_READ_JS = r"""
() => {
  const st = window.__designReviewGlance;
  if (!st) return { state: 'unavailable' };
  if (st.error) return { state: 'error', error: st.error };
  if (st.stopped) return { state: 'stopped' };
  const tr = st.current();
  if (!tr || tr.first === null) return { state: 'no_card' };
  const card = st.follow(tr);
  if (!card) return { state: 'card_gone', card: tr.sel, first_px: tr.first, changes: tr.changes, change_count: tr.change_count };
  const box = card.getBoundingClientRect(), now = card.offsetHeight;
  const flows = (el) => { for (let e = el; e && e !== tr.pane; e = e.parentElement) if (st.layer(getComputedStyle(e))) return false; return true; };
  let below = false, n = 0;
  for (const el of tr.pane.querySelectorAll('*')) { if (++n > 3000) break;
    if (card.contains(el) || el.contains(card) || !(card.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
    const r = el.getBoundingClientRect();
    if (r.top >= box.bottom - 1 && r.left < box.right && r.right > box.left && st.shown(el) && flows(el)) { below = true; break; } }
  return { state: 'measured', card: tr.sel, first_px: tr.first, settled_px: now, grew_px: now - tr.first, below,
    changes: tr.changes, change_count: tr.change_count };
}
"""

# A canvas this dark or this light is unambiguous; the band between is a mid-grey page that says nothing either way.
DARK_BELOW, LIGHT_ABOVE = 0.2, 0.5


def theme_agreement(requested: str, observed: Optional[dict]) -> "tuple[Optional[bool], Optional[dict]]":
    """`(applied, observed + rendered)`: did the page render the theme the walk asked for?

    `applied` is `False` when the `data-theme` attribute or a clearly dark/light canvas names the other theme (the
    attribute is the walk's own stamp, so it only disagrees when the app overwrote it), `True` when nothing
    disagrees, and `None` when the page could not be read at all: unknown is never folded into either answer.
    """
    if not isinstance(observed, dict):
        return None, None
    attr = str(observed.get("attr") or "")
    lum = observed.get("luminance")
    lum = float(lum) if isinstance(lum, (int, float)) and not isinstance(lum, bool) else None
    rendered = "dark" if lum is not None and lum < DARK_BELOW else ("light" if lum is not None and lum > LIGHT_ABOVE else None)
    disagrees = (attr in ("light", "dark") and attr != requested) or (rendered is not None and rendered != requested)
    return (not disagrees), {"attr": attr, "luminance": lum, "rendered": rendered or attr or None}


def build_script(geometry_js: Optional[str]) -> str:
    """The evaluate-ready script with the scaffold's effective-rect JS spliced in.

    `geometry_js` is `_geometry.py`'s `_EFFECTIVE_RECT_JS` (an `el => {...}`
    arrow function). `None` splices `null`, which makes the `targets` section
    report `GEOMETRY_MISSING` instead of measuring with a re-implementation.
    """
    body = f"({geometry_js})" if geometry_js else "null"
    return _MEASURE_JS.replace("__GEOMETRY__", body)


def default_params(
    hit_min: float = 44.0,
    primary_min: float = 48.0,
    boundary_min: float = 3.0,
    list_rows_max: int = 12,
    row_controls_max: int = 2,
    segmented_max: int = 5,
    primary_selector: str = "[class*=primary], button[type=submit]",
    exclude_selectors: Sequence[str] = (),
    components: Optional[Dict[str, object]] = None,
    components_error: Optional[str] = None,
) -> Dict[str, object]:
    """The parameter object the script receives; recorded verbatim in
    `metrics.json` so a consumer can see which floors a run measured with.

    `components` is `capture.vendored_components()`'s map (`{name: {roots, parts}}`);
    `None` makes the `inventory` section `unmeasured`, with `components_error` as the reason."""
    return {
        "hitMin": float(hit_min),
        "hitTol": HIT_TOLERANCE_PX,
        "primaryMin": float(primary_min),
        "boundaryMin": float(boundary_min),
        "listRowsMax": int(list_rows_max),
        "rowControlsMax": int(row_controls_max),
        "segmentedMax": int(segmented_max),
        "primarySelector": primary_selector,
        "interactive": INTERACTIVE_SELECTOR,
        "formControls": FORM_CONTROL_SELECTOR,
        "boundaryControls": BOUNDARY_CONTROL_SELECTOR,
        "glyphRe": GLYPH_ICON_RE,
        "glyphLabelMax": GLYPH_LABEL_MAX_CHARS,
        "labelFitMax": LABEL_FIT_MAX_CHARS,
        "fitTol": FIT_TOLERANCE_PX,
        "excludeSelectors": [str(s) for s in exclude_selectors],
        "components": components,
        "componentsError": components_error,
    }


SECTIONS = ("text", "controls", "targets", "icons", "nav", "feedback", "layout", "clearance", "a11y", "headings")


def section_errors(metrics: Dict[str, object]) -> Dict[str, str]:
    """`{section: error}` for every section the script could not compute."""
    out: Dict[str, str] = {}
    for name in SECTIONS:
        val = metrics.get(name)
        if isinstance(val, dict) and "error" in val:
            out[name] = str(val["error"])
        elif val is None:
            out[name] = "missing"
    return out


def metric_value(metrics: Dict[str, object], path: str) -> object:
    """Read a dotted `section.key` path from a screen's metrics.

    Returns `None` when the section errored or the key is absent — the caller
    (`evaluate.py`) turns `None` into `unmeasured`, never into a pass.
    """
    node: object = metrics
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict) and "error" in node:
        return None
    return node


def metric_paths() -> List[str]:
    """Every dotted path the script produces, for rubric validation."""
    keys = {
        "text": ["sizes", "weights", "runs", "under14", "under11", "under11_count", "min_px",
                 "low_contrast", "low_contrast_count", "body_font_family", "break_all",
                 "break_all_count", "uppercase", "uppercase_count", "glyph_icons", "glyph_icon_count",
                 "raw_identifiers", "raw_identifier_count", "heading_smaller_than_body", "heading_smaller_than_body_count",
                 "code_off_spec", "code_off_spec_count"],
        "casing": ["labels", "label_count", "labels_seen"],
        "controls": ["total", "font_family_mismatch", "font_family_mismatch_count", "mono_code_field",
                     "mono_code_field_count", "boundary_low",
                     "boundary_low_count", "ua_styled", "ua_styled_count", "label_fit_bad", "label_fit_bad_count",
                     "segmented_bad", "segmented_bad_count", "switch_count", "switch_on_count", "switches_on", "icon_button_count", "icon_buttons_painted",
                     "icon_buttons_painted_count", "icon_only_count", "icon_only_unlabelled", "reference_pill_count", "reference_pill_styles", "reference_pill_variants"],
        "targets": ["total", "small", "small_count", "overlaps", "overlap_count", "overlap_scan_truncated", "covered", "covered_count",
                    "primary", "primary_min_height", "in_summary"],
        "icons": ["boxes", "elements"],
        "nav": ["primary_count", "pane_scroll_top", "pane_header_visible", "settings_tab_count", "settings_tabs", "home_title", "header_context"],
        "feedback": ["toast_count", "toasts_tinted", "toasts_tinted_count", "empty_state_count", "empty_states_no_action"],
        "layout": ["control_overflow", "control_overflow_count", "clipped_trailing", "clipped_trailing_count", "clipped_trailing_excluded", "clipped_trailing_excluded_count", "turn_headers", "repeated_headers", "repeated_header_count", "overflow_x", "scroll_w", "inner_w", "inner_h", "pane_h", "lists", "rows_over_limit",
                   "rows_over_limit_count", "danger_rows", "content_w", "content_span", "pane_split", "radii", "open_section_count",
                   "empty_open_sections", "empty_open_section_count", "multi_secondary_rows", "multi_secondary_row_count",
                   "painted_row_verbs", "painted_row_verb_count", "delete_row_verbs", "delete_row_verb_count"],
        "overlay": ["surfaces", "destructive", "destructive_bad", "destructive_bad_count"],
        "dialogs": ["count", "with_commit", "quiet_commit", "quiet_commit_count"],
        "clearance": ["bars", "hidden_rows", "hidden_row_count"],
        "a11y": ["unnamed", "unnamed_count", "zoom_locked", "text_size_control", "text_size_stamped"],
        "coverage": ["overlays", "overlay_count"],
        "inventory": ["unmeasured", "controls", "controls_count", "kinds", "components", "text", "text_count"],
        "glance": ["state", "card", "first_px", "settled_px", "grew_px", "below", "changes", "change_count", "idle", "idle_ms", "pending"],
    }
    return [f"{s}.{k}" for s, ks in keys.items() for k in ks]
