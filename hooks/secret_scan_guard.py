"""Block `git commit` when a live secret is about to be committed.

Triggers on `PreToolUse` for `Bash`. When the command is a `git commit`, scans
the **staged diff** (`git diff --cached`) — and the command string itself — for
a real credential. Today the one pattern that matters across this fleet is a
Slack **bot token** (`xoxb-…`): the user keeps Slack creds in a secret-managed
location (`.env` / `SLACK_BOT_TOKEN`), never in a tracked file. This is the wire
that catches the mistake before a token lands in `git log` (claude-config#74).

Why scan the staged diff, not just the command string: the no-AI-trailer guard
only needs the commit *message*, which lives in the command. A leaked secret
instead lives in a **file** being committed, so the command string alone is
blind to it — we have to look at what's actually staged.

Matching is deliberately narrow so it never trips on this repo's own docs, which
legitimately contain the *placeholder* forms `xoxb-…` and `xoxb-<token>`. A real
token is `xoxb-` followed by hyphen-separated digit groups and a long alnum
secret tail; the placeholders have no such body and are ignored.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# A live Slack bot token: `xoxb-` then at least two hyphen-joined digit groups
# and an alphanumeric secret tail of 8+ chars. The placeholder forms this repo's
# own docs use (`xoxb-…`, `xoxb-<token>`, bare `xoxb-`) lack that body and so do
# not match.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Slack bot token (xoxb-)", r"xoxb-\d{6,}-\d{6,}-[A-Za-z0-9]{8,}"),
)


def _is_git_commit(cmd: str) -> bool:
    return "git" in cmd and "commit" in cmd


def _staged_diff(repo_cwd: Path) -> str:
    """Return the staged diff for the repo at ``repo_cwd`` (best-effort).

    Any failure (not a repo, git missing, timeout) yields ``""`` — the guard then
    falls back to scanning just the command string and never blocks spuriously.
    """
    try:
        res = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=str(repo_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout or ""


def _scan(text: str) -> "tuple[str, str] | None":
    """Return ``(label, pattern)`` of the first secret found, else ``None``."""
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            return label, pattern
    return None


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Bash":
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd or not _is_git_commit(cmd):
        _lib.allow()

    # Scan both the staged content and the command string itself (a secret could
    # ride in via an inline `git add` + commit one-liner, or a heredoc).
    haystack = cmd + "\n" + _staged_diff(_lib.cwd(payload))

    hit = _scan(haystack)
    if hit:
        label, pattern = hit
        _lib.block(
            "Blocked: a live secret is staged for commit (" + label + "). "
            "The user keeps credentials in a secret-managed location (.env / "
            "SLACK_BOT_TOKEN), never in a tracked file. Unstage the file, move "
            "the value into .env (or the OS keyring), and reference it from there "
            "before committing. If this is a false positive on a placeholder, "
            "redact the token body so it no longer looks live."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
