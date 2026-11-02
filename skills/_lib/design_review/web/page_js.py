"""The in-page scripts of the web-page walk: raw facts only, never a verdict.

Each constant is a JavaScript arrow function `page.evaluate` runs. They return
geometry, computed styles and element lists; `checks.py` applies the pass
rules in Python, where they are unit-tested without a browser. Stdlib-only
module (plain strings), imported by `page_walk.py` under the target's
interpreter.

`MEASURE_JS` takes `{hit_min, control, body_min, label_min, generic}` and
returns the per-viewport facts; `FOLD_JS` runs before the page is scrolled
(what loads eagerly below the first screen); `MENU_HIT_JS`, `CAROUSEL_JS`,
`STICKY_JS` and `FOCUS_JS` back the interactive checks; `PERF_INIT_JS` and
`PERF_READ_JS` read the Web Vitals of a cold load.
"""

# Shared helpers, prefixed to every script that needs them.
_HELPERS = r"""
const shown = el => { if (!el || !el.getBoundingClientRect) return false; const r = el.getBoundingClientRect();
  return r.width > 0.5 && r.height > 0.5 && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}); };
const rect = el => { const r = el.getBoundingClientRect();
  return {x: Math.round(r.left), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height),
          top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right)}; };
const label = el => { const t = (el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('alt') || '')
  .replace(/\s+/g, ' ').trim(); return `${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/)[0] : ''} "${t.slice(0, 40)}"`; };
const rgba = s => { const m = String(s).match(/rgba?\(([^)]+)\)/); if (!m) return null;
  const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const over = (top, under) => { const a = top[3]; return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a),
  top[2] * a + under[2] * (1 - a), 1]; };
// The composited background behind an element, or null when an image or gradient sits under it.
const background = el => { const layers = [];
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const cs = getComputedStyle(n);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
    const c = rgba(cs.backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
  let bg = [255, 255, 255, 1]; for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg); return bg; };
const contrastOf = el => { const cs = getComputedStyle(el); const fg = rgba(cs.color); const bg = background(el);
  if (!fg || !bg) return null; return Math.round(ratio(over(fg, bg), bg) * 100) / 100; };
const isLarge = el => { const cs = getComputedStyle(el); const px = parseFloat(cs.fontSize);
  return px >= 24 || (px >= 18.66 && parseInt(cs.fontWeight, 10) >= 700); };
const SKIP_TEXT = 'script,style,noscript,template,svg,[aria-hidden="true"]';
// One entry per element that directly holds visible text.
const textRuns = root => { const seen = new Set(); const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) { if (!n.nodeValue.trim()) continue;
    const el = n.parentElement; if (!el || seen.has(el) || el.closest(SKIP_TEXT) || !shown(el)) continue;
    seen.add(el); out.push(el); } return out; };
const sections = () => { const main = document.querySelector('main, [role=main]') || document.body;
  const s = [...main.querySelectorAll(':scope > section, :scope > .band, :scope > article > section')].filter(shown);
  return s.length ? s : [main]; };
"""

