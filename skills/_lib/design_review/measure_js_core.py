"""The first half of the in-page measurement script `measure.py` assembles.

The arrow function's opening: the colour, visibility and selector helpers every
section shares, then the text, casing, controls, targets, icons, nav and
feedback sections. `measure_js_layout.py` holds the rest of the body and the
closing brace; `measure.py` joins the two, so neither is valid JS alone.
"""

MEASURE_JS_CORE = r"""
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
  // or title (several words, capital first; title when every word after the first is capitalised; evaluate.py reads a title resting on two or fewer such words as ambiguous (#1463)). `literal` is
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
    const mism = [], monoFields = [], lowB = [], ua = []; let total = 0;
    // A text-entry control in the spec's mono stack is a code field (design.md: code is mono), not a control that
    // missed `font: inherit`; buttons, selects and any non-mono mismatch still count (#1462).
    const isMono = (fam) => /^\s*ui-monospace(,|\s*$)/i.test(fam) || /(^|[\s,])monospace\s*$/i.test(fam);
    const isTextEntry = (el) => el.matches('textarea, input[type=text], input:not([type])');
    q(params.formControls).forEach(el => { total++;
      const s = getComputedStyle(el);
      if (s.fontFamily === bodyFam) return;
      const rec = {sel: sel(el), family: s.fontFamily.slice(0,60)};
      (isTextEntry(el) && isMono(s.fontFamily) ? monoFields : mism).push(rec); });
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
      mono_code_field: monoFields.slice(0,CAP), mono_code_field_count: monoFields.length,
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

"""
