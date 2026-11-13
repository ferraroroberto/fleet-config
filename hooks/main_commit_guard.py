"""Refuse `git commit` on a repo's default branch.

`global-CLAUDE.md` says never commit to `main` directly, with two sanctioned
exceptions. This is the wire that holds the rule for a commit; `branch_before_edit_guard`
holds it for a launcher worker's edits.

Triggers on `PreToolUse` for `Bash` and `PowerShell`. A command that runs no
`git commit` returns at once on `_lib.runs_git_commit`, with no git spawn. For
each commit it finds (`pre_commit_no_ai_trailer.commit_invocations` follows a
leading `cd` and `git -C`; a commit inside `bash -c "…"` is judged from the
payload cwd) it asks git three things through `_lib.run_git`: the checked-out
branch, whether HEAD exists, and the repo's default branch. It refuses when the
branch is the default.

What passes without a grant:

- any other branch, including `quick/<slug>`: `/quick` commits on its ephemeral
  branch and fast-forwards main afterwards, so its commit is never on main;
- a detached HEAD, and the first commit of a new repo (HEAD unborn), which can
  take no PR path.

The other sanctioned exception is a scheduled map refresh (`/system-map`,
`/config-map`) committing its generated `architecture/` output on main. It runs
with nobody attending, so the skill grants itself a short-lived marker first
(`skills/_lib/commit_guard/marker.py grant`) and revokes it after. The grant
must run as its own command, and succeed, before the `git commit` command: this
guard judges a whole command before any part of it runs, so a `grant && git
commit` chain finds no marker and is refused. A marker
covers one repo and the path prefixes it names; the commit's staged files must
all sit under them. An expired, garbled or foreign marker grants nothing.

A fact git cannot establish (a directory that is not a repo, a failing
`rev-parse`, an unreadable `cd`) is NOT CONFIRMED: the commit goes through with
a warning, never a block, because a guard that cannot read the branch must not
stop every commit. The same holds for a staged list the marker check cannot read.

Escape: `CLAUDE_HOOKS_ALLOW_MAIN_COMMIT=1` in the environment the session was
started from. A prefix typed into the command itself never reaches this process.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from branch_before_edit_guard import default_branch  # noqa: E402
from pre_commit_no_ai_trailer import commit_invocations  # noqa: E402

SHELL_TOOLS = ("Bash", "PowerShell")
ALLOW_ENV = "CLAUDE_HOOKS_ALLOW_MAIN_COMMIT"
# One file per repo under the hooks state dir, named by `repo_key`. The grant side is
# skills/_lib/commit_guard/marker.py; the tree boundary forbids importing across, so the
# two spell the directory, the key and the JSON fields alike and the acceptance test
# drives one through the other.
MARKER_DIR = "main-commit-allow"


class Probe(NamedTuple):
    """What git says about the commit's repo: ``state`` is ``"default"``, ``"other"`` or ``"unconfirmed"``."""

    state: str
    branch: str = ""
    top: str = ""
    why: str = ""


def repo_key(top: str) -> str:
    """Stable id of a working tree, the marker file's name."""
    return hashlib.sha1(os.path.normcase(os.path.realpath(top)).encode("utf-8")).hexdigest()[:16]


def _git(where: Path, *args: str):
    return _lib.run_git(["-C", str(where), *args])


def probe(where: Path) -> Probe:
    """Whether a commit run in `where` lands on its repo's default branch."""
    head = _git(where, "symbolic-ref", "-q", "--short", "HEAD")
    if head.returncode == 1:  # not a symbolic ref: detached HEAD
        return Probe("other")
    branch = head.stdout.strip()
    if head.returncode != 0 or not branch:
        return Probe("unconfirmed", why=(head.stderr or "").strip()[:160] or "git could not read HEAD")
    if _git(where, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return Probe("other", branch)  # unborn: the repo's first commit
    if branch != default_branch(where):
        return Probe("other", branch)
    top = _git(where, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return Probe("unconfirmed", branch, why="git could not name the working tree")
    return Probe("default", branch, top.stdout.strip())


def _read_marker(top: str) -> Optional[dict]:
    path = _lib.state_dir() / MARKER_DIR / f"{repo_key(top)}.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("repo_key") != repo_key(top):
        return None
    try:
        if float(row.get("expires_at", 0)) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    paths = row.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        return None
    return row


def _outside(staged: List[str], prefixes: List[str]) -> List[str]:
    return [name for name in staged if not any(name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/") for p in prefixes)]


def _marker_verdict(top: str) -> "tuple[str, str]":
    """``("allow" | "warn" | "deny", detail)`` for a default-branch commit in `top`.

    ``allow``: a live marker covers every staged file. ``deny`` with a detail names
    the files a live marker does not cover; with an empty detail there is no marker.
    ``warn``: a live marker exists but the staged list could not be read.
    """
    row = _read_marker(top)
    if row is None:
        return "deny", ""
    staged = _git(Path(top), "diff", "--cached", "--name-only", "-z")
    if staged.returncode != 0:
        return "warn", f"could not read the staged files ({(staged.stderr or '').strip()[:120]})"
    names = [n for n in staged.stdout.split("\0") if n]
    stray = _outside(names, row["paths"])
    if stray:
        covered = ", ".join(row["paths"])
        return "deny", f"the {row.get('reason', 'map refresh')} grant covers only {covered}, but this commit also stages: {', '.join(stray[:8])}"
    return "allow", ""


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in SHELL_TOOLS:
        _lib.allow()
    cmd = _lib.command_string(payload)
    if not cmd or not _lib.runs_git_commit(cmd):
        _lib.allow()
    if os.environ.get(ALLOW_ENV) == "1":
        _lib.allow()

    base = _lib.cwd(payload)
    places = [where for _words, where in commit_invocations(cmd, base)] or [base]
    unconfirmed: List[str] = []
    for where in places:
        if where is None:
            unconfirmed.append("a `cd` whose target could not be read")
            continue
        found = probe(where)
        if found.state == "unconfirmed":
            unconfirmed.append(f"{where}: {found.why}")
            continue
        if found.state != "default":
            continue
        verdict, detail = _marker_verdict(found.top)
        if verdict == "allow":
            continue
        if verdict == "warn":
            unconfirmed.append(f"{where}: {detail}")
            continue
        _lib.block(
            f"Blocked: git commit on '{found.branch}', the default branch of {found.top}. "
            f"{detail + '. ' if detail else ''}"
            "Cut a branch first — git checkout -b <type>/<issue-N>-<slug> — and commit there "
            "(global-CLAUDE.md: never commit to main directly). A change below the issue threshold "
            "goes through /quick, which commits on its own quick/<slug> branch. Roberto can start "
            f"the session with {ALLOW_ENV}=1 for a deliberate one-off."
        )
    if unconfirmed:
        _lib.warn(
            "main_commit_guard: NOT CONFIRMED whether this commit lands on the default branch "
            f"({'; '.join(unconfirmed)}). The commit was not blocked; check the branch before pushing."
        )
    _lib.allow()


if __name__ == "__main__":
    main()