MEASURE_JS = "(params) => {" + _HELPERS + r"""
  const main = document.querySelector('main, [role=main]');
  const footer = document.querySelector('.site-footer, body > footer, footer');
  const header = document.querySelector('.site-header, body > header, header');
  const runs = textRuns(document.body);

  // ---- text
  const BODY = 'p, li, figcaption, blockquote, dd, td';
  const META = 'small, .small, .meta, label, .label, .price, time';
  const text = {runs: runs.length, min_px: null, under_label: [], body_under_floor: [], measure_over: [],
                leading_under_floor: [], low_contrast: [], not_start_aligned: [], heading_smaller_than_body: []};
  for (const el of runs) { const cs = getComputedStyle(el); const px = parseFloat(cs.fontSize);
    text.min_px = text.min_px === null ? px : Math.min(text.min_px, px);
    if (px < params.label_min - 0.01) text.under_label.push(`${label(el)} ${px}px`);
    const upper = cs.textTransform === 'uppercase';
    if (!upper && el.closest(BODY) && !el.closest(META) && !el.closest('header, nav, footer, .site-footer') && px < params.body_min - 0.01)
      text.body_under_floor.push(`${label(el)} ${px}px`);
    if (footer && footer.contains(el)) continue;  // the footer band has its own floors (footer_runs)
    const c = contrastOf(el); const floor = isLarge(el) ? 3 : 4.5;
    if (c !== null && c < floor) text.low_contrast.push(`${label(el)} ${c}:1 (floor ${floor})`); }
  for (const f of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]), select, textarea'))
    if (shown(f) && parseFloat(getComputedStyle(f).fontSize) < params.body_min - 0.01)
      text.body_under_floor.push(`${label(f)} ${getComputedStyle(f).fontSize}`);
  const canvas = document.createElement('canvas').getContext('2d');
  for (const p of (main || document.body).querySelectorAll('p, li')) { if (!shown(p) || p.closest('nav, .card, .swipe-nav')) continue;
    const cs = getComputedStyle(p); const px = parseFloat(cs.fontSize); const lh = cs.lineHeight === 'normal' ? 1.2 * px : parseFloat(cs.lineHeight);
    const t = p.innerText.replace(/\s+/g, ' ').trim(); if (!t) continue;
    if (lh / px < 1.49 && p.getBoundingClientRect().height > lh * 1.5) text.leading_under_floor.push(`${label(p)} ${(lh / px).toFixed(2)}`);
    if (t.length >= 120 && p.tagName === 'P') { canvas.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const avg = canvas.measureText(t).width / t.length;
      const width = p.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const cpl = avg > 0 ? width / avg : 0; if (cpl > 75.5) text.measure_over.push(`${label(p)} ${Math.round(cpl)} ch`); } }
  for (const el of document.querySelectorAll('main p, main h1, main h2, main h3, main h4, main li, main blockquote, main figcaption, footer p')) {
    if (!shown(el) || el.closest('button, .btn, .video, nav, .swipe-nav, .quote')) continue;
    const a = getComputedStyle(el).textAlign; if (a === 'center' || a === 'right' || a === 'end') text.not_start_aligned.push(`${label(el)} ${a}`); }
  for (const h of document.querySelectorAll('main h1, main h2, main h3, main h4, main h5, main h6')) { if (!shown(h)) continue;
    let n = h.nextElementSibling; while (n && !/^P$/.test(n.tagName) && !/^H\d$/.test(n.tagName)) n = n.nextElementSibling;
    if (n && n.tagName === 'P' && shown(n) && parseFloat(getComputedStyle(n).fontSize) > parseFloat(getComputedStyle(h).fontSize) + 0.5)
      text.heading_smaller_than_body.push(`${label(h)} ${getComputedStyle(h).fontSize} < ${getComputedStyle(n).fontSize}`); }

  // ---- footer band: body text 7:1, label heads 4.5:1
  const footer_runs = [];
  if (footer) for (const el of runs.filter(e => footer.contains(e))) { const c = contrastOf(el); if (c === null) continue;
    const head = !!el.closest('strong, b, h1, h2, h3, h4, h5, h6, .label') || getComputedStyle(el).textTransform === 'uppercase';
    footer_runs.push({text: label(el), ratio: c, floor: head ? 4.5 : 7}); }

  // ---- targets
  const INTERACTIVE = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex="-1"])';
  const inline = el => { if (el.tagName !== 'A' || getComputedStyle(el).display !== 'inline') return false;
    const block = el.closest('p, li, dd, td, figcaption'); return !!block && block.innerText.trim().length > el.innerText.trim().length + 1; };
  const targets = [...document.querySelectorAll(INTERACTIVE)].filter(el => shown(el) && !el.closest('[aria-hidden="true"]') && !inline(el)).slice(0, 400)
    .map(el => ({el, r: el.getBoundingClientRect()}));
  const small = [];
  const under = t => t.r.width < params.hit_min - 0.5 || t.r.height < params.hit_min - 0.5;
  const centre = t => [t.r.left + t.r.width / 2, t.r.top + t.r.height / 2];
  const distToRect = (p, r) => Math.hypot(Math.max(r.left - p[0], 0, p[0] - r.right), Math.max(r.top - p[1], 0, p[1] - r.bottom));
  for (const t of targets) { if (!under(t)) continue; const c = centre(t);
    const crowded = targets.some(u => u !== t && !u.el.contains(t.el) && !t.el.contains(u.el)
      && (distToRect(c, u.r) < 12 || (under(u) && Math.hypot(c[0] - centre(u)[0], c[1] - centre(u)[1]) < 24)));
    if (crowded) small.push(`${label(t.el)} ${Math.round(t.r.width)}x${Math.round(t.r.height)}`); }
  const control_under_height = [];
  for (const el of document.querySelectorAll('.btn, button, input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select')) {
    if (!shown(el) || el.closest('.card, .swipe-nav, .menu-toggle, .video, header, nav') || el.matches('.menu-toggle')) continue;
    const h = el.getBoundingClientRect().height; if (h < params.control - 0.5) control_under_height.push(`${label(el)} ${Math.round(h)}px`); }

  // ---- accessibility
  const landmarks_missing = [];
  if (!document.documentElement.getAttribute('lang')) landmarks_missing.push('lang');
  if (!main) landmarks_missing.push('main landmark');
  const skip = [...document.querySelectorAll('a[href^="#"]')].some(a => { const id = a.getAttribute('href').slice(1);
    const t = id && document.getElementById(id); return !!t && !!main && (t === main || t.contains(main) || main.contains(t)); });
  if (!skip) landmarks_missing.push('skip link');
  const img_missing_alt = [...document.images].filter(i => !i.hasAttribute('alt')).map(i => (i.currentSrc || i.src).split('/').pop());
  const field_unlabelled = [];
  for (const f of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), select, textarea')) {
    if (!shown(f)) continue;
    const byLabel = [...(f.labels || [])].some(l => shown(l) && l.innerText.trim());
    const byRef = (f.getAttribute('aria-labelledby') || '').split(/\s+/).some(id => { const e = id && document.getElementById(id); return e && shown(e); });
    if (!byLabel && !byRef) field_unlabelled.push(label(f) + (f.placeholder ? ` placeholder "${f.placeholder}"` : '')); }
  const motion = [...document.querySelectorAll('video[autoplay], audio[autoplay], marquee, iframe[src*="autoplay=1"]')].map(label);
  for (const a of document.getAnimations ? document.getAnimations() : []) {
    const t = a.effect && a.effect.getTiming ? a.effect.getTiming() : {};
    if (a.playState === 'running' && t.iterations === Infinity) motion.push(`infinite animation on ${a.effect.target ? label(a.effect.target) : 'an element'}`); }

  // ---- page facts
  const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].filter(h => shown(h) && !h.closest('[aria-hidden="true"]'));
  const heading_skip = []; let prev = 0;
  for (const h of headings) { const lv = +h.tagName[1]; if (prev && lv > prev + 1) heading_skip.push(`h${prev}→h${lv} ${label(h)}`); prev = lv; }
  const primary = sections().map((s, i) => { const bg = background(s) || [255, 255, 255, 1];
    const n = [...s.querySelectorAll('.btn, button, a[role=button], input[type=submit]')].filter(b => shown(b)
      && !b.closest('.card, .swipe-nav, .video, header, nav, .menu-toggle') && (() => { const c = rgba(getComputedStyle(b).backgroundColor);
        return c && c[3] > 0.5 && ratio(over(c, bg), bg) >= 1.5; })()).length; return {section: i, primary: n}; });
  const header_contains_fixed = [];
  if (header) { const cs = getComputedStyle(header);
    const bf = cs.backdropFilter || cs.webkitBackdropFilter; if (bf && bf !== 'none') header_contains_fixed.push(`backdrop-filter: ${bf}`);
    if (cs.filter && cs.filter !== 'none') header_contains_fixed.push(`filter: ${cs.filter}`);
    if (cs.transform && cs.transform !== 'none') header_contains_fixed.push(`transform: ${cs.transform}`);
    if (cs.perspective && cs.perspective !== 'none') header_contains_fixed.push(`perspective: ${cs.perspective}`);
    if (/paint|layout|strict|content/.test(cs.contain || '')) header_contains_fixed.push(`contain: ${cs.contain}`);
    if (/transform|filter|perspective/.test(cs.willChange || '')) header_contains_fixed.push(`will-change: ${cs.willChange}`); }
  const routes = [...document.querySelectorAll('header a[href], footer a[href], .site-header a[href], .site-footer a[href]')];
  const contact_route_missing = !routes.some(a => /^(mailto|tel):/i.test(a.getAttribute('href'))
    || /contact|get in touch|book a call/i.test(a.getAttribute('href') + ' ' + a.textContent));
  const generic = new RegExp(`^(${params.generic})$`, 'i');
  const vague_link_text = [...document.querySelectorAll('a[href]')].filter(a => shown(a) && !a.closest('[aria-hidden="true"]')
    && a.getAttribute('aria-hidden') !== 'true' && generic.test((a.getAttribute('aria-label') || a.innerText || '').replace(/\s+/g, ' ').trim())).map(label);
  const eager_embed = [...document.querySelectorAll('iframe[src]')].filter(f => { try { return new URL(f.src, location.href).host !== location.host; }
    catch (e) { return false; } }).filter(f => f.getAttribute('loading') !== 'lazy').map(f => f.src.slice(0, 80));

  // phone reading order: content blocks per row (or section), with a key stable across viewports
  const BLOCK = 'h1, h2, h3, h4, p, ul, ol, blockquote, figure, picture, img, video, iframe, .video, form, a.btn, button';
  const blocks = [];
  sections().forEach((s, si) => { const rows = [...s.querySelectorAll('.row')];
    [...s.querySelectorAll(BLOCK)].forEach((el, bi) => {
      if (el.closest('.card, .cards, .carousel, [data-carousel], .products, .swipe-nav, nav, header') || !shown(el)) return;
      const outer = el.parentElement && el.parentElement.closest(BLOCK); if (outer && s.contains(outer)) return;
      let kind = /^H\d$/.test(el.tagName) ? 'title' : /^(P|UL|OL|BLOCKQUOTE)$/.test(el.tagName) ? 'text'
        : /^(FORM|BUTTON)$/.test(el.tagName) || el.matches('a.btn') ? 'action' : 'picture';
      // A paragraph that is only its links (a button row, a "read the archive" link) is an action, not text.
      if (kind === 'text' && el.tagName === 'P' && el.querySelector('a, button')
          && ![...el.querySelectorAll('a, button')].reduce((t, a) => t.replace(a.innerText, ''), el.innerText).trim()) kind = 'action';
      if (kind === 'text' && !el.innerText.trim()) return;
      const row = el.closest('.row'); const r = el.getBoundingClientRect();
      blocks.push({key: `${si}:${bi}`, group: `${si}:${row && s.contains(row) ? rows.indexOf(row) : -1}`, kind,
                   top: Math.round(r.top + scrollY), bottom: Math.round(r.bottom + scrollY), left: Math.round(r.left), right: Math.round(r.right),
                   text: label(el)}); }); });

  // a picture beside text: the share of the row the text column gets
  const media_rows = [];
  for (const m of (main || document.body).querySelectorAll('img, .video')) { if (!shown(m) || m.closest('.card, .carousel, [data-carousel], .products, header, footer')) continue;
    let a = m; for (let up = 0; up < 6 && a.parentElement && a.parentElement !== main; up++, a = a.parentElement) {
      const p = a.parentElement; const ar = a.getBoundingClientRect();
      const sib = [...p.children].find(c => c !== a && shown(c) && c.querySelector('p') && (() => { const r = c.getBoundingClientRect();
        return r.top < ar.bottom && ar.top < r.bottom && (r.left >= ar.right - 1 || r.right <= ar.left + 1); })());
      if (sib) { const pr = p.getBoundingClientRect(); media_rows.push({row_w: Math.round(pr.width), text_w: Math.round(sib.getBoundingClientRect().width),
        media_w: Math.round(ar.width), text: label(sib)}); break; } } }

  // images that share a role
  const role_images = [];
  for (const i of (main || document.body).querySelectorAll('.media img, .card img')) { if (!shown(i) || i.closest('.cutout')) continue;
    let role; const card = i.closest('.card');
    if (card) { const set = card.closest('.cards, .carousel, [data-carousel], .products');
      role = `card set ${set ? [...document.querySelectorAll('.cards, .carousel, [data-carousel], .products')].indexOf(set) : 'loose'}`; }
    else { const m = i.closest('.media'); role = m.classList.contains('feature') ? 'feature' : m.classList.contains('thumb') ? 'thumb' : 'tile'; }
    role_images.push({role, w: Math.round(i.getBoundingClientRect().width), src: (i.currentSrc || i.src).split('/').pop()}); }

  // portraits beside a name
  const portraits = [];
  for (const i of (main || document.body).querySelectorAll('.thumb img, img.portrait, .portrait img')) { if (!shown(i)) continue;
    const row = i.closest('.row') || i.parentElement.parentElement; const cs = getComputedStyle(i);
    const name = [...row.querySelectorAll('h2, h3, h4, strong, .name')].find(n => shown(n) && !n.contains(i));
    const r = i.getBoundingClientRect();
    portraits.push({src: (i.currentSrc || i.src).split('/').pop(), w: Math.round(r.width), h: Math.round(r.height), radius: cs.borderTopLeftRadius,
                    top: Math.round(r.top + scrollY), name_top: name ? Math.round(name.getBoundingClientRect().top + scrollY) : null}); }

  // carousels: the row's box and each card's box
  const carousels = [...document.querySelectorAll('[data-carousel], .carousel')].filter(shown).map((t, i) => {
    const tr = t.getBoundingClientRect(); const nav = t.nextElementSibling && t.nextElementSibling.classList.contains('swipe-nav') ? t.nextElementSibling : null;
    const nr = nav && shown(nav) ? nav.getBoundingClientRect() : null;
    return {index: i, left: tr.left, right: tr.right, top: tr.top, scrolls: t.scrollWidth > t.clientWidth + 2,
            gap: parseFloat(getComputedStyle(t).columnGap) || 0,
            cards: [...t.children].filter(shown).map(c => { const r = c.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, w: r.width}; }),
            nav_centre: nr ? (nr.left + nr.right) / 2 : null}; });

  // image weight facts: responsive variants, explicit dimensions, a modern format
  const images_unoptimised = [];
  for (const i of (main || document.body).querySelectorAll('img')) { const src = i.currentSrc || i.src; if (!src || /\.svg(\?|$)/i.test(src)) continue;
    if (i.getBoundingClientRect().width < 64 && i.naturalWidth < 128) continue;
    const miss = []; if (!i.getAttribute('srcset') && !i.closest('picture')) miss.push('srcset');
    if (i.getAttribute('srcset') && !i.getAttribute('sizes')) miss.push('sizes');
    if (!i.getAttribute('width') || !i.getAttribute('height')) miss.push('width/height');
    if (!/\.(webp|avif)(\?|$)/i.test(src)) miss.push('modern format');
    if (miss.length) images_unoptimised.push(`${src.split('/').pop()} (${miss.join(', ')})`); }

  return {
    text, footer_runs,
    targets: {total: targets.length, small, control_under_height},
    a11y: {landmarks_missing, img_missing_alt, field_unlabelled, motion},
    layout: {scroll_w: document.documentElement.scrollWidth, inner_w: document.documentElement.clientWidth},
    page: {h1_count: headings.filter(h => h.tagName === 'H1').length, heading_skip, primary, header_contains_fixed,
           contact_route_missing, vague_link_text, eager_embed, blocks, media_rows, role_images, portraits, carousels},
    images_unoptimised,
    height: document.documentElement.scrollHeight,
  };
}"""

