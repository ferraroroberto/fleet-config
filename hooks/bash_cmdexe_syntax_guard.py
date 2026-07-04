"""Nudge away from cmd.exe-only syntax passed to the Bash tool.

Triggers on `PreToolUse` for `Bash` only. **Non-blocking** — emits a single
one-line nudge on stdout (exit 0) and lets the command run. The Bash tool on
this machine runs Git Bash, not cmd.exe, so cmd-only constructs either error
or silently do the wrong thing.

Three independent checks, surfaced from the `/insights` friction data:

1. **`%VAR%` env-var reference** (e.g. `%USERPROFILE%`) — cmd.exe variable
   expansion syntax; Git Bash treats `%` literally. Requires 2+ characters
   between the percent signs, so a bare printf spec (`%s`, `%d` — no closing
   `%`) or a single-letter date-format run (`+%Y%m%d`) never matches.
   Nudge: use `$VAR` (bash) / `$env:VAR` (PowerShell tool).

2. **cmd-only builtin + flag** (`dir /s`, `del /f`, `copy /y`, ...) — only
   fires when the `/flag` is directly attached to one of these cmd builtin
   names, so an unrelated `/s`-shaped URL path or bash flag never matches.
   Nudge: use the POSIX equivalent or the PowerShell tool.

3. **Caret (`^`) line-continuation** — a caret followed by a newline with
   more command after it. Bash continues lines with a trailing backslash;
   PowerShell with a backtick. Nudge: use the shell-appropriate continuation
   character.

Allow-listed (passes through silently): a bare `%` with no closing `%`
(printf formats), single-letter `%`-format runs, `/flag`s not attached to a
cmd builtin name, and a trailing caret with nothing meaningful after it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# cmd.exe `%VAR%` expansion — requires 2+ chars between the percent signs to
# dodge bare printf specs (`%s`) and single-letter date-format runs (`%Y%m%d`).
ENV_VAR_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]+%")

# A cmd-only builtin immediately followed by a `/flag` (dir /s, del /f, copy /y, ...).
CMD_BUILTIN_FLAG_RE = re.compile(
    r"\b(?:dir|del|copy|move|md|ren|rd|rmdir|xcopy|attrib|cls|type)\s+/[A-Za-z]\b",
    re.IGNORECASE,
)

# A caret at end-of-line (optional trailing whitespace) with more command after it.
CARET_CONTINUATION_RE = re.compile(r"\^[ \t]*\n(?!\s*$)")


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Bash":
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    if ENV_VAR_RE.search(cmd):
        _lib.warn(
            "Nudge: this looks like cmd.exe `%VAR%` env-var syntax, which Git Bash "
            "treats literally instead of expanding. Use `$VAR` (bash) or `$env:VAR` "
            "(PowerShell tool)."
        )

    if CMD_BUILTIN_FLAG_RE.search(cmd):
        _lib.warn(
            "Nudge: this looks like a cmd.exe builtin with a `/flag` (e.g. `dir /s`, "
            "`del /f`, `copy /y`), which doesn't exist in Git Bash. Use the POSIX "
            "equivalent or the PowerShell tool."
        )

    if CARET_CONTINUATION_RE.search(cmd):
        _lib.warn(
            "Nudge: this looks like a cmd.exe caret (`^`) line-continuation, which "
            "Git Bash doesn't recognize. Use `\\` (bash) or a backtick (PowerShell) "
            "to continue a line."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
