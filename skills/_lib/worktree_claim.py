"""Concurrency primitive for the issue-* skills: claim-or-worktree.

Two top-level agent sessions working the **same** repo used to collide because
they shared one working directory — a `git checkout` in session B rewrote the
tree under session A mid-build (fleet-config#143). The collision window is not
branch-cut time; it is the minutes-long *study* phase before an agent writes
anything. By the time the second agent cuts its branch, the damage is done.

The fix is first-come-first-served, claimed at the **very first action**:

  - The first session to `acquire` a repo wins its **primary checkout** and works
    on `main` in place, exactly as before.
  - Every session after that gets `MODE=worktree` and builds in an isolated
    sibling worktree (`<repo>-wt-<N>`) on its own branch, sharing the repo's
    object store. Separate HEAD + separate files ⇒ no checkout/HEAD race.
  - `release` (on finish, from the primary) frees the claim for the next session.

The claim is a directory created with an **atomic** `mkdir` under the repo's
*common* git dir (`git rev-parse --git-common-dir`), which every worktree of the
repo shares — so the claim is visible from the primary checkout and from every
linked worktree. Exactly one racer wins the `mkdir`; the rest fall through to
worktree mode. A crashed session's claim is reclaimed once it ages past the TTL
(no fragile PID-liveness check on Windows).

Windows-specific by design (the fleet is Windows): worktree `.venv` is a
**junction** to the primary's `.venv` (`mklink /J`) — worktrees don't share
untracked files, and a 24-repo fleet can't recreate heavy venvs per worktree.
The teardown order is load-bearing: a junction MUST be stripped with
`rmdir` (reparse-safe, no `/s`) BEFORE `git worktree remove`, or git's recursive
delete follows the junction and wipes the *real* venv (proven the hard way; same
junction footgun as uninstall.ps1, fleet-config#136).

Subcommands:

  acquire <repo-root> [--issue N] [--branch B] [--ttl-hours H]
      Atomically claim the primary checkout. Prints `MODE=primary` (work in
      place) or `MODE=worktree` (caller then calls setup-worktree). Reclaims a
      claim older than the TTL.

  setup-worktree <repo-root> <issue-N> <branch>
      `git worktree add <repo>-wt-<N> -b <branch> <origin-main>` + junction the
      primary's .venv into it. Prints `WORKTREE=<path>`.

  release <repo-root>
      Remove the primary claim. Idempotent. (Worktree sessions never hold it.)

  remove-worktree <worktree-path>
      Reparse-safe teardown: strip the .venv junction, then
      `git worktree remove --force` + `git worktree prune`.

  status <repo-root>
      Print the current claim holder (if any) and `git worktree list`.

stdlib + the `git` CLI only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):  # UTF-8 even when stdout is captured (cp1252 fallback)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

LOCK_NAME = "issue-claim.lock"
META_NAME = "meta.json"
DEFAULT_TTL_HOURS = 8.0


# ---- pure helpers (unit-tested without git) -------------------------------

def worktree_path(repo_root: Path, issue: str) -> Path:
    """Sibling worktree path: `<parent>/<repo-name>-wt-<N>`.

    Same convention as /issue-batch. The `<repo>-wt-<N>` *prefix-matches* the
    repo's `cwd_prefix` in projects.toml, so notify_on_idle still names the right
    project (a `.worktrees/` layout would break that match).
    """
    return repo_root.parent / f"{repo_root.name}-wt-{issue}"


def is_stale(meta: Optional[dict], now: float, ttl_hours: float) -> bool:
    """A claim is stale once it ages past the TTL (or if its meta is unreadable).

    No PID-liveness check: a one-shot helper invocation can't capture the
    long-lived agent-session PID, and Windows PID checks are unreliable. The TTL
    is the crash-safety valve — a generous default so a legitimately long build
    is never reclaimed out from under itself.
    """
    if not meta:
        return True
    created = meta.get("created")
    try:
        return (now - float(created)) > ttl_hours * 3600.0
    except (TypeError, ValueError):
        return True


def read_meta(lock_dir: Path) -> Optional[dict]:
    try:
        return json.loads((lock_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(lock_dir: Path, meta: dict) -> None:
    (lock_dir / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def try_acquire(lock_dir: Path, meta: dict, now: float, ttl_hours: float) -> Tuple[str, dict]:
    """Atomically claim `lock_dir`. Returns ('primary', meta) on win, else
    ('worktree', holder-meta). Reclaims a stale lock; loses a reclaim race
    gracefully to worktree mode. Pure filesystem — hermetic, no git.
    """
    try:
        lock_dir.mkdir(parents=False)  # atomic: FileExistsError if already held
    except FileExistsError:
        holder = read_meta(lock_dir)
        if not is_stale(holder, now, ttl_hours):
            return "worktree", holder or {}
        # Stale -> reclaim. rmtree + re-mkdir isn't atomic as a pair, so a
        # concurrent reclaimer may win the re-mkdir; that racer's FileExistsError
        # below sends it to worktree mode. Net: exactly one ends up primary.
        shutil.rmtree(lock_dir, ignore_errors=True)
        try:
            lock_dir.mkdir(parents=False)
        except FileExistsError:
            return "worktree", read_meta(lock_dir) or {}
    write_meta(lock_dir, meta)
    return "primary", meta


# ---- git / junction ops (Windows; thin subprocess wrappers) ---------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def common_dir(repo: Path) -> Path:
    """The shared git dir (one per repo, visible from every worktree)."""
    out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(out)


def lock_dir_for(repo: Path) -> Path:
    return common_dir(repo) / LOCK_NAME


def main_ref(repo: Path) -> str:
    """origin's default branch, e.g. 'origin/main'. Falls back to origin/main."""
    res = _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
    ref = res.stdout.strip()
    if res.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)
    return "origin/main"