FOLD_JS = "() => {" + _HELPERS + r"""
  return [...document.images].filter(i => { const r = i.getBoundingClientRect();
    return r.width > 0 && r.top + scrollY > innerHeight && i.getAttribute('loading') !== 'lazy'; })
    .map(i => (i.currentSrc || i.src).split('/').pop());
}"""

# The phone menu, once open: is each link the element at its own centre?
MENU_HIT_JS = "(navSel) => {" + _HELPERS + r"""
  const nav = document.querySelector(navSel); if (!nav) return {nav: false, links: []};
  return {nav: true, links: [...nav.querySelectorAll('a[href]')].map(a => { const r = a.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const inside = r.width > 0 && r.height > 0 && x >= 0 && x < innerWidth && y >= 0 && y < innerHeight;
    const hit = inside ? document.elementFromPoint(x, y) : null;
    return {href: a.getAttribute('href'), hit: !!hit && (hit === a || a.contains(hit))}; })};
}"""

# One carousel's scroll state, by index among `[data-carousel], .carousel`.
CAROUSEL_JS = r"""(i) => { const t = [...document.querySelectorAll('[data-carousel], .carousel')].filter(e => e.getBoundingClientRect().width > 0)[i];
  if (!t) return null; const first = [...t.children].find(c => c.getBoundingClientRect().width > 0);
  return {scroll_left: t.scrollLeft, card_w: first ? first.getBoundingClientRect().width : 0,
          gap: parseFloat(getComputedStyle(t).columnGap) || 0}; }"""

