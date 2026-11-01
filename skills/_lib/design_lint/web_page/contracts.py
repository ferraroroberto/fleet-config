"""The web-page contract set: greppable checks of `design.web.md` sections 5 to 8.

Each check reads the site's own CSS and markup (`css_own`, `markup_own`, the
third-party bundles left out) and returns `_result` rows, the shape the
web-app contracts return, so `apply_accepted` and /design-sync read both sets
alike. Ids carry a `web-` prefix so a web-page finding never reads as a PWA one.
A component the site does not use is `NA`, never a pass.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from ..contracts import build_ctx
from ..contracts._ctx import _ContractsCtx, _evidence, _loc_at, _result
from ..css import _BLOCK_RE
from ..files import read_text, rel

_HEADER_SELECTOR_RE = re.compile(r"(?:^|[\s,>+~])header\b|\.[\w-]*header\b", re.I)
_CONTAINING_BLOCK_RE = re.compile(r"(?<![\w-])(backdrop-filter|filter|transform)\s*:\s*([^;}]*)")
_FIXED_TRACK_RE = re.compile(
    r"grid-template-columns\s*:\s*minmax\(\s*0\s*,\s*\d+(?:\.\d+)?px\s*\)\s+(?:minmax\(\s*0\s*,\s*)?1fr")
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _rule_loc(blob: str, m: "re.Match[str]") -> str:
    """`file:line` of a `_BLOCK_RE` rule's selector, past the whitespace the match starts with."""
    sel = m.group(1)
    return _loc_at(blob, m.start(1) + len(sel) - len(sel.lstrip()))


def _check_focus_ring(ctx: _ContractsCtx) -> List[dict]:
    m = re.search(r":focus-visible[^{}]*\{[^{}]*\boutline\s*:\s*(?!none\b)[^;}]+", ctx.css_own)
    if m:
        return [_result("web-focus-visible", "PASS", "a :focus-visible outline is declared",
                        _loc_at(ctx.css_own, m.start()))]
    return [_result("web-focus-visible", "FAIL",
                    "no :focus-visible outline (design.web.md section 8: 2px solid, 2px offset)")]


def _check_motion(ctx: _ContractsCtx) -> List[dict]:
    ev = _evidence(ctx.css_own, r"@media[^{]*prefers-reduced-motion")
    if ev:
        return [_result("web-reduced-motion", "PASS", "prefers-reduced-motion handled", ev)]
    return [_result("web-reduced-motion", "FAIL",
                    "no prefers-reduced-motion block (design.web.md section 8)")]


def _check_header(ctx: _ContractsCtx) -> List[dict]:
    rules = [m for m in _BLOCK_RE.finditer(ctx.css_own) if _HEADER_SELECTOR_RE.search(m.group(1))]
    if not rules:
        return [_result("web-header", "WARN", "no header rule found in the site's CSS")]
    for m in rules:
        for decl in _CONTAINING_BLOCK_RE.finditer(m.group(2)):
            if decl.group(2).strip().lower() != "none":
                return [_result("web-header", "FAIL",
                                f"`{decl.group(1)}` on the header makes it the containing block "
                                "of the fixed phone menu panel (design.web.md section 6, Header)",
                                _loc_at(ctx.css_own, m.start(2) + decl.start()))]
    rows = [_result("web-header", "PASS", "no backdrop-filter, filter or transform on the header",
                    _rule_loc(ctx.css_own, rules[0]))]
    current = _evidence(ctx.markup_own, r"aria-current\s*=\s*[\"']page[\"']")
    rows.append(_result("web-header-current", "PASS" if current else "WARN",
                        "the current page carries aria-current=\"page\"" if current
                        else "no aria-current=\"page\" on the current nav item", current))
    return rows


def _check_phone_menu(ctx: _ContractsCtx) -> List[dict]:
    expanded = _evidence(ctx.markup_own, r"aria-expanded")
    controls = _evidence(ctx.markup_own, r"aria-controls")
    if expanded and controls:
        return [_result("web-phone-menu", "PASS", "the menu button carries aria-expanded and aria-controls",
                        expanded)]
    if expanded:
        return [_result("web-phone-menu", "WARN", "the menu button has aria-expanded but no aria-controls",
                        expanded)]
    return [_result("web-phone-menu", "FAIL",
                    "no menu button with aria-expanded (design.web.md section 6, Phone menu)")]


def _check_sections(ctx: _ContractsCtx) -> List[dict]:
    ev = _evidence(ctx.css_own, r"container-type\s*:\s*inline-size")
    if ev:
        return [_result("web-sections-container", "PASS",
                        "a size container is declared, so image roles resolve against 100cqi", ev)]
    return [_result("web-sections-container", "WARN",
                    "no `container-type: inline-size` on the content container (design.web.md section 5)")]


