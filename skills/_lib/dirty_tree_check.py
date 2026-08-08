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
      STATUS=CLEAN|DIRTY|UNKNOWN
      BRANCH=<current-branch>   (empty when UNKNOWN)
      REASON=<text>   (only when STATUS=DIRTY or UNKNOWN)

`UNKNOWN` means the facts could not be gathered -- a path that doesn't exist,
a directory that isn't a repo, a `git` that failed. It is a third state on
purpose: a failed probe used to yield empty strings that read as facts, so a
repo that was never inspected got a confident `DIRTY` (fleet-config#570, five
repos at once, all five actually clean) and, in `built` mode, silently
satisfied the "is the tree clean" half of the did-the-agent-save-anything
test. Callers must render `UNKNOWN` as `❓` and never fold it into `✅`/`❌`.

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
    status: str  # "CLEAN" | "DIRTY" | "UNKNOWN"
    reason: Optional[str]


class Unreadable(Exception):
    """The repo's facts could not be established — so there is no verdict.

    Raised instead of letting a failed `git` return an empty string that then
    reads as a fact: an empty branch name is unequal to `main` (→ a confident
    `DIRTY` about a repo never inspected) and empty porcelain reads as *clean*
    (fleet-config#570 — five repos reported DIRTY at once, all five clean).
    This helper exists to be the independent check on a sub-agent's
    self-report, so a manufactured verdict here is worse than no verdict.
    """


def evaluate(
    mode: str,
    current_branch: str,
    default_branch: str,
    expected_branch: Optional[str],
    porcelain_empty: bool,
    commits_ahead: Optional[int],
) -> Result:
    """Pure decision: no git calls, just the facts already gathered.

    `commits_ahead=None` means the count could not be established (no
    `origin/<default>` to compare against, say). It only matters to the `built`
    mode's "did the agent save anything?" test, and only when the tree is
    clean — with uncommitted changes present the answer is already yes. Where
    it does matter, the honest answer is `UNKNOWN`, never the `DIRTY` that a
    silent coercion to `0` used to produce.
    """
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
        if porcelain_empty and commits_ahead is None:
            return Result("UNKNOWN", "tree is clean but the commits-ahead count could not be established")
        if porcelain_empty and commits_ahead == 0:
            return Result("DIRTY", "reported changes but tree is clean and branch has no commits ahead — nothing found")
        return Result("CLEAN", None)

    raise ValueError(f"unknown mode: {mode}")


def _run_git(repo_path: Path, *args: str) -> str:
    """Stripped stdout, or `Unreadable` — never an empty string standing in for
    a fact. `git_run.run_git` defaults to `check=False` and documents that the
    caller inspects `.returncode`; this caller used not to."""
    r = git_run.run_git(["-C", str(repo_path), *args])
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        raise Unreadable(
            f"git {' '.join(args)} failed (exit {r.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return r.stdout.strip()


def detect_default_branch(repo_path: Path) -> str:
    """Bare branch name, no candidate probing on `symbolic-ref` failure (unlike
    `git_run.resolve_default_branch_ref`'s other callers) -- `candidates=()`
    reproduces that quirk on top of the shared helper (fleet-config#485)."""
    ref = git_run.resolve_default_branch_ref(repo_path, candidates=(), final_fallback="main")
    return ref[len("origin/"):] if ref.startswith("origin/") else ref


def gather(repo_path: Path, default_branch: str) -> tuple[str, bool, Optional[int]]:
    """Collect the live git facts `evaluate` needs.

    Raises `Unreadable` when the branch or the working tree can't be read —
    without those two there is nothing to judge. The commits-ahead count is
    softer: `origin/<default>` legitimately may not exist (a repo with no
    remote), so that one comes back as `None` and `evaluate` decides whether
    it mattered.
    """
    current_branch = _run_git(repo_path, "branch", "--show-current")
    porcelain = _run_git(repo_path, "status", "--porcelain")
    porcelain_empty = porcelain == ""
    try:
        commits_ahead: Optional[int] = int(
            _run_git(repo_path, "rev-list", "--count", f"origin/{default_branch}..HEAD")
        )
    except (Unreadable, ValueError):
        commits_ahead = None
    return current_branch, porcelain_empty, commits_ahead


def cmd_check(repo_path: Path, mode: str, expect_branch: Optional[str], default_branch: Optional[str]) -> None:
    # Cheap, precise pre-check. Deliberately only tests existence and not for a
    # `.git` entry: `-C` works fine from a subdirectory of a repo, which has
    # none, so a `.git` test would reject paths that are perfectly readable.
    # Anything else that isn't a repo, git itself names in the error below.
    if not repo_path.exists():
        _report("UNKNOWN", "", f"no such path: {repo_path}")
        return
    try:
        resolved_default = default_branch or detect_default_branch(repo_path)
        current_branch, porcelain_empty, commits_ahead = gather(repo_path, resolved_default)
    except Unreadable as exc:
        # Exit 0 and an empty BRANCH, same as any other reported outcome — this
        # helper reports, it never blocks, and callers key on STATUS.
        _report("UNKNOWN", "", str(exc))
        return
    result = evaluate(mode, current_branch, resolved_default, expect_branch, porcelain_empty, commits_ahead)
    _report(result.status, current_branch, result.reason)


def _report(status: str, branch: str, reason: Optional[str]) -> None:
    print(f"STATUS={status}")
    print(f"BRANCH={branch}")
    if reason:
        print(f"REASON={reason}")


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
