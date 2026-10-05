"""Component contracts for transient feedback: the neutral frosted toast.

design.md's `toast` is the nav-bar's glass; only a real error tints, a success
never does (fleet-config#1200). The rendered twin is `/design-review`'s
COLOR-05, which can only judge a toast that happens to be on screen; this scan
reads the app's own CSS, so it decides without one.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..colormath import is_green, parse_color
from ..css import _ANY_DECL_RE, _BLOCK_RE
from ._ctx import _ContractsCtx, _loc_at, _result

_TOAST_SEL_RE = re.compile(r"toast|snackbar", re.I)
# A status colour a toast must not wear: the fleet's own success/on tokens (and the usual green aliases).
_GREEN_VAR_RE = re.compile(r"var\(\s*--(?:success|on|ok|good|green)\b", re.I)
_HEX_RE = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{6})\b", re.I)
_RGB_RE = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})", re.I)
_TINT_PROPS = ("background", "background-color", "border", "border-color", "border-left", "border-left-color",
               "border-top", "border-top-color", "box-shadow", "outline", "outline-color")


def _green_in(value: str) -> Optional[str]:
    """The green token or literal a declaration value draws with, else None."""
    var = _GREEN_VAR_RE.search(value)
    if var:
        return var.group(0)
    for m in _HEX_RE.finditer(value):
        if is_green(parse_color(m.group(0), {})):  # the located token is exactly #rgb / #rrggbb, so it always parses
            return m.group(0)
    # Kept local: unlike parse_color it reads space-separated `rgb(0 200 0 / 50%)`, a value cut before
    # its `)`, and clamps channels over 255.
    for m in _RGB_RE.finditer(value):
        r, g, b = (min(255, int(x)) for x in m.groups())
        if is_green((r, g, b, 1.0)):
            return m.group(0)
    return None


def _check_toast_neutral(ctx: _ContractsCtx) -> List[dict]:
    css = ctx.css_own
    seen, hits = False, []
    for block in _BLOCK_RE.finditer(css):
        selector, body = block.group(1).strip(), block.group(2)
        if "@" in selector or not _TOAST_SEL_RE.search(selector):
            continue
        seen = True
        for decl in _ANY_DECL_RE.finditer(body):
            prop, value = decl.group(1).lower(), decl.group(2)
            green = _green_in(value) if prop in _TINT_PROPS else None
            if green:
                hits.append((block.start(1) + (len(block.group(1)) - len(block.group(1).lstrip())),
                             f"{selector.splitlines()[-1].strip()[:50]} {prop}: {green}"))
                break
    if not seen:
        return [_result("toast-neutral", "NA", "no toast rule found (app may not ship a toast)")]
    if hits:
        pos, what = hits[0]
        return [_result("toast-neutral", "FAIL",
                        f"a toast is drawn green ({what}) — design.md's toast is the neutral frosted surface, only an error tints",
                        _loc_at(css, pos))]
    return [_result("toast-neutral", "PASS", "no toast rule draws a green (success) background or border")]
