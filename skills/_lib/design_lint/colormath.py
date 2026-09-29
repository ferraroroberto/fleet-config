"""Colour maths shared by `design_lint` and `design_review` (fleet-config#1065).

Parsing a spec colour string, compositing it over a background, and scoring
WCAG contrast is needed on both sides of the `design_lint` / `design_review`
boundary: `design_lint/contracts/a11y.py`'s `spec-contrast` check (a static
lens over `design.md` alone) and `design_review/mockups.py`'s now-vs-proposed
swatches (rendered review) both want it, and `design_review/evaluate.py`'s
`spec.*` rules are what originated it. `design_review` legitimately depends on
`design_lint` (`rubric.py` already reaches into `design_lint/spec.py` the same
way), but the reverse is a layering inversion: it used to live in
`evaluate.py` and reach back UP into `design_review` via a runtime
`sys.path` insert + call-time import, which meant importing this one function
executed the whole rendered-review package (`design_review/__init__` pulling
in `cli`, `capture`, `fleet`, `report`, ...) just to get colour maths. Living
here, as a stdlib leaf with no dependency on either package, it can be
imported normally (module level, no cycle) from both directions.

stdlib only.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

AA_NORMAL = 4.5
AA_LARGE = 3.0

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$")
_MIX_RE = re.compile(r"^color-mix\(\s*in\s+srgb\s*,\s*var\(--([A-Za-z0-9-]+)\)\s+(\d+(?:\.\d+)?)%\s*,\s*transparent\s*\)$")
_VAR_RE = re.compile(r"^var\(--([A-Za-z0-9-]+)\)$")

RGBA = Tuple[float, float, float, float]


def parse_color(value: str, tokens: Dict[str, str], depth: int = 0) -> Optional[RGBA]:
    """A spec colour string -> (r, g, b, a) in 0..255 / 0..1, or None if unknown.

    Understands hex, `rgb()`/`rgba()`, `transparent`, `var(--name)` (resolved
    through `colors.<name>`), and the spec's own `color-mix(in srgb,
    var(--accent) 16%, transparent)` derivative form.
    """
    if depth > 4 or value is None:
        return None
    v = str(value).strip()
    if v == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    m = _HEX_RE.match(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = _RGB_RE.match(v)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4) or 1))
    m = _VAR_RE.match(v)
    if m:
        return parse_color(tokens.get(f"colors.{m.group(1)}", ""), tokens, depth + 1)
    m = _MIX_RE.match(v)
    if m:
        base = parse_color(tokens.get(f"colors.{m.group(1)}", ""), tokens, depth + 1)
        if base is None:
            return None
        return (base[0], base[1], base[2], float(m.group(2)) / 100.0)
    return None


def composite(fg: RGBA, bg: RGBA) -> RGBA:
    a = fg[3]
    return (fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1.0)


def luminance(c: RGBA) -> float:
    def f(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def contrast(a: RGBA, b: RGBA) -> float:
    l1, l2 = luminance(a), luminance(b)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def _hex(c: RGBA) -> str:
    return "#" + "".join(f"{int(round(x)):02x}" for x in c[:3])


def spec_pairs(tokens: Dict[str, str]) -> List[Dict[str, object]]:
    """Every `components.<name>` with both `textColor` and `backgroundColor`,
    composited over `colors.card`, with its WCAG ratio and AA threshold."""
    card = parse_color(tokens.get("colors.card", "#ffffff"), tokens) or (255.0, 255.0, 255.0, 1.0)
    out: List[Dict[str, object]] = []
    names = sorted({k.split(".")[1] for k in tokens if k.startswith("components.") and k.count(".") >= 2})
    for name in names:
        fg_raw = tokens.get(f"components.{name}.textColor")
        bg_raw = tokens.get(f"components.{name}.backgroundColor")
        if not fg_raw or not bg_raw:
            continue
        fg = parse_color(fg_raw, tokens)
        bg = parse_color(bg_raw, tokens)
        if fg is None or bg is None:
            out.append({"component": name, "fg": fg_raw, "bg": bg_raw, "ratio": None, "threshold": AA_NORMAL})
            continue
        bg_c = composite(bg, card)
        fg_c = composite(fg, bg_c)
        typo = tokens.get(f"components.{name}.typography", "")
        large = "heading" in typo
        out.append({"component": name, "fg": _hex(fg_c), "bg": _hex(bg_c),
                    "ratio": round(contrast(fg_c, bg_c), 2), "threshold": AA_LARGE if large else AA_NORMAL})
    return out
