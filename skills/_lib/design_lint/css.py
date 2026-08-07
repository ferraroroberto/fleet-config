"""CSS/JS/HTML source primitives shared by every lens.

Comment stripping that preserves line numbers, the `@media` flattener, custom
property extraction per theme, value normalization, and the two declaration
regexes that both the `adoption` lens and the button-tier contract read. Kept
newline-neutral throughout: every lens reports `file:line`, so a transform that
drops a newline silently moves every finding below it.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


def strip_comments(text: str, kind: str, *,
                    blank_regex_literals: bool = False) -> str:
    """Blank out comments, preserving newlines so file:line stays correct.

    Without this, a `/* ... @media (color-gamut: p3) ... */` prose comment
    makes the media scanner swallow the following `:root` block (the bug this
    fixed against home-automation's real stylesheet).

    `blank_regex_literals` (js only) additionally drops regex literals. It is
    opt-in, for the scanners that read JS source as *rendered output* — a
    regex's character class is input matching, the exact opposite of UI copy
    (#416). Every other caller wants the pattern text left alone.
    """
    def nl(m: "re.Match[str]") -> str:
        return "\n" * m.group(0).count("\n")

    if kind == "css":
        return re.sub(r"/\*.*?\*/", nl, text, flags=re.S)
    if kind == "js":
        return _strip_js_comments(text, blank_regex_literals=blank_regex_literals)
    if kind == "html":
        return re.sub(r"<!--.*?-->", nl, text, flags=re.S)
    return text


_REGEX_DISALLOWED_PRECEDING = frozenset("_$)]")


def _regex_literal_ok(last_sig: Optional[str]) -> bool:
    """True when a bare `/` at this point plausibly opens a regex literal
    rather than being a division operator — the standard lexer heuristic:
    only an identifier/`)`/`]` (a value just produced) means division."""
    if last_sig is None:
        return True
    return not (last_sig.isalnum() or last_sig in _REGEX_DISALLOWED_PRECEDING)


def _strip_js_comments(text: str, *, blank_regex_literals: bool = False) -> str:
    """Blank out `//` line comments and `/* */` block comments, tracking
    string/template-literal state so a `//` inside a URL or quoted string
    (e.g. `'see https://example.com'`) isn't mistaken for a comment opener
    (fleet-config#394 — a bare per-line regex was over-flagging emoji sites
    inside `//` comments as rendered-text glyphs).

    Regex literals (`/[^`]/g`) are skipped wholesale rather than fed through
    the quote/backtick tracker above — a quote or backtick inside a regex's
    character class would otherwise open a phantom string that swallows
    every real comment for the rest of the file. `blank_regex_literals` also
    drops the literal from the output, for callers reading the source as
    rendered text (#416).
    """
    out: List[str] = []
    i, n = 0, len(text)
    in_str: Optional[str] = None
    last_sig: Optional[str] = None
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
                last_sig = "x"
            i += 1
            continue
        if c in ("'", '"', "`"):
            in_str = c
            out.append(c)
            i += 1
            continue
        if c == "/" and text[i + 1:i + 2] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and text[i + 1:i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("\n" * text.count("\n", i, end))
            i = end
            continue
        if c == "/" and _regex_literal_ok(last_sig):
            j, in_class, closed = i + 1, False, False
            while j < n:
                cj = text[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "\n":
                    break
                if cj == "[":
                    in_class = True
                elif cj == "]":
                    in_class = False
                elif cj == "/" and not in_class:
                    closed = True
                    j += 1
                    break
                j += 1
            if closed:
                while j < n and text[j].isalpha():
                    j += 1
                # A regex literal never spans lines (the scan above breaks on
                # "\n"), so dropping one is newline-neutral and file:line
                # stays exact — the invariant find_emoji_sites relies on.
                out.append("" if blank_regex_literals else text[i:j])
                i = j
                last_sig = "x"
                continue
        out.append(c)
        if not c.isspace():
            last_sig = c
        i += 1
    return "".join(out)


_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);")

DARK_SELECTOR_HINTS = ('[data-theme="dark"]', "[data-theme='dark']", ".theme-dark", ".dark")


def _flatten_media(css: str) -> List[Tuple[str, str]]:
    """Yield (media_condition, inner_css) with '' for top level.

    One nesting level of @media is enough for the fleet's stylesheets.
    """
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(css):
        m = re.search(r"@media\s*([^{]+)\{", css[i:])
        if not m:
            out.append(("", css[i:]))
            break
        out.append(("", css[i:i + m.start()]))
        # find the matching close brace for the media block
        depth = 1
        j = i + m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1).strip(), css[i + m.end():j - 1]))
        i = j
    return out


def parse_custom_props(css: str, fname: str) -> Dict[str, Dict[str, Tuple[str, int]]]:
    """Extract custom-property definitions per theme.

    Returns {'light': {name: (value, line)}, 'dark': {...}}. P3 wide-gamut
    `@media (color-gamut: p3)` twins are excluded (they would clobber the sRGB
    values the spec comparison targets).
    """
    css = strip_comments(css, "css")
    themes: Dict[str, Dict[str, Tuple[str, int]]] = {"light": {}, "dark": {}}
    for media, chunk_css in _flatten_media(css):
        if "color-gamut" in media:
            continue
        media_dark = "prefers-color-scheme" in media and "dark" in media
        for bm in _BLOCK_RE.finditer(chunk_css):
            selector = bm.group(1).strip().splitlines()[-1].strip()
            body = bm.group(2)
            sel_dark = any(h in selector for h in DARK_SELECTOR_HINTS)
            is_root = ":root" in selector or selector == "html"
            if not (is_root or sel_dark):
                continue
            theme = "dark" if (sel_dark or media_dark) else "light"
            # line numbers: count newlines up to the declaration
            base = css.find(body)
            for dm in _DECL_RE.finditer(body):
                line = css[: base + dm.start()].count("\n") + 1 if base >= 0 else 0
                themes[theme][dm.group(1)] = (dm.group(2).strip(), line)
    return themes


def normalize_value(v: str) -> str:
    v = v.strip().lower()
    v = re.sub(r"\s+", " ", v)
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        v = "#" + "".join(ch * 2 for ch in m.group(1))
    return v


# Declaration-level primitives. Shared: the `adoption` lens measures every
# declaration with them, and the button-tier contract re-reads button rules
# through the same two patterns — one definition, not two that can drift.
_COLOR_LITERAL_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|\boklab\(")

_ANY_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+);")