def is_primary_checkout(repo: Path) -> bool:
    """True for the primary checkout, False for a linked worktree."""
    git_dir = _git(repo, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip()
    return Path(git_dir).resolve() == common_dir(repo).resolve()


def _strip_junction(path: Path) -> None:
    """Remove a directory junction by its reparse point ONLY, never its target.

    `rmdir` without `/s` removes an empty dir or a reparse point and refuses a
    non-empty real dir — so it can NEVER recurse into a junction's target. This
    is the load-bearing step: doing it before `git worktree remove` is what
    keeps the primary's real .venv intact (fleet-config#136 / #143).
    """
    if path.exists() or path.is_symlink():
        subprocess.run(["cmd", "/c", "rmdir", str(path)], capture_output=True, text=True)


def setup_worktree(repo: Path, issue: str, branch: str) -> Path:
    wt = worktree_path(repo, issue)
    if wt.exists():
        sys.exit(f"Worktree path already exists: {wt}\n"
                 f"Probably stale — clean with: git -C {repo} worktree remove --force {wt}")
    _git(repo, "worktree", "add", str(wt), "-b", branch, main_ref(repo))

    venv = repo / ".venv"
    if venv.is_dir():
        link = wt / ".venv"
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(venv)],
            capture_output=True, text=True,
        )
        if res.returncode != 0 or not link.exists():
            # Roll back the half-made worktree so we never leave a broken one
            # behind. Strip any partial junction FIRST (reparse-safe) so the
            # rollback remove can't follow it into the primary's real venv.
            _strip_junction(link)
            _git(repo, "worktree", "remove", "--force", str(wt), check=False)
            sys.exit(f"Failed to junction .venv into the worktree: {res.stderr.strip() or res.stdout.strip()}")
    return wt


def remove_worktree(wt: Path) -> None:
    if not wt.exists():
        print(f"Worktree already gone: {wt}")
        return
    primary = common_dir(wt).parent  # common dir is <primary>/.git
    _strip_junction(wt / ".venv")     # MUST precede the remove (see _strip_junction)
    _git(primary, "worktree", "remove", "--force", str(wt), check=False)
    _git(primary, "worktree", "prune", check=False)
    print(f"Removed worktree: {wt}")


# ---- CLI ------------------------------------------------------------------

def _resolve_repo(arg: str) -> Path:
    repo = Path(arg).resolve()
    if not repo.exists():
        # Agents often pass the repo name instead of "." or an absolute path.
        # When CWD's basename matches, they clearly mean CWD (fleet-config#162).
        cwd = Path.cwd()
        if cwd.name.casefold() == Path(arg).name.casefold() and not Path(arg).is_absolute():
            return cwd
        sys.exit(f"No such repo path: {repo}")
    return repo


def cmd_acquire(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    meta = {
        "created": time.time(),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "issue": args.issue,
        "branch": args.branch,
        "repo": str(repo),
    }
    mode, holder = try_acquire(lock, meta, time.time(), args.ttl_hours)
    print(f"MODE={mode}")
    if mode == "worktree" and holder:
        print(f"# primary held since {holder.get('created_iso', '?')} "
              f"by issue {holder.get('issue', '?')} on {holder.get('branch', '?')}",
              file=sys.stderr)
    return 0


def cmd_setup_worktree(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    wt = setup_worktree(repo, args.issue, args.branch)
    print(f"WORKTREE={wt}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    if lock.exists():
        shutil.rmtree(lock, ignore_errors=True)
        print(f"Released claim on {repo}")
    else:
        print(f"No claim to release on {repo}")
    return 0


def cmd_remove_worktree(args: argparse.Namespace) -> int:
    remove_worktree(Path(args.worktree_path).resolve())
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    """Print `primary` or `worktree` for the current checkout — the deterministic
    primary-vs-linked-worktree decision /issue-finish keys its teardown on."""
    repo = _resolve_repo(args.repo_root)
    print("primary" if is_primary_checkout(repo) else "worktree")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    holder = read_meta(lock) if lock.exists() else None
    if holder:
        print(f"CLAIM=held  issue={holder.get('issue')}  branch={holder.get('branch')}  "
              f"since={holder.get('created_iso')}")
    else:
        print("CLAIM=free")
    print(_git(repo, "worktree", "list", check=False).stdout.strip())
    return 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="worktree_claim", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("acquire", help="atomically claim the primary checkout")
    a.add_argument("repo_root")
    a.add_argument("--issue", default=None)
    a.add_argument("--branch", default=None)
    a.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    a.set_defaults(func=cmd_acquire)

    s = sub.add_parser("setup-worktree", help="create the sibling worktree + junction .venv")
    s.add_argument("repo_root")
    s.add_argument("issue")
    s.add_argument("branch")
    s.set_defaults(func=cmd_setup_worktree)

    r = sub.add_parser("release", help="release the primary claim (idempotent)")
    r.add_argument("repo_root")
    r.set_defaults(func=cmd_release)

    rw = sub.add_parser("remove-worktree", help="reparse-safe worktree teardown")
    rw.add_argument("worktree_path")
    rw.set_defaults(func=cmd_remove_worktree)

    md = sub.add_parser("mode", help="print 'primary' or 'worktree' for the cwd checkout")
    md.add_argument("repo_root")
    md.set_defaults(func=cmd_mode)

    st = sub.add_parser("status", help="show claim holder + worktree list")
    st.add_argument("repo_root")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
