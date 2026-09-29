"""The one reader for a repo's `.fleet.toml` (fleet-config#1062).

Every `_lib` consumer of a repo-authored `.fleet.toml` (`[e2e]`, `[worktree]`,
`[cert]`) used to hand-roll the same load-degrade-on-error-pick-a-table
sequence. They read here instead: a missing, unreadable or malformed file is
`None` (or its own state), never an exception, because the file is
repo-authored data that travels between repos.

stdlib only.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

FILENAME = ".fleet.toml"


def read_text(repo: Path) -> Optional[str]:
    """The repo's `.fleet.toml` text, or `None` when absent or unreadable."""
    try:
        return (repo / FILENAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parsed TOML, or `None` for empty text or a syntax error."""
    if not text:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def load_state(repo: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(data, state)`, state `absent` / `invalid` / `ok`; data only when `ok`.
    An empty file is a valid, empty table."""
    text = read_text(repo)
    if text is None:
        return None, "absent"
    try:
        return tomllib.loads(text), "ok"
    except tomllib.TOMLDecodeError:
        return None, "invalid"


def load(repo: Path) -> Optional[Dict[str, Any]]:
    """The repo's parsed `.fleet.toml`, or `None` when absent, unreadable or invalid."""
    return load_state(repo)[0]


def table(data: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """The named top-level table of parsed data, or `None` when it is not a table."""
    found = (data or {}).get(name)
    return found if isinstance(found, dict) else None


def positive_int(raw: object) -> Optional[int]:
    """`raw` when it is a real positive integer, else `None` -- a bool, a
    string, 0 or a negative never counts, so a typo cannot silently raise a
    budget."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw
