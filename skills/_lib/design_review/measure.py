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
    controls  total, font_family_mismatch [..], font_family_mismatch_count,
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
              turn_headers [{who, count}], repeated_headers [{sel, text, who, blocks}], repeated_header_count
    overlay   surfaces, destructive [..], destructive_bad [..], destructive_bad_count
    dialogs   count, with_commit, quiet_commit [{dialog, commit, label, filled, w, h, rival, rival_label, why}], quiet_commit_count
    clearance bars [..], hidden_rows [..], hidden_row_count
    a11y     unnamed [..], unnamed_count, zoom_locked, text_size_control, text_size_stamped
    headings  ["H2:Title@16px", ...]

Lists are capped (`CAP`) so a screen with hundreds of small controls still
serialises small; the `*_count` twin carries the true total.

stdlib only — the Playwright side is `walk.py`, executed by the *target*
repo's interpreter (this repo's venv stays stdlib-only).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from design_lint.rules import GLYPH_ICON_RENDERED_RE  # noqa: E402

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
# 1.4.11's exception, and the nav draws inactive tabs without one by design (#996).
BOUNDARY_CONTROL_SELECTOR = (
    "input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file])"
    ":not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), "
    "select, textarea, [role=switch]"
)
GLYPH_ICON_RE = GLYPH_ICON_RENDERED_RE
# A control's label is "short" up to this many characters; a glyph at either end of a longer run is punctuation (#1261).
GLYPH_LABEL_MAX_CHARS = 24
# A text run in a control is a label up to this many characters; a longer one is prose that may wrap (#1325).
LABEL_FIT_MAX_CHARS = 40
# Sub-pixel slack on label fit and container overflow: a 0.4px rounding spill is not a finding (#1325).
FIT_TOLERANCE_PX = 1.5

