"""Merge the supported native Codex quota fields into ``config.toml``.

The Codex TUI owns rendering. This helper only edits the ``[tui]``
``status_line`` array, preserving existing fields, comments, and unrelated
configuration. It is intentionally opt-in; ``install.ps1`` calls it only when
``-ConfigureCodexStatusline`` is supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path


REQUIRED_STATUS_ITEMS: tuple[str, ...] = (
    "context-used",
    "model",
    "current-dir",
    "git-branch",
    "five-hour-limit",
    "weekly-limit",
)

_TABLE_RE = re.compile(r"^[ \t]*\[([^\]]+)\][ \t]*(?:#.*)?\r?$", re.MULTILINE)
_STATUS_LINE_RE = re.compile(r"^[ \t]*status_line[ \t]*=[ \t]*", re.MULTILINE)
_DOTTED_STATUS_LINE_RE = re.compile(r"^[ \t]*tui[ \t]*\.[ \t]*status_line[ \t]*=[ \t]*", re.MULTILINE)


class ConfigError(ValueError):
    """Raised when the target config cannot be safely updated."""


def _syntax_mask(text: str) -> str:
    """Blank TOML strings and comments while retaining positions and newlines."""

    masked = list(text)
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
                masked[index] = " "
            index += 1
            continue
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, index):
                masked[index : index + 3] = "   "
                quote = None
                triple = False
                index += 3
                continue
            elif not triple and char == quote:
                quote = None
            if char not in "\r\n":
                masked[index] = " "
            index += 1
            continue
        if char == "#":
            comment = True
            masked[index] = " "
        elif char in "'\"":
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                masked[index : index + 3] = "   "
                index += 2
            else:
                masked[index] = " "
        index += 1
    return "".join(masked)


def default_config_path() -> Path:
    """Return Codex's user config path, respecting ``CODEX_HOME``."""

    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home) / "config.toml" if codex_home else Path.home() / ".codex" / "config.toml"


def _table_at(text: str, position: int) -> str | None:
    """Return the TOML table containing ``position``."""

    table: str | None = None
    for match in _TABLE_RE.finditer(text, 0, position):
        table = match.group(1).strip()
    return table


def _array_end(text: str, start: int) -> int:
    """Find an array's closing bracket without treating strings/comments as syntax."""

    depth = 0
    quote: str | None = None
    triple = False
    escaped = False
    comment = False
    index = start
    while index < len(text):
        char = text[index]
        if comment:
            if char in "\r\n":
                comment = False
            index += 1
            continue
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, index):
                quote = None
                triple = False
                index += 3
                continue
            elif not triple and char == quote:
                quote = None
            index += 1
            continue
        if char == "#":
            comment = True
        elif char in "'\"":
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                index += 2
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ConfigError("[tui].status_line has no closing ]")


def _parse_status_items(value: str) -> list[str]:
    """Parse and validate one status-line array value."""

    try:
        parsed = tomllib.loads(f"status_line = {value}")["status_line"]
    except (tomllib.TOMLDecodeError, KeyError) as exc:
        raise ConfigError("[tui].status_line must be a TOML array") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ConfigError("[tui].status_line must be an array of strings")
    return parsed


def _last_value_character(value: str) -> int | None:
    """Return the last non-comment, non-whitespace index before an array's ``]``."""

    comment = False
    quote: str | None = None
    escaped = False
    last: int | None = None
    for index, char in enumerate(value[1:-1], start=1):
        if comment:
            if char in "\r\n":
                comment = False
            continue
        if quote:
            last = index
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char == "#":
            comment = True
        elif char in "'\"":
            quote = char
            last = index
        elif not char.isspace():
            last = index
    return last


def _append_items(value: str, added: tuple[str, ...], newline: str) -> str:
    """Append ``added`` to a valid array while retaining its existing bytes."""

    rendered = ", ".join(json.dumps(item) for item in added)
    body = value[1:-1]
    if not body.strip():
        return f"[{rendered}]"
    if "\n" not in value and "\r" not in value:
        return value[:-1] + ", " + rendered + "]"

    last = _last_value_character(value)
    with_comma = value
    if last is not None and value[last] != ",":
        with_comma = value[: last + 1] + "," + value[last + 1 :]
    closing_line_start = with_comma.rfind("\n", 0, len(with_comma) - 1) + 1
    closing_indent = with_comma[closing_line_start : len(with_comma) - 1]
    if closing_indent.strip():
        return with_comma[:-1] + " " + rendered + "]"
    item_indent = closing_indent + "    "
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#"):
            item_indent = line[: len(line) - len(stripped)]
            break
    insertion = f"{item_indent}{rendered}{newline}{closing_indent}"
    return with_comma[:closing_line_start] + insertion + "]"


