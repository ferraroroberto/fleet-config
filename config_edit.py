"""Shared primitives for the repo-root config writers (fleet-config#928).

`codex_model_policy.py`, `session_retention.py` and `codex_statusline.py` each
edit a user-level config file in place. They share the atomic-write sequence
and the line-oriented TOML table/assignment regexes, so both live here once —
the third-caller rule from the global CLAUDE.md.
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
