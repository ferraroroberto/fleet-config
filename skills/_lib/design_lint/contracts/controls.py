"""Component contracts for the fleet's interactive controls.

Switch on-colour, the one-boolean-control rule, disclosure boxes and their
chevron, the button tier vocabulary, the theme toggle, and the row-height
scale — everything that is about a named component's recipe.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ..css import _ANY_DECL_RE, _BLOCK_RE, _COLOR_LITERAL_RE, normalize_value, strip_comments
from ..files import read_text, rel
from ._ctx import _ContractsCtx, _evidence, _loc_at, _result


def _check_switch_on_green(ctx: _ContractsCtx) -> List[dict]:
    # 4. switch on-track color (THE green decision)
    css_all = ctx.css_all
    sw = re.search(r"\.toggle\.on[^{}]*\{([^{}]*)\}", css_all)
    if not sw:
        return [_result("switch-on-green", "NA", "no .toggle.on rule found (app may not ship a switch)")]
    body = sw.group(1)
    ev = _evidence(css_all, r"\.toggle\.on[^{}]*\{")
    if re.search(r"var\(--(on|success)\b", body):
        return [_result("switch-on-green", "PASS", "switch on-track uses the success token", ev)]
    if "var(--accent" in body:
        return [_result("switch-on-green", "FAIL", "switch on-track is the accent — design.md v2 says success (green)", ev)]
    return [_result("switch-on-green", "WARN", f"switch on-track is not tokenized: {body.strip()[:60]}", ev)]


def _check_no_native_checkbox(ctx: _ContractsCtx) -> List[dict]:
    # 5. native checkboxes (the one-boolean-control rule)
    markup_all = ctx.markup_all
    cb = re.search(r"type=[\"']checkbox[\"']", markup_all)
    if cb:
        n = len(re.findall(r"type=[\"']checkbox[\"']", markup_all))
        return [_result("no-native-checkbox", "FAIL",
                         f"{n} native checkbox(es) — the fleet boolean control is the switch",
                         _evidence(markup_all, r"type=[\"']checkbox[\"']"))]
    return [_result("no-native-checkbox", "PASS", "no native checkboxes")]


def _check_disclosure_box(ctx: _ContractsCtx) -> List[dict]:
    # 6. disclosure closed-box contract (only if the app has disclosures)
    css_all, markup_all = ctx.css_all, ctx.markup_all
    if not re.search(r"<details|\.collapse-summary|details\[open\]", css_all + markup_all):
        return [_result("disclosure-box", "NA", "no details/summary disclosures found")]
    h = re.search(r"height:\s*52px", css_all)
    p = re.search(r"padding:\s*0 14px", css_all)
    d = re.search(r"\[open\][^{}]*summary[^{}]*\{[^{}]*border-bottom", css_all)
    missing = [name for name, hit in
               (("closedHeight 52px", h), ("summaryPadding 0 14px", p),
                ("open divider", d)) if not hit]
    if not missing:
        return [_result("disclosure-box", "PASS", "52px closed box + 0 14px padding + open divider all present",
                         _evidence(css_all, r"height:\s*52px"))]
    return [_result("disclosure-box", "FAIL", "disclosure contract incomplete — missing: " + ", ".join(missing))]


def _check_button_tiers(ctx: _ContractsCtx) -> List[dict]:
    # 11. button tiers — the fleet button vocabulary (design.md Components;
    #     fleet-config#296). Hardcoded fills and a filled "ghost" are FAILs;
    #     a solid accent outside the primary and a tint without accent text
    #     are WARNs for /design-sync's judgment layer to arbitrate.
    css_all = ctx.css_all
    hardcoded: List[str] = []
    ghost_inverted: List[str] = []
    solid_strays: List[str] = []
    tint_off: List[str] = []
    n_btn = 0
    for bm in _BLOCK_RE.finditer(css_all):
        sel = bm.group(1).strip().splitlines()[-1].strip()
        if not re.search(r"btn|button", sel, re.I):
            continue
        n_btn += 1
        decls = {p.lower(): v.strip()
                 for p, v in _ANY_DECL_RE.findall(bm.group(2) + ";")}
        for prop in ("background", "background-color", "color", "border",
                     "border-color", "border-top", "border-bottom"):
            v = decls.get(prop)
            if v and "var(" not in v and _COLOR_LITERAL_RE.search(v):
                hardcoded.append(f"{sel} {{ {prop}: {v[:40]} }}")
        bg = decls.get("background", decls.get("background-color", ""))
        # base ghost rule only — a trailing state class (.copied, .danger-flash)
        # or pseudo is a legitimate state flash, not the resting recipe
        if (re.search(r"\.[A-Za-z0-9_-]*ghost[A-Za-z0-9_-]*$", sel) and bg
                and bg.split()[0] not in ("transparent", "none")):
            ghost_inverted.append(f"{sel} {{ background: {bg[:40]} }}")
        if bg == "var(--accent)" and not re.search(r"detail-save|primary", sel):
            solid_strays.append(sel)
        if "var(--accent-soft)" in bg:
            col = decls.get("color")
            if col and col != "var(--accent)":
                tint_off.append(f"{sel} {{ color: {col[:30]} }}")
    bits = []
    if hardcoded:
        bits.append(f"{len(hardcoded)} hardcoded button color(s): "
                    + "; ".join(hardcoded[:4]))
    if ghost_inverted:
        bits.append(f"{len(ghost_inverted)} ghost class(es) with a fill "
                    "(ghost = transparent; a tinted fill is the tint tier): "
                    + "; ".join(ghost_inverted[:4]))
    if solid_strays:
        bits.append("solid accent fill outside the primary: "
                    + ", ".join(solid_strays[:6]))
    if tint_off:
        bits.append("tint fill without accent text: "
                    + "; ".join(tint_off[:4]))
    if n_btn == 0:
        return [_result("button-tiers", "NA", "no button rules found")]
    if hardcoded or ghost_inverted:
        return [_result("button-tiers", "FAIL", " | ".join(bits))]
    if bits:
        return [_result("button-tiers", "WARN", " | ".join(bits))]
    return [_result("button-tiers", "PASS",
                     f"{n_btn} button rule(s) conform to the tier vocabulary")]


def _check_theme_toggle(ctx: _ContractsCtx) -> List[dict]:
    # 12. user-selectable theme — pre-paint data-theme boot + persisted .theme
    #     toggle + dual scheme-gated theme-color metas (design.md Colors "Theme
    #     switching"; fleet-config#290). Grep-level only — whether the glyph
    #     shows the action stays LLM judgment in /design-sync step 4. Both
    #     stamp idioms (dataset.theme / setAttribute) and both key shapes
    #     (`.theme` literal / a theme-named constant) are canonical.
    index_files, markup_all = ctx.index_files, ctx.markup_all
    spec_light, spec_dark = ctx.spec_light, ctx.spec_dark
    if not index_files:
        return [_result("theme-toggle", "NA", "no index.html found")]
    stamp_re = r"dataset\.theme|setAttribute\(\s*['\"]data-theme['\"]"
    toggle_re = (r"localStorage\.setItem\(\s*"
                 r"(?:['\"][^'\"]*\.theme['\"]|\w*theme\w*\s*,)")
    missing_boot: List[str] = []
    meta_gaps: List[str] = []
    for p in index_files:
        text = strip_comments(read_text(p), "html")
        body_at = text.lower().find("<body")
        head = text[:body_at] if body_at >= 0 else text
        boot = (re.search(stamp_re, head)
                and re.search(r"localStorage\.getItem\(\s*['\"][^'\"]*\.theme['\"]", head)
                and "prefers-color-scheme" in head)
        if not boot:
            missing_boot.append(rel(ctx.root, p))
            continue
        light_meta = dark_meta = None
        for mm in re.finditer(r"<meta\b[^>]*name=[\"']theme-color[\"'][^>]*>",
                              text, re.I):
            tag = mm.group(0)
            media = re.search(r"media=[\"']([^\"']*)[\"']", tag)
            content = re.search(r"content=[\"']([^\"']*)[\"']", tag)
            if not media or "prefers-color-scheme" not in media.group(1):
                continue
            if "light" in media.group(1):
                light_meta = content.group(1) if content else ""
            elif "dark" in media.group(1):
                dark_meta = content.group(1) if content else ""
        if light_meta is None or dark_meta is None:
            meta_gaps.append(f"{rel(ctx.root, p)} (missing the scheme-gated "
                             "theme-color meta pair)")
            continue
        want_light = normalize_value(spec_light.get("colors.canvas", ""))
        want_dark = normalize_value((spec_dark or {}).get("colors.canvas", ""))
        if want_light and normalize_value(light_meta) != want_light:
            meta_gaps.append(f"{rel(ctx.root, p)} (light theme-color "
                             f"{light_meta} != spec canvas {want_light})")
        if want_dark and normalize_value(dark_meta) != want_dark:
            meta_gaps.append(f"{rel(ctx.root, p)} (dark theme-color "
                             f"{dark_meta} != spec dark canvas {want_dark})")
    if missing_boot:
        return [_result("theme-toggle", "FAIL",
                         "no pre-paint data-theme boot script in <head> (localStorage "
                         ".theme key + prefers-color-scheme fallback — design.md Colors "
                         "theme switching, fleet-config#290): " + ", ".join(missing_boot))]
    if not re.search(toggle_re, markup_all, re.I):
        return [_result("theme-toggle", "FAIL",
                         "boot script present but no persisted theme toggle — no "
                         "localStorage setItem on a .theme key (or theme-named constant) "
                         "anywhere (design.md Colors theme switching, fleet-config#290)")]
    if meta_gaps:
        return [_result("theme-toggle", "WARN",
                         "theme mechanism present but the theme-color metas drift: "
                         + "; ".join(meta_gaps))]
    return [_result("theme-toggle", "PASS",
                     "pre-paint boot + persisted .theme toggle + dual theme-color "
                     f"metas on {len(index_files)} index.html",
                     _evidence(markup_all, toggle_re, re.I))]


def _check_chevron_placement(ctx: _ContractsCtx) -> List[dict]:
    # 14. chevron placement — disclosure summaries pin the chevron right,
    #     never a leading arrow (design.md disclosure.chevron: right;
    #     app-launcher#362 shipped a leading `›` the lint never caught).
    markup_all = ctx.markup_all
    leading_chevrons: List[str] = []
    trailing_ok = 0
    for sm in re.finditer(r"<summary\b[^>]*>(.*?)</summary>", markup_all, re.S | re.I):
        content = sm.group(1)
        chevron_m = (re.search(r'class=["\'][^"\']*chevron[^"\']*["\']', content, re.I)
                     or re.search(r"[›⌄▾▸❱➤]", content))
        if not chevron_m:
            continue
        title_m = None
        for tm in re.finditer(r">([^<>]*[A-Za-z0-9][^<>]*)<", content):
            text_run = tm.group(1)
            if (re.fullmatch(r"[\s›⌄▾▸❱➤]*", text_run)):
                continue  # the chevron glyph's own text run
            title_m = tm
            break
        if title_m is None:
            continue
        if chevron_m.start() < title_m.start():
            leading_chevrons.append(_loc_at(markup_all, sm.start(1) + chevron_m.start()))
        else:
            trailing_ok += 1
    if leading_chevrons:
        return [_result("chevron-placement", "FAIL",
                         f"{len(leading_chevrons)} disclosure(s) with a leading chevron "
                         "(design.md disclosure.chevron: right — never a leading arrow): "
                         + ", ".join(leading_chevrons[:6]))]
    if trailing_ok:
        return [_result("chevron-placement", "PASS",
                         f"chevron right-pinned on {trailing_ok} disclosure(s)")]
    return [_result("chevron-placement", "NA", "no chevron-bearing disclosures found")]


def _check_row_height_scale(ctx: _ContractsCtx) -> List[dict]:
    # 15. row-height scale — repeating list/action-rail rows draw their
    #     height from the 3-step scale (rows.sm 44px / rows.md 52px /
    #     rows.lg 60px), not an ad hoc literal (design.md Components
    #     list-row; app-launcher#365/PR#380).
    css_all, spec_light = ctx.css_all, ctx.spec_light
    allowed_row_px = set()
    for key, val in spec_light.items():
        if key.startswith("rows.") and val.endswith("px"):
            allowed_row_px.add(val)
    if not allowed_row_px:
        allowed_row_px = {"44px", "52px", "60px"}
    row_strays: Dict[str, int] = {}
    n_row_rules = 0
    for bm in _BLOCK_RE.finditer(css_all):
        selector = bm.group(1)
        sel_low = selector.lower()
        if "row" not in sel_low and "action-rail" not in sel_low:
            continue
        n_row_rules += 1
        for dm in re.finditer(r"\b(?:min-height|height):\s*([^;]+);", bm.group(2)):
            val = dm.group(1).strip()
            if val.startswith("var(") or "calc(" in val:
                continue
            if not re.fullmatch(r"\d+px", val):
                continue
            if val not in allowed_row_px:
                row_strays[val] = row_strays.get(val, 0) + 1
    if row_strays:
        top = ", ".join(f"{k}x{v}" for k, v in sorted(row_strays.items(), key=lambda kv: -kv[1])[:8])
        return [_result("row-height-scale", "WARN",
                         f"row/action-rail heights outside the {', '.join(sorted(allowed_row_px))} "
                         f"scale: {top}")]
    if n_row_rules:
        return [_result("row-height-scale", "PASS",
                         f"all fixed row/action-rail heights on the {', '.join(sorted(allowed_row_px))} scale")]
    return [_result("row-height-scale", "NA", "no row/action-rail height rules found")]
