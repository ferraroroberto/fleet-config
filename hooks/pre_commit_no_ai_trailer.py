"""Block an AI attribution trailer on its way into git history or GitHub.

Triggers on `PreToolUse` for `Bash` and `PowerShell`, on two sinks:

- `git … commit` (see `_lib.runs_git_commit`): the message embedded in the
  command string (`-m "..."`, `-m '...'`, heredoc and here-string forms) and
  the file a `-F`/`--file` names, resolved against the payload `cwd`, a leading
  `cd` and `git -C`.
- `gh issue|pr create|edit|comment|review`: the inline `--body`/`-b` and every
  `--body-file`/`-F` file, read by the same operand walk `secret_scan_guard`
  uses for its publish sink.

Why: the user has explicitly rejected `Co-Authored-By: Claude` (and any other
AI/Claude/Anthropic trailer) in every commit and PR. This hook is the wire that
catches the mistake before it lands in `git log` or on a public repo.

A commit message is scanned whole, so any mention of a trailer is refused. A
GitHub body is often *about* the trailer (an issue asking for this very guard),
so a text pattern only counts there when it starts a line; the session-link
patterns need a concrete URL and are refused anywhere in the body.
"""

from __future__ import annotations

import re
import sys

# Resolve the sibling _lib without requiring this dir on sys.path
from pathlib import Path
from typing import List, Optional, Tuple
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from secret_scan import shell_paths  # noqa: E402
from secret_scan_guard import (  # noqa: E402
    CONTINUATION_RE,
    GH_PUBLISH_RE,
    SEGMENT_SPLIT_RE,
    body_file_operands,
    read_body,
    resolve_operand,
)


# Phrases that are a trailer when they start a line of a GitHub body.
TEXT_PATTERNS = (
    r"Co-Authored-By:\s*Claude",
    r"Co-Authored-By:\s*.*@anthropic\.com",
    r"Generated\s+with\s+\[?Claude\s+Code\]?",
    r"Generated\s+with.*Claude",
    r"\xf0\x9f\xa4\x96\s*Generated\s+with",  # robot emoji + Generated with (UTF-8 bytes form)
    r"🤖\s*Generated\s+with",
    r"<noreply@anthropic\.com>",
)
# The session-link trailer the Claude Code harness asks agents to append, and
# the bare session URL it asks for in a PR body. Both need the URL, so prose
# naming the trailer passes.
LINK_PATTERNS = (
    r"\bClaude-Session:\s*https?://",
    r"https?://claude\.ai/code/session_[A-Za-z0-9]",
)
FORBIDDEN_PATTERNS = TEXT_PATTERNS + LINK_PATTERNS

_COMMIT_FLAGS = re.compile(r"-[A-Za-z]*F$")
# A body is "starts a line" after any of these: markdown quote, bullet, indent.
_LINE_LEAD = r"^[ \t>*\-]*"
# The inline body flag; the body's first line starts right after its opening quote.
_BODY_FLAG_RE = re.compile(r"""(?:--body|-b)(?:\s+|=)\s*["']?""")
# Spellings of a line break that stay literal inside a quoted shell argument.
_LITERAL_NEWLINE_RE = re.compile(r"\\n|`n")
MAX_QUOTED_LINE = 120


def first_trailer(text: str, *, line_start: bool) -> Optional[Tuple[str, str]]:
    """``(pattern, offending line)`` for the first trailer in `text`, else ``None``.

    ``line_start`` limits `TEXT_PATTERNS` to a line's start (a GitHub body);
    otherwise every pattern matches anywhere, as for a commit message.
    """
    for pattern in FORBIDDEN_PATTERNS:
        if line_start and pattern in TEXT_PATTERNS:
            found = re.search("(?m)" + _LINE_LEAD + "(?:" + pattern + ")", text, flags=re.IGNORECASE)
        else:
            found = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if found:
            start = text.rfind("\n", 0, found.start()) + 1
            end = text.find("\n", found.end())
            line = text[start:end if end != -1 else len(text)].strip()
            return pattern, line[:MAX_QUOTED_LINE]
    return None


def _gh_inline_text(cmd: str) -> str:
    """`cmd` with a line break wherever a body line could start.

    A one-line `--body "Co-Authored-By: Claude"` has its trailer right after the
    quote, and a literal `\\n` or `` `n `` inside a quoted body is a line break
    the posted markdown will not have but a reader of the command still sees.
    """
    text = _BODY_FLAG_RE.sub(lambda m: m.group(0) + "\n", cmd)
    return _LITERAL_NEWLINE_RE.sub("\n", text)


