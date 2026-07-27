"""Block an `Edit`/`Write` on `main`/`master` from a launcher-dispatched worker.

Triggers on `PreToolUse` for `Edit`/`Write`. **Blocks** (exit 2) when HEAD is
`main` or `master` **and** the process is a launcher-dispatched session (the
`APP_LAUNCHER_SESSION_ID` env var App Launcher injects into Board/Job children —
same idiom `session_state.py` reads). A bare interactive session (Roberto typing
in a terminal) carries no such env var and is never touched.

Why: `global-CLAUDE.md`'s "never commit to `main` directly" rule already exists;
this closes the enforcement gap that let two launcher-dispatched workers start
editing files on `main` without cutting a branch first (fleet-config#442,
fleet-config#464). The rule is easy to follow when work starts with an edit and
easy to skip when it starts with reading — this fires at the exact moment a
worker skipped `/issue-start`'s branch-cut step.

Scoped narrowly on purpose: this must never block a legitimate interactive
main-branch write, most notably `/design-sync apply`, which deliberately writes
straight into a dirty `main` with no branch step.

Escape hatch: set `CLAUDE_HOOKS_ALLOW_MAIN_EDIT=1` for the rare case a
launcher-dispatched flow needs a deliberate main-branch write.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


PROTECTED_BRANCHES = {"main", "master"}


def _current_branch(repo_cwd: Path) -> "str | None":
    """Return the checked-out branch name, or ``None`` on any failure.

    Fails open (returns ``None``) on a non-repo cwd, detached HEAD, missing
    git, or a timeout — the guard never blocks on an ambiguous read.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_cwd),
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_lib.NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    branch = res.stdout.strip()
    if not branch or branch == "HEAD":  # detached HEAD marker
        return None
    return branch


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in ("Edit", "Write"):
        _lib.allow()

    if os.environ.get("CLAUDE_HOOKS_ALLOW_MAIN_EDIT") == "1":
        _lib.allow()

    branch = _current_branch(_lib.cwd(payload))
    if branch is None or branch not in PROTECTED_BRANCHES:
        _lib.allow()

    if not os.environ.get("APP_LAUNCHER_SESSION_ID", "").strip():
        _lib.allow()

    _lib.block(
        f"Blocked: editing on '{branch}' from a launcher-dispatched session. "
        "Cut a branch first — git checkout -b <type>/<issue-N>-<slug> — before "
        "editing (global-CLAUDE.md: never commit to main directly). Set "
        "CLAUDE_HOOKS_ALLOW_MAIN_EDIT=1 to override for a deliberate main-branch write."
    )


if __name__ == "__main__":
    main()
