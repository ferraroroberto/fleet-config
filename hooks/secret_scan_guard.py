"""Block `git commit` when a live secret is about to be committed.

Triggers on `PreToolUse` for `Bash`. When the command is a `git commit`, scans
the **staged diff** (`git diff --cached`) — and the command string itself — for
a real credential. Today the one pattern that matters across this fleet is a
Slack **bot token** (`xoxb-…`): the user keeps Slack creds in a secret-managed
location (`.env` / `SLACK_BOT_TOKEN`), never in a tracked file. This is the wire
that catches the mistake before a token lands in `git log` (fleet-config#74).

Why scan the staged diff, not just the command string: the no-AI-trailer guard
only needs the commit *message*, which lives in the command. A leaked secret
instead lives in a **file** being committed, so the command string alone is
blind to it — we have to look at what's actually staged.

Matching is deliberately narrow so it never trips on this repo's own docs, which
legitimately contain the *placeholder* forms `xoxb-…` and `xoxb-<token>`: a real
token has a long secret body and the placeholders do not.

What counts as a credential is **not** decided here — `_lib.SECRET_PATTERNS` is
the one definition for this tier, shared with `context_filter`'s redactor
(fleet-config#561). This module used to carry its own one-family copy, which had
drifted strictly narrower than the redactor's four, so the guard blocked a Slack
token and waved through an OpenAI key, a GitHub PAT, and an AWS access key id.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


def _is_git_commit(cmd: str) -> bool:
    return "git" in cmd and "commit" in cmd


def _staged_diff(repo_cwd: Path) -> str:
    """Return the staged diff for the repo at ``repo_cwd`` (best-effort).

    Any failure (not a repo, git missing, timeout) yields ``""`` — the guard then
    falls back to scanning just the command string and never blocks spuriously.

    Routed through :func:`_lib.run_git` rather than a hand-rolled
    ``subprocess.run(["git", …])`` (fleet-config#677): this fires on **every
    commit in every repo in the fleet**, and a raw spawn silently opts out of
    ``GIT_OPTIONAL_LOCKS=0`` — the one-line fix that exists precisely because
    ``git diff`` takes ``.git/index.lock`` to write back a refreshed stat cache,
    and a hook killed mid-refresh strands a 0-byte lock that blocks every write
    in that repo while every read keeps exiting 0 (fleet-config#667).
    """
    try:
        res = _lib.run_git(["-C", str(repo_cwd), "diff", "--cached"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout or ""


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

    hit = _lib.scan_for_secret(haystack)
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