# The largest blank stretch beside the text that a sticky picture leaves in the current viewport.
STICKY_JS = "() => {" + _HELPERS + r"""
  const header = document.querySelector('.site-header, body > header, header');
  const hb = header && getComputedStyle(header).position.match(/sticky|fixed/) ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
  const main = document.querySelector('main, [role=main]') || document.body; let worst = 0; let count = 0;
  for (const s of main.querySelectorAll('*')) { if (getComputedStyle(s).position !== 'sticky' || !s.querySelector('img, .video') && s.tagName !== 'IMG') continue;
    let col = s; while (col.parentElement && col.parentElement !== main && col.parentElement.children.length < 2) col = col.parentElement;
    const row = col.parentElement; if (!row) continue;
    const text = [...row.children].filter(c => c !== col && c.querySelector('p')).sort((a, b) => b.innerText.length - a.innerText.length)[0];
    if (!text) continue; count++;
    const rr = row.getBoundingClientRect(), tr = text.getBoundingClientRect(), sr = s.getBoundingClientRect();
    if (tr.left < sr.right - 1 && sr.left < tr.right - 1) continue;  // stacked, not beside
    const top = Math.max(hb, rr.top, tr.top), bottom = Math.min(innerHeight, rr.bottom, tr.bottom);
    if (bottom - top < 50) continue;
    const st = Math.max(sr.top, top), sb = Math.min(sr.bottom, bottom);
    const blank = sb <= st ? bottom - top : Math.max(st - top, bottom - sb);
    worst = Math.max(worst, Math.round(blank)); }
  return {sticky: count, blank: worst};
}"""

