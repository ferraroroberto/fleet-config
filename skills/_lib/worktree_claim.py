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

The claim is a directory published **atomically, fully populated** (meta.json
included) via a temp-dir-then-`rename` under the repo's *common* git dir
(`git rev-parse --git-common-dir`), which every worktree of the repo shares —
so the claim is visible from the primary checkout and from every linked
worktree (see `_publish_claim`; a bare `mkdir`-then-write-`meta.json` let two
racers both win, fleet-config#334). Exactly one racer wins the rename; the
rest fall through to worktree mode. A crashed session's claim is reclaimed
once it ages past the TTL (no fragile PID-liveness check on Windows).

Windows-specific by design (the fleet is Windows): worktree `.venv` is a
**junction** to the primary's `.venv` (`mklink /J`) — worktrees don't share
untracked files, and a 24-repo fleet can't recreate heavy venvs per worktree.
The teardown order is load-bearing: a junction MUST be stripped with
`rmdir` (reparse-safe, no `/s`) BEFORE `git worktree remove`, or git's recursive
delete follows the junction and wipes the *real* venv (proven the hard way; same
junction footgun as uninstall.ps1, fleet-config#136).

`git worktree add` populates tracked files only, so a repo's own gitignored
runtime config (`config/webapp_config.json`, `config/apps.json`, ... whatever
each repo's own `config.json`-pattern requires) never makes it into the new
worktree. Left unfixed, an e2e suite that boots a disposable webapp+session-host
hits its own missing-config guard for nearly every test and mass-skips silently
-- the pre-ship gate still reports green (fleet-config#470). `setup_worktree`
copies the primary's `config/*.json` (excluding `*.sample.json` templates) into
the worktree right after the checkout is created.

Subcommands:

  acquire <repo-root> [--issue N] [--branch B] [--ttl-hours H]
      Atomically claim the primary checkout. Prints `MODE=primary` (work in
      place) or `MODE=worktree` (caller then calls setup-worktree). Reclaims a
      claim older than the TTL, or one whose recorded branch no longer exists
      (merged-and-deleted => leaked claim, self-heals on the next acquire).

  setup-worktree <repo-root> <issue-N> <branch>
      `git worktree add <repo>-wt-<N> -b <branch> <origin-main>` + junction the
      primary's .venv into it, then copy the primary's gitignored
      `config/*.json` runtime config (excluding `*.sample.json` templates,
      which are already tracked) into the worktree. Prints `WORKTREE=<path>`.

  release <repo-root>
      Remove the primary claim. Idempotent. (Worktree sessions never hold it.)

  assert-owner <repo-root> <issue-N>
      Guard before a primary-tree `git checkout <main>` (issue-finish step 5,
      issue-start step 4): refuses (exit 1) if the tree is dirty or the claim
      is held by a *different* issue; passes (exit 0) if the tree is clean and
      the claim is free or already owned by <issue-N>. Fleet-config#473.

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
from typing import Callable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

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


def is_stale(
    meta: Optional[dict],
    now: float,
    ttl_hours: float,
    branch_exists: Optional["Callable[[str], bool]"] = None,
) -> bool:
    """A claim is stale once it ages past the TTL, its meta is unreadable, or its
    recorded branch no longer exists.

    No PID-liveness check: a one-shot helper invocation can't capture the
    long-lived agent-session PID, and Windows PID checks are unreliable. The TTL
    is the crash-safety valve — a generous default so a legitimately long build
    is never reclaimed out from under itself.

    `branch_exists` is an injected predicate (`branch -> bool`) so this stays a
    pure, git-free function for the unit tests; the CLI wires in a git-backed
    one. When a claim records a `branch` and that branch is already
    merged-and-deleted, the claim is definitionally done — treat it as stale so a
    leaked claim self-heals on the next `acquire` instead of blocking for the
    full TTL (fleet-config#174). A claim with no recorded branch, or when no
    predicate is supplied, falls back to the TTL alone.
    """
    if not meta:
        return True
    created = meta.get("created")
    try:
        if (now - float(created)) > ttl_hours * 3600.0:
            return True
    except (TypeError, ValueError):
        return True
    branch = meta.get("branch")
    if branch and branch_exists is not None and not branch_exists(branch):
        return True
    return False


def read_meta(lock_dir: Path) -> Optional[dict]:
    try:
        return json.loads((lock_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(lock_dir: Path, meta: dict) -> None:
    (lock_dir / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _publish_claim(lock_dir: Path, meta: dict) -> bool:
    """Atomically create `lock_dir` **fully populated** with meta.json.

    `mkdir` then a separate `write_meta` (the pre-#334 shape) leaves a window
    where `lock_dir` exists but meta.json doesn't yet — a concurrent racer that
    hits `FileExistsError` on its own `mkdir` in that window reads `None` meta,
    treats it as stale, and reclaims (`rmtree` + re-`mkdir`) out from under the
    first winner, which then blindly overwrites meta and *also* returns
    "primary" (fleet-config#334: two sessions both got MODE=primary on the same
    repo, silently losing one session's uncommitted work).

    Fix: write meta into a private temp dir first (invisible to every other
    process — its name is never guessed), then publish with one `Path.rename`.
    On Windows `os.rename` never replaces an existing destination — it raises
    if `lock_dir` already exists — so the rename is the single atomic
    compare-and-swap: `lock_dir` is only ever observed either fully absent or
    fully populated, never in between.
    """
    tmp_dir = lock_dir.with_name(f"{lock_dir.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp_dir.mkdir(parents=False)
    write_meta(tmp_dir, meta)
    try:
        tmp_dir.rename(lock_dir)
        return True
    except OSError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False


def owner_check(holder: Optional[dict], issue: str, dirty: bool) -> Tuple[bool, str]:
    """Decide whether `issue` may switch this checkout's primary tree to main.

    Pure decision for the `assert-owner` guard (fleet-config#473): a checkout
    that lands a merge (`/issue-finish` step 5) or syncs main (`/issue-start`
    step 4) must refuse rather than blindly `git checkout <main>` when either
    (a) the tree is dirty — uncommitted work that isn't this session's to
    switch out from under, or (b) the claim is live and held by a *different*
    issue. Passes when the tree is clean and the claim is free or already
    owned by `issue`. Returns `(ok, reason)`; `reason` is the human-readable
    line the CLI prints either way.
    """
    if dirty:
        return False, "working tree has uncommitted changes"
    if holder and str(holder.get("issue")) != str(issue):
        return False, (f"primary held since {holder.get('created_iso', '?')} "
                        f"by issue {holder.get('issue', '?')} on {holder.get('branch', '?')}")
    return True, ("free" if not holder else "owned")


def try_acquire(
    lock_dir: Path,
    meta: dict,
    now: float,
    ttl_hours: float,
    branch_exists: Optional["Callable[[str], bool]"] = None,
) -> Tuple[str, dict]:
    """Atomically claim `lock_dir`. Returns ('primary', meta) on win, else
    ('worktree', holder-meta). Reclaims a stale lock; loses a reclaim race
    gracefully to worktree mode. Pure filesystem — hermetic, no git; the
    optional `branch_exists` predicate (passed straight to `is_stale`) is the
    only seam where the CLI injects git, so the tests stay hermetic.
    """
    if _publish_claim(lock_dir, meta):
        return "primary", meta
    holder = read_meta(lock_dir)
    if not is_stale(holder, now, ttl_hours, branch_exists):
        return "worktree", holder or {}
    # Stale -> reclaim. rmtree + re-publish isn't atomic as a pair, so a
    # concurrent reclaimer may win the re-publish; that racer's failed rename
    # below sends it to worktree mode. Net: exactly one ends up primary.
    shutil.rmtree(lock_dir, ignore_errors=True)
    if _publish_claim(lock_dir, meta):
        return "primary", meta
    return "worktree", read_meta(lock_dir) or {}


# ---- git / junction ops (Windows; thin subprocess wrappers) ---------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return git_run.run_git(["-C", str(repo), *args], check=check)


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


def branch_exists_on_remote(repo: Path, branch: str) -> bool:
    """True if `branch` still exists on origin (or as a local ref).

    Used to detect a leaked claim whose branch is already merged-and-deleted
    (fleet-config#174). Shells to `git ls-remote` — stdlib + git CLI only, per
    the module contract; no new dependency. On any git failure (offline, no
    remote, transient error) we return True: an inability to *prove* the branch
    is gone must never reclaim a possibly-live claim out from under a peer.
    """
    if not branch:
        return True
    res = _git(repo, "ls-remote", "--heads", "origin", branch, check=False)
    if res.returncode != 0:
        return True  # can't tell -> assume present, fall back to the TTL
    if res.stdout.strip():
        return True
    # Not on the remote — it may be a local-only branch not yet pushed.
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return local.returncode == 0


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
        subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )


def copy_runtime_config(repo: Path, wt: Path) -> list:
    """Copy the primary's gitignored `config/*.json` into a fresh worktree.

    `git worktree add` populates tracked files only, so a repo's own
    gitignored runtime config (`config/webapp_config.json`, `config/apps.json`,
    ...) never lands in the new worktree. An e2e suite that boots a disposable
    webapp+session-host then hits its own missing-config guard for nearly
    every test and mass-skips silently, while the pre-ship gate still reports
    green (fleet-config#470). `*.sample.json` templates are already tracked
    and excluded; a destination file that already exists (e.g. a prior partial
    setup) is left alone rather than overwritten. No-op if the repo has no
    `config/` dir. Returns the list of copied destination paths.
    """
    copied = []
    src_dir = repo / "config"
    if not src_dir.is_dir():
        return copied
    dst_dir = wt / "config"
    for src in sorted(src_dir.glob("*.json")):
        if src.name.endswith(".sample.json"):
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue
        dst_dir.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def setup_worktree(repo: Path, issue: str, branch: str) -> Path:
    wt = worktree_path(repo, issue)
    if wt.exists():
        sys.exit(f"Worktree path already exists: {wt}\n"
                 f"Probably stale — clean with: git -C {repo} worktree remove --force {wt}")
    _git(repo, "worktree", "add", str(wt), "-b", branch, main_ref(repo))
    copied = copy_runtime_config(repo, wt)
    if copied:
        print(f"CONFIG_COPIED={len(copied)}: "
              f"{', '.join(p.name for p in copied)}", file=sys.stderr)

    venv = repo / ".venv"
    if venv.is_dir():
        link = wt / ".venv"
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(venv)],
            capture_output=True, text=True, creationflags=NO_WINDOW,
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

def _resolve_path_arg(arg: str) -> Optional[Path]:
    """Resolve a path-ish CLI arg, tolerating the bare-name forms agents pass.

    Returns the resolved existing path, or None if nothing plausible exists.
    Three shapes are accepted: an ordinary relative/absolute path; the current
    repo's own name from inside it (-> CWD, fleet-config#162); and a sibling
    worktree name from the parent repo (-> CWD.parent/<name>, fleet-config#165 —
    e.g. `remove-worktree fleet-config-wt-7` run from inside `fleet-config`).
    """
    repo = Path(arg).resolve()
    if repo.exists():
        return repo
    # Only a bare, non-absolute name (no directory components) gets the fallback.
    p = Path(arg)
    if not p.is_absolute() and p.parent == Path("."):
        cwd = Path.cwd()
        if cwd.name.casefold() == arg.casefold():
            return cwd
        sibling = cwd.parent / arg
        if sibling.exists():
            return sibling
    return None


def _resolve_repo(arg: str) -> Path:
    resolved = _resolve_path_arg(arg)
    if resolved is None:
        sys.exit(f"No such repo path: {Path(arg).resolve()}")
    return resolved


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
    mode, holder = try_acquire(
        lock, meta, time.time(), args.ttl_hours,
        branch_exists=lambda b: branch_exists_on_remote(repo, b),
    )
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
    # Tolerate the bare sibling-name form (fleet-config#165). When nothing
    # resolves, hand the literal resolved path to remove_worktree so it prints
    # its honest "already gone" message rather than exiting.
    resolved = _resolve_path_arg(args.worktree_path)
    remove_worktree(resolved or Path(args.worktree_path).resolve())
    return 0


def cmd_assert_owner(args: argparse.Namespace) -> int:
    """Refuse a `git checkout <main>` unless it's safe for `<issue>` to run it.

    Called immediately before the checkout in `/issue-finish` step 5 and
    `/issue-start` step 4 (fleet-config#473) — the claim system routes a
    *second* session into a worktree, but says nothing about whether the
    process about to switch the primary tree's HEAD is actually the claim
    holder. Exits 0 (`ASSERT_OWNER=pass`) when the tree is clean and the claim
    is free or owned by this issue; exits 1 (`ASSERT_OWNER=refuse: <reason>`)
    otherwise, printing the same "primary held since ..." shape the claim
    system already uses so a human or chief sees why the checkout stopped.
    """
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    holder = read_meta(lock) if lock.exists() else None
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    ok, reason = owner_check(holder, args.issue, dirty)
    if ok:
        print(f"ASSERT_OWNER=pass ({reason})")
        return 0
    print(f"ASSERT_OWNER=refuse: {reason}", file=sys.stderr)
    return 1


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

    ao = sub.add_parser("assert-owner", help="refuse a main checkout unless it's safe for <issue>")
    ao.add_argument("repo_root")
    ao.add_argument("issue")
    ao.set_defaults(func=cmd_assert_owner)

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
