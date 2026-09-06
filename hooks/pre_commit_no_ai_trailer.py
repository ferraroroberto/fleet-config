"""Block `git commit` with an AI attribution trailer.

Triggers on `PreToolUse` for `Bash`. Looks at the commit message embedded in
the command string (handles `-m "..."`, `-m '...'`, and heredoc forms) and
refuses if it contains any of the standard AI attributions.

Why: the user has explicitly rejected `Co-Authored-By: Claude` (and any other
AI/Claude/Anthropic trailer) in every commit. This hook is the wire that
catches the mistake before it lands in `git log`.
"""

from __future__ import annotations

import re
import sys

# Resolve the sibling _lib without requiring this dir on sys.path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


FORBIDDEN_PATTERNS = (
    r"Co-Authored-By:\s*Claude",
    r"Co-Authored-By:\s*.*@anthropic\.com",
    r"Generated\s+with\s+\[?Claude\s+Code\]?",
    r"Generated\s+with.*Claude",
    r"\xf0\x9f\xa4\x96\s*Generated\s+with",  # robot emoji + Generated with (UTF-8 bytes form)
    r"🤖\s*Generated\s+with",
    r"<noreply@anthropic\.com>",
)


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd or "git" not in cmd or "commit" not in cmd:
        _lib.allow()

    # Cheap and broad: search the whole command string. Catches `-m "..."`,
    # `-m '...'`, `-F file`, heredoc `<<'EOF' ... EOF`, etc.
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE | re.DOTALL):
            _lib.block(
                "Blocked: commit message contains an AI attribution trailer "
                "(matched: " + pattern + "). "
                "The user explicitly rejects `Co-Authored-By: Claude`, "
                "`Generated with Claude Code`, and similar. "
                "Re-draft the commit message without it."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
