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
from typing import List, Optional, Sequence

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


def run_git_bytes(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    """`run_git` for the callers that need **raw bytes**, not decoded text.

    Same spawn, same `creationflags=NO_WINDOW` and `env=git_env()` — only the
    decoding differs. Exists because `git show <ref>:<path>` and
    `git ls-tree` are read for byte-exact hashing (`vendored_drift`) or for
    content that must round-trip untouched (`system-map`/`config-map`
    `build_data`), and `run_git`'s `errors="replace"` would silently rewrite a
    byte before it was hashed. Those call sites used to hand-roll their own
    `subprocess.run(["git", …])` to get bytes, which also opted them out of
    `git_env()` — the wrapper is the *only* thing that had to be given up, so
    now it isn't (fleet-config#677).
    """
    return subprocess.run(
        ["git", *args], capture_output=True, check=check, timeout=timeout,
        creationflags=NO_WINDOW, env=git_env(),
    )


class Unreadable(Exception):
    """A repo's facts could not be established — so there is no verdict.

    Raised by `run_git_or_raise` instead of letting a failed `git` return an
    empty string that then reads as a fact: empty porcelain reads as *clean*
    and an empty branch name is unequal to `main`, so a repo that was never
    inspected would otherwise be reported as available, or dispatched, or
    called DIRTY — all of them inventions (fleet-config#570: five repos
    reported DIRTY at once, all five clean).

    One class for both `dirty_tree_check` and `repo_preflight`
    (fleet-config#677). They used to carry byte-identical private copies under
    a comment claiming a shared raiser could not satisfy both "different
    vocabularies" — untrue: the vocabularies differ in how each *caller*
    renders the caught exception, not in the raising, and both re-exported
    names still resolve here so `except dirty_tree_check.Unreadable` keeps
    working.
    """


def run_git_or_raise(repo_path: Path, *args: str) -> str:
    """Stripped stdout for `git -C <repo_path> <args>`, or `Unreadable`.

    Never an empty string standing in for a fact — see `Unreadable`. The
    message carries the exit code plus git's first stderr line, which is what
    makes an unreadable repo diagnosable from a sweep's output alone.
    """
    r = run_git(["-C", str(repo_path), *args])
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        raise Unreadable(
            f"git {' '.join(args)} failed (exit {r.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return r.stdout.strip()


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


def changed_files(repo_path: Path, base: str) -> Optional[List[str]]:
    """Files changed on HEAD since its merge-base with `base` (three-dot), or
    `None` when the diff could not be taken at all.

    **`None`, never `[]`.** An unresolvable base ref, a detached/unborn HEAD,
    an unreadable repo — none of those mean "nothing was touched", but each of
    `deploy_coverage`, `ux_surface` and `docs_shots_plan` used to carry its own
    verbatim copy of this helper that swallowed the non-zero exit and returned
    an empty list, rendering as `TOUCHED=no` / `STALE=` / `UNMAPPED=`: a failed
    probe indistinguishable from a clean diff, in the very machinery those
    gates exist to make honest (fleet-config#681). `deploy_coverage`'s own
    module docstring already spelled out the rule its `_changed_files` broke —
    "a flow that cannot tell whether it was touched must not silently assume it
    wasn't" (`project-scaffolding#199`). Callers turn `None` into their own
    `unknown` state; none of them may turn it back into `no`.
    """
    res = run_git(["-C", str(repo_path), "diff", "--name-only", f"{base}...HEAD"])
    if res.returncode != 0:
        return None
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


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