# `__GEOMETRY__` is replaced by `build_script`. The script is one arrow
# function taking `params` so Playwright's `page.evaluate(script, params)`
# passes the resolved spec values in.
_MEASURE_JS = r"""
(params) => {
  const CAP = 80;
  const LABEL_CAP = 160;
  // The pairwise overlap test is O(n^2) over hit targets, so it scans only the first OVERLAP_SCAN_MAX of
  // them: a stated cost bound (400 targets = ~80k pair checks), well above a normal screen. `targets.overlap_scan_truncated`
  // records when it bit, so targets past it are known to be unchecked rather than silently skipped.
  const OVERLAP_SCAN_MAX = 400;
  const out = {};
  const section = (name, fn) => { try { out[name] = fn(); } catch (e) { out[name] = {error: String(e && e.message || e).slice(0, 300)}; } };

  // ---- colour helpers (canvas-parsed rgba, WCAG luminance, compositing)
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = (c) => { ctx.clearRect(0,0,1,1); ctx.fillStyle = '#000'; ctx.fillStyle = c;
    ctx.fillRect(0,0,1,1); const d = ctx.getImageData(0,0,1,1).data; return [d[0],d[1],d[2],d[3]/255]; };
  const over = (f, b) => { const a = f[3]; return [0,1,2].map(i => f[i]*a + b[i]*(1-a)).concat([1]); };
  const lum = (c) => { const f = v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(c[0]) + 0.7152*f(c[1]) + 0.0722*f(c[2]); };
  const ratio = (a,b) => { const l1=lum(a), l2=lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };
  const r2 = (x) => Math.round(x*100)/100;
  const hex = (c) => '#' + [0,1,2].map(i => Math.round(c[i]).toString(16).padStart(2,'0')).join('');
  // Embedded content (a scaled preview themed by the session, not by the app) is declared in
  // [design.review].exclude_selectors and is never measured as app UI: not visible, to every measure (#1185).
  const EXCLUDE = (params.excludeSelectors || []).join(', ');
  const visible = (el) => { if (EXCLUDE && el.closest(EXCLUDE)) return false;
    const r = el.getBoundingClientRect(); if (r.width<1||r.height<1) return false;
    const s = getComputedStyle(el); if (s.visibility==='hidden'||s.display==='none'||+s.opacity===0) return false;
    // Content of a closed <details> keeps real boxes in both engines; only checkVisibility() says it is hidden (#998).
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility()) return false;
    // SVG has no offsetParent — climb to the nearest HTML ancestor for the check.
    let h = el; while (h && !(h instanceof HTMLElement)) h = h.parentElement;
    if (!h) return true;
    if (h.closest('[hidden]')) return false;
    return !!h.offsetParent || getComputedStyle(h).position==='fixed'; };
  const bgOf = (el) => { const stack = []; let n = el;
    while (n && n.nodeType===1) { const c = rgba(getComputedStyle(n).backgroundColor); if (c[3]>0) { stack.push(c); if (c[3]>=0.999) break; } n = n.parentElement; }
    let base = rgba(getComputedStyle(document.documentElement).backgroundColor); if (base[3]===0) base=[255,255,255,1];
    for (let i=stack.length-1;i>=0;i--) base = over(stack[i], base); return base; };
  const sel = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#'+el.id;
    const c = (el.getAttribute('class')||'').trim().split(/\s+/).filter(Boolean).slice(0,3).join('.'); if (c) s += '.'+c; return s; };
  const txt = (el) => (el.getAttribute('aria-label')||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,40);
  // A control's full label (aria-label, else its text), whitespace-collapsed; filled = it draws a background that differs
  // from the surface behind it at rest (a button painted the surface's own colour is not filled); area = its box.
  const labelOf = (el) => (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim();
  const filled = (el) => { const own = rgba(getComputedStyle(el).backgroundColor); if (own[3] < 0.02) return false;
    const behind = bgOf(el.parentElement || el), now = over(own, behind);
    return [0,1,2].some(i => Math.abs(now[i] - behind[i]) > 6); };
  const area = (el) => { const r = el.getBoundingClientRect(); return r.width * r.height; };
  // An open popup: a menu, listbox or dialog. Absolutely positioned, it paints over the flow like a fixed layer (#1029).
  const POPUP = '[role=menu], [role=listbox], [role=dialog], dialog';
  // The layer an element paints in (itself or its nearest such ancestor): a fixed or sticky box, or an
  // absolutely positioned popup; else null, the flow.
  const layerOf = (el) => { for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
    const p = getComputedStyle(e).position;
    if (p === 'fixed' || p === 'sticky' || (p === 'absolute' && e.matches(POPUP))) return e; } return null; };
  const effRect = __GEOMETRY__;
  const pane = document.querySelector('[role=tabpanel]:not([hidden])') || document.querySelector('section.pane:not([hidden])') || document.querySelector('main') || document.body;
  const dialog = document.querySelector('dialog[open]');
  const scope = dialog || document.body;
  const q = (s) => [...scope.querySelectorAll(s)].filter(visible);
  const inScope = (el) => !dialog || dialog.contains(el);

  // ---- text: sizes, weights, contrast, wrapping, transforms, glyph icons
  section('text', () => {
    const sizes = {}, weights = {}, low = [], under11 = [], breakAll = [], upper = [], glyphs = [];
    let runs = 0, under14 = 0, minPx = Infinity;
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    const glyphRe = new RegExp(params.glyphRe, 'u');
    // A glyph is an icon only when it stands alone or leads or ends a short control label (a button, link or tab).
    // Inside a sentence or a title it is punctuation: user content such as a task title is data, not an icon the
    // app drew, and it comes and goes with the data, not the build (#1261).
    const glyphEdge = new RegExp('^(?:' + params.glyphRe + ')|(?:' + params.glyphRe + ')$', 'u');
    const CONTROL = 'button, a, [role=button], [role=link], [role=tab], [role=menuitem]';
    const INACTIVE = ':disabled, [aria-disabled=true]';
    // A raw identifier rendered as ordinary text (#1336): an IPv4 address, a MAC address or a snake_case token of two
    // or more parts (lower or UPPER). Code the app prints on purpose (code, pre, kbd, samp, a terminal), a form field's
    // value and a select's options are not ordinary text. The boundaries are what keep it narrow: an address needs four
    // octets of 0-255 (so a version `1.2.3`, a date and a time are not one), a MAC needs six pairs, and a token that sits
    // in a path, a URL, a file name (`my_file.txt`), a handle or a hyphenated word is part of something larger. A name the
    // user typed (a job called `nightly_backup`) cannot be told from one the app printed, so it counts: that is a finding
    // for the app to word, not a pattern to allow-list.
    const IDENT_SKIP = 'code, pre, kbd, samp, textarea, select, option, [contenteditable=true], script, style, noscript, .xterm, .xterm-rows';
    const IDENT_RES = [
      ['ip', /(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\w]|\.\d)/g],
      ['mac', /(?<![\w:-])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![\w:-])/g],
      ['snake', /(?<![\w.\/\\@#$%-])(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w@\/\\-]|\.\w)/g],
    ];
    const idents = [];
    const isGlyphIcon = (raw, el) => [...raw].length <= 2
      || (raw.length <= params.glyphLabelMax && !!el.closest(CONTROL) && glyphEdge.test(raw));
    while (walker.nextNode()) { const t = walker.currentNode; const raw = t.nodeValue.trim(); if (!raw) continue;
      const el = t.parentElement; if (!el || !visible(el) || !inScope(el)) continue;
      if (el.closest('svg, script, style, noscript, .xterm, .xterm-rows')) continue;
      if (!el.closest(IDENT_SKIP)) for (const [kind, re] of IDENT_RES) for (const m of raw.matchAll(re)) idents.push({sel: sel(el), kind, text: m[0].slice(0, 40)});
      // Preformatted text is content the app prints (a log, a code sample), not an icon it authors (#1119).
      if (glyphRe.test(raw) && !seen.has(el) && !el.closest('pre, code, samp, kbd') && isGlyphIcon(raw, el)) glyphs.push({sel: sel(el), text: raw.slice(0, 40)});
      if (seen.has(el)) continue; seen.add(el);
      const s = getComputedStyle(el); const px = Math.round(parseFloat(s.fontSize)*100)/100;
      runs++; sizes[px] = (sizes[px]||0)+1; weights[s.fontWeight] = (weights[s.fontWeight]||0)+1;
      if (px < 14) under14++; if (px < minPx) minPx = px;
      const fg = rgba(s.color); const bg = bgOf(el); const cr = ratio(over(fg,bg), bg);
      const large = px>=24 || (px>=18.66 && +s.fontWeight>=700);
      // WCAG 2.2 SC 1.4.3 exempts text in an inactive UI component: a disabled control's label is still a
      // run (size, weight) but never a contrast failure (#1385).
      if (cr < (large?3:4.5) && !el.closest(INACTIVE)) low.push({sel: sel(el), text: raw.slice(0,40), px, ratio: r2(cr), fg: hex(over(fg,bg)), bg: hex(bg), large});
      if (px < 11) under11.push({sel: sel(el), text: raw.slice(0,40), px});
      if (s.wordBreak === 'break-all') breakAll.push({sel: sel(el), text: raw.slice(0,40)});
      if (s.textTransform === 'uppercase') upper.push({sel: sel(el), text: raw.slice(0,40)});
    }
    // A heading drawn smaller than the body text it introduces (#1341). The body text is the first prose sibling among
    // the heading's next three visible siblings; the scan stops at another heading. Prose is a block of at least three
    // words and twenty characters of its own text (a p, blockquote, li or dd by its whole text), never a control, so a
    // stat value, a badge, a button, a list or an empty spacer is not body text and a heading with none is not measured.
    // Equal is not smaller, and 0.5px of rounding is not either. A heading drawn in capitals is the `overline` role, a 12px
    // group header: the one place design.md allows caps (TYPE-05 polices the rest), so its small size is the role, not a
    // heading below its body.
    const HEADING = 'h1, h2, h3, h4, h5, h6, [role=heading]';
    const NOT_PROSE = 'button, a, input, select, textarea, summary, label, [role=button], [role=tab], [role=link], [role=menuitem]';
    const BLOCK_PROSE = new Set(['P', 'BLOCKQUOTE', 'LI', 'DD']);
    const proseText = (el) => (BLOCK_PROSE.has(el.tagName) ? (el.textContent || '')
      : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' ')).replace(/\s+/g, ' ').trim();
    const isProse = (el) => { if (el.matches(HEADING) || el.matches(NOT_PROSE) || el.closest(NOT_PROSE) || el.querySelector(HEADING)) return false;
      const t = proseText(el); return t.length >= 20 && t.split(' ').length >= 3; };
    const inverted = [];
    q(HEADING).forEach(h => { let seenSib = 0;
      if (getComputedStyle(h).textTransform === 'uppercase') return;
      for (let sib = h.nextElementSibling; sib && seenSib < 3; sib = sib.nextElementSibling) {
        if (!visible(sib)) continue; seenSib++;
        if (sib.matches(HEADING)) break;
        if (!isProse(sib)) continue;
        const hp = parseFloat(getComputedStyle(h).fontSize), bp = parseFloat(getComputedStyle(sib).fontSize);
        if (hp + 0.5 < bp) inverted.push({sel: sel(h), text: txt(h), px: r2(hp), body_sel: sel(sib), body_px: r2(bp)});
        break; } });
    // Code drawn off the spec (#1342). design.md fixes code at two sizes: an inline code chip is 0.875em of the text around
    // it, never `caption` (12px), and a listing (a `pre`, a fenced block or a diff) is 13px; code never sits on a surface
    // darker than the card holding it, which is the true-black `canvas-subtle` hole in dark. So each visible `pre`, `code`
    // and `kbd` is read three ways. A `pre` under 13px (a quarter pixel of rounding excused) is a small listing. A `code` or
    // `kbd` that is not inside a `pre` is inline: it fails at caption size (12px, the one size the spec names) or more than
    // a quarter pixel under 0.875em of its parent (so 12.25px in 14px text passes and 12px in 14px text still fails as caption).
    // Any of the three whose own fill, composited over the surface behind it, is darker than that surface fails, but only
    // when the surface is itself dark (luminance under 0.2, the RENDERED_THEME dark band): in light the neutral-soft chip
    // is a shade darker than its card by design. Code with no fill, a terminal (`.xterm`) and a `code` inside a `pre`
    // (its size is the listing's) are not read for size.
    const codeOff = [];
    q('pre, code, kbd').forEach(el => { if (el.closest('.xterm, .xterm-rows, svg')) return;
      const s = getComputedStyle(el), px = parseFloat(s.fontSize), host = el.parentElement || el, why = [];
      if (el.tagName === 'PRE') { if (px + 0.25 < 13) why.push('listing under 13px'); }
      else if (!host.closest('pre')) { const pp = parseFloat(getComputedStyle(host).fontSize);
        if (Math.abs(px - 12) < 0.1) why.push('inline at caption size'); else if (px + 0.25 < 0.875 * pp) why.push('inline under 0.875em'); }
      const own = rgba(s.backgroundColor);
      if (own[3] >= 0.02) { const behind = bgOf(host), now = over(own, behind);
        if (lum(behind) < 0.2 && lum(now) + 0.005 < lum(behind)) why.push('darker than its card'); }
      if (why.length) codeOff.push({sel: sel(el), text: txt(el), px: r2(px), why: why.join(', ')}); });
    return { sizes, weights, runs, under14, under11: under11.slice(0,CAP), under11_count: under11.length,
      heading_smaller_than_body: inverted.slice(0,CAP), heading_smaller_than_body_count: inverted.length,
      code_off_spec: codeOff.slice(0,CAP), code_off_spec_count: codeOff.length,
      min_px: runs ? minPx : null, low_contrast: low.slice(0,CAP), low_contrast_count: low.length,
      body_font_family: getComputedStyle(document.body).fontFamily,
      break_all: breakAll.slice(0,CAP), break_all_count: breakAll.length,
      uppercase: upper.slice(0,CAP), uppercase_count: upper.length,
      glyph_icons: glyphs.slice(0,CAP), glyph_icon_count: glyphs.length,
      raw_identifiers: idents.slice(0,CAP), raw_identifier_count: idents.length };
  });

  // ---- casing: how the app's labels are cased (#1365). Each button, tab, menu item and heading label, plus a
  // short leaf element whose class names it a stat or label, is recorded once per text with its class and its
  // style: upper (no lowercase letter), lower (starts lowercase), capitalised (one word, capital first), sentence
  // or title (several words, capital first; title when every word after the first is capitalised). `literal` is
  // set when the characters typed in the markup are already capitals, which text-transform cannot see (TYPE-05);
  // the style is of the rendered text. Whether two styles of one class clash across the run is evaluate.py's call
  // (TYPE-06). A label with a hyphen or other identifier character is data or a name, not a casing choice,
  // and is not recorded, nor is a label with a digit or a control made of several text elements; a brand-cased word
  // inside a label (iPhone, PDF) is skipped when judging the rest.
  section('casing', () => {
    const ROW = 'li, tr, [role=listitem], [role=row], [role=option], [role=gridcell]';
    const STAT = /(?:^|[-_])(?:stat|stats|label|counter|count|metric|kpi|tally)(?:$|[-_])/i;
    const MINOR = new Set(['a','an','and','as','at','but','by','for','in','nor','of','on','or','the','to','via','vs','with']);
    const WORD = /^\p{L}[\p{L}'’]*$/u, PLAIN = /^\p{Lu}?\p{Ll}[\p{Ll}'’]*$/u;
    const textOf = (el) => { const parts = []; const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) { const n = w.currentNode, p = n.parentElement; if (!p || p.closest('svg, script, style, noscript')) continue;
        const r = p.getBoundingClientRect(); if (r.width <= 1 && r.height <= 1) continue; parts.push(n.nodeValue); }
      return parts.join(' ').replace(/\s+/g, ' ').trim(); };
    const words = (text) => { const out = [];
      for (const tok of text.split(' ')) { const t = tok.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, ''); if (!/\p{L}/u.test(t)) continue;
        if (!WORD.test(t)) return null; out.push(t); }
      return out.length && out.length <= 5 ? out : null; };
    const styleOf = (ws) => {
      if (ws.join('').replace(/[^\p{L}]/gu, '').length < 2) return null;
      if (!ws.some(w => /\p{Ll}/u.test(w))) return 'upper';
      const plain = ws.filter(w => PLAIN.test(w)); if (!plain.length) return null;
      if (/^\p{Ll}/u.test(plain[0])) return 'lower';
      const rest = plain.slice(1).filter(w => !MINOR.has(w.toLowerCase()));
      if (!rest.length) return 'capitalised';
      return rest.every(w => /^\p{Lu}/u.test(w)) ? 'title' : 'sentence'; };
    const rendered = (text, tt) => tt === 'uppercase' ? text.toUpperCase() : tt === 'lowercase' ? text.toLowerCase()
      : tt === 'capitalize' ? text.replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase()) : text;
    const found = new Map(); let seen = 0;
    // A label with a digit is a count or a reading, and a control holding two text-bearing children is a row of a
    // name and an action: neither is one label's casing.
    const add = (el, cls) => { const text = textOf(el); if (!text || text.length > 40 || /\d/.test(text)) return;
      if ([...el.children].filter(c => (c.textContent || '').trim()).length > 1) return;
      const src = words(text); if (!src) return;
      const srcStyle = styleOf(src); if (!srcStyle) return;
      const shown = rendered(text, getComputedStyle(el).textTransform), style = styleOf(words(shown) || src) || srcStyle;
      seen++; const key = cls + '|' + text; if (found.has(key)) return;
      found.set(key, {sel: sel(el), cls, text, style, literal: srcStyle === 'upper'}); };
    q('button, [role=button], [role=tab], [role=menuitem]').forEach(el => add(el, el.matches('[role=tab]') ? 'tab' : el.matches('[role=menuitem]') ? 'menuitem' : 'button'));
    q('h1, h2, h3, h4, h5, h6, [role=heading]').filter(el => !el.closest(ROW)).forEach(el => add(el, 'heading'));
    q('[class]').filter(el => !el.children.length && !el.closest(ROW) && !el.matches('button, a, [role=button], [role=tab], [role=menuitem], h1, h2, h3, h4, h5, h6'))
      .forEach(el => { const tok = (el.getAttribute('class') || '').split(/\s+/).find(c => STAT.test(c)); if (tok) add(el, 'label:' + tok); });
    const labels = [...found.values()];
    return { labels: labels.slice(0, LABEL_CAP), label_count: labels.length, labels_seen: seen };
  });

  // ---- form controls: font family vs body, boundary contrast, UA styling
  section('controls', () => {
    const bodyFam = getComputedStyle(document.body).fontFamily;
    const mism = [], lowB = [], ua = []; let total = 0;
    q(params.formControls).forEach(el => { total++;
      const s = getComputedStyle(el);
      if (s.fontFamily !== bodyFam) mism.push({sel: sel(el), family: s.fontFamily.slice(0,60)}); });
    const drawsBorder = (st) => (parseFloat(st.borderTopWidth) || 0) > 0 && rgba(st.borderTopColor)[3] > 0;
    // An inset box-shadow with no offset and no blur is a drawn edge too: the 44px-field recipe keeps the visible
    // control inside a transparent border band and draws its 1px boundary as `inset 0 0 0 1px` (#1185). Its colours.
    const insetEdges = (st) => { const v = st.boxShadow; if (!v || v === 'none') return [];
      return v.split(/,(?![^(]*\))/).map(p => { if (!/\binset\b/.test(p)) return null;
        const m = p.match(/rgba?\([^)]*\)/); if (!m) return null;
        const [x, y, blur, spread] = (p.replace(m[0], '').match(/-?\d*\.?\d+px/g) || []).map(parseFloat);
        if (x !== 0 || y !== 0 || blur !== 0 || !(spread >= 1)) return null;
        const c = rgba(m[0]); return c[3] > 0 ? c : null; }).filter(Boolean); };
    q(params.boundaryControls).forEach(el => {
      // A switch with visible text is a labelled toggle, identified by its label like a button (#996).
      if (el.getAttribute('role') === 'switch' && el.textContent.trim()) return;
      // A field whose wrapper draws the boundary (the vendored filter: a bordered label around a
      // borderless input) takes the wrapper's -- the nearest bordered ancestor hugging it, 2 levels up.
      let host = el, s = getComputedStyle(el);
      if (!drawsBorder(s) && !insetEdges(s).length && rgba(s.backgroundColor)[3] === 0) {
        const h = el.getBoundingClientRect().height;
        for (let a = el.parentElement, i = 0; a && i < 2; a = a.parentElement, i++) { const as = getComputedStyle(a);
          if (drawsBorder(as) && a.getBoundingClientRect().height <= h * 2 + 2) { host = a; s = as; break; } }
      }
      const surface = host.parentElement ? bgOf(host.parentElement) : [255,255,255,1];
      const bw = parseFloat(s.borderTopWidth) || 0; const bc = rgba(s.borderTopColor);
      let boundary = null, via = 'border';
      if (bw > 0 && bc[3] > 0) boundary = over(bc, surface);
      else {
        const edges = insetEdges(s).map(c => over(c, surface)).sort((a, b) => ratio(b, surface) - ratio(a, surface));
        if (edges.length) { boundary = edges[0]; via = 'inset-shadow'; }
        else { const own = rgba(s.backgroundColor); if (own[3] > 0) { boundary = over(own, surface); via = 'fill'; } }
      }
      if (boundary) { const cr = ratio(boundary, surface);
        if (cr < params.boundaryMin) lowB.push({sel: sel(el), label: txt(el), ratio: r2(cr), boundary: hex(boundary), surface: hex(surface), via}); }
      else lowB.push({sel: sel(el), label: txt(el), ratio: 1, boundary: 'none', surface: hex(surface), via: 'none'});
    });
    q('a[href]').forEach(el => { const c = getComputedStyle(el).color;
      if (c === 'rgb(0, 0, 238)' || c === 'rgb(85, 26, 139)') ua.push({sel: sel(el), why: 'default link colour'}); });
    q('hr').forEach(el => { const s = getComputedStyle(el);
      if (s.borderTopStyle === 'inset' || s.borderTopStyle === 'outset') ua.push({sel: sel(el), why: 'bare hr'}); });
    // segmented controls: every tablist/radiogroup except the primary tablist
    const primary = document.querySelector('[role=tablist]');
    const segs = [];
    // "wrapped" counts the label's own line boxes, never the option's box height: padding
    // and a 44px min-height make a single-line segment tall without wrapping it (#997).
    // Rects of every in-flow text node, clustered into lines by vertical overlap; text under an
    // absolutely/fixed positioned element (a badge, an sr-only label) is not part of the label's flow.
    const inFlow = (n, o) => { for (let e = n.parentElement; e && e !== o; e = e.parentElement) {
      const p = getComputedStyle(e).position; if (p === 'absolute' || p === 'fixed') return false; } return true; };
    const linesOf = rs => { rs.sort((a, b) => a.top - b.top); let lines = 0, bottom = -Infinity;
      rs.forEach(b => { if (b.top >= bottom - 1) { lines++; bottom = b.bottom; } else bottom = Math.max(bottom, b.bottom); });
      return lines; };
    const textRects = n => { const r = document.createRange(); r.selectNodeContents(n);
      return [...r.getClientRects()].filter(b => b.width > 0 && b.height > 0); };
    const lineCount = o => { const rs = []; const w = document.createTreeWalker(o, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) { if (!n.textContent.trim() || !inFlow(n, o)) continue; rs.push(...textRects(n)); }
      return linesOf(rs); };
    q('[role=tablist], [role=radiogroup], .segmented').filter(g => g !== primary).forEach(g => {
      const opts = [...g.querySelectorAll('[role=tab], [role=radio], button, label')].filter(visible);
      const wrapped = opts.some(o => lineCount(o) > 1);
      if (opts.length > params.segmentedMax || wrapped) segs.push({sel: sel(g), options: opts.length, wrapped}); });
    // The switch's on-state colour (#1200): a switch that is on draws its track in the accent. Only a track the
    // element paints itself is read; one drawn by a pseudo-element or child has no readable colour (null).
    const switches = q('[role=switch]').filter(visible), switchesOn = [];
    switches.forEach(el => { if (!(el.checked === true || el.getAttribute('aria-checked') === 'true')) return;
      const own = rgba(getComputedStyle(el).backgroundColor);
      const surface = el.parentElement ? bgOf(el.parentElement) : [255,255,255,1];
      switchesOn.push({sel: sel(el), track: own[3] > 0 ? hex(over(own, surface)) : null}); });
    // Icon buttons draw nothing at rest (#1259): a glyph-only button with a fill, border or shadow that shows
    // against its surface is painted. A state (pressed, current, expanded, selected, checked) may draw itself.
    const shows = (c, surface) => c[3] > 0.02 && Math.max(...[0,1,2].map(i => Math.abs(over(c, surface)[i] - surface[i]))) > 2;
    const textShown = (el) => [el, ...el.querySelectorAll('*')].some(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
      && (() => { const r = e.getBoundingClientRect(), st = getComputedStyle(e);
        return r.width > 2 && r.height > 2 && (st.clip === 'auto' || !st.clip) && st.clipPath === 'none'; })());
    const STATE = '[aria-pressed=true], [aria-current]:not([aria-current=false]), [aria-expanded=true], [aria-selected=true], [aria-checked=true]';
    const iconButtons = q('button:not([role]), [role=button]').filter(el => el.querySelector('svg, img') && !textShown(el) && !el.matches(STATE));
    const painted = [];
    iconButtons.forEach(el => { const st = getComputedStyle(el); const surface = el.parentElement ? bgOf(el.parentElement) : [255,255,255,1];
      const via = [];
      if (shows(rgba(st.backgroundColor), surface) || st.backgroundImage !== 'none') via.push('fill');
      if (['Top','Right','Bottom','Left'].some(k => (parseFloat(st['border'+k+'Width']) || 0) > 0
          && !['none','hidden'].includes(st['border'+k+'Style']) && shows(rgba(st['border'+k+'Color']), surface))) via.push('border');
      const shadows = st.boxShadow && st.boxShadow !== 'none' ? (st.boxShadow.match(/rgba?\([^)]*\)/g) || ['#000']) : [];
      if (shadows.some(c => rgba(c)[3] > 0.02)) via.push('shadow');
      if (via.length) painted.push({sel: sel(el), label: txt(el).slice(0,30), via: via.join('+'), fill: hex(over(rgba(st.backgroundColor), surface))}); });
    // Icon-only controls with no label beside them (#1366). An icon-only control is a button, role=button or link
    // holding a glyph (svg or img) and no visible text; its accessible name is aria-label, aria-labelledby, title, an
    // img alt or svg title, or screen-reader-only text. It has a label beside it when it sits in a <label> with text, or
    // its previous or next sibling is a bare text node or a non-interactive element showing text: a neighbouring
    // button or link is another control, not this one's label. Whether the name is a conventional glyph's is
    // evaluate.py's call (COMP-09), which also reads the sprite glyph (the <use> id) a mapped row verb must carry (#1428).
    // A control with no name at all is A11Y-01's.
    const ICON_ONLY = 'button, [role=button], a[href]';
    const CONTROLS = ICON_ONLY + ', input, select, textarea';
    const aloneText = (n) => !!n && (n.nodeType === 3 ? !!n.textContent.trim()
      : n.nodeType === 1 && !n.matches('svg, img, script, style') && !n.matches(CONTROLS)
        && !n.querySelector(CONTROLS) && textShown(n));
    const nameOf = (el) => { const lb = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => { const t = id && document.getElementById(id); return t ? t.textContent : ''; }).join(' ');
      const g = el.querySelector('img[alt], svg[aria-label], svg title');
      return (el.getAttribute('aria-label') || lb || el.getAttribute('title') || (g && (g.getAttribute('alt') || g.getAttribute('aria-label') || g.textContent)) || el.textContent || '').replace(/\s+/g, ' ').trim(); };
    const iconOnly = q(ICON_ONLY).filter(el => visible(el) && el.querySelector('svg, img') && !textShown(el));
    const unlabelled = iconOnly.filter(el => { const lab = el.closest('label');
      if (lab && [...lab.childNodes].some(n => n !== el && aloneText(n))) return false;
      return !aloneText(el.previousSibling) && !aloneText(el.nextSibling); })
      .map(el => { const u = el.querySelector('svg use'); const ref = u ? (u.getAttribute('href') || u.getAttribute('xlink:href') || '') : '';
        return {sel: sel(el), name: nameOf(el).slice(0, 40), glyph: ref.replace(/^[^#]*#/, '').slice(0, 40)}; });
    // Reference pills are one shape (#1259): every link drawn as a chip or pill on a screen shares its height,
    // radius, padding and font size. Colour is not compared: a status pill keeps its status colour on the same shape.
    const radiusPx = (v, r) => { const n = parseFloat(v) || 0; return /%$/.test(v) ? n / 100 * Math.min(r.width, r.height) : n; };
    const pillStyles = {}; let pillCount = 0;
    q('a[href], [role=link]').filter(el => el.getAttribute('role') !== 'tab' && !el.closest('[role=tablist], pre, code') && textShown(el))
      .forEach(el => { const st = getComputedStyle(el), r = el.getBoundingClientRect();
        const surface = el.parentElement ? bgOf(el.parentElement) : [255,255,255,1];
        const drawn = shows(rgba(st.backgroundColor), surface) || ((parseFloat(st.borderTopWidth) || 0) > 0 && shows(rgba(st.borderTopColor), surface));
        const rad = radiusPx(st.borderTopLeftRadius, r), h = Math.round(r.height);
        const chipClass = /(^|[\s_-])(chip|pill)([\s_-]|$)/i.test(el.getAttribute('class') || '');
        if (!chipClass && !(drawn && rad >= r.height / 2 - 1)) return;
        pillCount++;
        const shape = { h, radius: rad >= r.height / 2 - 1 ? 'pill' : Math.round(rad) + 'px',
          padding: [st.paddingTop, st.paddingRight, st.paddingBottom, st.paddingLeft].map(p => Math.round(parseFloat(p) || 0) + 'px').join(' '),
          font: st.fontSize };
        const key = [shape.h, shape.radius, shape.padding, shape.font].join('|');
        const v = pillStyles[key] || (pillStyles[key] = {...shape, count: 0, sel: sel(el), label: txt(el).slice(0,30)}); v.count++; });
    const pillVariants = Object.values(pillStyles).sort((a, b) => b.count - a.count);
    // A control's label fits its box (#1325): a short label stays on one line, and its text and glyph stay inside the
    // border box, side to side. A text run over labelFitMax characters is prose, not a label; text under an ellipsis on
    // a clipped box is a deliberate truncation; segmented options are COMP-03's. Sideways only: a line box taller than a tight
    // line-height reaches past the border box vertically on a healthy button.
    const labelFit = [];
    q('button, [role=button], a[href]').forEach(el => {
      if (el.matches('[role=tab], [role=radio]') || el.closest('.segmented, [role=tablist], [role=radiogroup]')) return;
      const st = getComputedStyle(el); if (el.tagName === 'A' && st.display === 'inline') return;
      const box = el.getBoundingClientRect(); let lines = 1, left = Infinity, right = -Infinity;
      const truncated = (n) => { for (let e = n.parentElement; e; e = e.parentElement) { const s = getComputedStyle(e);
        if (s.textOverflow === 'ellipsis' && s.overflowX !== 'visible') return true; if (e === el) break; } return false; };
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) { const t = n.textContent.trim(); if (!t || !inFlow(n, el)) continue;
        const rs = textRects(n); if (!truncated(n)) rs.forEach(b => { left = Math.min(left, b.left); right = Math.max(right, b.right); });
        if (t.length <= params.labelFitMax) lines = Math.max(lines, linesOf(rs)); }
      el.querySelectorAll('svg, img').forEach(g => { const b = g.getBoundingClientRect(); if (b.width < 1 || !inFlow(g.firstChild || g, el)) return;
        left = Math.min(left, b.left); right = Math.max(right, b.right); });
      const spill = Math.max(box.left - left, right - box.right, 0);
      if (lines > 1 || spill > params.fitTol) labelFit.push({sel: sel(el), label: txt(el).slice(0,30), lines, spill_px: Math.round(spill), w: Math.round(box.width)}); });
    return { total, font_family_mismatch: mism.slice(0,CAP), font_family_mismatch_count: mism.length,
      icon_button_count: iconButtons.length, icon_buttons_painted: painted.slice(0,CAP), icon_buttons_painted_count: painted.length,
      icon_only_count: iconOnly.length, icon_only_unlabelled: unlabelled.slice(0,CAP),
      reference_pill_count: pillCount, reference_pill_styles: pillVariants.slice(0,CAP), reference_pill_variants: pillVariants.length,
      switch_count: switches.length, switch_on_count: switchesOn.length, switches_on: switchesOn.slice(0,CAP),
      boundary_low: lowB.slice(0,CAP), boundary_low_count: lowB.length, ua_styled: ua.slice(0,CAP), ua_styled_count: ua.length,
      label_fit_bad: labelFit.slice(0,CAP), label_fit_bad_count: labelFit.length,
      segmented_bad: segs.slice(0,CAP), segmented_bad_count: segs.length };
  });

  // ---- hit targets: effective rects (scaffold JS), overlap, primary action, summary controls
  section('targets', () => {
    if (!effRect) throw new Error('GEOMETRY_MISSING');
    const els = q(params.interactive).filter(el => !el.disabled);
    const rectOf = (el) => { const r = effRect(el);
      return {el, l: r.left - r.expandLeft, t: r.top - r.expandTop, rr: r.right + r.expandRight, b: r.bottom + r.expandBottom, vw: r.right - r.left, vh: r.bottom - r.top}; };
    const rects = els.map(rectOf);
    const small = []; const floor = params.hitMin - params.hitTol;
    rects.forEach(x => { const w = x.rr - x.l, h = x.b - x.t;
      if (w < floor || h < floor) small.push({sel: sel(x.el), label: txt(x.el).slice(0,30), w: Math.round(w), h: Math.round(h), vw: Math.round(x.vw), vh: Math.round(x.vh)}); });
    // A target takes no tap outside an ancestor that clips its overflow: options scrolled out of a
    // menu's max-height lie over the rows beneath it. The overlap test clips each rect to every such
    // box on its containing-block chain (an absolutely positioned popup escapes a static clipper
    // between it and its containing block); a target clipped away entirely leaves the test (#1155).
    const cbParent = (e) => { const p = getComputedStyle(e).position; if (p === 'fixed') return null;
      let a = e.parentElement;
      if (p === 'absolute') while (a && a !== document.body && getComputedStyle(a).position === 'static' && getComputedStyle(a).transform === 'none') a = a.parentElement;
      return a; };
    const clipped = (x) => { let {l, t, rr, b} = x;
      for (let a = cbParent(x.el); a && a !== document.body && a !== document.documentElement; a = cbParent(a)) {
        const s = getComputedStyle(a); if (s.overflowX === 'visible' && s.overflowY === 'visible') continue;
        const c = a.getBoundingClientRect(); const cl = c.left + a.clientLeft, ct = c.top + a.clientTop;
        if (s.overflowX !== 'visible') { l = Math.max(l, cl); rr = Math.min(rr, cl + a.clientWidth); }
        if (s.overflowY !== 'visible') { t = Math.max(t, ct); b = Math.min(b, ct + a.clientHeight); } }
      return {...x, l, t, rr, b}; };
    const hits = rects.map(clipped).filter(x => x.l < x.rr && x.t < x.b);
    // A pair is covered, not an overlap, when a fixed or sticky layer takes the tap where the two
    // expanded rects meet and the other control's own box sits under that layer: the nav pill over
    // scrolled content, a full-screen overlay over cards (#1019). It is covered too when one control's
    // layer paints on top of the whole intersection: a switch whose expansion reaches into a nav tab
    // never gets a tap there, whatever the scroll offset (#1094). Pairs within one layer still count,
    // and so does a control painted over a layer's control, since it wins taps inside it.
    const SAMPLES = 5;
    const layerWins = (a, b) => [[a, b], [b, a]].some(([top, other]) => {
      const cover = layerOf(top.el); if (!cover || cover.contains(other.el)) return false;
      const l = Math.max(a.l, b.l, 0), r = Math.min(a.rr, b.rr, innerWidth), t = Math.max(a.t, b.t, 0), bt = Math.min(a.b, b.b, innerHeight);
      if (l >= r || t >= bt) return false;
      for (let i = 0; i < SAMPLES; i++) for (let j = 0; j < SAMPLES; j++) {
        const hit = document.elementFromPoint(l + (r - l) * (i + 0.5) / SAMPLES, t + (bt - t) * (j + 0.5) / SAMPLES);
        if (!hit || !cover.contains(hit)) return false; }
      return true; });
    const meetsInView = (a, b) => { const x = (Math.max(a.l, b.l) + Math.min(a.rr, b.rr)) / 2, y = (Math.max(a.t, b.t) + Math.min(a.b, b.b)) / 2;
      return x >= 0 && y >= 0 && x < innerWidth && y < innerHeight; };
    const coveredInView = (a, b) => {
      const x = (Math.max(a.l, b.l) + Math.min(a.rr, b.rr)) / 2, y = (Math.max(a.t, b.t) + Math.min(a.b, b.b)) / 2;
      const hit = document.elementFromPoint(x, y); const cover = hit && layerOf(hit);
      if (!cover) return false;
      const under = [a, b].find(o => !cover.contains(o.el)); if (!under) return false;
      const u = under.el.getBoundingClientRect(), c = cover.getBoundingClientRect();
      return (u.left < c.right && c.left < u.right && u.top < c.bottom && c.top < u.bottom) || layerWins(a, b); };
    // elementFromPoint sees nothing outside the viewport, so a pair that meets below the fold (the last
    // row's menu on a long list) is scrolled into view, re-measured and decided there. Every scroller
    // on the way, the page included, goes back to where it was, instantly (#1155).
    const covered = (a, b) => {
      if (meetsInView(a, b)) return coveredInView(a, b);
      const fixed = (o) => { const ly = layerOf(o.el); return !!ly && getComputedStyle(ly).position === 'fixed'; };
      const anchor = [a, b].find(o => !fixed(o)) || a;
      const saved = []; for (let e = anchor.el.parentElement; e; e = e.parentElement) saved.push([e, e.scrollTop, e.scrollLeft]);
      try { anchor.el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
        const a2 = clipped(rectOf(a.el)), b2 = clipped(rectOf(b.el));
        const meet = !(a2.rr <= b2.l || b2.rr <= a2.l || a2.b <= b2.t || b2.b <= a2.t);
        return meet && meetsInView(a2, b2) && coveredInView(a2, b2);
      } finally { saved.forEach(([e, top, left]) => e.scrollTo({top, left, behavior: 'instant'})); } };
    const overlaps = [], coveredPairs = []; let overlapCount = 0, coveredCount = 0; const n = Math.min(hits.length, OVERLAP_SCAN_MAX);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { const a = hits[i], b = hits[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const sep = a.rr <= b.l || b.rr <= a.l || a.b <= b.t || b.b <= a.t;
      if (sep) continue;
      if (covered(a, b)) { coveredCount++; if (coveredPairs.length < CAP) coveredPairs.push({a: sel(a.el), b: sel(b.el)}); }
      else { overlapCount++; if (overlaps.length < CAP) overlaps.push({a: sel(a.el), b: sel(b.el)}); } }
    const primary = q(params.primarySelector).map(el => { const r = el.getBoundingClientRect(); return {sel: sel(el), label: txt(el).slice(0,30), h: Math.round(r.height), w: Math.round(r.width)}; });
    const inSummary = q('summary').reduce((acc, s) => acc + s.querySelectorAll(params.interactive.replace(/summary,\s*/, '')).length, 0);
    return { total: els.length, small: small.slice(0,CAP), small_count: small.length, overlaps, overlap_count: overlapCount,
      overlap_scan_truncated: hits.length > OVERLAP_SCAN_MAX,
      covered: coveredPairs, covered_count: coveredCount,
      primary: primary.slice(0,CAP), primary_min_height: primary.length ? Math.min(...primary.map(p => p.h)) : null, in_summary: inSummary };
  });

  // ---- icons: box sizes, and which element each box is (#1020) -- the glyph (a sprite
  // `<use href="#i-NAME">`, else data-icon or the first non-`icon` class), the control or
  // identified ancestor hosting it, and that host's accessible name; ICON_CAP per size.
  section('icons', () => { const boxes = {}, elements = {}; const ICON_CAP = 12;
    // A chart is not an icon (#1211): an SVG with preserveAspectRatio="none" is stretched to fill its box, which an icon
    // never is (local-llm-hub's 162x64 sparklines were scored off the icon size steps).
    q('svg').filter(el => el.getAttribute('preserveAspectRatio') !== 'none').forEach(el => { const r = el.getBoundingClientRect(); const k = Math.round(r.width)+'x'+Math.round(r.height); boxes[k] = (boxes[k]||0)+1;
      const list = elements[k] || (elements[k] = []); if (list.length >= ICON_CAP) return;
      const use = el.querySelector('use'); const href = use ? (use.getAttribute('href') || use.getAttribute('xlink:href') || '') : '';
      const glyph = href.includes('#') ? href.slice(href.indexOf('#') + 1).replace(/^i-/, '')
        : (el.getAttribute('data-icon') || (el.getAttribute('class') || '').trim().split(/\s+/).filter(c => c && c !== 'icon')[0] || '');
      const ctl = el.closest(params.interactive); const host = ctl || el.closest('[id]') || el.parentElement || el;
      list.push({glyph, host: sel(host), label: ctl ? txt(ctl).slice(0, 30) : ''}); });
    return { boxes, elements }; });

  // ---- navigation
  section('nav', () => {
    const list = document.querySelector('[role=tablist]');
    const tabs = list ? [...list.querySelectorAll('[role=tab]')] : [];
    const heading = pane.querySelector('h1, h2, h3');
    const top = heading ? heading.getBoundingClientRect().top : null;
    // Settings is never a primary tab (#1200): a tab whose own name says settings, or a text-less tab drawing a gear glyph.
    const SETTINGS_WORD = /(^|[^a-z])(settings?|preferences)([^a-z]|$)/i, GEAR_GLYPH = /^(i-)?(settings|gear|cog|sliders)/i;
    const settingsTabs = tabs.filter(t => {
      const visible = (t.textContent || '').replace(/\s+/g, ' ').trim();
      const names = [visible, t.getAttribute('aria-label') || '', t.getAttribute('title') || '', t.getAttribute('data-tab') || '', t.id || ''];
      if (names.some(n => SETTINGS_WORD.test(n))) return true;
      const use = t.querySelector('use'); const href = use ? (use.getAttribute('href') || use.getAttribute('xlink:href') || '') : '';
      const glyph = href.includes('#') ? href.slice(href.indexOf('#') + 1) : ((t.querySelector('svg') || t).getAttribute('data-icon') || '');
      return !visible && GEAR_GLYPH.test(glyph);
    }).map(t => ({ sel: sel(t), label: ((t.textContent || '').replace(/\s+/g, ' ').trim() || t.getAttribute('aria-label') || '').slice(0, 30) }));
    // The home tab's page-header title against the app's name (#1360): the manifest `name` / `short_name` is the app's
    // name, the first tab's own label is the tab's. Read here, judged in evaluate.py (NAV-04). The manifest is a
    // same-origin static file, read once with a synchronous GET so the script stays synchronous; a missing, unreadable
    // or nameless manifest is reported as such and never guessed at.
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const manifest = (() => {
      const link = document.querySelector('link[rel~=manifest]');
      if (!link) return {state: 'absent', names: []};
      try { const x = new XMLHttpRequest(); x.open('GET', link.href, false); x.send();
        if (x.status !== 0 && (x.status < 200 || x.status > 299)) return {state: 'unreadable', names: []};
        const j = JSON.parse(x.responseText);
        const names = [j && j.name, j && j.short_name].filter(n => typeof n === 'string' && norm(n)).map(n => n.replace(/\s+/g, ' ').trim());
        return {state: names.length ? 'ok' : 'unnamed', names};
      } catch (e) { return {state: 'unreadable', names: []}; } })();
    const first = tabs[0] || null;
    const isHome = !!first && first.getAttribute('aria-selected') === 'true';
    // The header sits in the pane (the vendored home-head per tab) or once above the panes (task-os): the pane's own
    // title first, else the first visible one on the page.
    const titles = [...document.querySelectorAll('.home-title')].filter(visible);
    const titleEl = isHome ? titles.find(t => pane.contains(t)) || titles[0] || null : null;
    const homeTitle = { is_home: isHome, manifest: manifest.state, app_names: manifest.names,
      tab: first ? ((first.textContent || '').replace(/\s+/g, ' ').trim() || first.getAttribute('aria-label') || first.getAttribute('data-tab') || '').slice(0, 40) : null,
      title: titleEl ? (titleEl.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40) : null };
    // The page header's context line (#1395): the `.status` slot of the visible `.home-head`, the pane's own first, else
    // the first on the page. `header` says whether a header was found at all; `text` is the line as drawn (null when the
    // header has no slot, '' when the slot is empty). `static` says whether that exact text is already in the page as
    // served (markup nobody wired), as against written by script: one data state cannot tell a fixed line from a derived
    // one that happens not to change, but a line sitting in the served markup is fixed. null = the source was unreadable.
    // Judged across the screens of one tab in evaluate.py (NAV-05).
    const heads = [...document.querySelectorAll('.home-head')].filter(visible);
    const headEl = heads.find(h => pane.contains(h)) || heads[0] || null;
    const statusEl = headEl ? headEl.querySelector('.status') : null;
    const selected = tabs.find(t => t.getAttribute('aria-selected') === 'true') || null;
    const lineText = statusEl ? (statusEl.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80) : null;
    const servedLines = !lineText ? null : (() => {
      try { const x = new XMLHttpRequest(); x.open('GET', location.href, false); x.send();
        if (x.status < 200 || x.status > 299) return null;
        return [...new DOMParser().parseFromString(x.responseText, 'text/html').querySelectorAll('.home-head .status')]
          .map(e => norm(e.textContent).slice(0, 80)); } catch (e) { return null; } })();
    const headerContext = { header: !!headEl,
      tab: selected ? ((selected.textContent || '').replace(/\s+/g, ' ').trim() || selected.getAttribute('aria-label') || selected.getAttribute('data-tab') || '').slice(0, 40) : null,
      text: lineText, static: servedLines ? servedLines.includes(norm(lineText)) : null };
    return { primary_count: tabs.length, pane_scroll_top: pane.scrollTop || 0,
      pane_header_visible: heading ? (top >= 0 && top <= window.innerHeight) : null,
      settings_tab_count: settingsTabs.length, settings_tabs: settingsTabs, home_title: homeTitle, header_context: headerContext };
  });

  // ---- feedback: a toast is neutral, only a real error tints (#1200). A toast is a visible fixed-position
  // element whose class says toast/snackbar; a green background or border (hue 75-170, saturation >= 0.25 once
  // composited over its surface) is the success tint the standard forbids. Most screens show no toast: the
  // count says so, and evaluate.py reads zero as not applicable, never as neutral.
  section('feedback', () => {
    const isGreen = (c) => { const r = c[0]/255, g = c[1]/255, b = c[2]/255; const mx = Math.max(r,g,b), mn = Math.min(r,g,b), d = mx - mn;
      if (d === 0) return false; const l = (mx + mn) / 2; const sat = d / (1 - Math.abs(2*l - 1));
      const h = (mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60; const hue = (h + 360) % 360;
      return sat >= 0.25 && hue >= 75 && hue <= 170; };
    const toasts = q('[class*=toast], [class*=snackbar]').filter(el => getComputedStyle(el).position === 'fixed');
    const tinted = [];
    toasts.forEach(el => { const st = getComputedStyle(el); const surface = el.parentElement ? bgOf(el.parentElement) : [255,255,255,1];
      const own = rgba(st.backgroundColor); const bc = rgba(st.borderTopColor); const via = [];
      if (own[3] > 0.05 && isGreen(over(own, surface))) via.push('background');
      if ((parseFloat(st.borderTopWidth) || 0) > 0 && bc[3] > 0.05 && isGreen(over(bc, surface))) via.push('border');
      if (via.length) tinted.push({sel: sel(el), label: txt(el).slice(0,30), via: via.join('+')}); });
    // An empty-state block with no way forward (#1367). A block is a visible .empty-state or [data-state=empty]
    // element, innermost when one holds another, whose lifecycle state is not loading, error or stale (the nearest
    // [data-state] says; with none, a message that ends in an ellipsis or opens with a reading word is a loading
    // line). It has a way forward when it holds an enabled button, link or role=button. Whether the sentence names a
    // control elsewhere on the screen stays with the judge (J-09). Most screens are populated and show no block: the
    // count says so, and evaluate.py reads zero as not measured, never as a pass.
    const ACTION = 'button, a[href], [role=button], input[type=button], input[type=submit]';
    const cands = q('.empty-state, [data-state="empty"]');
    const msgOf = (el) => { let t = el.textContent || ''; el.querySelectorAll(ACTION).forEach(c => { t = t.replace(c.textContent || '', ''); });
      return t.replace(/\s+/g, ' ').trim(); };
    const emptyBlocks = cands.filter(el => !cands.some(o => o !== el && el.contains(o))).filter(el => {
      const host = el.closest('[data-state]'); const state = host ? (host.getAttribute('data-state') || '').trim().toLowerCase() : '';
      if (/^(loading|error|stale)$/.test(state)) return false;
      const m = msgOf(el); return !(/(…|\.\.\.)$/.test(m) || /^(loading|reading|checking|connecting)\b/i.test(m)); });
    const hasAction = (el) => [...el.querySelectorAll(ACTION)].some(c => visible(c) && !c.disabled && c.getAttribute('aria-disabled') !== 'true');
    const noAction = emptyBlocks.filter(el => !hasAction(el)).map(el => ({sel: sel(el), text: msgOf(el).slice(0, 40)}));
    return { toast_count: toasts.length, toasts_tinted: tinted.slice(0,CAP), toasts_tinted_count: tinted.length,
      empty_state_count: emptyBlocks.length, empty_states_no_action: noAction.slice(0,CAP) };
  });

  // ---- layout
  section('layout', () => {
    const de = document.documentElement;
    const lists = []; const rowsOver = []; const dangerRows = new Set(); const radii = {};
    q('ul, ol, table, [role=list]').forEach(l => { const rows = l.querySelectorAll(':scope > li, :scope > tbody > tr, :scope > tr, :scope > [role=listitem]').length;
      // The filter may sit any level up to the pane: a board's lanes are each a <section>, and the
      // one filter above them serves every lane's list (#1155).
      if (rows > params.listRowsMax) { let filter = false;
        for (let e = l.parentElement; e && !filter; e = e.parentElement) {
          filter = !!e.querySelector('input[type=search], input[placeholder*="filter" i], input[placeholder*="search" i], [role=searchbox]');
          if (e === pane || e.matches('[role=tabpanel], main, body')) break; }
        lists.push({sel: sel(l), rows, has_filter: filter}); } });
    // Secondary lines under a repeating row's title (#1338). A "line" is a text block, not a rendered line: the units
    // are the block containers holding the row's text (a <br> splits one), so one long meta line that wraps stays one
    // unit however many line boxes it takes, and the spans of one meta line (a flex row, wrapping or not) are one.
    // Left out: chips and badges (by class or by a small painted inline box), the row's trailing controls and anything in a box up to 64px square (the avatar,
    // the date tile), a nested row, an open disclosure's body. The title is the largest unit (then the heaviest, then
    // the topmost); a secondary line is a unit below it, left-aligned with it and no larger, and units on the same
    // visual band are one line. Only a row of a list (two or more siblings), outside a nav, menu or popup, is judged.
    const ROWISH = 'li, tr, [role=listitem], [role=row]';
    const CHIP_CLASS = /(^|[-_\s])(chip|badge|pill|kebab|accessory)($|[-_\s])/i;
    const secondaryLines = (row, extras, disclosed) => {
      if (!row.parentElement || [...row.parentElement.children].filter(c => c.tagName === row.tagName).length < 2) return null;
      if (row.closest('nav, [role=navigation], [role=menu], [role=menubar], [role=listbox], [role=tablist], [role=tree], ' + POPUP)) return null;
      const units = [];
      const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const host = n.parentElement; if (!host || !/\S/.test(n.data)) continue;
        if (host.closest('script, style, template, noscript, svg, summary') || host.closest(ROWISH) !== row || !visible(host)) continue;
        if (disclosed(host) || extras.some(c => c.contains(host))) continue;
        let skip = false;
        for (let e = host; e && e !== row && !skip; e = e.parentElement) {
          const st = getComputedStyle(e), r = e.getBoundingClientRect();
          skip = (typeof e.className === 'string' && CHIP_CLASS.test(e.className)) || e.matches('[role=status]')
            || (st.display.startsWith('inline') && r.height <= 32 && (rgba(st.backgroundColor)[3] > 0.05 || (parseFloat(st.borderTopWidth) || 0) > 0))
            || (e !== host && r.width > 0 && r.width <= 64 && r.height <= 64); }
        if (skip) continue;
        const range = document.createRange(); range.selectNodeContents(n);
        const rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0); if (!rects.length) continue;
        let cont = host; while (cont !== row && /^(inline|contents)$/.test(getComputedStyle(cont).display)) cont = cont.parentElement;
        const par = cont !== row ? cont.parentElement : null, ps = par ? getComputedStyle(par) : null;
        if (ps && ps.display.includes('flex') && ps.flexDirection.startsWith('row') && ps.flexWrap !== 'nowrap') cont = par;
        let br = 0; cont.querySelectorAll('br').forEach(b => { if (n.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING) br++; });
        const hs = getComputedStyle(host), fs = parseFloat(hs.fontSize) || 0, fw = parseInt(hs.fontWeight, 10) || 400;
        let u = units.find(x => x.cont === cont && x.br === br);
        if (!u) { u = {cont, br, top: Infinity, bottom: -Infinity, left: Infinity, fs: 0, fw: 0, text: ''}; units.push(u); }
        rects.forEach(r => { u.top = Math.min(u.top, r.top); u.bottom = Math.max(u.bottom, r.bottom); u.left = Math.min(u.left, r.left); });
        u.fs = Math.max(u.fs, fs); u.fw = Math.max(u.fw, fw); u.text += n.data; }
      if (!units.length) return null;
      const title = [...units].sort((a, b) => b.fs - a.fs || b.fw - a.fw || a.top - b.top || a.left - b.left)[0];
      const below = units.filter(u => u !== title && u.top >= title.bottom - 2 && Math.abs(u.left - title.left) <= 6 && u.fs <= title.fs + 0.5)
        .sort((a, b) => a.top - b.top);
      const bands = [];
      below.forEach(u => { const b = bands[bands.length - 1];
        const lap = b ? Math.min(u.bottom, b.bottom) - Math.max(u.top, b.top) : 0;
        if (b && lap >= 0.5 * Math.min(u.bottom - u.top, b.bottom - b.top)) b.bottom = Math.max(b.bottom, u.bottom);
        else bands.push({top: u.top, bottom: u.bottom}); });
      return {lines: bands.length, title: title.text.replace(/\s+/g, ' ').trim().slice(0, 30)}; };
    const multiSecondary = [], paintedVerbs = [], deleteVerbs = [];
    const DELETE_NAME = /\b(?:delete|remove|trash|discard|erase)\b/i;
    const paintShows = (c, surface) => c[3] > 0.02 && Math.max(...[0,1,2].map(i => Math.abs(over(c, surface)[i] - surface[i]))) > 2;
    // A count chip is a disclosure, not a verb (#1429, owner decision 2026-10-14): a button whose whole visible label is
    // a plus and a number, optionally followed by "more" ("+3", "+3 more"), reveals more of the same row and acts on
    // nothing. Structural and narrow: the label is the entire text, so "+ Add" or "Archive +3" is still a verb.
    const COUNT_CHIP = /^\+\s*\d+(?:\s+more)?$/i;
    const ROW_VERB_NOT = '[role=tab], [role=switch], [role=checkbox], [role=radio], [role=menuitem], [role=option], summary, '
      + '[aria-pressed=true], [aria-current]:not([aria-current=false]), [aria-expanded=true], [aria-selected=true], [aria-checked=true]';
    q(ROWISH).forEach(row => {
      // A week of a month grid is not an action row: the WAI-ARIA grid pattern's rows (#1017).
      if ((row.tagName === 'TR' || row.getAttribute('role') === 'row') && row.closest('[role=grid], [role=treegrid]')) return;
      // An open disclosure inside the row holds the actions the row would otherwise show, so its
      // controls are not the row's: a popup or a fixed box, an open <details>' body, or the region an
      // expanded trigger names in aria-controls. The trigger itself (the kebab, the summary) still
      // counts as the row's accessory (#1029).
      const regions = [...row.querySelectorAll('[aria-expanded=true][aria-controls]')]
        .flatMap(t => t.getAttribute('aria-controls').trim().split(/\s+/)).map(id => document.getElementById(id))
        .filter(r => r && r !== row && row.contains(r));
      const disclosed = (c) => { for (let e = c.parentElement; e && e !== row; e = e.parentElement) {
        if (e.matches(POPUP) || regions.includes(e) || getComputedStyle(e).position === 'fixed') return true;
        if (e.tagName === 'DETAILS' && e.open) { const s = c.closest('summary'); if (!s || s.parentElement !== e) return true; } }
        return false; };
      // "besides the row itself": a row whose tap target is one control spanning most of it
      // (action-row's main button) does not count that control (#996).
      const ctl = [...row.querySelectorAll(params.interactive)].filter(visible).filter(c => !disclosed(c));
      const rw = row.getBoundingClientRect().width;
      const main = ctl.reduce((a, c) => (!a || c.getBoundingClientRect().width > a.getBoundingClientRect().width) ? c : a, null);
      const extras = main && main.getBoundingClientRect().width >= rw * 0.5 ? ctl.filter(c => c !== main) : ctl;
      if (extras.length > params.rowControlsMax) rowsOver.push({sel: sel(row), controls: extras.length});
      const sec = secondaryLines(row, extras, disclosed);
      if (sec && sec.lines > 1) multiSecondary.push({sel: sel(row), label: sec.title, lines: sec.lines});
      // A row verb draws nothing at rest (#1420): a visible button in a row's own controls with a fill, border or
      // shadow that shows against the row's surface is painted, text or glyph alike. The row's tap-target control
      // (`main`), a switch, tab, checkbox, radio or menu item, a count chip ("+3 more", #1429) and a control in a
      // pressed, current, expanded, selected or checked state are not verbs; a state may draw itself.
      const verbHost = !row.closest('nav, [role=navigation], [role=menu], [role=menubar], [role=listbox], [role=tablist], [role=tree]');
      if (verbHost) extras.forEach(c => {
        if (!c.matches('button, [role=button]') || c.matches(ROW_VERB_NOT) || c.closest(ROWISH) !== row) return;
        if (COUNT_CHIP.test(txt(c).trim())) return;
        const st = getComputedStyle(c), surface = bgOf(c.parentElement || row), via = [];
        if (paintShows(rgba(st.backgroundColor), surface) || st.backgroundImage !== 'none') via.push('fill');
        if (['Top','Right','Bottom','Left'].some(k => (parseFloat(st['border'+k+'Width']) || 0) > 0
            && !['none','hidden'].includes(st['border'+k+'Style']) && paintShows(rgba(st['border'+k+'Color']), surface))) via.push('border');
        const sh = st.boxShadow && st.boxShadow !== 'none' ? (st.boxShadow.match(/rgba?\([^)]*\)/g) || ['#000']) : [];
        if (sh.some(s => rgba(s)[3] > 0.02)) via.push('shadow');
        if (via.length) paintedVerbs.push({sel: sel(c), label: txt(c).slice(0, 30) || c.getAttribute('title') || 'icon', via: via.join('+'), row: sel(row)}); });
      // Delete and Remove are never a visible control on a row, a sheet's editing list included (#1420, owner decision
      // 2026-10-14): a visible button in the row's own controls named delete, remove, trash, discard or erase (its
      // accessible name, title or visible text, a class or a glyph reference) belongs in the row menu or an edit mode.
      if (verbHost) extras.forEach(c => {
        if (!c.matches('button, [role=button]') || c.matches('[role=tab], [role=switch], [role=checkbox], [role=radio], [role=menuitem], [role=option], summary') || c.closest(ROWISH) !== row) return;
        const glyph = [...c.querySelectorAll('use')].map(u => u.getAttribute('href') || u.getAttribute('xlink:href') || '').join(' ');
        const named = [c.getAttribute('aria-label'), c.getAttribute('title'), c.textContent, typeof c.className === 'string' ? c.className : '', glyph].filter(Boolean).join(' ').replace(/[_-]+/g, ' ');
        if (DELETE_NAME.test(named)) deleteVerbs.push({sel: sel(c), label: txt(c).slice(0, 30) || c.getAttribute('title') || 'icon', row: sel(row)}); });
      if (ctl.some(c => /danger|destructive|delete|remove/i.test(c.className || ''))) dangerRows.add(row); });
    const all = q('*');
    all.forEach(el => { const r = getComputedStyle(el).borderTopLeftRadius; if (r && r !== '0px') radii[r] = (radii[r]||0)+1; });
    const content = pane.getBoundingClientRect();
    // content_span: the pane plus a detail pane docked beside it (master-detail, #996) -- a
    // visible full-height element right of the pane, outside it and outside the nav.
    let spanRight = content.right;
    all.forEach(el => { if (el.contains(pane) || pane.contains(el) || el.closest('[role=tablist]')) return;
      const r = el.getBoundingClientRect();
      if (r.left >= content.right - 2 && r.height >= window.innerHeight * 0.5 && r.width >= window.innerWidth * 0.2)
        spanRight = Math.max(spanRight, r.right); });
    // pane_split: two regions side by side inside the pane itself (a month and its day pane, a list and its
    // detail, #1361) -- the pane is two-dimensional though no detail pane is docked outside it. Same size floor
    // as the docked pane (half the window tall, a fifth wide), so a row of short cards is not a split.
    const splitIn = (host) => {
      const boxes = [...host.children].filter(c => visible(c) && !['fixed', 'absolute'].includes(getComputedStyle(c).position))
        .map(c => c.getBoundingClientRect()).filter(r => r.height >= window.innerHeight * 0.5 && r.width >= window.innerWidth * 0.2)
        .sort((a, b) => a.left - b.left);
      return boxes.some((a, i) => boxes.slice(i + 1).some(b => b.left >= a.right - 2 && b.top < a.bottom && a.top < b.bottom)); };
    const paneSplit = splitIn(pane) || [...pane.querySelectorAll('*')].some(splitIn);
    // A control past the side of its own container (#1325): the nearest ancestor that clips, is a dialog or draws a
    // box. The page need not scroll for it to be broken (a dialog card clips or scrolls its own overflow). A
    // scroller that is none of those, or snaps, scrolls its controls on purpose; a control in a fixed layer inside the
    // container is positioned against the viewport, not the container.
    const drawsBox = (a, st) => rgba(st.backgroundColor)[3] > 0.02
      || ['Left','Right'].some(k => (parseFloat(st['border'+k+'Width']) || 0) > 0 && !['none','hidden'].includes(st['border'+k+'Style']));
    const containerOf = (el) => { for (let a = el.parentElement; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
      const st = getComputedStyle(a); if (st.display === 'contents') continue;
      const boxed = a.matches('dialog, [role=dialog]') || drawsBox(a, st);
      if (st.overflowX === 'auto' || st.overflowX === 'scroll') { if (boxed && st.scrollSnapType === 'none') return a; return null; }
      if (st.overflowX === 'hidden' || st.overflowX === 'clip' || boxed) return a; }
      return null; };
    const spilled = [];
    q(params.interactive).filter(visible).forEach(el => {
      const c = containerOf(el); if (!c) return;
      const layer = layerOf(el); if (layer && layer !== c && c.contains(layer)) return;
      const r = el.getBoundingClientRect(), cr = c.getBoundingClientRect(), left = cr.left + c.clientLeft, right = left + c.clientWidth;
      const over = Math.max(left - r.left, r.right - right);
      if (over > params.fitTol) spilled.push({sel: sel(el), label: txt(el).slice(0,30), container: sel(c), over_px: Math.round(over), side: r.right - right >= left - r.left ? 'right' : 'left'}); });
    // An expanded section that shows nothing (#1337): an open <details> (its body is every child but the summary) or
    // the panel an expanded trigger names in aria-controls (a trigger naming no panel in the page, or a menu, listbox
    // or combobox popup, has no section to judge). Empty = no rendered text (a glyph from a pseudo-element counts), no
    // graphic (image, svg, canvas, media, frame), no control and no sign the content is still on its way (a spinner,
    // skeleton or busy/status region, or a loading/pending/error/stale data-state): content in flight is not empty.
    // A body holding an empty-state block is that block's to answer (COMP-10), so it is skipped here.
    const BUSY = '[aria-busy=true], [role=status], [role=alert], [role=progressbar], [aria-live]:not([aria-live=off]), [data-state=loading], '
      + '[class*=spinner i], [class*=skeleton i], [class*=loading i], [class*=shimmer i]';
    const SOMETHING = 'img, svg, canvas, video, audio, picture, iframe, object, embed, meter, progress, [role=img], button, a[href], '
      + 'input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [role=switch], [role=checkbox], [contenteditable], [tabindex]:not([tabindex="-1"])';
    const shows = (el) => {
      if (el.matches('script, style, template, noscript')) return false;
      if (getComputedStyle(el).display !== 'contents' && typeof el.checkVisibility === 'function' && !el.checkVisibility({visibilityProperty: true})) return false;
      if (el.shadowRoot || el.matches(SOMETHING) || el.matches(BUSY)) return true;
      if (['::before', '::after'].some(p => { const c = getComputedStyle(el, p).content; return c && !['none', 'normal', '""', "''"].includes(c); })) return true;
      return [...el.childNodes].some(n => n.nodeType === 3 ? /[^\s​-‍⁠﻿]/.test(n.data) : n.nodeType === 1 && shows(n)); };
    const inFlight = (el) => !!el.closest('[aria-busy=true]') || /^(loading|pending|error|stale)$/.test(((el.closest('[data-state]') || el).getAttribute('data-state') || '').trim().toLowerCase())
      || el.matches(BUSY);
    const emptyBlocks = q('.empty-state, [data-state="empty"]');
    const openSections = new Map();
    q('details[open]').forEach(d => { const s = d.querySelector(':scope > summary');
      openSections.set(d, {kids: [...d.childNodes].filter(n => n !== s), label: s ? txt(s) : ''}); });
    q('[aria-expanded="true"][aria-controls]').filter(t => !t.matches('[aria-haspopup]:not([aria-haspopup=false]), [role=combobox], [role=tab], [role=treeitem], [role=row], [role=menuitem]'))
      .forEach(t => t.getAttribute('aria-controls').trim().split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).forEach(p => {
        if (p.hasAttribute('hidden') || !inScope(p) || p.contains(t) || openSections.has(p) || (EXCLUDE && p.closest(EXCLUDE))) return;
        if (typeof p.checkVisibility === 'function' && !p.checkVisibility()) return;
        openSections.set(p, {kids: [...p.childNodes], label: txt(t)}); }));
    const emptyOpen = [];
    openSections.forEach(({kids, label}, el) => {
      if (inFlight(el) || emptyBlocks.some(b => el.contains(b))) return;
      if (kids.some(n => n.nodeType === 3 ? /[^\s​-‍⁠﻿]/.test(n.data) : n.nodeType === 1 && shows(n))) return;
      emptyOpen.push({sel: sel(el), label}); });
    // Trailing figures cut by an element's own ellipsis (#1343). design.md keeps the parts a step line must never lose (`+N -M`,
    // `N failed`, a duration) as non-shrinking trailing items outside the ellipsis; a single-line element that truncates them
    // is measurable from geometry. A candidate is a visible element with `text-overflow: ellipsis` on a clipping box
    // (`overflow-x` not visible) that keeps its text on one line (`white-space` nowrap or pre) and really overflows
    // (`scrollWidth` over `clientWidth`). Its hidden text is found per word: each whitespace-delimited word's last character
    // is a Range, and a word is cut when that character's box is collapsed to zero width (the engine lays out nothing the
    // ellipsis replaces, which is how WebKit reports it) or ends past the element's content edge (a word cut in half counts;
    // one that lies whole beside the ellipsis glyph is still read in full). The hidden part holds a count-like token only when a cut word is a number
    // (digits, a `+N` / `-N` / minus-sign figure, a percentage, a decimal, a clock time or a duration such as 2.4s, 1m30s, 3:45)
    // or a status word (failed, errors, warnings, passed, skipped, pending) straight after a number. A cut word that is only
    // words, a file name or a date does not count, and neither does text that wraps, a line clamp, or a box that fits. An
    // element inside one already counted is the same cut line.
    const NUMLIKE = /^[+\-\u2212\u2013\u00b1]?\d[\d.,:]*(?:%|ms|min|[smhdkMxB])?(?:\d+(?:[.,]\d+)?(?:ms|[smhd]))*$/;
    const STATUSWORD = /^(?:failed|failures?|errors?|warnings?|passed|skipped|pending)$/i;
    const clippedEls = [], clippedTrailing = [];
    all.forEach(el => {
      const st = getComputedStyle(el);
      if (st.textOverflow !== 'ellipsis' || st.overflowX === 'visible' || !/^(?:nowrap|pre)$/.test(st.whiteSpace)) return;
      if (el.scrollWidth <= el.clientWidth + 1 || clippedEls.some(c => c.contains(el))) return;
      const edge = el.getBoundingClientRect().left + el.clientLeft + el.clientWidth - (parseFloat(st.paddingRight) || 0), toks = [];
      const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = tw.nextNode(); n; n = tw.nextNode()) for (const m of n.textContent.matchAll(/\S+/g)) {
        const end = m.index + m[0].length, rg = document.createRange(); rg.setStart(n, end - 1); rg.setEnd(n, end);
        const b = rg.getBoundingClientRect();
        toks.push({t: m[0].replace(/^[(\[]+|[)\],;.]+$/g, ''), cut: b.width === 0 || b.right > edge + 0.5}); }
      const figures = toks.filter((k, i) => k.cut && (NUMLIKE.test(k.t) || (STATUSWORD.test(k.t) && i > 0 && NUMLIKE.test(toks[i - 1].t)))).map(k => k.t);
      if (figures.length) { clippedEls.push(el);
        clippedTrailing.push({sel: sel(el), text: txt(el), why: 'cuts ' + figures.slice(0, 4).join(' '), over_px: Math.round(el.scrollWidth - el.clientWidth)}); } });
    // A who-when header repeated inside one turn (#1344). design.md's chat-turn contract gives an agent's turn ONE header
    // (mark, name, time, copy, collapse) and never one card per text fragment, each with its own "who - when". To stay on that
    // structure and off ordinary lists that repeat a label, a block counts only when ALL of these hold: its header (the
    // <summary> of a <details>, else its first visible element child, looking through at most two single-child wrappers such as
    // li > details) carries a control (a button, a link or the summary's own disclosure: the contract's copy and collapse);
    // the header holds no body element (a paragraph, list, heading or table: a wrapper whose first child is a whole card is not a header);
    // that header's visible text outside controls and icons reads "<who> <clock time> [<short hint>]", the who being one to
    // three words with no digits and a chat speaker (agent, assistant, you, a named coding agent: SPEAKER), so a bare "Today",
    // a date, a title or an event list that reads "Standup 09:00" on every card never qualifies; and the block holds more than
    // its header. A run is two or more CONSECUTIVE visible siblings whose headers are identical once the clock time becomes a
    // placeholder, so "Agent 10:42" and "Agent 10:43" are the same header, and a different who or hint breaks the run.
    // `turn_headers` lists every qualifying who: how the rule knows the walk reached a rendered transcript at all.
    const WHOWHEN = /^([A-Za-z\u00c0-\u024f][^\d\n]{0,31}?)[\s\u00b7\u2022|,\u2013\u2014-]*(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?)(?!\d)(.{0,30})$/;
    const SPEAKER = /^(?:agent|assistant|claude|codex|gemini|pi|copilot|grok|antigravity|chief|you|user|me|bot|ai)\b/i;
    const kidsOf = (n) => [...n.children].filter(visible);
    const headerOf = (b) => { let n = b;
      for (let d = 0; d < 3; d++) {
        if (n.tagName === 'DETAILS') return n.querySelector(':scope > summary');
        const k = kidsOf(n); if (!k.length) return null;
        if (k.length === 1 && d < 2) { n = k[0]; continue; }
        return k[0]; }
      return null; };
    const headerText = (h) => { const out = [], tw = document.createTreeWalker(h, NodeFilter.SHOW_TEXT);
      for (let n = tw.nextNode(); n; n = tw.nextNode()) { const pe = n.parentElement;
        const ctl = pe && pe.closest('button, a, svg, [aria-hidden=true]');
        if (!pe || (ctl && h.contains(ctl))) continue;
        if (n.data.trim() && visible(pe)) out.push(n.data.trim()); }
      return out.join(' ').replace(/\s+/g, ' ').trim(); };
    const turnHeader = (b) => { const h = headerOf(b); if (!h || !visible(h)) return null;
      if (!(h.tagName === 'SUMMARY' || h.querySelector('button, [role=button], a[href], summary'))) return null;
      if (h.querySelector('p, ul, ol, li, pre, blockquote, table, details, section, article, h1, h2, h3, h4, h5, h6')) return null;
      const m = WHOWHEN.exec(headerText(h)); if (!m) return null;
      const who = m[1].replace(/[\s\u00b7\u2022|,\u2013\u2014-]+$/, '').trim(); if (!who || who.split(/\s+/).length > 3 || !SPEAKER.test(who)) return null;
      if ((b.textContent || '').trim().length <= (h.textContent || '').trim().length) return null;
      const hint = m[3].trim();
      return { who, key: who.toLowerCase() + '|' + hint.toLowerCase(), shown: who + ' \u00b7 <time>' + (hint ? ' ' + hint : '') }; };
    const turnWhos = new Map(), repeatedHeaders = [];
    new Set(all.filter(e => e.children.length > 1)).forEach(parent => {
      const blocks = kidsOf(parent).map(b => ({ b, th: turnHeader(b) }));
      blocks.forEach(x => { if (x.th) turnWhos.set(x.th.who, (turnWhos.get(x.th.who) || 0) + 1); });
      for (let i = 0; i < blocks.length;) {
        let j = i + 1; while (blocks[i].th && j < blocks.length && blocks[j].th && blocks[j].th.key === blocks[i].th.key) j++;
        if (blocks[i].th && j - i >= 2) repeatedHeaders.push({ sel: sel(parent), text: blocks[i].th.shown, who: blocks[i].th.who, blocks: j - i });
        i = j; } });
    return { control_overflow: spilled.slice(0,CAP), control_overflow_count: spilled.length,
      turn_headers: [...turnWhos].map(([who, count]) => ({who, count})).slice(0,CAP), repeated_headers: repeatedHeaders.slice(0,CAP), repeated_header_count: repeatedHeaders.length,
      clipped_trailing: clippedTrailing.slice(0,CAP), clipped_trailing_count: clippedTrailing.length, overflow_x: de.scrollWidth > window.innerWidth + 1, scroll_w: de.scrollWidth, inner_w: window.innerWidth, inner_h: window.innerHeight,
      pane_h: pane.scrollHeight, lists: lists.slice(0,CAP), rows_over_limit: rowsOver.slice(0,CAP), rows_over_limit_count: rowsOver.length,
      multi_secondary_rows: multiSecondary.slice(0,CAP), multi_secondary_row_count: multiSecondary.length,
      painted_row_verbs: paintedVerbs.slice(0,CAP), painted_row_verb_count: paintedVerbs.length,
      delete_row_verbs: deleteVerbs.slice(0,CAP), delete_row_verb_count: deleteVerbs.length,
      danger_rows: dangerRows.size, content_w: Math.round(content.width), content_span: Math.round(spanRight - content.left), pane_split: paneSplit, radii,
      open_section_count: openSections.size, empty_open_sections: emptyOpen.slice(0,CAP), empty_open_section_count: emptyOpen.length };
  });

  // ---- overlay: a destructive control in a dialog or menu is quiet (#1364). The surfaces the walk opens are a
  // dialog, a role=menu and the body of an open popover disclosure (a row's kebab menu). Inside one, a control
  // labelled delete / remove / clear / discard must draw no fill at rest (the outlined recipe: transparent on a
  // hairline) and must not be the largest labelled control there. Fill is measured against the surface behind
  // it, so a button painted the surface's own colour is not filled; hover and focus are not at rest. A confirm
  // dialog is measured like any other: its destructive verb is outlined too, never tinted (#1403).
  section('overlay', () => {
    const SURFACE = 'dialog[open], [role=dialog], [role=menu]';
    const CONTROLS = 'button, a[href], [role=button], [role=menuitem]';
    const DESTRUCTIVE = /\b(delete|remove|clear|discard)\b/i;
    const popoverBodies = [...document.querySelectorAll('details[open]')].flatMap(d => [...d.children]
      .filter(c => c.tagName !== 'SUMMARY' && /^(absolute|fixed)$/.test(getComputedStyle(c).position)));
    // With a dialog open only that dialog is measured, as in every section: a menu left open on the page behind it is not on this screen.
    const surfaces = new Set([...document.querySelectorAll(SURFACE), ...popoverBodies].filter(visible).filter(inScope));
    const surfaceOf = (el) => { for (let e = el.parentElement; e; e = e.parentElement) if (surfaces.has(e)) return e; return null; };
    const byOwner = new Map();
    [...document.querySelectorAll(CONTROLS)].filter(visible).forEach(c => { const s = surfaceOf(c); if (!s) return;
      if (!byOwner.has(s)) byOwner.set(s, []); byOwner.get(s).push(c); });
    const found = [];
    byOwner.forEach((controls, surface) => {
      const isDestructive = (c) => DESTRUCTIVE.test(labelOf(c));
      // "Largest" is among controls that carry a visible text label, so a lone close glyph never makes Delete the biggest.
      const others = controls.filter(c => !isDestructive(c) && (c.textContent || '').trim());
      const biggest = others.reduce((m, c) => Math.max(m, area(c)), 0);
      controls.filter(isDestructive).forEach(c => {
        const r = c.getBoundingClientRect(), fill = filled(c), larger = others.length > 0 && area(c) > biggest * 1.05;
        found.push({sel: sel(c), label: labelOf(c).slice(0,30), surface: sel(surface), filled: fill, largest: larger,
          w: Math.round(r.width), h: Math.round(r.height), why: [fill && 'filled', larger && 'largest'].filter(Boolean).join('+')}); }); });
    const bad = found.filter(f => f.why);
    return { surfaces: surfaces.size, destructive: found.slice(0,CAP), destructive_bad: bad.slice(0,CAP), destructive_bad_count: bad.length };
  });

  // ---- dialogs: the commit is the most prominent control in its footer (#1339). Per open dialog, the commit is its
  // `type=submit` button, else a control `params.primarySelector` matches (the last one, as the footer sits last). Its
  // rivals are the other labelled controls on its row or below it -- the footer; a control above it is the body, where
  // the modal contract puts a Delete. The commit is quieter when a rival is filled and it is not, or when the two are
  // alike in fill and the rival's box is more than 5% larger. A dialog with no commit is not measured (`with_commit`).
  section('dialogs', () => {
    const CONTROLS = 'button, input[type=submit], input[type=button], [role=button]';
    const dialogs = [...document.querySelectorAll('dialog[open], [role=dialog]')].filter(visible).filter(inScope);
    const quiet = []; let withCommit = 0;
    dialogs.forEach(d => {
      const labelled = [...d.querySelectorAll(CONTROLS)].filter(visible).filter(c => !c.closest('[role=menu]') && (labelOf(c) || c.value));
      const commits = labelled.filter(c => c.matches('[type=submit]') || c.matches(params.primarySelector));
      const commit = commits[commits.length - 1]; if (!commit) return;
      withCommit++;
      const top = commit.getBoundingClientRect().top - 2, cf = filled(commit), ca = area(commit);
      const rival = labelled.filter(c => c !== commit && c.getBoundingClientRect().top >= top)
        .map(c => ({c, f: filled(c), a: area(c)}))
        .filter(x => (x.f && !cf) || (x.f === cf && x.a > ca * 1.05))
        .sort((x, y) => (y.f - x.f) || (y.a - x.a))[0];
      if (!rival) return;
      const r = commit.getBoundingClientRect();
      quiet.push({dialog: sel(d), commit: sel(commit), label: labelOf(commit).slice(0,30) || commit.value, filled: cf, w: Math.round(r.width), h: Math.round(r.height),
        rival: sel(rival.c), rival_label: labelOf(rival.c).slice(0,30), why: rival.f && !cf ? 'filled' : 'larger'});
    });
    return { count: dialogs.length, with_commit: withCommit, quiet_commit: quiet.slice(0,CAP), quiet_commit_count: quiet.length };
  });

  // ---- clearance: a list's last row must scroll clear of a fixed bar anchored to the bottom (#1019).
  // Each scroller goes to its end (instant, whatever scroll-behavior says) and back; the walk runs
  // this after its screenshots, so nothing captured moves.
  section('clearance', () => {
    const bars = [...document.querySelectorAll('body *')].filter(el => { if (getComputedStyle(el).position !== 'fixed' || !visible(el)) return false;
      const r = el.getBoundingClientRect(); return r.bottom >= innerHeight * 0.75 && r.bottom <= innerHeight + 1 && r.height <= innerHeight * 0.3; })
      .filter((el, _, all) => !all.some(o => o !== el && o.contains(el)));
    const rows = [];
    if (bars.length) q('ul, ol, table, [role=list]').forEach(l => { if (layerOf(l)) return;
      const rs = [...l.querySelectorAll(':scope > li, :scope > tbody > tr, :scope > tr, :scope > [role=listitem]')].filter(visible);
      if (rs.length) rows.push({list: l, row: rs[rs.length - 1]}); });
    const scrollerOf = (el) => { for (let e = el.parentElement; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const o = getComputedStyle(e).overflowY; if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 1) return e; }
      return document.scrollingElement || document.documentElement; };
    const scrollers = new Map(); rows.forEach(r => { const s = scrollerOf(r.row); if (!scrollers.has(s)) scrollers.set(s, s.scrollTop); });
    const hidden = [];
    try {
      scrollers.forEach((_, s) => s.scrollTo({top: s.scrollHeight, behavior: 'instant'}));
      rows.forEach(({list, row}) => { const r = row.getBoundingClientRect();
        const bar = bars.find(b => { const c = b.getBoundingClientRect(); return r.bottom > c.top + 1 && r.top < c.bottom && r.left < c.right && c.left < r.right; });
        if (bar) hidden.push({sel: sel(list), row: sel(row), bar: sel(bar), under_px: Math.round(r.bottom - bar.getBoundingClientRect().top)}); });
    } finally { scrollers.forEach((top, s) => s.scrollTo({top, behavior: 'instant'})); }
    return { bars: bars.map(sel).slice(0, CAP), hidden_rows: hidden.slice(0, CAP), hidden_row_count: hidden.length };
  });

  // ---- accessibility
  section('a11y', () => {
    const unnamed = [];
    q('button, a[href], [role=button]').forEach(el => { const name = (el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent||'').trim();
      if (!name && !el.querySelector('img[alt], svg[aria-label], svg title')) unnamed.push(sel(el)); });
    const vp = document.querySelector('meta[name=viewport]'); const c = (vp && vp.getAttribute('content') || '').toLowerCase();
    const locked = /user-scalable\s*=\s*(no|0)/.test(c) || /maximum-scale\s*=\s*1(\.0*)?(\s|,|$)/.test(c);
    // A text-size control by its own markers: the vendored one is `#textSizeControl` with `data-textsize` buttons
    // (never on <html>, which carries the boot script's stamp), named by aria-label or by aria-labelledby (#1185).
    const sizeWords = /(text|font)\s*size/i;
    const named = (el) => sizeWords.test(el.getAttribute('aria-label') || '')
      || (el.getAttribute('aria-labelledby') || '').split(/\s+/).some(id => { const t = id && document.getElementById(id); return !!t && sizeWords.test(t.textContent || ''); });
    const tsc = !!document.querySelector('[data-text-size], [data-textsize]:not(html), #textSize, #fontSize, #textSizeControl')
      || [...document.querySelectorAll('[aria-label], [aria-labelledby]')].some(named);
    const stamped = document.documentElement.hasAttribute('data-textsize');
    return { unnamed: unnamed.slice(0,CAP), unnamed_count: unnamed.length, zoom_locked: locked, text_size_control: tsc, text_size_stamped: stamped };
  });

  if (EXCLUDE) section('excluded', () => ({ selectors: params.excludeSelectors, elements: scope.querySelectorAll(EXCLUDE).length }));
  section('headings', () => q('h1,h2,h3,h4').map(h => h.tagName+':'+h.textContent.trim().slice(0,30)+'@'+getComputedStyle(h).fontSize).slice(0, CAP));
  return out;
}
"""


