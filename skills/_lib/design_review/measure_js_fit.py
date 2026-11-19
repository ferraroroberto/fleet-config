"""The in-page script that measures whether an open overlay fits the viewport (LAYOUT-11).

Its own script, not a section of the big measurement: the walk runs it at the phone viewports a sheet is judged at
(390x664 with Safari's toolbars up, 390x844), re-sizing the page between reads, and the big script is too heavy to
repeat. Read only: it opens, clicks and scrolls nothing.

`FIT_JS` is an arrow function taking `{primary, primaryFallback, exclude}` and returning, for the viewport as it
stands, `{vw, vh, roots: [...]}`, one root per open overlay (a `<dialog>`, a popover, a `role=dialog` / `alertdialog`
or a `role=menu`, the same set `coverage.open_roots` lists):

    key, id      the root's key and its own or nearest `data-surface` id (null when none)
    top, bottom, left, right   the root's box in CSS px
    inside       the box sits inside the viewport (0.5px slack for sub-pixel rounding)
    primary      null (a menu or popover has no primary action) or
                 {state, sel, top, bottom}: `visible` (on screen and the element on top at its centre),
                 `cut` (a box that reaches past the viewport, or covered), `missing` (a declared selector that
                 matches nothing in the root), `none` (no declared selector and nothing recognisable as one) or
                 `covered` (another open overlay sits over this one, so its action is not the one the person sees)
    overflow     px the page or the root scrolls sideways

Only the topmost overlay is judged for its primary. A root is covered when an unrelated open root overlaps it and wins
the hit test inside the overlap; a sheet opened over another sheet hides the lower one's action by design, so the lower
layer reads `covered`, which is not a finding. Its box and sideways overflow are still read.

A primary is the declared `primary` selector, else the last visible match of `primaryFallback` (the measurement's
primary selector), else the last visible button named Done, Save, Add, Apply, OK or Confirm that is not a list or menu
option (a row titled "Add a parent" is content, not the sheet's action). Naming none is `none`, which is not a
finding: a sheet with no footer action still has its box and its overflow checked.
"""

FIT_JS = r"""
(params) => {
  const OVERLAYS = 'dialog, [popover], [role=dialog], [role=alertdialog], [role=menu]';
  const EXCLUDE = (params.exclude || []).join(', ');
  const TOL = 0.5;
  const vw = innerWidth, vh = innerHeight;
  const shown = (el) => {
    if (EXCLUDE && el.closest(EXCLUDE)) return false;
    if (el.closest('[aria-hidden=true], [inert]')) return false;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el); if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) return false;
    return typeof el.checkVisibility !== 'function' || el.checkVisibility();
  };
  const isOpen = (el) => {
    if (el.tagName === 'DIALOG' && !el.open) return false;
    if (el.hasAttribute('popover')) { try { if (!el.matches(':popover-open')) return false; } catch (e) { /* no popover API */ } }
    if (!shown(el)) return false;
    const r = el.getBoundingClientRect(); return r.right > 0 && r.bottom > 0 && r.left < vw && r.top < vh;
  };
  const keyOf = (el) => { const tag = el.tagName.toLowerCase(); if (el.id) return tag + '#' + el.id;
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0]; if (cls) return tag + '.' + cls;
    const role = el.getAttribute('role'); return role ? tag + '[role=' + role + ']' : tag; };
  const selOf = (el) => { const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0];
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : cls ? '.' + cls : ''); };
  const named = /^\s*(done|save|add|apply|ok|confirm)\b/i;
  const LISTED = '[role=listbox], [role=option], [role=menu], [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox], li';
  const onTop = (el) => {
    const b = el.getBoundingClientRect(), cx = b.left + b.width / 2, cy = b.top + b.height / 2;
    const inView = b.top >= -TOL && b.bottom <= vh + TOL && b.left >= -TOL && b.right <= vw + TOL;
    const hit = inView ? document.elementFromPoint(cx, cy) : null;
    return { visible: inView && !!hit && (hit === el || el.contains(hit)), top: Math.round(b.top), bottom: Math.round(b.bottom) };
  };
  const primaryOf = (root, topmost) => {
    if (!topmost) return { state: 'covered', sel: null, top: null, bottom: null };
    const visibleIn = (sel) => { try { return [...root.querySelectorAll(sel)].filter(shown); } catch (e) { return []; } };
    let el = null, declared = !!params.primary;
    if (declared) {
      let all = [];
      try { all = [...root.querySelectorAll(params.primary)]; } catch (e) { all = []; }
      if (!all.length) return { state: 'missing', sel: params.primary, top: null, bottom: null };
      el = all.filter(shown).pop() || all[all.length - 1];
    } else {
      el = visibleIn(params.primaryFallback || 'button[type=submit]').pop()
        || visibleIn('button, [role=button]').filter(b => !b.closest(LISTED)
          && named.test(b.textContent || b.getAttribute('aria-label') || '')).pop() || null;
    }
    if (!el) return { state: 'none', sel: null, top: null, bottom: null };
    const t = onTop(el);
    return { state: t.visible ? 'visible' : 'cut', sel: selOf(el), top: t.top, bottom: t.bottom };
  };
  const doc = document.documentElement;
  const open = [...document.querySelectorAll(OVERLAYS)].filter(isOpen);
  const coveredBy = (el) => open.some((other) => {
    if (other === el || el.contains(other) || other.contains(el)) return false;
    const a = el.getBoundingClientRect(), b = other.getBoundingClientRect();
    const l = Math.max(a.left, b.left, 0), t = Math.max(a.top, b.top, 0);
    const rr = Math.min(a.right, b.right, vw), bb = Math.min(a.bottom, b.bottom, vh);
    if (rr - l < 1 || bb - t < 1) return false;
    const hit = document.elementFromPoint((l + rr) / 2, (t + bb) / 2);
    return !!hit && other.contains(hit) && !el.contains(hit);
  });
  const roots = [];
  open.forEach((el) => {
    const r = el.getBoundingClientRect(), carrier = el.closest('[data-surface]');
    const menu = el.getAttribute('role') === 'menu' || el.hasAttribute('popover');
    roots.push({
      key: keyOf(el), id: carrier ? carrier.getAttribute('data-surface') : null,
      top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right),
      inside: r.top >= -TOL && r.bottom <= vh + TOL && r.left >= -TOL && r.right <= vw + TOL,
      primary: menu ? null : primaryOf(el, !coveredBy(el)),
      overflow: Math.max(0, doc.scrollWidth - doc.clientWidth, el.scrollWidth - el.clientWidth),
    });
  });
  return { vw, vh, roots };
}
"""
