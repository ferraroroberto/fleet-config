"""Configure ten-year local session retention for Claude Code and Codex.

The helper updates only the retention keys it owns. Claude's JSON settings keep
all unrelated values (including machine-local permissions and secrets), while
Codex's TOML retains comments, ordering, and unrelated bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_edit import (  # noqa: E402
    atomic_write,
    default_codex_config_path,
    merge_table,
)

RETENTION_DAYS = 3650


class RetentionConfigError(ValueError):
    """Raised when a live config cannot be updated without guessing."""


def default_claude_settings_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(config_dir) / "settings.json" if config_dir else Path.home() / ".claude" / "settings.json"


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


def merge_codex_history(text: str) -> tuple[str, tuple[str, ...]]:
    """Pin save-all history and remove the oldest-entry byte cap."""

    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RetentionConfigError(f"Codex config is not valid TOML: {exc}") from exc
    history = parsed.get("history", {})
    if history is not None and not isinstance(history, dict):
        raise RetentionConfigError("Codex history config must be a table")

    changed: list[str] = []
    if history.get("persistence") != "save-all":
        changed.append("history.persistence")
    if "max_bytes" in history:
        changed.append("history.max_bytes")
    updated = merge_table(text, "history", {"persistence": '"save-all"'}, drop=("max_bytes",))
    try:
        effective = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise RetentionConfigError(f"updated Codex config is not valid TOML: {exc}") from exc
    if effective.get("history", {}).get("persistence") != "save-all":
        raise RetentionConfigError("Codex history.persistence did not resolve to save-all")
    if "max_bytes" in effective.get("history", {}):
        raise RetentionConfigError("Codex history.max_bytes is still set")
    return updated, tuple(changed)


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
            atomic_write(claude_path, json.dumps(claude_updated, indent=2, ensure_ascii=False) + "\n")
            claude_state = "updated"
        if codex_state != "unchanged":
            atomic_write(codex_path, codex_updated)
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
