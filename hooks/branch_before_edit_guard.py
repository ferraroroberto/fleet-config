"""Block an edit on the default branch from a launcher-
dispatched worker.

Triggers on `PreToolUse` for native `Edit`/`Write`/`MultiEdit` and Codex
`apply_patch`. **Blocks** when any target file's own directory resolves to the repo's default branch
(`main`/`master`, or whatever `origin/HEAD` says) **and** the process is a
launcher-dispatched session (the `APP_LAUNCHER_SESSION_ID` env var App
Launcher injects into Board/Job children — same idiom `session_state.py`
reads). A bare interactive session (Roberto typing in a terminal) carries no
such env var and is never touched.

Why: `global-CLAUDE.md`'s "never commit to `main` directly" rule already
exists; this closes the enforcement gap that let two launcher-dispatched
workers start editing files on the default branch without cutting a branch
first (fleet-config#442, fleet-config#464).

**Take 2.** The first attempt (PR #472) resolved the branch from the
session's `cwd` and was reverted the same evening (PR #477) for two live
fleet-wide false positives: it judged a worktree worker by the *primary*
checkout's branch, and it blocked writes to paths entirely outside any repo
(e.g. the standing chief writing scratch files under `E:\\tmp\\chief`) because
the session's cwd repo happened to sit on `main`. This version resolves
strictly from the directory containing the file actually being written —
a worktree's own directory naturally resolves that worktree's own HEAD, and a
non-repo target naturally fails the git call and allows, with no
special-casing for either failure mode.

**Take 2's own false positive (fleet-config#489).** Resolving from the target
directory fixed writes *outside* a repo but still blocked writes *inside* one
that git deliberately ignores — `life-os`'s `/journal-daily` could not write
its gitignored `.active-skill` marker, and the standing chief could not write
`hooks/state/chief-handover.md` (gitignored, but reached through a junction so
the guard sees a `fleet-config` file on `main`). Both share the property that
makes take-1's non-repo case safe: the write can never become a commit, so the
rule this guard enforces cannot be broken by it. `_is_ignored` therefore
exempts gitignored targets — and only those. An untracked-but-not-ignored new
file *can* be committed to the default branch, so it stays blocked.

Escape hatch: set `CLAUDE_HOOKS_ALLOW_MAIN_EDIT=1` for the rare case a
launcher-dispatched flow needs a deliberate default-branch write.

Git access is `_lib.run_git` / `_lib.resolve_default_branch_ref`, i.e. this
hook's *own* tree. It used to `sys.path`-insert `../skills/_lib` and import
`git_run` directly, which broke the convention every other hook here states —
the two trees install independently, so a hook must stay importable with
nothing but its own directory on `sys.path` (fleet-config#564).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


GUARDED_TOOLS = ("Edit", "Write", "MultiEdit", "apply_patch")


def _current_branch(target_dir: Path) -> "str | None":
    """Return the checked-out branch for `target_dir`, or `None` on any
    failure — a non-repo directory, detached HEAD, or a git error all fail
    open the same way. This single call is also the "is this a repo at all"
    check: `rev-parse` on a non-repo path simply returns non-zero.
    """
    res = _lib.run_git(["-C", str(target_dir), "rev-parse", "--abbrev-ref", "HEAD"])
    if res.returncode != 0:
        return None
    branch = res.stdout.strip()
    if not branch or branch == "HEAD":  # detached HEAD marker
        return None
    return branch


def _default_branch(target_dir: Path) -> str:
    """Bare default-branch name for `target_dir`'s repo.

    Uses `_lib.resolve_default_branch_ref`'s default candidate-probing
    (`origin/main`, `main`, `master`) rather than `dirty_tree_check`'s
    `candidates=()` variant — that variant skips probing on `symbolic-ref`
    failure and would default every repo with no `origin` configured to
    `"main"`, silently losing `master` detection for a remote-less repo
    (including this guard's own unit-test fixtures).
    """
    ref = _lib.resolve_default_branch_ref(target_dir)
    return ref[len("origin/"):] if ref.startswith("origin/") else ref


def _is_ignored(target: Path) -> bool:
    """Whether git ignores `target`, i.e. whether writing it could ever become
    a commit on the branch this guard protects.

    `check-ignore` matches a *pathname* against the ignore rules and does not
    require the file to exist, so a `Write` creating a brand-new gitignored
    file resolves correctly. Exit 0 means ignored, 1 means not ignored; any
    other exit (128, a git error) is *not* treated as ignored — by this point
    the caller has already established a repo on the default branch, so an
    unresolvable probe must fail closed rather than silently open the gate.

    `target` must already be junction-resolved (see `main`): unlike `-C`, which
    git follows through a Windows junction on its own, the *pathname argument*
    is matched lexically against the worktree root, so the junction spelling
    `C:\\Users\\rober\\.claude\\hooks\\state\\chief-handover.md` exits 128 with
    "is outside repository at 'E:/automation/fleet-config'" — the fail-closed
    path, which would have left fleet-config#489's second live repro blocked.
    """
    res = _lib.run_git(["-C", str(target.parent), "check-ignore", "-q", str(target)])
    return res.returncode == 0


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in GUARDED_TOOLS:
        _lib.allow()

    # Cheapest *and* most selective gate first (fleet-config#680). Only a
    # launcher-dispatched session can ever be blocked here, and the vast
    # majority of live sessions are interactive ones that carry no such
    # variable — so asking this before `_current_branch`/`_default_branch`
    # keeps two `git` spawns off every Edit/Write in every ordinary session
    # fleet-wide. `run_git`'s own docstring calls this "by far the
    # highest-frequency git spawn this repo owns"; the whole decision below is
    # pure, so ordering these guards changes cost, never verdict.
    if not os.environ.get("APP_LAUNCHER_SESSION_ID", "").strip():
        _lib.allow()

    if os.environ.get("CLAUDE_HOOKS_ALLOW_MAIN_EDIT") == "1":
        _lib.allow()

    edit = _lib.edit_event(payload)
    if edit.status != "known":
        _lib.allow()

    for change in edit.targets:
        # Deletes and rename sources still mutate the source path. Check both
        # endpoints so one multi-file patch cannot hide a main-tree edit behind
        # a safe worktree target.
        paths = (change.path,) if change.source_path is None else (change.source_path, change.path)
        for raw_target in paths:
            # Resolve junctions/symlinks up front. `~/.claude/hooks/` is a
            # junction into this repo; non-strict resolution also handles new
            # files. Fall back to the raw path on a pathological resolution
            # failure, preserving the previous fail-closed behavior.
            try:
                target = raw_target.resolve()
            except OSError:
                target = raw_target
            target_dir = target.parent
            branch = _current_branch(target_dir)
            if branch is None:
                continue
            default_branch = _default_branch(target_dir)
            if branch != default_branch or _is_ignored(target):
                continue
            _lib.block(
                f"Blocked: editing on '{branch}' from a launcher-dispatched session. "
                "Cut a branch first — git checkout -b <type>/<issue-N>-<slug> — before "
                "editing (global-CLAUDE.md: never commit to main directly). Set "
                "CLAUDE_HOOKS_ALLOW_MAIN_EDIT=1 to override for a deliberate default-branch write."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
