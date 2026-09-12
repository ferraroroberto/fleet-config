"""Shared read of one field out of a SKILL.md YAML frontmatter block (fleet-config#615).

Three call sites parsed a `---`-delimited frontmatter block for a single field's
value independently: `skills/_lib/skill_description.py`'s `frontmatter_description()`
(description only), `.claude/skills/config-map/build_data.py`'s `_frontmatter_field()`
(any field, reads the file itself). One parser here; both now delegate to it —
`frontmatter_description()` for the description-specific '' contract that
`/context-audit` and `/context-purge` depend on, `_frontmatter_field()` for the
Path-reading, `Optional[str]`-returning contract `/config-map` depends on.

`frontmatter_error()` is the other half (fleet-config#845): `frontmatter_field()`
is a line regex, so it happily extracts a value from a block no YAML loader
accepts. A real parse is what the agent harness does, and a block that fails it
loses its `description:` — the skill's whole routing surface — silently.

stdlib only, no I/O — callers own reading the file.
"""

from __future__ import annotations

import re
from typing import Optional, Set

_KEY_LINE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?: +(.*))?$")
_BLOCK_SCALAR_HEADER = re.compile(r"^[|>][1-9+-]{0,2}\s*(?:#.*)?$")
# A plain scalar cannot open with one of these (YAML 1.2 §7.3.3 c-indicator).
# `-`, `?` and `:` are indicators only when followed by whitespace.
_FORBIDDEN_FIRST = set(",[]{}#&*!|>'\"%@`")
_BARE_INDICATOR = re.compile(r"^[-?:](?:\s|$)")


def _quoted_error(value: str) -> Optional[str]:
    """Error for a single-line single- or double-quoted scalar, or None."""
    quote = value[0]
    i = 1
    while i < len(value):
        ch = value[i]
        if quote == '"' and ch == "\\":
            i += 2
            continue
        if ch == quote:
            if quote == "'" and value[i + 1:i + 2] == "'":
                i += 2  # '' is an escaped quote inside a single-quoted scalar
                continue
            rest = value[i + 1:]
            if rest.strip() and not re.match(r"^\s+#", rest):
                return f"text after the closing {quote} quote: {rest.strip()[:40]!r}"
            return None
        i += 1
    return f"{quote}-quoted value is not closed on its own line"


def _plain_error(value: str) -> Optional[str]:
    """Error for a single-line plain (unquoted) scalar, or None."""
    if value[0] in _FORBIDDEN_FIRST or _BARE_INDICATOR.match(value):
        return f"unquoted value starts with the YAML indicator {value[0]!r}"
    if re.search(r":\s", value) or value.endswith(":"):
        return "unquoted value contains ': ' (a YAML mapping indicator)"
    m = re.search(r"\s#", value)
    if m:
        return (f"unquoted value is cut off at ' #' (a YAML comment) — the parsed "
                f"value ends at {value[:m.start()][-30:]!r}")
    return None


def frontmatter_error(text: str) -> Optional[str]:
    """Why `text`'s `---` frontmatter would not parse as written, or None.

    None also for a file with no frontmatter at all — that is the caller's
    "no description" case, not a parse failure. Otherwise checks the narrow
    subset of YAML a SKILL.md frontmatter uses: top-level `key: value` lines
    whose values are single-line plain or quoted scalars. A value a real YAML
    loader would reject — or would silently truncate, which loses the same
    routing text without an error (a ` #` comment, a plain scalar continued on
    the next line) — is reported, so no check can read "a line that looks like
    a description exists" as "the description parses". Block scalars (`|`,
    `>`) and nested blocks under an empty-valued key are accepted without
    validating their contents.
    """
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return "opening '---' line carries trailing text"
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return "frontmatter has no closing '---' line"

    seen: Set[str] = set()
    nested_ok = False  # indented lines are content of the previous key
    plain_key: Optional[str] = None  # previous key held a plain scalar
    for n, raw in enumerate(lines[1:close], start=2):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if line[0] == "\t":
                return f"line {n}: tab indentation is not allowed in YAML"
            if plain_key is not None:
                return (f"line {n}: unquoted `{plain_key}:` value continues onto "
                        f"the next line — single-line readers see only its first line")
            if not nested_ok:
                return f"line {n}: indented line does not belong to any key"
            continue
        m = _KEY_LINE.match(line)
        if not m and re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*:\t", line):
            return f"line {n}: tab after ':' is not a YAML separator"
        if not m:
            return f"line {n}: not a `key: value` entry: {line[:50]!r}"
        key, value = m.group(1), (m.group(2) or "").strip()
        if key in seen:
            return f"line {n}: duplicate key `{key}`"
        seen.add(key)
        nested_ok, plain_key = False, None
        if not value or value.startswith("#"):
            nested_ok = True
            continue
        if value[0] in "|>":
            if not _BLOCK_SCALAR_HEADER.match(value):
                return f"line {n}: malformed block scalar header {value!r}"
            nested_ok = True
            continue
        error = _quoted_error(value) if value[0] in "'\"" else _plain_error(value)
        if error:
            return f"line {n}: `{key}`: {error}"
        if value[0] not in "'\"":
            plain_key = key
    return None


def frontmatter_field(text: str, field: str) -> Optional[str]:
    """The YAML `<field>:` value from a `---`-delimited frontmatter block, or None.

    Returns None for a file with no frontmatter or no `<field>:` key. The block
    is searched from just past the opening `---`, up to a closing `\\n---` if one
    exists — an unterminated frontmatter still yields whatever fields it has.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None
