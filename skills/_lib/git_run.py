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

import subprocess
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402


def run_git(args: Sequence[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """Run `git <args>`, UTF-8 decoded with undecodable bytes replaced.

    Pass `-C <repo>` as part of `args` to target a specific working tree —
    the convention every call site in this repo uses. `check=True` raises
    `subprocess.CalledProcessError` on a non-zero exit (matching
    `subprocess.run`'s own contract); the default `check=False` leaves the
    caller to inspect `.returncode`/`.stdout`/`.stderr`.

    `creationflags=NO_WINDOW` is what makes this the *only* place most helpers
    need to think about console suppression: a scheduled `claude -p` job has no
    console of its own, so every unsuppressed `git` spawn beneath it flashes a
    window on screen (fleet-config#412).
    """
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
        creationflags=NO_WINDOW,
    )


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
