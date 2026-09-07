"""Configure two-year local session retention for Claude Code and Codex.

The helper updates only the retention keys it owns. Claude's JSON settings keep
all unrelated values (including machine-local permissions and secrets), while
Codex's TOML retains comments, ordering, and unrelated bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any


RETENTION_DAYS = 730
_TABLE_RE = re.compile(r"^[ \t]*\[([^\]]+)\][ \t]*(?:#.*)?(?:\r?\n)?$")
_ASSIGNMENT_RE = re.compile(r"^([ \t]*)([A-Za-z0-9_.-]+)[ \t]*=")


class RetentionConfigError(ValueError):
    """Raised when a live config cannot be updated without guessing."""


def default_claude_settings_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(config_dir) / "settings.json" if config_dir else Path.home() / ".claude" / "settings.json"


def default_codex_config_path() -> Path:
    config_dir = os.environ.get("CODEX_HOME")
    return Path(config_dir) / "config.toml" if config_dir else Path.home() / ".codex" / "config.toml"


def merge_claude_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return settings with the retention floor pinned and opt-out removed."""

    updated = json.loads(json.dumps(settings))
    changed: list[str] = []
    if updated.get("cleanupPeriodDays") != RETENTION_DAYS:
        updated["cleanupPeriodDays"] = RETENTION_DAYS
        changed.append("cleanupPeriodDays")
    env = updated.get("env")
    if env is not None and not isinstance(env, dict):
        raise RetentionConfigError("Claude settings env must be an object")
    if isinstance(env, dict) and "CLAUDE_CODE_SKIP_PROMPT_HISTORY" in env:
        del env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"]
        changed.append("env.CLAUDE_CODE_SKIP_PROMPT_HISTORY")
    return updated, tuple(changed)


def _inline_comment(line: str) -> str:
    """Return a TOML line's inline comment, ignoring hashes inside strings."""

    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#":
            return line[index:].rstrip("\r\n")
    return ""


def merge_codex_history(text: str) -> tuple[str, tuple[str, ...]]:
    """Pin save-all history and remove the oldest-entry byte cap."""

    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RetentionConfigError(f"Codex config is not valid TOML: {exc}") from exc
    history = parsed.get("history", {})
    if history is not None and not isinstance(history, dict):
        raise RetentionConfigError("Codex history config must be a table")

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    table: str | None = None
    history_header: int | None = None
    persistence_found = False
    changed: list[str] = []
    output: list[str] = []

    for line in lines:
        table_match = _TABLE_RE.match(line)
        if table_match:
            table = table_match.group(1).strip()
            if table == "history":
                history_header = len(output)
            output.append(line)
            continue
        assignment = _ASSIGNMENT_RE.match(line)
        key = assignment.group(2) if assignment else None
        is_persistence = key == "history.persistence" and table is None
        is_persistence = is_persistence or (table == "history" and key == "persistence")
        is_max = key == "history.max_bytes" and table is None
        is_max = is_max or (table == "history" and key == "max_bytes")
        if is_max:
            changed.append("history.max_bytes")
            continue
        if is_persistence:
            persistence_found = True
            indent = assignment.group(1) if assignment else ""
            rendered_key = key or "persistence"
            comment = _inline_comment(line)
            suffix = f" {comment}" if comment else ""
            replacement = f'{indent}{rendered_key} = "save-all"{suffix}{newline}'
            if line != replacement:
                changed.append("history.persistence")
            output.append(replacement)
            continue
        output.append(line)

    if not persistence_found:
        assignment = f'persistence = "save-all"{newline}'
        if history_header is not None:
            output.insert(history_header + 1, assignment)
        else:
            if output and not output[-1].endswith(("\n", "\r")):
                output[-1] += newline
            if output and output[-1].strip():
                output.append(newline)
            output.extend((f"[history]{newline}", assignment))
        changed.append("history.persistence")

    updated = "".join(output)
    try:
        effective = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise RetentionConfigError(f"updated Codex config is not valid TOML: {exc}") from exc
    if effective.get("history", {}).get("persistence") != "save-all":
        raise RetentionConfigError("Codex history.persistence did not resolve to save-all")
    if "max_bytes" in effective.get("history", {}):
        raise RetentionConfigError("Codex history.max_bytes is still set")
    return updated, tuple(dict.fromkeys(changed))


def _atomic_write(path: Path, text: str) -> None:
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


def configure(claude_path: Path, codex_path: Path, *, apply: bool) -> tuple[str, str]:
    """Check or apply both configs, returning their individual states."""

    try:
        claude_original = json.loads(claude_path.read_text(encoding="utf-8")) if claude_path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionConfigError(f"Claude settings are unreadable: {exc}") from exc
    if not isinstance(claude_original, dict):
        raise RetentionConfigError("Claude settings root must be an object")
    claude_updated, _ = merge_claude_settings(claude_original)

    try:
        codex_original = codex_path.read_text(encoding="utf-8") if codex_path.exists() else ""
    except OSError as exc:
        raise RetentionConfigError(f"Codex config is unreadable: {exc}") from exc
    codex_updated, _ = merge_codex_history(codex_original)

    claude_state = "unchanged" if claude_updated == claude_original else "update-needed"
    codex_state = "unchanged" if codex_updated == codex_original else "update-needed"
    if apply:
        if claude_state != "unchanged":
            _atomic_write(claude_path, json.dumps(claude_updated, indent=2, ensure_ascii=False) + "\n")
            claude_state = "updated"
        if codex_state != "unchanged":
            _atomic_write(codex_path, codex_updated)
            codex_state = "updated"
    return claude_state, codex_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--check", action="store_true")
    parser.add_argument("--claude-settings", type=Path, default=default_claude_settings_path())
    parser.add_argument("--codex-config", type=Path, default=default_codex_config_path())
    args = parser.parse_args()
    try:
        claude_state, codex_state = configure(
            args.claude_settings, args.codex_config, apply=args.apply,
        )
        print(f"SESSION_RETENTION claude={claude_state} codex={codex_state} days={RETENTION_DAYS}")
        return 0
    except RetentionConfigError as exc:
        print(f"SESSION_RETENTION status=error detail={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
