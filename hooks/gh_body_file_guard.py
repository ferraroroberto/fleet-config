"""Nudge away from the two cross-shell payload traps that mangle GitHub bodies.

Triggers on `PreToolUse` for `Bash` only. **Non-blocking** — emits a single
one-line nudge on stdout (exit 0) and lets the command run. The user decides
whether the match is real; a false positive costs one line, never a blocked
call.

Two independent checks, surfaced from the `/insights` friction data:

1. **Inline `gh` body carrying a heredoc or backticks.** A `gh (issue|pr)
   (create|comment|edit)` with an inline `--body`/`-b` plus a backtick or a
   `<<` heredoc breaks under Bash — the backtick triggers command substitution,
   the heredoc expands `$`/backticks — producing a malformed PR/issue body.
   Nudge: write the markdown with the `Write` tool to a unique tmp file, then
   pass `--body-file <tmp>` (never reuse a generic name — `gh` silently consumes
   stale content).

2. **PowerShell here-string run through Bash.** A `@'…'@` / `@"…"@` here-string
   is PowerShell syntax; in the Bash tool it mis-parses and corrupts the body.
   Nudge: use the PowerShell tool, or `--body-file`.

Allow-listed (passes through silently): `--body-file` / `-F` payloads, plain
`gh` reads (`gh issue list`), and any non-`gh` Bash command without a
here-string.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# A gh write subcommand that takes a body: `gh issue create|comment`,
# `gh pr create|comment|edit`.
GH_WRITE_RE = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:create|comment|edit)\b")

# An inline body flag — `--body` / `-b` — but NOT `--body-file` (the fix we want).
INLINE_BODY_RE = re.compile(r"(?:--body(?!-file)|-b)\b")

HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w+")  # `<<EOF`, `<<'EOF'`, `<<-EOF`
BACKTICK_RE = re.compile(r"`")

# PowerShell here-string opener `@'` / `@"` and closer `'@` / `"@`.
HERESTRING_OPEN_RE = re.compile(r"@['\"]")
HERESTRING_CLOSE_RE = re.compile(r"['\"]@")


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Bash":
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # 1) Inline gh body with a risky construct -> nudge to --body-file.
    if (
        GH_WRITE_RE.search(cmd)
        and INLINE_BODY_RE.search(cmd)
        and (BACKTICK_RE.search(cmd) or HEREDOC_RE.search(cmd))
    ):
        _lib.warn(
            "Nudge: this `gh` command builds the body inline with a heredoc/backticks, "
            "which Bash mangles (command substitution / `$` expansion). Write the markdown "
            "to a UNIQUE tmp file with the Write tool, then pass `--body-file <tmp>` "
            "(never reuse a generic name - gh silently consumes stale content)."
        )

    # 2) PowerShell here-string run through the Bash tool -> wrong shell.
    if HERESTRING_OPEN_RE.search(cmd) and HERESTRING_CLOSE_RE.search(cmd):
        _lib.warn(
            "Nudge: this looks like a PowerShell here-string (@'...'@ / @\"...\"@) running through "
            "the Bash tool - it will mis-parse. Use the PowerShell tool for here-strings, or "
            "pass the payload via `--body-file <tmp>`."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
