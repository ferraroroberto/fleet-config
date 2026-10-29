"""Detect issue-number references inside fenced code blocks of a Markdown doc.

Companion to the vendored `scripts/docs_ratchet.py`, which by design does not
scan fenced code blocks (its own docstring: "Not counted: fenced code
blocks..."). That is the exact gap `fleet-config#1697` found: README.md's
`## Layout` tree is a fenced block, so an issue reference or a provenance
aside ("split from X, repo#931") can keep accumulating there without the
docs-ratchet gate ever noticing. This module is the fenced-block half the
vendored script deliberately leaves out, kept local (not vendored) because
`scripts/docs_ratchet.py` is re-vendored byte-for-byte from project-scaffolding
and is never hand-edited (fleet-config CLAUDE.md).

Mirrors the vendored script's own reference pattern and fence-boundary
detection (backtick or tilde, closing fence at least as long as the opener)
so a hit here means the same thing a hit outside a fence would mean there.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

_FENCE_OPEN = re.compile(r"^[ \t>]*(`{3,}|~{3,})(.*)$")
#: `#` plus 1-5 digits, not preceded by `&`, `#` or `/` and not followed by a
#: word character or `-` -- same shape as scripts/docs_ratchet.py's `_REF`.
_REF = re.compile(r"(?<![&#/])#(\d{1,5})(?![\w-])")


def scan_fenced_issue_refs(text: str) -> List[Tuple[int, str]]:
    """Return `(1-based line number, the matched line's trimmed text)` for
    every issue-number reference found inside a fenced code block of `text`.
    A reference outside any fence is not this function's job -- the vendored
    docs_ratchet already owns that half."""
    hits: List[Tuple[int, str]] = []
    fence_char = ""
    fence_len = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not fence_char:
            opened = _FENCE_OPEN.match(line)
            if opened:
                fence_char = opened.group(1)[0]
                fence_len = len(opened.group(1))
            continue
        close_pattern = r"^[ \t>]*" + re.escape(fence_char) + "{" + str(fence_len) + ",}[ \t]*$"
        if re.match(close_pattern, line):
            fence_char = ""
            fence_len = 0
            continue
        if _REF.search(line):
            hits.append((lineno, line.strip()))
    return hits


def scan_file(path: Path) -> List[Tuple[int, str]]:
    return scan_fenced_issue_refs(path.read_text(encoding="utf-8"))
