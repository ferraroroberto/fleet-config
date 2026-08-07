"""HTML/JS document scanning shared by the contract checks.

Structural facts a regex over one blob cannot answer: whether the app shell is
the standalone scroller, whether `nav.tabs` is nested inside `main.app`, where
rendered-text emoji glyphs actually live, and which `<dialog>` elements are
editor modals. Each returns plain data so a contract check stays a thin,
readable rule over it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from .css import strip_comments
from .files import read_text, rel


_STANDALONE_MEDIA_RE = re.compile(r"@media[^{]*display-mode:\s*standalone[^{]*\{")


def standalone_shell_present(css: str) -> bool:
    """True when a `(display-mode: standalone)` media block makes the app shell
    the scroller: position: fixed + 100lvh sizing + overflow-y: auto — the
    home-automation#303 architecture the design.md nav contract requires (all
    real scrolling inside `.app`, never the window; removes the pill-drift
    cause). Works on comment-stripped CSS; nesting inside another @media is
    fine (the inner standalone block is matched directly)."""
    for m in _STANDALONE_MEDIA_RE.finditer(css):
        depth, i = 1, m.end()
        start = i
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        block = css[start:i]
        if (re.search(r"position:\s*fixed", block) and "100lvh" in block
                and re.search(r"overflow-y:\s*auto", block)):
            return True
    return False


_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}
_TAG_STREAM_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")
_TAG_CLASS_RE = re.compile(r"class=[\"']([^\"']*)[\"']", re.I)


def nav_nested_in_app(html_files: List[Path]) -> bool:
    """True when a `<nav class="tabs">` is a DOM descendant of `<main
    class="app">` — the _vendored/nav/README.md load-bearing rule is that
    they must be `<body>` siblings; an installed iOS PWA can capture a fixed
    nav inside a scroll container and anchor it there instead of the
    viewport (home-automation#232, app-launcher#369)."""
    for p in html_files:
        text = strip_comments(read_text(p), "html")
        stack: List[Tuple[str, set]] = []
        for m in _TAG_STREAM_RE.finditer(text):
            closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
            if closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i:]
                        break
                continue
            cls_m = _TAG_CLASS_RE.search(attrs)
            classes = set(cls_m.group(1).split()) if cls_m else set()
            if tag == "nav" and "tabs" in classes:
                if any(t == "main" and "app" in c for t, c in stack):
                    return True
            self_closing = attrs.rstrip().endswith("/") or tag in _VOID_TAGS
            if not self_closing:
                stack.append((tag, classes))
    return False


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # mahjong/cards, symbols & pictographs, transport, supplemental, emoticons
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "⌀-⏿"           # misc technical (e.g. the branch glyph U+2387)
    "☀-➿"           # misc symbols + dingbats (e.g. U+2605 star, U+2600 sun)
    "⬀-⯿"           # misc symbols and arrows
    "]"
)
_TAG_RE = re.compile(r"<[^>]*>")


_VENDOR_DIR_PARTS = {"vendor", "vendors"}


def _is_third_party(path: Path) -> bool:
    """A bundled third-party library under `vendor/`. Distinct from the
    fleet's own `_vendored/` component family, which stays in scope — those
    are fleet-authored and byte-compared by the `vendored` lens."""
    return any(part.lower() in _VENDOR_DIR_PARTS for part in path.parts)


def find_emoji_sites(root: Path, html_files: List[Path], js_files: List[Path]
                      ) -> List[str]:
    """`file:line` for emoji glyphs in rendered text — HTML text nodes (tags
    stripped so attribute values don't count) and JS source (string literals
    used as UI copy, e.g. an empty-state message baked into a .js file, are
    indistinguishable from other JS text at grep level, so the whole file is
    scanned — app-launcher#368).

    Two things are *not* rendered text and are excluded (#416): JS regex
    literals, whose character classes match glyphs coming *in* off a terminal
    rather than drawing any (app-launcher's `/^[●⏺•◉○]$/` reply-block parser),
    and third-party `vendor/` bundles, whose internal glyph tables aren't the
    adopting app's icon choice and can't be fixed there anyway (xterm.js's
    VT100 DEC Special Graphics map)."""
    sites: List[str] = []
    for p in html_files:
        if _is_third_party(p):
            continue
        text = strip_comments(read_text(p), "html")
        text_nodes = _TAG_RE.sub(" ", text)
        for m in _EMOJI_RE.finditer(text_nodes):
            line = text_nodes.count("\n", 0, m.start()) + 1
            sites.append(f"{rel(root, p)}:{line}")
    for p in js_files:
        if _is_third_party(p):
            continue
        text = strip_comments(read_text(p), "js", blank_regex_literals=True)
        for m in _EMOJI_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            sites.append(f"{rel(root, p)}:{line}")
    return sites

# ------------------------------------------------------------ editor modal
#
# A native <dialog> containing real editable fields is an "editor modal" and
# held to design.md's `modal` component contract (fleet-config#307 — the
# app-launcher#70 root cause: `.stacked` was styled only inside
# `.settings-card`, so the same class fell back to unstyled inline labels
# inside <dialog> forms, and raw <fieldset> groups had no CSS at all).
# A <form> wrapper is NOT required (fleet-config#342): home-automation#409's
# three JS-managed editors carry bare input/select fields with a plain
# type="button" Save, and the old form-based classifier NA'd every modal-*
# check for them. What makes a dialog an editor is the fields, not the
# wrapper; a field-less alert/results dialog stays out of scope.


_DIALOG_RE = re.compile(r"<dialog\b([^>]*)>(.*?)</dialog>", re.S | re.I)
_LABEL_CLASS_RE = re.compile(r"<label\b[^>]*\bclass=[\"']([^\"']*)[\"']", re.I)
_FIELDSET_OPEN_RE = re.compile(r"<fieldset\b([^>]*)>", re.I)
_BUTTON_RE = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S | re.I)
_FOOTER_CONTAINER_RE = re.compile(r"<(div|footer)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_FIELDSET_TAG_PAT = re.compile(r"^fieldset(?:[.:#\[]|$)", re.I)
# a persistence boundary: submit buttons or save/apply/confirm/done-named
# controls — what separates a *staged* editor from a live-control dialog.
_SAVE_AFFORDANCE_RE = re.compile(
    r'type=["\']submit["\']|(?:id|class)=["\'][^"\']*(?:save|submit|apply|confirm|done)[^"\']*["\']',
    re.I)


def _editor_modals(root: Path, html_files: List[Path]) -> List[dict]:
    """`<dialog>` blocks that contain a real input/select/textarea — i.e.
    detail/rename/settings editors, not a plain alert or results dialog.
    A `<form>` wrapper is optional (fleet-config#342)."""
    modals: List[dict] = []
    for p in html_files:
        text = strip_comments(read_text(p), "html")
        for m in _DIALOG_RE.finditer(text):
            attrs, inner = m.group(1), m.group(2)
            if not re.search(r"<input\b|<select\b|<textarea\b", inner, re.I):
                continue
            cls_m = _TAG_CLASS_RE.search(attrs)
            classes = set(cls_m.group(1).split()) if cls_m else set()
            line = text.count("\n", 0, m.start()) + 1
            modals.append({"file": rel(root, p), "line": line, "classes": classes,
                           "inner": inner, "text": text, "inner_start": m.start(2)})
    return modals


def _loc_in_modal(modal: dict, local_pos: int) -> str:
    abs_pos = modal["inner_start"] + local_pos
    line = modal["text"].count("\n", 0, abs_pos) + 1
    return f"{modal['file']}:{line}"