# Which theme did the page render? (fleet-config#1216) The walk stamps `data-theme` and sets the context's
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
) -> Dict[str, object]:
    """The parameter object the script receives; recorded verbatim in
    `metrics.json` so a consumer can see which floors a run measured with."""
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
        "controls": ["total", "font_family_mismatch", "font_family_mismatch_count", "boundary_low",
                     "boundary_low_count", "ua_styled", "ua_styled_count", "label_fit_bad", "label_fit_bad_count",
                     "segmented_bad", "segmented_bad_count", "switch_count", "switch_on_count", "switches_on", "icon_button_count", "icon_buttons_painted",
                     "icon_buttons_painted_count", "icon_only_count", "icon_only_unlabelled", "reference_pill_count", "reference_pill_styles", "reference_pill_variants"],
        "targets": ["total", "small", "small_count", "overlaps", "overlap_count", "covered", "covered_count",
                    "primary", "primary_min_height", "in_summary"],
        "icons": ["boxes", "elements"],
        "nav": ["primary_count", "pane_scroll_top", "pane_header_visible", "settings_tab_count", "settings_tabs", "home_title", "header_context"],
        "feedback": ["toast_count", "toasts_tinted", "toasts_tinted_count", "empty_state_count", "empty_states_no_action"],
        "layout": ["control_overflow", "control_overflow_count", "clipped_trailing", "clipped_trailing_count", "turn_headers", "repeated_headers", "repeated_header_count", "overflow_x", "scroll_w", "inner_w", "inner_h", "pane_h", "lists", "rows_over_limit",
                   "rows_over_limit_count", "danger_rows", "content_w", "content_span", "pane_split", "radii", "open_section_count",
                   "empty_open_sections", "empty_open_section_count", "multi_secondary_rows", "multi_secondary_row_count",
                   "painted_row_verbs", "painted_row_verb_count", "delete_row_verbs", "delete_row_verb_count"],
        "overlay": ["surfaces", "destructive", "destructive_bad", "destructive_bad_count"],
        "dialogs": ["count", "with_commit", "quiet_commit", "quiet_commit_count"],
        "clearance": ["bars", "hidden_rows", "hidden_row_count"],
        "a11y": ["unnamed", "unnamed_count", "zoom_locked", "text_size_control", "text_size_stamped"],
    }
    return [f"{s}.{k}" for s, ks in keys.items() for k in ks]
