"""Dark control-fill contract (design.md Colors, dark theme).

A filled control, chip or badge is lighter than the card it sits on. In the dark
palette the neutral fills step *up* from the card (`--card` `#161b22`, control
fill `#21262d`); a fill that steps *down* to the canvas (`#0d1117`) or below
(`#010409`) reads as a hole punched in the card instead of a control on it. Two
apps shipped exactly that, so this is checked here, from the CSS alone.

Dark custom properties resolve through `var()` chains (a dark override first,
then the light `:root` value the dark theme inherits) to a colour, which is
compared to the dark `--card` by relative luminance, after compositing over it.

- `dark-fill-token`: the control fill tokens (`--input-bg`, `--card-2`) resolve
  to a colour darker than the dark `--card`.
- `dark-fill-selector`: a rule whose rightmost compound names a control (`button`,
  `input`, `select`, `textarea`, `.chip`, `.pill`, `.badge`, `.button`, `.button-*`)
  paints a `background` that resolves, in dark, darker than `--card`.
- `dark-track-fill`: the same test for a recessed track (`meter`, `track`,
  `progress`), as a WARN, since a recessed track can be intentional.

A selector keeps only its last unconditional background, in source order, with a
dark-scoped rule folded onto the bare selector it overrides; a rule with a
pseudo-class or pseudo-element is skipped, so a `:hover` shade is not judged
against the resting card. A rule that only applies to the light theme is skipped.
A deliberate recess is declared in the repo's `[[design.accepted]]`.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..colormath import RGBA, composite, luminance, parse_color
from ..css import _BLOCK_RE, DARK_SELECTOR_HINTS
from ..css import _DECL_RE as _CUSTOM_DECL_RE
from ..selectors import _compounds, _split_top_level_commas, selector_group
from ._ctx import _ContractsCtx, _loc_at, _result
from .color_scheme import _os_media_spans

# A fill this close to the card is the same surface, not a recess.
_EPSILON = 1e-4
_TOKEN_FILLS = ("--input-bg", "--card-2")
_CARD_TOKENS = ("--card", "--surface")
_TEXT_TOKENS = ("--ink", "--fg", "--text")

_CONTROL_RE = re.compile(
    r"^(?:button|input|select|textarea)$"
    r"|\.(?:chip|pill|badge|button(?:-[\w-]+)?)$")
_TRACK_RE = re.compile(r"meter|track|progress")

_BACKGROUND_RE = re.compile(r"(?<![\w-])background(?:-color)?\s*:\s*([^;}]+)")
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,(.*))?\)", re.S)
_MIX_RE = re.compile(r"color-mix\(\s*in\s+srgb\s*,\s*(.+?)\s+(\d+(?:\.\d+)?)%\s*,\s*(.+?)\s*\)")
_LIGHT_MEDIA_RE = re.compile(r"@media[^{]*prefers-color-scheme\s*:\s*light[^{]*\{", re.I)
_DARK_MEDIA_RE = re.compile(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{", re.I)
_GAMUT_MEDIA_RE = re.compile(r"@media[^{]*color-gamut[^{]*\{", re.I)
_LIGHT_ATTR_RE = re.compile(r"""\[\s*data-theme\s*=\s*["']?light["']?\s*\]""", re.I)
_NOT_LIGHT_RE = re.compile(r""":not\(\s*\[\s*data-theme\s*=\s*["']?light["']?\s*\]\s*\)""", re.I)


def _resolve(value: str, props: Dict[str, str], depth: int = 0) -> Optional[RGBA]:
    """A CSS colour value to RGBA, following `var()` chains through `props`; None when not static."""
    if depth > 8:
        return None
    v = re.sub(r"\s*!important\s*$", "", value.strip())
    if v == "none":  # `background: none` paints nothing, the same as `transparent`
        return (0.0, 0.0, 0.0, 0.0)
    if v.lower() == "currentcolor":  # the inherited text colour, modelled as the theme's own text token
        return next((c for c in (_resolve(f"var({t})", props, depth + 1) for t in _TEXT_TOKENS)
                     if c is not None), None)
    m = _VAR_RE.fullmatch(v)
    if m:
        raw = props.get(m.group(1))
        got = _resolve(raw, props, depth + 1) if raw is not None else None
        if got is None and m.group(2):
            got = _resolve(m.group(2), props, depth + 1)
        return got
    m = _MIX_RE.fullmatch(v)
    if m:
        base = _resolve(m.group(1), props, depth + 1)
        other = _resolve(m.group(3), props, depth + 1)
        if base is None or other is None:
            return None
        share = float(m.group(2)) / 100.0
        if other[3] == 0.0:  # a tint: the colour at `share` opacity
            return (base[0], base[1], base[2], base[3] * share)
        if base[3] == 1.0 and other[3] == 1.0:
            return tuple(base[i] * share + other[i] * (1 - share) for i in range(3)) + (1.0,)  # type: ignore[return-value]
        return None
    return parse_color(v, {})


def _first_colour(value: str, props: Dict[str, str]) -> Optional[RGBA]:
    """The colour of a `background` value, which may be a shorthand (`var(--x) url(...) no-repeat`)."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in value.strip():
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch.isspace() and depth == 0:
            if buf:
                parts.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    for part in parts:
        got = _resolve(part, props)
        if got is not None:
            return got
    return None


def _darker(fill: RGBA, card: RGBA) -> bool:
    return luminance(composite(fill, card)) < luminance(card) - _EPSILON


def _hex(c: RGBA) -> str:
    return "#" + "".join(f"{int(round(x)):02x}" for x in c[:3])


def _root_theme(selector: str) -> Optional[str]:
    """`dark` or `light` when `selector` is a theme root and nothing narrower, else None."""
    if _selector_key(selector) == "":
        return "dark" if any(h in selector for h in DARK_SELECTOR_HINTS) else "light"
    if _selector_key(_LIGHT_ATTR_RE.sub(" ", selector)) == "":
        return "light"
    return None


def _root_props(css: str) -> Dict[str, Dict[str, Tuple[str, int]]]:
    """Theme-level custom properties: `{theme: {name: (value, blob offset)}}`.

    Only a rule that is the theme root itself counts (`:root`, `html`, `:root[data-theme="dark"]`,
    a `prefers-color-scheme: dark` root). A property a component redeclares for its own subtree
    (`[data-theme="dark"] .board-modes { --card: ... }`) does not change the theme's card."""
    dark_media = _os_media_spans(css, _DARK_MEDIA_RE)
    gamut_media = _os_media_spans(css, _GAMUT_MEDIA_RE)
    out: Dict[str, Dict[str, Tuple[str, int]]] = {"light": {}, "dark": {}}
    for bm in _BLOCK_RE.finditer(css):
        if any(a <= bm.start(2) < b for a, b in gamut_media):
            continue  # a wide-gamut twin would clobber the sRGB value
        themes = [_root_theme(s) for s in _split_top_level_commas(selector_group(bm.group(1)))]
        if not themes or None in themes:
            continue
        dark = any(a <= bm.start(2) < b for a, b in dark_media) or themes[0] == "dark"
        for dm in _CUSTOM_DECL_RE.finditer(bm.group(2)):
            out["dark" if dark else "light"][dm.group(1)] = (dm.group(2).strip(), bm.start(2) + dm.start())
    return out


def _dark_context(ctx: _ContractsCtx) -> Optional[Tuple[Dict[str, str], Dict[str, Tuple[str, int]], RGBA]]:
    """`(dark props, dark declarations with their blob offset, resolved dark card)`, or None when
    the app has no dark palette or no resolvable dark `--card`."""
    themes = _root_props(ctx.css_own)
    if not themes["dark"]:
        return None
    decls = {**themes["light"], **themes["dark"]}
    props = {name: value for name, (value, _pos) in decls.items()}
    for token in _CARD_TOKENS:
        card = _resolve(f"var({token})", props) if token in props else None
        if card is not None:
            return props, decls, (card[0], card[1], card[2], 1.0)
    return None


def _token_findings(ctx: _ContractsCtx, props: Dict[str, str], decls: Dict[str, Tuple[str, int]],
                    card: RGBA) -> Tuple[List[str], List[str], int]:
    """`(darker-than-card tokens, unresolved tokens, tokens measured)`."""
    low: List[str] = []
    unknown: List[str] = []
    measured = 0
    for name in _TOKEN_FILLS:
        if name not in decls:
            continue
        where = _loc_at(ctx.css_own, decls[name][1])
        fill = _resolve(f"var({name})", props)
        if fill is None:
            unknown.append(f"{where} {name}")
            continue
        measured += 1
        if _darker(fill, card):
            low.append(f"{where} {name} {_hex(composite(fill, card))} < card {_hex(card)}")
    return low, unknown, measured


def _selector_key(selector: str) -> Optional[str]:
    """The selector with its dark scope removed, or None when it only applies to the light theme."""
    if _LIGHT_ATTR_RE.search(_NOT_LIGHT_RE.sub("", selector)):
        return None
    key = _NOT_LIGHT_RE.sub("", selector)
    for hint in DARK_SELECTOR_HINTS:
        key = key.replace(hint, " ")
    return re.sub(r"\s+", " ", re.sub(r"(?<![\w-])html(?![\w-])", " ", key.replace(":root", " "))).strip()


def _background_fills(ctx: _ContractsCtx, props: Dict[str, str]
                      ) -> Dict[str, Tuple[Optional[RGBA], int, str, bool]]:
    """Per control selector, its last background in source order:
    `{key: (colour or None if unresolved, blob offset, selector, is_track)}`."""
    light_spans = _os_media_spans(ctx.css_own, _LIGHT_MEDIA_RE)
    last: Dict[str, Tuple[Optional[RGBA], int, str, bool]] = {}
    for bm in _BLOCK_RE.finditer(ctx.css_own):
        if any(a <= bm.start(2) < b for a, b in light_spans):
            continue
        sel_line = selector_group(bm.group(1))
        if not sel_line or sel_line.startswith("@"):
            continue
        decls = _BACKGROUND_RE.findall(bm.group(2))
        if not decls:
            continue
        for sel in _split_top_level_commas(sel_line):
            key = _selector_key(sel)
            comps = _compounds(key) if key else []
            if not comps or ":" in comps[-1]:
                continue
            is_track = bool(_TRACK_RE.search(comps[-1]))
            if not (is_track or _CONTROL_RE.search(comps[-1])):
                continue
            # A custom property the rule declares for itself (`.chip { --tone: ...; background: ...var(--tone)... }`).
            local = {**props, **{m.group(1): m.group(2).strip() for m in _CUSTOM_DECL_RE.finditer(bm.group(2))}}
            last[key] = (_first_colour(decls[-1], local), bm.start(2), sel, is_track)
    return last


def _check_dark_control_fill(ctx: _ContractsCtx) -> List[dict]:
    dark = _dark_context(ctx)
    if dark is None:
        na = "no dark palette with a resolvable --card"
        return [_result("dark-fill-token", "NA", na), _result("dark-fill-selector", "NA", na),
                _result("dark-track-fill", "NA", na)]
    props, decls, card = dark
    return [_token_row(ctx, props, decls, card), *_selector_rows(ctx, props, card)]


def _token_row(ctx: _ContractsCtx, props: Dict[str, str], decls: Dict[str, Tuple[str, int]],
               card: RGBA) -> dict:
    low, unknown, measured = _token_findings(ctx, props, decls, card)
    if low:
        return _result("dark-fill-token", "FAIL",
            f"{len(low)} dark control fill token(s) darker than the dark card, so a filled control "
            "reads as a hole in its card instead of a control on it (design.md Colors, dark): "
            + "; ".join(low), low[0].split(" ")[0])
    if unknown:
        return _result("dark-fill-token", "WARN",
            "dark control fill token(s) not resolvable to a colour, so not measured against the card: "
            + "; ".join(unknown), unknown[0].split(" ")[0])
    if not measured:
        return _result("dark-fill-token", "NA", "no --input-bg or --card-2 defined")
    return _result("dark-fill-token", "PASS",
        f"{measured} dark control fill token(s) are lighter than the dark card {_hex(card)}")


def _selector_rows(ctx: _ContractsCtx, props: Dict[str, str], card: RGBA) -> List[dict]:
    fills = _background_fills(ctx, props)
    rows: List[dict] = []
    for check_id, track, summary in (
            ("dark-fill-selector", False,
             "control rule(s) fill darker than the dark card, so the control reads as a hole in "
             "its card (design.md Colors, dark)"),
            ("dark-track-fill", True,
             "track rule(s) fill darker than the dark card; a recessed track can be intentional, "
             "declare it in [[design.accepted]] if so")):
        mine = {k: v for k, v in fills.items() if v[3] is track}
        low, unknown, measured = [], [], 0
        for colour, pos, sel, _track in mine.values():
            where = _loc_at(ctx.css_own, pos)
            if colour is None:
                unknown.append(f"{where} {sel}")
                continue
            measured += 1
            if _darker(colour, card):
                low.append(f"{where} {sel} {_hex(composite(colour, card))}")
        if low:
            rows.append(_result(check_id, "WARN" if track else "FAIL",
                f"{len(low)} {summary}: " + "; ".join(low), low[0].split(" ")[0]))
        elif unknown:
            rows.append(_result(check_id, "WARN",
                f"{len(unknown)} rule(s) whose background is not statically resolvable, so not measured "
                f"against the dark card ({measured} measured): " + "; ".join(unknown[:6]),
                unknown[0].split(" ")[0]))
        elif measured:
            rows.append(_result(check_id, "PASS",
                f"{measured} {'track' if track else 'control'} background(s) are not darker than the dark card"))
        else:
            rows.append(_result(check_id, "NA", f"no {'track' if track else 'control'} background found"))
    return rows
