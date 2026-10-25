"""The second half of the in-page measurement script `measure.py` assembles.

The layout, overlay, dialogs, clearance, a11y, coverage and inventory sections,
then the closing `return out;`. It continues `measure_js_core.py`'s function
body, so it is only valid JS appended to that half.
"""

MEASURE_JS_LAYOUT = r"""  // ---- layout
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
    // Trailing figures cut by an element's own ellipsis (#1343), narrowed to status and meta text (#1438). design.md keeps the
    // parts a step line must never lose (`+N -M`, `N failed`, a duration) as non-shrinking trailing items outside the ellipsis; a
    // single-line element that truncates them is measurable from geometry. A candidate is a visible element with
    // `text-overflow: ellipsis` on a clipping box (`overflow-x` not visible) that keeps its text on one line (`white-space`
    // nowrap or pre) and really overflows (`scrollWidth` over `clientWidth`). Its hidden text is found per word: each
    // whitespace-delimited word's last character is a Range, and a word is cut when that character's box is collapsed to zero
    // width (the engine lays out nothing the ellipsis replaces, which is how WebKit reports it) or ends past the element's
    // content edge (a word cut in half counts; one that lies whole beside the ellipsis glyph is still read in full). A cut word
    // is a figure when it is a number (digits, a `+N` / `-N` / minus-sign figure, a percentage, a decimal, a clock time or a
    // duration such as 2.4s, 1m30s, 3:45) or a status word (failed, errors, warnings, passed, skipped, pending) straight after
    // a number. A cut word that is only words, a file name or a date is no figure, and neither is text that wraps, a line
    // clamp or a box that fits. A cut figure COUNTS only in status or meta text, never as a lone number inside a run of words
    // (a house number in an address, a version string, a phone number or a clock time in a title a person typed), when ANY of:
    //   slot   the element is a `<time>`, a `[role=status]` or a `[data-state]` element, or it or its parent carries a class
    //          with a `meta`, `status`, `count(s)`, `badge`, `chip`, `duration`, `stats`, `time` or `timestamp` part when
    //          split at `-` and `_` (`action-row-meta`, `release-row .meta`, `status-row`, never `task-title`);
    //   pair   the figure is one half of a `+N -M` pair, or sits right beside a number-and-status-word pair (`2 failed 1m30s`,
    //          `failed 8%`: a number next to a status word on either side);
    //   line   the text is a status line, a `·` / `•` / `|` separated list, and the figure's own part is at most three words
    //          with at least half of them a number or a status word (`· 3 conversations`, `· took 2m10s`, never `Via Roma 14`).
    // What the narrowing drops is kept, not lost: `clipped_trailing_excluded` is a sample of the cut-figure rows it let pass
    // (the same shape as `clipped_trailing`), `clipped_trailing_excluded_count` the true total, so the exclusion can be reviewed.
    // An element inside one already counted is the same cut line.
    const NUMLIKE = /^[+\-−–±]?\d[\d.,:]*(?:%|ms|min|[smhdkMxB])?(?:\d+(?:[.,]\d+)?(?:ms|[smhd]))*$/;
    const STATUSWORD = /^(?:failed|failures?|errors?|warnings?|passed|skipped|pending)$/i;
    const SIGNED = /^[+\-−–±]\d/, LISTSEP = /^[·•|]$/;
    const SLOTPART = /^(?:meta|status|counts?|badge|chip|duration|stats|time|timestamp)$/i;
    const slotClass = (e) => !!e && [...e.classList].some(c => c.split(/[-_]/).some(w => SLOTPART.test(w)));
    const clippedEls = [], clippedTrailing = [], clippedExcluded = [];
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
      const num = (i) => i >= 0 && i < toks.length && NUMLIKE.test(toks[i].t), stat = (i) => i >= 0 && i < toks.length && STATUSWORD.test(toks[i].t);
      const found = toks.map((k, i) => i).filter(i => toks[i].cut && (num(i) || (stat(i) && num(i - 1))));
      if (!found.length) return;
      const slot = el.matches('time, [role=status], [data-state]') || slotClass(el) || slotClass(el.parentElement);
      const seg = []; let at = 0;
      toks.forEach((k, i) => { if (LISTSEP.test(k.t)) at++; seg[i] = at; });
      const isList = toks.some(k => LISTSEP.test(k.t));
      const counts = (i) => slot
        || (SIGNED.test(toks[i].t) && (i > 0 && SIGNED.test(toks[i - 1].t) || i + 1 < toks.length && SIGNED.test(toks[i + 1].t)))
        || (num(i) && (stat(i - 1) || stat(i + 1))) || (stat(i) && (num(i - 1) || num(i + 1)))
        || (isList && (() => { const own = toks.filter((k, j) => seg[j] === seg[i] && !LISTSEP.test(k.t));
             return own.length <= 3 && own.filter(k => NUMLIKE.test(k.t) || STATUSWORD.test(k.t)).length * 2 >= own.length; })());
      const figures = found.filter(counts).map(i => toks[i].t), dropped = found.filter(i => !counts(i)).map(i => toks[i].t);
      if (figures.length) { clippedEls.push(el);
        clippedTrailing.push({sel: sel(el), text: txt(el), why: 'cuts ' + figures.slice(0, 4).join(' '), over_px: Math.round(el.scrollWidth - el.clientWidth)}); }
      else clippedExcluded.push({sel: sel(el), text: txt(el), why: 'cuts ' + dropped.slice(0, 4).join(' ') + ' in prose', over_px: Math.round(el.scrollWidth - el.clientWidth)}); });
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
      clipped_trailing: clippedTrailing.slice(0,CAP), clipped_trailing_count: clippedTrailing.length,
      clipped_trailing_excluded: clippedExcluded.slice(0,8), clipped_trailing_excluded_count: clippedExcluded.length, overflow_x: de.scrollWidth > window.innerWidth + 1, scroll_w: de.scrollWidth, inner_w: window.innerWidth, inner_h: window.innerHeight,
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
  // this after its screenshots, so nothing captured moves. A transient overlay (an open menu, listbox, dialog, popover,
  // toast) covers rows by design and closes on a tap outside it, so it is not a bar to clear (#1464).
  section('clearance', () => {
    const TRANSIENT = '[role=menu], [role=listbox], [role=dialog], [role=alertdialog], .row-menu, .toast';
    const transient = (el) => !!el.closest(TRANSIENT) || el.hasAttribute('popover');  // a closed popover is not visible, so any left here is open
    const bars = [...document.querySelectorAll('body *')].filter(el => { if (getComputedStyle(el).position !== 'fixed' || !visible(el) || transient(el)) return false;
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

  // ---- coverage: every overlay surface in the DOM and whether it is open on this screen (#1353). Read only: listing a
  // surface never opens, focuses or clicks it. Not a rule input -- evaluate.py folds the screens into the report's
  // "not walked" line. A key is `tag#id`, else `tag.firstClass`, so a state class (`.is-open`) never splits a surface.
  section('coverage', () => {
    const OVERLAYS = 'dialog, [popover], [role=dialog], [role=alertdialog]';
    const keyOf = (el) => { const tag = el.tagName.toLowerCase(); if (el.id) return tag + '#' + el.id;
      const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0]; if (cls) return tag + '.' + cls;
      const role = el.getAttribute('role'); return role ? tag + '[role=' + role + ']' : el.hasAttribute('popover') ? tag + '[popover]' : tag; };
    const kindOf = (el) => el.tagName === 'DIALOG' ? 'dialog' : el.hasAttribute('popover') ? 'popover' : 'role-dialog';
    // Open = shown on this screen: a <dialog> with `open`, a popover matching :popover-open, and in every case visible,
    // inside the viewport (an off-canvas drawer is closed) and not under aria-hidden or inert. A dialog shell the walk's
    // showModal() pass opened before the app filled it renders no box, so it stays closed: nothing in it was measured.
    const isOpen = (el) => { if (el.closest('[aria-hidden=true], [inert]')) return false;
      if (el.tagName === 'DIALOG' && !el.open) return false;
      if (el.hasAttribute('popover')) { try { if (!el.matches(':popover-open')) return false; } catch (e) { /* no popover API: visibility decides */ } }
      if (!visible(el)) return false;
      const r = el.getBoundingClientRect(); return r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight; };
    const byKey = new Map();
    document.querySelectorAll(OVERLAYS).forEach(el => { if (EXCLUDE && el.closest(EXCLUDE)) return;
      const key = keyOf(el), open = isOpen(el), seen = byKey.get(key);
      if (seen) seen.open = seen.open || open; else byKey.set(key, {key, kind: kindOf(el), open}); });
    const all = [...byKey.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    return { overlays: all.slice(0, CAP), overlay_count: all.length };
  });

  // ---- inventory: every visible control by kind and component, every text role by the fonts it renders in (#1497).
  // Read only, never a rule input yet: #1383's cross-app comparator reads it. A control is `vendored:<name>` when its own
  // classes (read in order, so `icon-button action-row-kebab` is the icon-button), else its wrapping <label>'s, carry a
  // catalog component's root class, or one of its part classes inside that component's root; else `bespoke`. With no
  // catalog every control is `unknown`, never `bespoke`, and `unmeasured` says why. A text role comes from structure --
  // tag, ARIA role, a part class (`*-title`, `*-meta`, `*-chip`, `*-overline`), the nearest one up from the run -- never
  // from its size, because size is what is compared. Labels are masked (digit runs become `#`, so dates and times
  // do too) and truncated: no captured text reaches a report. Entries are sorted and carry nothing positional or timed.
  section('inventory', () => {
    const INVENTORY_CAP = 200;
    const cat = params.components || null;
    const roots = new Map(), parts = new Map(), rootSel = {};
    if (cat) Object.keys(cat).sort().forEach(name => { const c = cat[name] || {};
      (c.roots || []).forEach(k => roots.set(k, name)); (c.parts || []).forEach(k => parts.set(k, name));
      rootSel[name] = (c.roots || []).map(k => '.' + CSS.escape(k)).join(', '); });
    const classes = (el) => (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean);
    const marked = (el) => { for (const c of classes(el)) { if (roots.has(c)) return roots.get(c);
      const p = parts.get(c); if (p && rootSel[p] && el.closest(rootSel[p])) return p; } return null; };
    const componentOf = (el) => { if (!cat) return 'unknown';
      const lab = el.closest('label'), name = marked(el) || (lab && lab !== el ? marked(lab) : null);
      return name ? 'vendored:' + name : 'bespoke'; };
    const typeOf = (el) => (el.getAttribute('type') || '').toLowerCase();
    const kindOf = (el, comp) => { const role = (el.getAttribute('role') || '').toLowerCase(), tag = el.tagName;
      if (role === 'switch' || comp === 'vendored:switch') return 'switch';
      if (comp === 'vendored:range-tab') return 'range-tab';
      if (comp === 'vendored:segmented') return 'segmented';
      if (role === 'tab') return 'tab';
      if (/^menuitem/.test(role)) return 'menuitem';
      if (tag === 'SELECT') return 'select';
      if (role === 'combobox' || (tag === 'INPUT' && el.hasAttribute('list'))) return 'combobox';
      if (role === 'checkbox' || role === 'radio' || (tag === 'INPUT' && /^(checkbox|radio)$/.test(typeOf(el)))) return 'checkbox';
      if (tag === 'TEXTAREA') return 'textarea';
      if (tag === 'INPUT' && !/^(button|submit|reset|image)$/.test(typeOf(el))) return 'input';
      if (role === 'link' || (tag === 'A' && !role)) return 'link';
      // A button that renders no text of its own is an icon button, whatever its classes say.
      return comp === 'vendored:icon-button' || !(el.innerText || '').trim() ? 'icon-button' : 'button'; };
    const roleOf = (el) => { const r = el.getAttribute('role'); if (r) return r.toLowerCase(); const t = el.tagName;
      if (t === 'BUTTON' || t === 'SUMMARY') return 'button';
      if (t === 'A') return 'link';
      if (t === 'SELECT') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
      if (t === 'TEXTAREA') return 'textbox';
      if (t === 'INPUT') return ({checkbox: 'checkbox', radio: 'radio', range: 'slider', search: 'searchbox', number: 'spinbutton',
        button: 'button', submit: 'button', reset: 'button', image: 'button'})[typeOf(el)] || 'textbox';
      return ''; };
    const mask = (s) => (s || '').replace(/\d+/g, '#').replace(/\s+/g, ' ').trim().slice(0, 40);
    // The control's name, never a field's value: aria-label, its <label>, a placeholder or title, else (a button) its text.
    const nameOf = (el) => mask(el.getAttribute('aria-label') || (el.labels && el.labels.length ? el.labels[0].innerText : '')
      || el.getAttribute('placeholder') || el.getAttribute('title') || (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) ? '' : el.innerText));
    const fontOf = (el) => { const s = getComputedStyle(el);
      return {family: (s.fontFamily.split(',')[0] || '').trim().replace(/^["']|["']$/g, ''), px: r2(parseFloat(s.fontSize)), weight: String(s.fontWeight)}; };
    const keyOf = (el) => { const tag = el.tagName.toLowerCase(); if (el.id) return tag + '#' + el.id;
      const cls = classes(el)[0]; if (cls) return tag + '.' + cls;
      const role = el.getAttribute('role'); return role ? tag + '[role=' + role + ']' : tag; };
    const tally = (obj, k) => { obj[k] = (obj[k] || 0) + 1; };
    const sortedObj = (obj) => Object.fromEntries(Object.keys(obj).sort().map(k => [k, obj[k]]));

    // A select or combobox carries its option count (#1505), so the comparator can place it in a bucket: a <select>'s own
    // options, a datalist's, or the listbox an aria-controls names when that is in the DOM; else none, never a guess.
    const optionsOf = (el) => {
      if (el.tagName === 'SELECT') return el.options.length;
      if (el.tagName === 'INPUT' && el.list) return el.list.options.length;
      const id = el.getAttribute('aria-controls'), list = id && document.getElementById(id);
      return list ? list.querySelectorAll('[role=option]').length || null : null; };
    // A segmented or range-tab control carries the key of the group it sits in (#1505), so two groups on one screen stay
    // two: the parent's key, numbered in document order when several share one.
    const groupKeys = new Map(), groupSeen = {};
    const groupOf = (el) => { const g = el.parentElement; if (!g) return '';
      if (!groupKeys.has(g)) { const k = keyOf(g), i = groupSeen[k] = (groupSeen[k] || 0) + 1; groupKeys.set(g, i > 1 ? k + '~' + i : k); }
      return groupKeys.get(g); };

    const seen = new Map(), kinds = {}, comps = {};
    let total = 0;
    q(params.interactive + ', [role=link], [role=combobox], [role=radio], [role=menuitemcheckbox], [role=menuitemradio]').forEach(el => {
      const component = componentOf(el), kind = kindOf(el, component), f = fontOf(el);
      const entry = {key: keyOf(el), kind, component, role: roleOf(el), label: nameOf(el), family: f.family, px: f.px, weight: f.weight};
      if (kind === 'select' || kind === 'combobox') { const o = optionsOf(el); if (o !== null) entry.options = o; }
      if (kind === 'segmented' || kind === 'range-tab') entry.group = groupOf(el);
      const id = JSON.stringify(entry), had = seen.get(id);
      if (had) had.n++; else seen.set(id, Object.assign(entry, {n: 1}));
      tally(kinds, kind); tally(comps, component); total++; });
    const controls = [...seen.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)).map(e => e[1]);

    const has = (e, re) => classes(e).some(c => re.test(c));
    const OVERLINE = /(^|-)(overline|eyebrow|kicker)$/, CHIP = /(^|-)(chip|badge|tag|pill)$/;
    const SECONDARY = /(^|-)(meta|sub|subtitle|desc|description|hint|detail|secondary|muted)$/, TITLE = /(^|-)(title|name)$/;
    const CONTROL = 'button, [role=button], [role=tab], [role=switch], [role=menuitem], summary, select, input, textarea';
    const roleAt = (e) => { const t = e.tagName;
      if (/^(CODE|PRE|KBD|SAMP)$/.test(t)) return 'code';
      const h = /^H([1-4])$/.exec(t); if (h) return 'heading-' + h[1];
      if (e.getAttribute('role') === 'heading') { const l = +(e.getAttribute('aria-level') || 2); return l >= 1 && l <= 4 ? 'heading-' + l : 'body'; }
      if (has(e, OVERLINE)) return 'overline';
      if (has(e, CHIP)) return 'caption';
      if (has(e, SECONDARY)) return 'secondary';
      if (has(e, TITLE)) return 'row-title';
      if (e.matches(CONTROL)) return 'button-label';
      if (/^(SMALL|FIGCAPTION|CAPTION)$/.test(t)) return 'caption';
      return null; };
    const textRole = (el) => { for (let e = el; e && e.nodeType === 1 && e !== document.body; e = e.parentElement) { const r = roleAt(e); if (r) return r; }
      return 'body'; };
    const byRole = {};
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement; if (!el || !n.textContent.trim() || !visible(el)) continue;
      const role = textRole(el), f = fontOf(el), vk = JSON.stringify([f.family, f.px, f.weight]);
      const r = byRole[role] = byRole[role] || {role, runs: 0, variants: {}};
      r.runs++; const v = r.variants[vk] = r.variants[vk] || Object.assign(f, {runs: 0}); v.runs++; }
    const cmpVariant = (a, b) => (a.family < b.family ? -1 : a.family > b.family ? 1 : a.px - b.px || (+a.weight || 0) - (+b.weight || 0));
    const text = Object.keys(byRole).sort().map(k => ({role: k, runs: byRole[k].runs, variants: Object.values(byRole[k].variants).sort(cmpVariant)}));

    return { unmeasured: cat ? null : (params.componentsError || 'no component catalog passed in'),
      controls: controls.slice(0, INVENTORY_CAP), controls_count: total, kinds: sortedObj(kinds), components: sortedObj(comps),
      text, text_count: text.length };
  });

  if (EXCLUDE) section('excluded', () => ({ selectors: params.excludeSelectors, elements: scope.querySelectorAll(EXCLUDE).length }));
  section('headings', () => q('h1,h2,h3,h4').map(h => h.tagName+':'+h.textContent.trim().slice(0,30)+'@'+getComputedStyle(h).fontSize).slice(0, CAP));
  return out;
}
"""
