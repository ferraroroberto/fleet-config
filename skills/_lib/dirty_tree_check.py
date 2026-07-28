"""Post-flight dirty-tree check for fan-out skills (fleet-config#247).

Why this exists
----------------
`/cleanup-fleet`, `/issue-batch`, and `/issue-finish-batch` all pre-flight a
repo (dirty tree / wrong branch -> skip) *before* dispatching a sub-agent, but
none of them re-check the tree *after* the agent reports back. A sub-agent
that forgets a `git add`/`git commit`, or a merge that leaves the tree dirty,
was previously reported as a trusted `✅`/`📋` status with no verification. This
helper is run by the orchestrator (never the sub-agent that might have made
the mistake) right before a repo/branch is marked complete.

Two expected end-states, one per fan-out shape:
  merged  -- the agent reported MERGED (easy-tier /issue-yolo,
             /issue-finish-batch): expect a clean tree, back on the default
             branch.
  built   -- the agent reported a build-and-stop (hard-tier /cleanup-fleet,
             /issue-batch): expect to still be on the reported feature branch
             (never the default branch), with *some* evidence of work --
             either uncommitted tracked changes or commits ahead of
             origin/<default>. Neither present means the agent silently saved
             nothing.

Subcommand
----------
  check <repo-path> --mode {merged,built} [--expect-branch BRANCH]
        [--default-branch NAME]
    Prints:
      STATUS=CLEAN|DIRTY
      BRANCH=<current-branch>
      REASON=<text>   (only when STATUS=DIRTY)

This never blocks or auto-fixes -- it only reports, so the caller can
downgrade a self-reported status line. Always exits 0. The decision logic
(`evaluate`) is pure and unit-tested (`tests/test_dirty_tree_check.py`)
independent of the git plumbing around it. stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402


class Result(NamedTuple):
    status: str  # "CLEAN" | "DIRTY"
    reason: Optional[str]


def evaluate(
    mode: str,
    current_branch: str,
    default_branch: str,
    expected_branch: Optional[str],
    porcelain_empty: bool,
    commits_ahead: int,
) -> Result:
    """Pure decision: no git calls, just the facts already gathered."""
    if mode == "merged":
        if current_branch != default_branch:
            return Result("DIRTY", f"still on {current_branch}, expected to land on {default_branch}")
        if not porcelain_empty:
            return Result("DIRTY", "working tree not clean after merge")
        return Result("CLEAN", None)

    if mode == "built":
        if current_branch == default_branch:
            return Result("DIRTY", f"HEAD unexpectedly back on {default_branch}, expected {expected_branch}")
        if expected_branch is not None and current_branch != expected_branch:
            return Result("DIRTY", f"on {current_branch}, expected {expected_branch}")
        if porcelain_empty and commits_ahead == 0:
            return Result("DIRTY", "reported changes but tree is clean and branch has no commits ahead — nothing found")
        return Result("CLEAN", None)

    raise ValueError(f"unknown mode: {mode}")


def _run_git(repo_path: Path, *args: str) -> str:
    return git_run.run_git(["-C", str(repo_path), *args]).stdout.strip()


def detect_default_branch(repo_path: Path) -> str:
    """Bare branch name, no candidate probing on `symbolic-ref` failure (unlike
    `git_run.resolve_default_branch_ref`'s other callers) -- `candidates=()`
    reproduces that quirk on top of the shared helper (fleet-config#485)."""
    ref = git_run.resolve_default_branch_ref(repo_path, candidates=(), final_fallback="main")
    return ref[len("origin/"):] if ref.startswith("origin/") else ref


def gather(repo_path: Path, default_branch: str) -> tuple[str, bool, int]:
    """Collect the live git facts `evaluate` needs."""
    current_branch = _run_git(repo_path, "branch", "--show-current")
    porcelain = _run_git(repo_path, "status", "--porcelain")
    porcelain_empty = porcelain == ""
    count_raw = _run_git(repo_path, "rev-list", "--count", f"origin/{default_branch}..HEAD")
    try:
        commits_ahead = int(count_raw)
    except ValueError:
        commits_ahead = 0
    return current_branch, porcelain_empty, commits_ahead


def cmd_check(repo_path: Path, mode: str, expect_branch: Optional[str], default_branch: Optional[str]) -> None:
    resolved_default = default_branch or detect_default_branch(repo_path)
    current_branch, porcelain_empty, commits_ahead = gather(repo_path, resolved_default)
    result = evaluate(mode, current_branch, resolved_default, expect_branch, porcelain_empty, commits_ahead)
    print(f"STATUS={result.status}")
    print(f"BRANCH={current_branch}")
    if result.reason:
        print(f"REASON={result.reason}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Post-flight dirty-tree check for fan-out skills.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("repo_path", type=Path)
    c.add_argument("--mode", choices=("merged", "built"), required=True)
    c.add_argument("--expect-branch", default=None)
    c.add_argument("--default-branch", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "check":
        cmd_check(args.repo_path, args.mode, args.expect_branch, args.default_branch)


if __name__ == "__main__":
    main()