# The focused element: does it draw a ring, and is its centre under the sticky header?
FOCUS_JS = "() => {" + _HELPERS + r"""
  const el = document.activeElement; if (!el || el === document.body || el === document.documentElement) return null;
  const cs = getComputedStyle(el);
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 1) || (cs.boxShadow && cs.boxShadow !== 'none');
  const header = document.querySelector('.site-header, body > header, header'); const r = el.getBoundingClientRect();
  let obscured = false;
  if (header && !header.contains(el) && r.width > 0) { const hit = document.elementFromPoint(r.left + r.width / 2, Math.max(0, r.top + Math.min(r.height / 2, 8)));
    obscured = !!hit && header.contains(hit) && !el.contains(hit); }
  return {id: label(el), ring: !!ring, obscured};
}"""

PERF_INIT_JS = r"""(() => { const w = window.__wrPerf = {lcp: null, cls: 0, longtasks: []};
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) w.lcp = e.renderTime || e.loadTime || e.startTime; })
    .observe({type: 'largest-contentful-paint', buffered: true}); } catch (e) {}
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) w.cls += e.value; })
    .observe({type: 'layout-shift', buffered: true}); } catch (e) {}
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) w.longtasks.push([e.startTime, e.duration]); })
    .observe({type: 'longtask', buffered: true}); } catch (e) {}
})();"""

