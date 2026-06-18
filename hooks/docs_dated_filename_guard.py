"""Block dated retrospective filenames under a `docs/` directory.

Triggers on `PreToolUse` for `Write`. **Blocks** (exit 2) when the target file
sits under a `docs/` directory and its basename starts with a `YYYY-MM-DD-`
date prefix — e.g. `docs/2026-06-18-retro.md`.

Why: the global "Documentation discipline" rule — `docs/` is for durable
reference material a future reader re-opens, not dated per-PR retrospectives.
The issue + the PR that closes it + `git log` already are the changelog.

Escape hatch: set `CLAUDE_HOOKS_ALLOW_DATED_DOCS=1` for the rare intentional
case (a genuine `docs/2026-Q2-roadmap.md`-style artifact). Edits to an existing
file are unaffected — only `Write` (new-file / overwrite) is guarded.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# A leading `YYYY-MM-DD-` on the basename (the dated-retrospective shape).
DATED_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Write":
        _lib.allow()

    if os.environ.get("CLAUDE_HOOKS_ALLOW_DATED_DOCS") == "1":
        _lib.allow()

    target = _lib.file_path(payload)
    if target is None:
        _lib.allow()

    parts = [p.lower() for p in target.parts]
    if "docs" not in parts:
        _lib.allow()

    if not DATED_PREFIX_RE.match(target.name):
        _lib.allow()

    _lib.block(
        f"Blocked: '{target.name}' is a dated file under docs/. The 'Documentation "
        "discipline' rule keeps docs/ for durable, topic-named reference — not dated "
        "retrospectives (the issue + PR + git log are the changelog). Name it for the "
        "topic, or record the work in the GitHub issue/PR. Set "
        "CLAUDE_HOOKS_ALLOW_DATED_DOCS=1 to override for a genuine dated artifact."
    )


if __name__ == "__main__":
    main()
