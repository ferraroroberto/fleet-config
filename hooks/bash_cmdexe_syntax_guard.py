"""Reject or nudge cmd.exe-only syntax passed to the Bash tool.

Triggers on `PreToolUse` for `Bash` only. Most checks are non-blocking nudges,
but the native ``cmd.exe /c`` caller shape is blocked: Git Bash's MSYS argument
conversion rewrites ``/c`` to ``C:/``, starts an interactive cmd prompt, and
never executes the requested command. The other checks emit a one-line nudge
on stdout (exit 0) and let the command run.

Four independent checks, surfaced from the `/insights` friction data and
fleet-config#385:

1. **Native `cmd.exe /c` through Bash** — MSYS converts the single-slash
   switch to a drive path. Block it and direct the caller to the PowerShell
   tool or the MSYS-safe ``cmd.exe //c`` spelling.

2. **`%VAR%` env-var reference** (e.g. `%USERPROFILE%`) — cmd.exe variable
   expansion syntax; Git Bash treats `%` literally. Requires 2+ characters
   between the percent signs, so a bare printf spec (`%s`, `%d` — no closing
   `%`) or a single-letter date-format run (`+%Y%m%d`) never matches.
   Nudge: use `$VAR` (bash) / `$env:VAR` (PowerShell tool).

3. **cmd-only builtin + flag** (`dir /s`, `del /f`, `copy /y`, ...) — only
   fires when the `/flag` is directly attached to one of these cmd builtin
   names, so an unrelated `/s`-shaped URL path or bash flag never matches.
   Nudge: use the POSIX equivalent or the PowerShell tool.

4. **Caret (`^`) line-continuation** — a caret followed by a newline with
   more command after it. Bash continues lines with a trailing backslash;
   PowerShell with a backtick. Nudge: use the shell-appropriate continuation
   character.

Allow-listed (passes through silently): a bare `%` with no closing `%`
(printf formats), single-letter `%`-format runs, `/flag`s not attached to a
cmd builtin name, and a trailing caret with nothing meaningful after it.
"""

from __future__ import annotations

import re
import shlex
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

SHELL_OPERATORS = {";", "&&", "||", "|", "&", "\n"}
COMMAND_WRAPPERS = {"command", "env", "nohup"}


def _uses_msys_mangled_cmd(command: str) -> bool:
    """Return whether Bash will execute native cmd with a single-slash /c.

    Tokenize with Bash quoting rules so prose/search arguments containing the
    literal text ``cmd.exe /c`` do not false-positive. Only a cmd token in
    command position (start of input or after a shell operator) is considered.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False

    at_command_start = True
    for index, token in enumerate(tokens):
        if token in SHELL_OPERATORS:
            at_command_start = True
            continue
        if not at_command_start:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        if token.lower() in COMMAND_WRAPPERS:
            continue

        executable = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if executable not in {"cmd", "cmd.exe"}:
            at_command_start = False
            continue

        for argument in tokens[index + 1:]:
            if argument in SHELL_OPERATORS:
                break
            if argument.lower() == "/c":
                return True
        at_command_start = False

    return False


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Bash":
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    if _uses_msys_mangled_cmd(cmd):
        _lib.block(
            "Blocked: Git Bash/MSYS rewrites `cmd.exe /c` to `cmd.exe C:/`, "
            "which opens an interactive prompt instead of running the command. "
            "Use the PowerShell tool (preferred) or `cmd.exe //c ...`."
        )

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
