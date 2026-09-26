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
              body_font_family, break_all [..], uppercase [..], glyph_icons [..]
    controls  total, font_family_mismatch [..], font_family_mismatch_count,
              boundary_low [..], boundary_low_count, ua_styled [..],
              segmented_bad [..], segmented_bad_count
    targets   total, small [..], small_count, overlaps [..], overlap_count,
              covered [..], covered_count, primary [..], primary_min_height, in_summary
    icons     boxes {"WxH": n}, elements {"WxH": [{glyph, host, label}, ..]}
    nav       primary_count, pane_scroll_top, pane_header_visible
    layout    overflow_x, scroll_w, inner_w, inner_h, pane_h, lists [..],
              rows_over_limit [..], danger_rows, content_w, content_span, radii {r: n}
    clearance bars [..], hidden_rows [..], hidden_row_count
    a11y      unnamed [..], unnamed_count, zoom_locked, text_size_control
    headings  ["H2:Title@16px", ...]

Lists are capped (`CAP`) so a screen with hundreds of small controls still
serialises small; the `*_count` twin carries the true total.

stdlib only — the Playwright side is `walk.py`, executed by the *target*
repo's interpreter (this repo's venv stays stdlib-only).
"""
from __future__ import annotations

from typing import Dict, List, Optional

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
GLYPH_ICON_RE = r"[←-⇿■-◿⬀-⯿✖✕×⋮☰]"

# `__GEOMETRY__` is replaced by `build_script`. The script is one arrow
# function taking `params` so Playwright's `page.evaluate(script, params)`
# passes the resolved spec values in.
_MEASURE_JS = r"""
(params) => {
  const CAP = 80;
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
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width<1||r.height<1) return false;
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
  // The fixed or sticky layer an element paints in (itself or its nearest such ancestor), else null: the flow.
  const layerOf = (el) => { for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
    const p = getComputedStyle(e).position; if (p === 'fixed' || p === 'sticky') return e; } return null; };
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
    while (walker.nextNode()) { const t = walker.currentNode; const raw = t.nodeValue.trim(); if (!raw) continue;
      const el = t.parentElement; if (!el || !visible(el) || !inScope(el)) continue;
      if (el.closest('svg, script, style, noscript, .xterm, .xterm-rows')) continue;
      if (glyphRe.test(raw) && !seen.has(el)) glyphs.push({sel: sel(el), text: raw.slice(0, 40)});
      if (seen.has(el)) continue; seen.add(el);
      const s = getComputedStyle(el); const px = Math.round(parseFloat(s.fontSize)*100)/100;
      runs++; sizes[px] = (sizes[px]||0)+1; weights[s.fontWeight] = (weights[s.fontWeight]||0)+1;
      if (px < 14) under14++; if (px < minPx) minPx = px;
      const fg = rgba(s.color); const bg = bgOf(el); const cr = ratio(over(fg,bg), bg);
      const large = px>=24 || (px>=18.66 && +s.fontWeight>=700);
      if (cr < (large?3:4.5)) low.push({sel: sel(el), text: raw.slice(0,40), px, ratio: r2(cr), fg: hex(over(fg,bg)), bg: hex(bg), large});
      if (px < 11) under11.push({sel: sel(el), text: raw.slice(0,40), px});
      if (s.wordBreak === 'break-all') breakAll.push({sel: sel(el), text: raw.slice(0,40)});
      if (s.textTransform === 'uppercase') upper.push({sel: sel(el), text: raw.slice(0,40)});
    }
    return { sizes, weights, runs, under14, under11: under11.slice(0,CAP), under11_count: under11.length,
      min_px: runs ? minPx : null, low_contrast: low.slice(0,CAP), low_contrast_count: low.length,
      body_font_family: getComputedStyle(document.body).fontFamily,
      break_all: breakAll.slice(0,CAP), break_all_count: breakAll.length,
      uppercase: upper.slice(0,CAP), uppercase_count: upper.length,
      glyph_icons: glyphs.slice(0,CAP), glyph_icon_count: glyphs.length };
  });

  // ---- form controls: font family vs body, boundary contrast, UA styling
  section('controls', () => {
    const bodyFam = getComputedStyle(document.body).fontFamily;
    const mism = [], lowB = [], ua = []; let total = 0;
    q(params.formControls).forEach(el => { total++;
      const s = getComputedStyle(el);
      if (s.fontFamily !== bodyFam) mism.push({sel: sel(el), family: s.fontFamily.slice(0,60)}); });
    const drawsBorder = (st) => (parseFloat(st.borderTopWidth) || 0) > 0 && rgba(st.borderTopColor)[3] > 0;
    q(params.boundaryControls).forEach(el => {
      // A switch with visible text is a labelled toggle, identified by its label like a button (#996).
      if (el.getAttribute('role') === 'switch' && el.textContent.trim()) return;
      // A field whose wrapper draws the boundary (the vendored filter: a bordered label around a
      // borderless input) takes the wrapper's -- the nearest bordered ancestor hugging it, 2 levels up.
      let host = el, s = getComputedStyle(el);
      if (!drawsBorder(s) && rgba(s.backgroundColor)[3] === 0) {
        const h = el.getBoundingClientRect().height;
        for (let a = el.parentElement, i = 0; a && i < 2; a = a.parentElement, i++) { const as = getComputedStyle(a);
          if (drawsBorder(as) && a.getBoundingClientRect().height <= h * 2 + 2) { host = a; s = as; break; } }
      }
      const surface = host.parentElement ? bgOf(host.parentElement) : [255,255,255,1];
      const bw = parseFloat(s.borderTopWidth) || 0; const bc = rgba(s.borderTopColor);
      let boundary = null;
      if (bw > 0 && bc[3] > 0) boundary = over(bc, surface);
      else { const own = rgba(s.backgroundColor); if (own[3] > 0) boundary = over(own, surface); }
      if (boundary) { const cr = ratio(boundary, surface);
        if (cr < params.boundaryMin) lowB.push({sel: sel(el), label: txt(el), ratio: r2(cr), boundary: hex(boundary), surface: hex(surface)}); }
      else lowB.push({sel: sel(el), label: txt(el), ratio: 1, boundary: 'none', surface: hex(surface)});
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
    const lineCount = o => { const rs = []; const w = document.createTreeWalker(o, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) { if (!n.textContent.trim() || !inFlow(n, o)) continue;
        const r = document.createRange(); r.selectNodeContents(n);
        [...r.getClientRects()].forEach(b => { if (b.width > 0 && b.height > 0) rs.push(b); }); }
      rs.sort((a, b) => a.top - b.top); let lines = 0, bottom = -Infinity;
      rs.forEach(b => { if (b.top >= bottom - 1) { lines++; bottom = b.bottom; } else bottom = Math.max(bottom, b.bottom); });
      return lines; };
    q('[role=tablist], [role=radiogroup], .segmented').filter(g => g !== primary).forEach(g => {
      const opts = [...g.querySelectorAll('[role=tab], [role=radio], button, label')].filter(visible);
      const wrapped = opts.some(o => lineCount(o) > 1);
      if (opts.length > params.segmentedMax || wrapped) segs.push({sel: sel(g), options: opts.length, wrapped}); });
    return { total, font_family_mismatch: mism.slice(0,CAP), font_family_mismatch_count: mism.length,
      boundary_low: lowB.slice(0,CAP), boundary_low_count: lowB.length, ua_styled: ua.slice(0,CAP), ua_styled_count: ua.length,
      segmented_bad: segs.slice(0,CAP), segmented_bad_count: segs.length };
  });

  // ---- hit targets: effective rects (scaffold JS), overlap, primary action, summary controls
  section('targets', () => {
    if (!effRect) throw new Error('GEOMETRY_MISSING');
    const els = q(params.interactive).filter(el => !el.disabled);
    const rects = els.map(el => { const r = effRect(el);
      return {el, l: r.left - r.expandLeft, t: r.top - r.expandTop, rr: r.right + r.expandRight, b: r.bottom + r.expandBottom, vw: r.right - r.left, vh: r.bottom - r.top}; });
    const small = []; const floor = params.hitMin - params.hitTol;
    rects.forEach(x => { const w = x.rr - x.l, h = x.b - x.t;
      if (w < floor || h < floor) small.push({sel: sel(x.el), label: txt(x.el).slice(0,30), w: Math.round(w), h: Math.round(h), vw: Math.round(x.vw), vh: Math.round(x.vh)}); });
    // A pair is covered, not an overlap, when a fixed or sticky layer takes the tap where the two
    // expanded rects meet and the other control's own box sits under that layer: the nav pill over
    // scrolled content, a full-screen overlay over cards. Pairs within one layer still count, and so
    // does a control beside a bar whose expansion merely reaches into it (#1019).
    const covered = (a, b) => {
      const x = (Math.max(a.l, b.l) + Math.min(a.rr, b.rr)) / 2, y = (Math.max(a.t, b.t) + Math.min(a.b, b.b)) / 2;
      if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
      const hit = document.elementFromPoint(x, y); const cover = hit && layerOf(hit);
      if (!cover) return false;
      const under = [a, b].find(o => !cover.contains(o.el)); if (!under) return false;
      const u = under.el.getBoundingClientRect(), c = cover.getBoundingClientRect();
      return u.left < c.right && c.left < u.right && u.top < c.bottom && c.top < u.bottom; };
    const overlaps = [], coveredPairs = []; let overlapCount = 0, coveredCount = 0; const n = Math.min(rects.length, 400);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { const a = rects[i], b = rects[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const sep = a.rr <= b.l || b.rr <= a.l || a.b <= b.t || b.b <= a.t;
      if (sep) continue;
      if (covered(a, b)) { coveredCount++; if (coveredPairs.length < CAP) coveredPairs.push({a: sel(a.el), b: sel(b.el)}); }
      else { overlapCount++; if (overlaps.length < CAP) overlaps.push({a: sel(a.el), b: sel(b.el)}); } }
    const primary = q(params.primarySelector).map(el => { const r = el.getBoundingClientRect(); return {sel: sel(el), label: txt(el).slice(0,30), h: Math.round(r.height), w: Math.round(r.width)}; });
    const inSummary = q('summary').reduce((acc, s) => acc + s.querySelectorAll(params.interactive.replace(/summary,\s*/, '')).length, 0);
    return { total: els.length, small: small.slice(0,CAP), small_count: small.length, overlaps, overlap_count: overlapCount,
      covered: coveredPairs, covered_count: coveredCount,
      primary: primary.slice(0,CAP), primary_min_height: primary.length ? Math.min(...primary.map(p => p.h)) : null, in_summary: inSummary };
  });

  // ---- icons: box sizes, and which element each box is (#1020) -- the glyph (a sprite
  // `<use href="#i-NAME">`, else data-icon or the first non-`icon` class), the control or
  // identified ancestor hosting it, and that host's accessible name; ICON_CAP per size.
  section('icons', () => { const boxes = {}, elements = {}; const ICON_CAP = 12;
    q('svg').forEach(el => { const r = el.getBoundingClientRect(); const k = Math.round(r.width)+'x'+Math.round(r.height); boxes[k] = (boxes[k]||0)+1;
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
    return { primary_count: tabs.length, pane_scroll_top: pane.scrollTop || 0,
      pane_header_visible: heading ? (top >= 0 && top <= window.innerHeight) : null };
  });

  // ---- layout
  section('layout', () => {
    const de = document.documentElement;
    const lists = []; const rowsOver = []; const dangerRows = new Set(); const radii = {};
    q('ul, ol, table, [role=list]').forEach(l => { const rows = l.querySelectorAll(':scope > li, :scope > tbody > tr, :scope > tr, :scope > [role=listitem]').length;
      if (rows > params.listRowsMax) { const host = l.closest('[role=tabpanel], section, main') || pane;
        const filter = !!host.querySelector('input[type=search], input[placeholder*="filter" i], input[placeholder*="search" i], [role=searchbox]');
        lists.push({sel: sel(l), rows, has_filter: filter}); } });
    q('li, tr, [role=listitem], [role=row]').forEach(row => {
      // A week of a month grid is not an action row: the WAI-ARIA grid pattern's rows (#1017).
      if ((row.tagName === 'TR' || row.getAttribute('role') === 'row') && row.closest('[role=grid], [role=treegrid]')) return;
      // "besides the row itself": a row whose tap target is one control spanning most of it
      // (action-row's main button) does not count that control (#996).
      const ctl = [...row.querySelectorAll(params.interactive)].filter(visible);
      const rw = row.getBoundingClientRect().width;
      const main = ctl.reduce((a, c) => (!a || c.getBoundingClientRect().width > a.getBoundingClientRect().width) ? c : a, null);
      const extras = main && main.getBoundingClientRect().width >= rw * 0.5 ? ctl.filter(c => c !== main) : ctl;
      if (extras.length > params.rowControlsMax) rowsOver.push({sel: sel(row), controls: extras.length});
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
    return { overflow_x: de.scrollWidth > window.innerWidth + 1, scroll_w: de.scrollWidth, inner_w: window.innerWidth, inner_h: window.innerHeight,
      pane_h: pane.scrollHeight, lists: lists.slice(0,CAP), rows_over_limit: rowsOver.slice(0,CAP), rows_over_limit_count: rowsOver.length,
      danger_rows: dangerRows.size, content_w: Math.round(content.width), content_span: Math.round(spanRight - content.left), radii };
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
    const tsc = !!document.querySelector('[aria-label*="text size" i], [aria-label*="font size" i], [data-text-size], #textSize, #fontSize');
    return { unnamed: unnamed.slice(0,CAP), unnamed_count: unnamed.length, zoom_locked: locked, text_size_control: tsc };
  });

  section('headings', () => q('h1,h2,h3,h4').map(h => h.tagName+':'+h.textContent.trim().slice(0,30)+'@'+getComputedStyle(h).fontSize).slice(0, CAP));
  return out;
}
"""


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
    }


SECTIONS = ("text", "controls", "targets", "icons", "nav", "layout", "clearance", "a11y", "headings")


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
                 "break_all_count", "uppercase", "uppercase_count", "glyph_icons", "glyph_icon_count"],
        "controls": ["total", "font_family_mismatch", "font_family_mismatch_count", "boundary_low",
                     "boundary_low_count", "ua_styled", "ua_styled_count", "segmented_bad", "segmented_bad_count"],
        "targets": ["total", "small", "small_count", "overlaps", "overlap_count", "covered", "covered_count",
                    "primary", "primary_min_height", "in_summary"],
        "icons": ["boxes", "elements"],
        "nav": ["primary_count", "pane_scroll_top", "pane_header_visible"],
        "layout": ["overflow_x", "scroll_w", "inner_w", "inner_h", "pane_h", "lists", "rows_over_limit",
                   "rows_over_limit_count", "danger_rows", "content_w", "content_span", "radii"],
        "clearance": ["bars", "hidden_rows", "hidden_row_count"],
        "a11y": ["unnamed", "unnamed_count", "zoom_locked", "text_size_control"],
    }
    return [f"{s}.{k}" for s, ks in keys.items() for k in ks]