def commit_invocations(cmd: str, base: Path) -> List[Tuple[List[str], Optional[Path]]]:
    """``(words after commit, directory)`` for every `git commit` segment of `cmd`.

    The directory follows a leading `cd`/`Set-Location`/`pushd` and the commit's
    own `git -C <dir>`, as `secret_scan_guard.body_file_operands` follows them for
    a `gh` body, so a relative operand resolves where git will read it. ``None``
    means a `cd` whose target could not be read. A commit inside a nested shell's
    quoted argument is not a segment of its own and is not listed.
    """
    found: List[Tuple[List[str], Optional[Path]]] = []
    current: Optional[Path] = base
    for segment in SEGMENT_SPLIT_RE.split(CONTINUATION_RE.sub(" ", cmd)):
        words = _lib.shell_words(segment)
        if not words:
            continue
        git_at = next((i for i, w in enumerate(words) if _lib.GIT_TOKEN_RE.search(w)), None)
        if git_at is None:
            verb = words[0].lower()
            if verb in shell_paths.CD_VERBS or verb in shell_paths.POP_VERBS:
                current = shell_paths.follow_cd(words, current)
            continue
        flags = _lib.git_globals(words, git_at + 1)
        sub = flags.subcommand
        if sub >= len(words) or words[sub].lower() != "commit":
            continue
        where = shell_paths.resolve(flags.workdir, current) if flags.workdir else current
        found.append((words[sub + 1:], where))
    return found


def commit_message_files(cmd: str, base: Path) -> List[Tuple[str, Optional[Path]]]:
    """``(operand, directory)`` for every `-F`/`--file` of a `git commit` segment."""
    operands: List[Tuple[str, Optional[Path]]] = []
    for words, where in commit_invocations(cmd, base):
        i = 0
        while i < len(words):
            word = words[i]
            if word == "--file" or _COMMIT_FLAGS.match(word):
                if i + 1 < len(words):
                    operands.append((words[i + 1], where))
                i += 2
                continue
            if word.startswith("--file="):
                operands.append((word[len("--file="):], where))
            elif word.startswith("-F") and len(word) > 2:
                operands.append((word[2:], where))
            i += 1
    return operands


def _block_file(kind: str, path: Path, pattern: str, line: str) -> None:
    _lib.block(
        f"Blocked: {kind} {path} contains an AI attribution trailer "
        f"(matched: {pattern}). Remove this line from the file, then retry: {line!r}. "
        "The user explicitly rejects `Co-Authored-By: Claude`, "
        "`Generated with Claude Code`, a `Claude-Session:` link or a "
        "claude.ai session URL, and similar."
    )


def _scan_files(operands: List[Tuple[str, Optional[Path]]], kind: str, *, line_start: bool) -> None:
    for raw, raw_base in operands:
        path = resolve_operand(raw, raw_base)
        body = read_body(path) if path is not None else None
        hit = first_trailer(body, line_start=line_start) if body else None
        if hit:
            _block_file(kind, path, *hit)


def _guard_gh_publish(cmd: str, base: Path) -> None:
    hit = first_trailer(_gh_inline_text(cmd), line_start=True)
    if hit:
        _lib.block(
            "Blocked: a GitHub issue/PR body or comment carries an AI attribution "
            f"trailer or session link (matched: {hit[0]}). Remove this line from the "
            f"body, then retry: {hit[1]!r}. The user explicitly rejects "
            "`Co-Authored-By: Claude`, `Generated with Claude Code`, a "
            "`Claude-Session:` link or a claude.ai session URL, and similar; a "
            "public repo keeps them forever."
        )
    _scan_files(body_file_operands(cmd, base), "the body file", line_start=True)


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    if GH_PUBLISH_RE.search(cmd):
        _guard_gh_publish(cmd, _lib.cwd(payload))

    if not _lib.runs_git_commit(cmd):
        _lib.allow()

    # Cheap and broad: search the whole command string. Catches `-m "..."`,
    # `-m '...'`, heredoc `<<'EOF' ... EOF`, etc.
    hit = first_trailer(cmd, line_start=False)
    if hit:
        _lib.block(
            "Blocked: commit message contains an AI attribution trailer "
            "(matched: " + hit[0] + "). "
            "The user explicitly rejects `Co-Authored-By: Claude`, "
            "`Generated with Claude Code`, a `Claude-Session:` link, and similar. "
            "Re-draft the commit message without it."
        )

    # `-F <file>`: the message is in the file, which the command string never shows.
    _scan_files(commit_message_files(cmd, _lib.cwd(payload)), "the commit message file", line_start=False)

    _lib.allow()


if __name__ == "__main__":
    main()
