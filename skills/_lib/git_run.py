"""Shared `git` subprocess wrapper for skills/_lib helpers.

Every helper here needs the same three lines — shell out to `git`, capture
stdout/stderr as UTF-8, never crash on an undecodable byte — but before this
module existed each of `audit_issue.py`, `cert_drift.py`,
`dirty_tree_check.py`, `ux_surface.py`, and `worktree_claim.py` hand-rolled
its own copy, with inconsistent error handling and `dirty_tree_check.py`'s
copy the one that had drifted (missing `errors="replace"`, so a non-ASCII
commit message could raise `UnicodeDecodeError` mid-run). One place now owns
the subprocess plumbing; each call site keeps whatever return shape (`str` vs
`CompletedProcess`) and failure behavior (raise vs swallow) it needs on top.

stdlib only (plus the sibling `no_window` constant).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402


def git_env(base: Optional[dict] = None) -> dict:
    """`base` (default `os.environ`) plus `GIT_OPTIONAL_LOCKS=0`.

    **Do not "clean up" this variable.** `git status` (and `git diff`, and
    friends) take `.git/index.lock` purely to write back a refreshed stat
    cache — an optimisation, not part of producing the output. Kill such a
    process mid-refresh and it leaves exactly a 0-byte `index.lock` and
    nothing else touched. That is what happened fleet-wide on 2026-08-01:
    nine repos, nine 0-byte locks, all stamped the same second, and every one
    of those repos was silently unable to `git add`/`commit`/`pull`/`stash`
    for **fifteen days** (fleet-config#667).

    Fifteen days, because a stale lock is invisible to a read: `git status
    --porcelain`, `git fetch`, `git rev-list`, and an up-to-date `git pull
    --ff-only` **all exit 0 and print the right answer** with the lock
    sitting there — so `#570`'s raise-on-non-zero never fires and no sweep
    ever reports `UNKNOWN`. Detection is `index_lock.py`'s job; this is the
    half that stops them being created at all.

    `GIT_OPTIONAL_LOCKS=0` suppresses only *optional* locks — a requested
    write still takes the real index lock, and still refuses when one is
    already held. Verified, not assumed: `tests/test_git_run.py` drives both
    halves against a real repo.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    """Run `git <args>`, UTF-8 decoded with undecodable bytes replaced.

    Pass `-C <repo>` as part of `args` to target a specific working tree —
    the convention every call site in this repo uses. `check=True` raises
    `subprocess.CalledProcessError` on a non-zero exit (matching
    `subprocess.run`'s own contract); the default `check=False` leaves the
    caller to inspect `.returncode`/`.stdout`/`.stderr`. `timeout` is passed
    straight through (raising `subprocess.TimeoutExpired`) for the call sites
    that bound a crawl over an unknown tree.

    `creationflags=NO_WINDOW` is what makes this the *only* place most helpers
    need to think about console suppression: a scheduled `claude -p` job has no
    console of its own, so every unsuppressed `git` spawn beneath it flashes a
    window on screen (fleet-config#412). `env=git_env()` is the same
    one-place-fix argument for optional index locks — see that function
    (fleet-config#667).
    """
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
        creationflags=NO_WINDOW, env=git_env(),
    )


def run_git_checked(args: Sequence[str]) -> str:
    """Run `git <args>`; raise `SystemExit` on a non-zero exit, else return
    stripped stdout.

    The convenience wrapper every call site actually wants on top of
    `run_git` — most fleet code doesn't want to inspect `.returncode` itself,
    it wants "give me the output or blow up with a clear message." Was
    `audit_issue.py`'s private `_git`, promoted here (fleet-config#502) so
    `design_sweep_scan.py` / `fleet_audit_scan.py` reach a real public entry
    point instead of a sibling module's underscore-prefixed name.
    """
    r = run_git(args)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"git {' '.join(args)} failed (exit {r.returncode})")
    return (r.stdout or "").strip()


def resolve_default_branch_ref(
    repo_path: Path,
    candidates: Sequence[str] = ("origin/main", "main", "master"),
    final_fallback: str = "main",
) -> str:
    """The repo's default branch, preferring the remote's own `origin/HEAD`.

    On `symbolic-ref refs/remotes/origin/HEAD` success, returns the ref with
    the `refs/remotes/` prefix stripped (e.g. `origin/main`). On failure,
    probes `candidates` in order via `rev-parse --verify --quiet` and returns
    the first that resolves; if none do (or `candidates` is empty), returns
    `final_fallback`. `candidates`/`final_fallback` are parameterized so a
    caller with different fallback semantics (fleet-config#485) can reproduce
    its own exact behavior on top of this one implementation.
    """
    res = run_git(["-C", str(repo_path), "symbolic-ref", "refs/remotes/origin/HEAD"])
    ref = res.stdout.strip()
    if res.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)
    for cand in candidates:
        if run_git(["-C", str(repo_path), "rev-parse", "--verify", "--quiet", cand]).returncode == 0:
            return cand
    return final_fallback
