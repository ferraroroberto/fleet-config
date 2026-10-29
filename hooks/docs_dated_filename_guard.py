"""Block dated retrospective filenames under a `docs/` directory.

Triggers on `PreToolUse` for `Write` or Codex `apply_patch`. **Blocks** when a
new target file sits under a `docs/` directory and its basename starts with a `YYYY-MM-DD-`
date prefix — e.g. `docs/2026-06-18-retro.md`.

Why: the global "Documentation discipline" rule — `docs/` is for durable
reference material a future reader re-opens, not dated per-PR retrospectives.
The issue + the PR that closes it + `git log` already are the changelog.

Escape hatch: set `CLAUDE_HOOKS_ALLOW_DATED_DOCS=1` for the rare intentional
case (a genuine `docs/2026-Q2-roadmap.md`-style artifact). Edits to an existing
file are unaffected — only `Write` (new-file / overwrite) is guarded.

Tool-id vocabulary stays in `_lib.edit_event()` (fleet-config#1558): this used
to branch on Codex's own `apply_patch` id directly to tell a brand-new file
from an update to an existing one. `EditTarget.operation` already carries that
distinction uniformly across harnesses — `"add"`/`"write"` only ever name a
target being newly created — so filtering on it never needs to name a
harness's own tool id.
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

# `EditTarget.operation` values that mean "this target did not exist before
# this call" — native Write (always "write") or apply_patch's "Add File"
# ("add"). An update/rename/delete of an existing artifact is unaffected.
NEW_FILE_OPERATIONS = {"add", "write"}


def main() -> None:
    payload = _lib.read_stdin_json()
    edit = _lib.edit_event(payload)
    if edit.status != "known":
        _lib.allow()

    if os.environ.get("CLAUDE_HOOKS_ALLOW_DATED_DOCS") == "1":
        _lib.allow()

    for change in edit.targets:
        if change.operation not in NEW_FILE_OPERATIONS:
            continue
        target = change.path
        parts = [p.lower() for p in target.parts]
        if "docs" in parts and DATED_PREFIX_RE.match(target.name):
            _lib.block(
                f"Blocked: '{target.name}' is a dated file under docs/. The 'Documentation "
                "discipline' rule keeps docs/ for durable, topic-named reference — not dated "
                "retrospectives (the issue + PR + git log are the changelog). Name it for the "
                "topic, or record the work in the GitHub issue/PR. Set "
                "CLAUDE_HOOKS_ALLOW_DATED_DOCS=1 to override for a genuine dated artifact."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
