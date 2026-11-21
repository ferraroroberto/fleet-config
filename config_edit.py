"""Shared primitives for the repo-root config writers.

`codex_model_policy.py`, `session_retention.py` and `codex_statusline.py` each
edit a user-level config file in place. They share the atomic-write sequence
and the line-oriented TOML table/assignment regexes, so both live here once —
the third-caller rule from the global CLAUDE.md. The one quote- and
comment-aware TOML scanner lives here too, and so does
the `CODEX_HOME`-aware Codex config path all three resolve. The two writers of
plain `key = value` settings (`codex_model_policy.py`, `session_retention.py`)
share `merge_table`, the one byte-preserving set-or-drop editor for them.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "skills" / "_lib"))
from fleet_core import runtime_files  # noqa: E402

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


def table_ranges(lines: list[str]) -> list[tuple[str | None, int, int]]:
    """Return `(table, header_index, end)` for the top-level range (`table`
    `None`, header index 0) and every `[table]` header, line by line."""
    headers = [(index, match.group(1).strip()) for index, line in enumerate(lines) if (match := TABLE_RE.match(line))]
    ranges: list[tuple[str | None, int, int]] = [(None, 0, headers[0][0] if headers else len(lines))]
    for position, (start, name) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        ranges.append((name, start, end))
    return ranges


def merge_table(text: str, table: str | None, values: dict[str, str], drop: tuple[str, ...] = ()) -> str:
    """Set `key = value` for every `values` entry in `[table]` (`None` for the
    top level) and delete every `drop` key, leaving every other byte as it was.

    `values` holds already-rendered TOML values. A key is matched where it is
    written plainly in the table or, for a named table, dotted at the top level
    (`history.persistence = …`), and is rewritten under the name it was written
    with, keeping its indent and inline comment. A repeated assignment of a
    managed key is dropped. A missing key goes after the table's last
    non-blank line, or into a new `[table]` at the end of the file. Line
    endings follow the line replaced, else the file. A `drop` key's value must
    fit on one line.
    """
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    ranges = table_ranges(lines)
    target = next((item for item in ranges if item[0] == table), None)
    # (first line, end, key prefix) of every place a key of this table can sit.
    spans: list[tuple[int, int, str]] = []
    if table is not None:
        spans.append((0, ranges[0][2], f"{table}."))
    header = body = end = -1
    if target is not None:
        _, header, end = target
        body = header if table is None else header + 1
        spans.append((body, end, ""))

    output: list[str] = []
    seen: set[str] = set()
    anchor = 0
    for index, line in enumerate(lines):
        match = ASSIGNMENT_RE.match(line)
        name = ""
        if match:
            name = next((match.group(2)[len(prefix):] for first, last, prefix in spans
                         if first <= index < last and match.group(2).startswith(prefix)), "")
        if name in drop or (name in values and name in seen):
            continue
        if name in values:
            comment = inline_comment(line)
            suffix = f" {comment}" if comment else ""
            line_end = line[len(line.rstrip("\r\n")):] or newline
            output.append(f"{match.group(1)}{match.group(2)} = {values[name]}{suffix}{line_end}")
            seen.add(name)
        else:
            output.append(line)
        if index == header and table is not None or body <= index < end and line.strip():
            anchor = len(output)

    missing = [f"{key} = {value}{newline}" for key, value in values.items() if key not in seen]
    if not missing:
        return "".join(output)
    if target is None:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += newline
        if output and output[-1].strip():
            output.append(newline)
        output.append(f"[{table}]{newline}")
        anchor = len(output)
    elif anchor and not output[anchor - 1].endswith(("\n", "\r")):
        output[anchor - 1] += newline
    output[anchor:anchor] = missing
    return "".join(output)


def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` via a sibling temp file and `os.replace`, so a
    reader never sees a half-written config; the temp file is removed on any
    failure. Newlines are written exactly as given."""
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
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


def default_codex_config_path() -> Path:
    """Return Codex's user config path, respecting ``CODEX_HOME``."""
    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home) / "config.toml" if codex_home else Path.home() / ".codex" / "config.toml"