def _insert_into_tui(text: str, syntax: str, assignment: str, newline: str) -> str | None:
    """Insert ``assignment`` into an existing ``[tui]`` table, if present."""

    tables = list(_TABLE_RE.finditer(syntax))
    for index, table in enumerate(tables):
        if table.group(1).strip() != "tui":
            continue
        table_end = tables[index + 1].start() if index + 1 < len(tables) else len(text)
        prefix = text[:table_end]
        suffix = text[table_end:]
        separator = "" if prefix.endswith(("\n", "\r")) else newline
        return prefix + separator + assignment + newline + suffix
    return None


def merge_status_line(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``text`` with required native items appended exactly once.

    Existing status-line entries retain their order and bytes. The returned
    tuple lists only entries newly added by this call.
    """

    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml is not valid TOML: {exc}") from exc

    newline = "\r\n" if "\r\n" in text else "\n"
    syntax = _syntax_mask(text)
    assignments = [(match, "tui") for match in _STATUS_LINE_RE.finditer(syntax)]
    assignments.extend((match, None) for match in _DOTTED_STATUS_LINE_RE.finditer(syntax))
    for match, expected_table in sorted(assignments, key=lambda candidate: candidate[0].start()):
        if _table_at(syntax, match.start()) != expected_table:
            continue
        value_start = match.end()
        if value_start >= len(text) or text[value_start] != "[":
            raise ConfigError("[tui].status_line must be a TOML array")
        value_end = _array_end(text, value_start)
        value = text[value_start:value_end]
        existing = _parse_status_items(value)
        added = tuple(item for item in REQUIRED_STATUS_ITEMS if item not in existing)
        if not added:
            return text, ()
        updated_value = _append_items(value, added, newline)
        updated = text[:value_start] + updated_value + text[value_end:]
        tomllib.loads(updated)
        return updated, added

    assignment = f"status_line = [{', '.join(json.dumps(item) for item in REQUIRED_STATUS_ITEMS)}]"
    inserted = _insert_into_tui(text, syntax, assignment, newline)
    if inserted is not None:
        tomllib.loads(inserted)
        return inserted, REQUIRED_STATUS_ITEMS

    suffix = "" if not text or text.endswith(("\n", "\r")) else newline
    addition = f"{suffix}[tui]{newline}{assignment}{newline}"
    updated = text + addition
    tomllib.loads(updated)
    return updated, REQUIRED_STATUS_ITEMS


def update_config(path: Path) -> tuple[bool, tuple[str, ...]]:
    """Apply the merge atomically and return ``(changed, added_items)``."""

    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
    else:
        original = ""
    updated, added = merge_status_line(original)
    if updated == original:
        return False, added
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True, added


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--apply", action="store_true", help="update the target config atomically")
    actions.add_argument("--check", action="store_true", help="report whether an update is needed")
    parser.add_argument("--config", type=Path, default=default_config_path(), help="Codex config.toml path")
    return parser


def main() -> int:
    """Run the opt-in config update or its read-only check."""

    args = _parser().parse_args()
    try:
        if args.check:
            if args.config.exists():
                with args.config.open("r", encoding="utf-8", newline="") as handle:
                    original = handle.read()
            else:
                original = ""
            updated, added = merge_status_line(original)
            state = "unchanged" if updated == original else "update-needed"
            print(f"CODEX_STATUSLINE status={state} added={len(added)} path={args.config}")
            return 0
        changed, added = update_config(args.config)
        state = "updated" if changed else "unchanged"
        print(f"CODEX_STATUSLINE status={state} added={len(added)} path={args.config}")
        return 0
    except (OSError, ConfigError, tomllib.TOMLDecodeError) as exc:
        print(f"CODEX_STATUSLINE status=error detail={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
