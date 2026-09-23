"""Typography contracts (fleet-config#969).

Form controls inheriting the body font, uppercase only through the spec's
legal caps role, and `word-break: break-all` only on opaque strings. All three
WARN, never FAIL: they landed after the apps they flag, which fix them in
their own lanes.
"""
from __future__ import annotations

import re
from typing import List

from ..css import _ANY_DECL_RE, _BLOCK_RE
from ..selectors import selector_group
from ._ctx import _ContractsCtx, _loc_at, _result

_FORM_TAGS = ("button", "input", "select", "textarea")
# selectors where break-all is right: opaque strings with no word boundaries
_BREAK_ALL_OK_RE = re.compile(r"path|url|sha|hash|mono|code|token|uuid", re.I)


def _rules(css: str):
    """(selector list, {prop: value}, match start) for every innermost rule."""
    for bm in _BLOCK_RE.finditer(css):
        decls = {p.lower(): v.strip() for p, v in _ANY_DECL_RE.findall(bm.group(2) + ";")}
        yield selector_group(bm.group(1)), decls, bm.start(2)


def _check_form_font_inherit(ctx: _ContractsCtx) -> List[dict]:
    # 25. form controls inherit the body font — UA stylesheets give
    #     button/input/select/textarea their own font, so without a global
    #     `font: inherit` they render in the platform default (Arial on Windows
    #     Chrome). The vendored scaffold base ships the rule
    #     (project-scaffolding#266), so css_all — vendored included — counts.
    if not ctx.css_all.strip():
        return [_result("form-font-inherit", "NA", "no CSS found")]
    covered: set = set()
    where = None
    for sel, decls, pos in _rules(ctx.css_all):
        inherits = (decls.get("font", "").startswith("inherit")
                    or decls.get("font-family", "") == "inherit")
        if not inherits:
            continue
        # a bare element selector (optionally in a list) — `.card button` is
        # one control's fix, not the global reset
        for part in re.split(r"\s*,\s*", sel):
            tag = (part.strip().splitlines() or [""])[-1].strip().lower()
            if tag in _FORM_TAGS:
                covered.add(tag)
                where = where or _loc_at(ctx.css_all, pos)
    missing = [t for t in _FORM_TAGS if t not in covered]
    if missing:
        return [_result("form-font-inherit", "WARN",
                         "form controls keep the UA font — no global `font: inherit` "
                         f"(or `font-family: inherit`) for: {', '.join(missing)} "
                         "(design.md typography; project-scaffolding#266 base rule)", where)]
    return [_result("form-font-inherit", "PASS",
                     "button, input, select and textarea inherit the body font", where)]


def _check_uppercase_role(ctx: _ContractsCtx) -> List[dict]:
    # 26. caps only through the spec's legal role — design.md Typography
    #     makes `overline` (list group headers) the one uppercase use
    #     (fleet-config#964). Any other `text-transform: uppercase` WARNs.
    #     App-authored CSS only: a bundled library's labels aren't the app's.
    legal = [k.split(".")[1] for k, v in ctx.spec_light.items()
             if k.startswith("typography.") and k.endswith(".textTransform") and v == "uppercase"]
    stray: List[str] = []
    first = None
    for sel, decls, pos in _rules(ctx.css_own):
        if decls.get("text-transform", "").lower() != "uppercase":
            continue
        if legal and any(role in sel for role in legal):
            continue
        stray.append(sel.splitlines()[-1].strip()[:60])
        first = first or _loc_at(ctx.css_own, pos)
    if stray:
        allowed = ", ".join(legal) if legal else "none — the spec legalizes no caps role"
        return [_result("uppercase-role", "WARN",
                         f"{len(stray)} rule(s) uppercase text outside the legal caps role "
                         f"({allowed}); chips are sentence case and units are never "
                         "transformed (design.md Typography): " + "; ".join(stray[:6]), first)]
    return [_result("uppercase-role", "PASS", "no uppercase text outside the legal caps role")]


def _check_break_all(ctx: _ContractsCtx) -> List[dict]:
    # 27. break-all only on opaque strings — `word-break: break-all` splits a
    #     name mid-word ("Accou|nting"); names want `overflow-wrap: anywhere`.
    #     A path/hash/URL/code selector is the legitimate case.
    stray: List[str] = []
    first = None
    for sel, decls, pos in _rules(ctx.css_own):
        if decls.get("word-break", "").lower() != "break-all":
            continue
        if _BREAK_ALL_OK_RE.search(sel):
            continue
        stray.append(sel.splitlines()[-1].strip()[:60])
        first = first or _loc_at(ctx.css_own, pos)
    if stray:
        return [_result("break-all", "WARN",
                         f"{len(stray)} rule(s) use word-break: break-all on text that is not "
                         "a path, hash or URL; use overflow-wrap: anywhere so names never "
                         "break mid-word: " + "; ".join(stray[:6]), first)]
    return [_result("break-all", "PASS", "break-all only on path/hash/URL/code selectors")]
