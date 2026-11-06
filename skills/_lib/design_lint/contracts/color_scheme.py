"""Theme-following `color-scheme` contract (design.md Colors, theme switching).

Native UI (a `<select>` option list, date pickers, scrollbars) is drawn from
`color-scheme`. `light dark` follows the OS, so an app whose user chose the dark
theme on a light OS drew those controls light. The page must resolve
`color-scheme` from the chosen `data-theme`: `:root[data-theme="light"]` is
`light` and `:root[data-theme="dark"]` is `dark`; the vendored `base/base.css`
ships exactly these rules. A declaration inside `@media (prefers-color-scheme)`
follows the OS again, so it never counts as following the theme.

An app that declares no `color-scheme` at all is NA: this contract judges a
declaration that ignores the theme, not the absence of one.
"""
from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from ..css import strip_comments
from ..files import is_third_party, read_text, rel
from ._ctx import _ContractsCtx, _loc_at, _result

_THEMES = ("light", "dark")
_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_RE = re.compile(r"(?<![\w-])color-scheme\s*:\s*([^;}]+)", re.I)
_THEME_ATTR_RE = re.compile(r"""\[\s*data-theme\s*=\s*["']?(light|dark)["']?\s*\]""", re.I)
_OS_MEDIA_RE = re.compile(r"@media[^{]*prefers-color-scheme[^{]*\{", re.I)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_FILE_MARK_RE = re.compile(r"/\*FILE [^*]*\*/")


def _os_media_spans(css: str) -> List[Tuple[int, int]]:
    """Character spans of every `@media (prefers-color-scheme: ...) { ... }` body."""
    spans: List[Tuple[int, int]] = []
    for m in _OS_MEDIA_RE.finditer(css):
        depth, j = 1, m.end()
        while j < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        spans.append((m.end(), j))
    return spans


def _inline_styles(ctx: _ContractsCtx) -> str:
    """The app's own `<style>` blocks, shaped like `css_own` so `_loc_at` reports the HTML line."""
    out: List[str] = []
    for path in ctx.html_files:
        if is_third_party(path):
            continue
        text = read_text(path)
        for m in _STYLE_RE.finditer(text):
            body = strip_comments(m.group(1), "css")
            if _DECL_RE.search(body):
                # Leading newlines put the declaration on its real line in the HTML file.
                out.append(f"/*FILE {rel(ctx.root, path)}*/\n" + "\n" * text.count("\n", 0, m.start(1)) + body)
    return "\n".join(out)


def _check_color_scheme_follows_theme(ctx: _ContractsCtx) -> List[dict]:
    css = ctx.css_own + "\n" + _inline_styles(ctx)
    spans = _os_media_spans(css)
    covered: Dict[str, str] = {}      # theme -> where an explicit rule or single-scheme base sets it
    declared: List[str] = []          # every color-scheme declaration site, file:line
    for m in _BLOCK_RE.finditer(css):
        d = _DECL_RE.search(m.group(2))
        if not d:
            continue
        where = _loc_at(css, m.start(2) + d.start())
        declared.append(where)
        tokens: Set[str] = set(d.group(1).lower().split())
        selector = _FILE_MARK_RE.sub("", m.group(1))
        in_os_media = any(a <= m.start(2) < b for a, b in spans)
        attrs = {t.lower() for t in _THEME_ATTR_RE.findall(selector)}
        if attrs:
            # `[data-theme="dark"] select { color-scheme: dark }` is as valid as the :root rule.
            for theme in attrs:
                if tokens == {theme} and not in_os_media:
                    covered.setdefault(theme, where)
        elif not in_os_media and len(tokens) == 1 and tokens <= set(_THEMES):
            # A bare `:root { color-scheme: light }` is the light theme's rule: it never reads the OS.
            covered.setdefault(next(iter(tokens)), where)
    if not declared:
        return [_result("color-scheme-theme", "NA", "no color-scheme declared")]
    missing = [t for t in _THEMES if t not in covered]
    if missing:
        rules = ", ".join(f':root[data-theme="{t}"] {{ color-scheme: {t}; }}' for t in missing)
        return [_result("color-scheme-theme", "FAIL",
            "color-scheme follows the OS, not the theme the user chose, so native controls "
            f"(select list, date picker, scrollbar) draw in the wrong theme; add {rules} "
            "(design.md Colors, theme switching) or vendor base/base.css, which ships them",
            declared[0])]
    return [_result("color-scheme-theme", "PASS",
        "color-scheme resolves from data-theme for both themes", covered[_THEMES[0]])]
