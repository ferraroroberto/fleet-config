"""Shared throwaway-git-repo fixtures for the standalone `tests/test_*.py` files
(fleet-config#615).

`check_harness.py` retired the identical pass/fail-loop trio hand-rolled across
eight files; this retires the next hand-roll one level up — the ~15-20 line
`_git(cwd, *args)` subprocess wrapper plus the "init an `upstream` repo,
`checkout -b main`, `git config user.email/user.name` with the identical
hardcoded fleet-config commit-author address, commit a README, `git clone` to
`work`" fixture, hand-rolled independently in `test_git_run.py`,
`test_dirty_tree_check.py`, `test_chief_ops.py`, and `test_audit_issue.py`
(there as `_git567`).

Usage (matches the existing per-file shape so call sites barely change):

    from git_fixtures import run_git, make_upstream_and_clone

    upstream, work = make_upstream_and_clone(tmp, check)
    ref = run_git(work, "rev-parse", "HEAD", check=check)

Every failure is reported through the caller's own `check(cond, msg)` (from
`CheckHarness`) rather than raised — the git commands in the fixture are
expected to succeed, and a failure here should surface as a failed check in
the caller's own report, not a crash that hides the rest of the suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Tuple

# Hardcoded on purpose (fleet-config commit-author convention): a throwaway repo
# still needs a valid identity for `git commit` to succeed, and the exact
# address doesn't matter — it never leaves the temp dir.
_TEST_IDENTITY_EMAIL = "35553560+ferraroroberto@users.noreply.github.com"
_TEST_IDENTITY_NAME = "Test"


def run_git(cwd: Path, *args: str, check: Callable[[bool, str], None]) -> str:
    """Run `git -C <cwd> <args>`, report failure through `check`, return stripped stdout."""
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    check(proc.returncode == 0, f"git {' '.join(args)} in {cwd} failed: {proc.stderr}")
    return proc.stdout.strip()


def make_upstream_and_clone(
    root: Path, check: Callable[[bool, str], None], readme: str = "hello\n"
) -> Tuple[Path, Path]:
    """Build `<root>/upstream` (init, `checkout -b main`, identity, one README
    commit) and clone it to `<root>/work` (same identity configured). Returns
    `(upstream, work)`, both created and ready for further commits.
    """
    upstream = root / "upstream"
    work = root / "work"
    upstream.mkdir()
    run_git(upstream, "init", "-q", check=check)
    run_git(upstream, "checkout", "-q", "-b", "main", check=check)
    run_git(upstream, "config", "user.email", _TEST_IDENTITY_EMAIL, check=check)
    run_git(upstream, "config", "user.name", _TEST_IDENTITY_NAME, check=check)
    (upstream / "README.md").write_text(readme, encoding="utf-8")
    run_git(upstream, "add", "README.md", check=check)
    run_git(upstream, "commit", "-q", "-m", "initial", check=check)

    run_git(root, "clone", "-q", str(upstream), str(work), check=check)
    run_git(work, "config", "user.email", _TEST_IDENTITY_EMAIL, check=check)
    run_git(work, "config", "user.name", _TEST_IDENTITY_NAME, check=check)
    return upstream, work