PERF_READ_JS = r"""() => { const w = window.__wrPerf || {lcp: null, cls: 0, longtasks: []};
  const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
  const tbt = w.longtasks.filter(t => t[0] >= fcp).reduce((s, t) => s + Math.max(0, t[1] - 50), 0);
  const faces = [];
  for (const sheet of document.styleSheets) { let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const r of rules) if (r.constructor.name === 'CSSFontFaceRule')
      faces.push({family: r.style.getPropertyValue('font-family').replace(/["']/g, '').trim(),
                  display: r.style.getPropertyValue('font-display') || 'auto', src: r.style.getPropertyValue('src')}); }
  const blocking = [...document.querySelectorAll('head link[rel~=stylesheet], head script[src]')].filter(e =>
    (e.tagName === 'LINK' ? !e.media || e.media === 'all' || e.media === 'screen' : !e.async && !e.defer && e.type !== 'module'))
    .map(e => e.href || e.src).filter(u => { try { return new URL(u).host !== location.host; } catch (x) { return false; } });
  return {lcp_ms: w.lcp === null ? null : Math.round(w.lcp), cls: Math.round(w.cls * 1000) / 1000, tbt_ms: Math.round(tbt),
          fcp_ms: Math.round(fcp), font_faces: faces, render_blocking_third_party: blocking}; }"""