def _check_shape(ctx: _ContractsCtx) -> List[dict]:
    rows: List[dict] = []
    for check_id, pattern, what in (
            ("web-no-shadow", r"box-shadow\s*:\s*(?!none\b)[^;}]+", "a box-shadow"),
            ("web-no-gradient", r"(?:linear|radial|conic)-gradient\(", "a gradient")):
        ev = _evidence(ctx.css_own, pattern)
        rows.append(_result(check_id, "WARN" if ev else "PASS",
                            f"{what} is declared; design.web.md section 4 uses none" if ev
                            else f"no {what.split(' ', 1)[1]}", ev))
    return rows


def _check_card(ctx: _ContractsCtx) -> List[dict]:
    card_rules = [m for m in _BLOCK_RE.finditer(ctx.css_own) if re.search(r"\.card\b", m.group(1))]
    if not card_rules:
        return [_result("web-card", "NA", "no card component in the site's CSS")]
    stretched = next((m for m in card_rules if "::after" in m.group(1) or "::before" in m.group(1)
                      if re.search(r"\binset\s*:\s*0\b", m.group(2))), None)
    if stretched:
        return [_result("web-card", "PASS", "the card's link is stretched over the whole card",
                        _rule_loc(ctx.css_own, stretched))]
    return [_result("web-card", "WARN", "no stretched card link (a ::after with inset: 0) "
                    "(design.web.md section 6, Card)", _rule_loc(ctx.css_own, card_rules[0]))]


def _check_carousel(ctx: _ContractsCtx) -> List[dict]:
    rules = [m for m in _BLOCK_RE.finditer(ctx.css_own) if re.search(r"carousel", m.group(1))]
    if not rules:
        return [_result("web-carousel", "NA", "no carousel in the site's CSS")]
    body = "\n".join(m.group(2) for m in rules)
    missing = [p for p in ("grid-auto-columns", "scroll-snap-type") if not re.search(p + r"\s*:", body)]
    ev = _rule_loc(ctx.css_own, rules[0])
    if missing:
        return [_result("web-carousel", "FAIL", f"the carousel lacks {', '.join(missing)}: whole cards "
                        "come from grid-auto-columns with scroll snap (design.web.md section 6)", ev)]
    return [_result("web-carousel", "PASS", "whole-card carousel: grid-auto-columns with scroll snap", ev)]


def _check_footer(ctx: _ContractsCtx) -> List[dict]:
    landmark = _evidence(ctx.markup_own, r"<footer\b")
    if not landmark:
        return [_result("web-footer", "FAIL", "no <footer> landmark (design.web.md sections 6 and 8)")]
    band = _evidence(ctx.css_own, r"var\(\s*--footer-band\b")
    if band:
        return [_result("web-footer", "PASS", "a <footer> on the --footer-band band", band)]
    return [_result("web-footer", "WARN", "the footer does not use --footer-band", landmark)]


def _check_image_roles(ctx: _ContractsCtx) -> List[dict]:
    rows: List[dict] = []
    squeeze = _FIXED_TRACK_RE.search(ctx.css_own)
    rows.append(_result(
        "web-image-roles", "FAIL" if squeeze else "PASS",
        "a `minmax(0, <px>) 1fr` track grows to its max before the text gets any width; write the "
        "role as min(<px>, half the row) (design.web.md section 5, tablet band)" if squeeze
        else "no fixed-max media track", _loc_at(ctx.css_own, squeeze.start()) if squeeze else None))
    unsized: List[Tuple[str, int]] = []
    total = 0
    for path in ctx.html_files:
        text = read_text(path)
        for m in _IMG_TAG_RE.finditer(text):
            total += 1
            tag = m.group(0).lower()
            if not (re.search(r"\swidth\s*=", tag) and re.search(r"\sheight\s*=", tag)):
                unsized.append((rel(ctx.root, path), text.count("\n", 0, m.start()) + 1))
    if not total:
        rows.append(_result("web-image-dimensions", "NA", "no <img> in the site's HTML"))
    elif unsized:
        where = f"{unsized[0][0]}:{unsized[0][1]}"
        rows.append(_result("web-image-dimensions", "WARN",
                            f"{len(unsized)} of {total} <img> lack explicit width and height "
                            "(design.web.md section 7)", where))
    else:
        rows.append(_result("web-image-dimensions", "PASS", f"all {total} <img> carry width and height"))
    return rows


_WEB_CONTRACT_CHECKS: Tuple[Callable[[_ContractsCtx], List[dict]], ...] = (
    _check_focus_ring,
    _check_motion,
    _check_header,
    _check_phone_menu,
    _check_sections,
    _check_shape,
    _check_card,
    _check_carousel,
    _check_footer,
    _check_image_roles,
)


def web_contracts(root: Path, css_files: List[Path], html_files: List[Path],
                  js_files: List[Path], spec: Dict[str, str]) -> List[dict]:
    """Run the web-page contract set in order and concatenate its rows."""
    ctx = build_ctx(root, css_files, html_files, js_files, spec, None)
    rows: List[dict] = []
    for check_fn in _WEB_CONTRACT_CHECKS:
        rows.extend(check_fn(ctx))
    return rows
