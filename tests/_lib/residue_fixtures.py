"""Real-git fixture shared by `test_branch_residue.py` and `test_worktree_residue.py`.

Both suites need the same throwaway shape: a bare `origin.git`, a `primary` clone of it on
`main` with one pushed commit, and `git`/`commit` helpers that bypass this machine's global
commit hook. They differ only in the temp-dir prefix, which names the suite in a leftover dir.

stdlib plus `no_window`; a git failure raises `AssertionError` like the suites' own checks do.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402


def make_origin_and_primary(
    prefix: str,
) -> Tuple[Path, Path, Path, Callable[..., str], Callable[[Path, str, str, str], None]]:
    """Build the fixture under a fresh temp dir named `<prefix>-fixture-*`.

    Returns `(base, origin, primary, git, commit)`: `git(cwd, *args)` runs git there and returns
    stripped stdout, `commit(cwd, name, text, msg)` writes, adds and commits one file.
    """
    no_hooks = Path(tempfile.mkdtemp(prefix=f"{prefix}-nohooks-"))

    def git(cwd: Path, *args: str) -> str:
        # `core.hooksPath` -> an empty dir: this machine carries a global commit
        # hook that rejects non-allowlisted author emails.
        res = subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", f"core.hooksPath={no_hooks}", *args],
            cwd=str(cwd), capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        if res.returncode != 0:
            raise AssertionError(f"fixture git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout.strip()

    def commit(cwd: Path, name: str, text: str, msg: str) -> None:
        (cwd / name).write_text(text, encoding="utf-8")
        git(cwd, "add", name)
        git(cwd, "commit", "-q", "-m", msg)

    base = Path(tempfile.mkdtemp(prefix=f"{prefix}-fixture-")).resolve()
    origin = base / "origin.git"
    git(base, "init", "-q", "--bare", "-b", "main", str(origin))
    primary = base / "alpha"
    git(base, "clone", "-q", str(origin), str(primary))
    git(primary, "checkout", "-q", "-b", "main")
    commit(primary, "a.txt", "one\n", "base")
    git(primary, "push", "-q", "-u", "origin", "main")
    git(primary, "remote", "set-head", "origin", "main")
    return base, origin, primary, git, commit
