"""Shared primitives for the repo-root config writers (fleet-config#928).

`codex_model_policy.py`, `session_retention.py` and `codex_statusline.py` each
edit a user-level config file in place. They share the atomic-write sequence
and the line-oriented TOML table/assignment regexes, so both live here once —
the third-caller rule from the global CLAUDE.md. The one quote- and
comment-aware TOML scanner lives here too (fleet-config#1062).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# One `[table]` header line (optional trailing comment and newline) and the
# `key =` head of one assignment line, matched a line at a time.
TABLE_RE = re.compile(r"^[ \t]*\[([^\]]+)\][ \t]*(?:#.*)?(?:\r?\n)?$")
ASSIGNMENT_RE = re.compile(r"^([ \t]*)([A-Za-z0-9_.-]+)[ \t]*=")

# Per-character classes returned by `classify`.
CODE, STRING, COMMENT = "c", "s", "m"


def classify(text: str) -> list[str]:
    """Classify every character of `text` as `CODE`, `STRING` (quotes
    included) or `COMMENT` (the `#` included, its line break excluded), so a
    `#` or bracket inside a string is never mistaken for syntax."""
    kinds = [CODE] * len(text)
    quote: str | None = None
    triple = False
    escaped = False
    comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if comment:
            if char in "\r\n":
                comment = False
            else:
                kinds[index] = COMMENT
            index += 1
            continue
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, index):
                kinds[index : index + 3] = [STRING] * 3
                quote = None
                triple = False
                index += 3
                continue
            elif not triple and char == quote:
                quote = None
            kinds[index] = STRING
            index += 1
            continue
        if char == "#":
            comment = True
            kinds[index] = COMMENT
        elif char in "'\"":
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                kinds[index : index + 3] = [STRING] * 3
                index += 2
            else:
                kinds[index] = STRING
        index += 1
    return kinds


def syntax_mask(text: str) -> str:
    """Blank TOML strings and comments while retaining positions and line
    breaks."""
    return "".join(
        char if kind == CODE or char in "\r\n" else " "
        for char, kind in zip(text, classify(text))
    )


def inline_comment(line: str) -> str:
    """Return a TOML line's inline comment (no line break), or `""`."""
    kinds = classify(line)
    start = kinds.index(COMMENT) if COMMENT in kinds else -1
    return line[start:].rstrip("\r\n") if start >= 0 else ""


def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` via a sibling temp file and `os.replace`, so a
    reader never sees a half-written config; the temp file is removed on any
    failure. Newlines are written exactly as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
