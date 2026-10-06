"""Block `git commit` with an AI attribution trailer.

Triggers on `PreToolUse` for `Bash` and `PowerShell`. Runs only when the
command actually invokes `git … commit` (see `runs_git_commit`), then looks at
the commit message embedded in the command string (handles `-m "..."`,
`-m '...'`, heredoc and here-string forms) and refuses if it contains any of
the standard AI attributions.

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
    # The session-link trailer the Claude Code harness asks agents to append
    # (fleet-config#1288). Needs the URL, so prose naming the trailer passes.
    r"\bClaude-Session:\s*https?://",
)

# Whether a `git … commit` actually runs (fleet-config#1288). The old test was
# `"git" in cmd and "commit" in cmd`, so a `gh issue create` whose body quoted a
# trailer and said "git commit" was refused. Now `git` must sit at a command
# position (statement start, after `&&`/`;`/`|`/`&`/`(`, env assignments or a
# shell keyword), followed by git's global options and then `commit`. Quoted
# strings are single words, and heredoc and PowerShell here-string bodies are
# dropped first: their text is data, not commands. Only this decision ignores
# them — the trailer scan below still reads the whole command, since that is
# where a heredoc commit message lives.
_WORD = r"""(?:"(?:\\.|[^"\\])*"|'[^']*'|[^\s;&|(){}"'])+"""
_SHELL_TOKEN_RE = re.compile(r"&&|\|\||[;&|\n(){}]|" + _WORD)
_SEPARATORS = {"&&", "||", ";", "&", "|", "\n", "(", ")", "{", "}"}
_PREFIX_WORDS = {"then", "do", "else", "elif", "!", "time", "env", "command", "exec", "nohup"}
_ENV_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PS_HERE_STRING_RE = re.compile(r"@(['\"])\r?\n.*?\r?\n\1@", re.DOTALL)


def runs_git_commit(cmd: str) -> bool:
    """True when `cmd` runs `git [global options] commit` as a command."""
    text = _PS_HERE_STRING_RE.sub("''", _lib.strip_heredoc_bodies(cmd))
    tokens = _SHELL_TOKEN_RE.findall(text)
    at_start = True
    for i, token in enumerate(tokens):
        if token in _SEPARATORS:
            at_start = True
            continue
        if not at_start or token in _PREFIX_WORDS or _ENV_ASSIGN_RE.match(token):
            continue
        at_start = False
        if not _lib.GIT_TOKEN_RE.search(token.strip("\"'")):
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            j += 2 if tokens[j] in _lib.GIT_GLOBAL_OPTS_WITH_VALUE else 1
        if j < len(tokens) and tokens[j].lower() == "commit":
            return True
    return False


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd or not runs_git_commit(cmd):
        _lib.allow()

    # Cheap and broad: search the whole command string. Catches `-m "..."`,
    # `-m '...'`, `-F file`, heredoc `<<'EOF' ... EOF`, etc.
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE | re.DOTALL):
            _lib.block(
                "Blocked: commit message contains an AI attribution trailer "
                "(matched: " + pattern + "). "
                "The user explicitly rejects `Co-Authored-By: Claude`, "
                "`Generated with Claude Code`, a `Claude-Session:` link, and similar. "
                "Re-draft the commit message without it."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
