"""Unit tests for skills/_lib/git_run.py's `resolve_default_branch_ref` (fleet-config#485).

Exercises the shared default-branch resolver against a real throwaway git
repo (upstream + clone, so `origin/HEAD` resolves like it would in a real
fleet repo) -- the same pattern `test_dirty_tree_check.py` uses. Covers the
`symbolic-ref` success path, the candidate-probing fallback, the
terminal-fallback case, and the `candidates=()` shape `dirty_tree_check.py`
depends on to reproduce its own no-probing quirk.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_git_run.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import git_run  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


tmp = Path(tempfile.mkdtemp(prefix="test_git_run_"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)

    # ---- symbolic-ref succeeds: origin/HEAD -> origin/main ----
    ref = git_run.resolve_default_branch_ref(work)
    check(ref == "origin/main", f"symbolic-ref success: expected 'origin/main', got {ref!r}")

    # candidates=() must not change the success path -- only the fallback.
    ref = git_run.resolve_default_branch_ref(work, candidates=(), final_fallback="main")
    check(ref == "origin/main", f"symbolic-ref success ignores candidates=(): got {ref!r}")

    # ---- symbolic-ref fails (no remote at all): probe candidates in order ----
    bare = tmp / "bare_no_remote"
    bare.mkdir()
    _git(bare, "init", "-q")
    _git(bare, "checkout", "-q", "-b", "main")
    _git(bare, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(bare, "config", "user.name", "Test")
    (bare / "README.md").write_text("hello\n", encoding="utf-8")
    _git(bare, "add", "README.md")
    _git(bare, "commit", "-q", "-m", "initial")

    ref = git_run.resolve_default_branch_ref(bare)
    check(ref == "main", f"no remote, local 'main' exists: expected 'main' (2nd candidate), got {ref!r}")

    # ---- symbolic-ref fails, none of the default candidates exist ----
    _git(bare, "checkout", "-q", "-b", "trunk")
    _git(bare, "branch", "-D", "main")
    ref = git_run.resolve_default_branch_ref(bare)
    check(ref == "main", f"no remote, no main/master: falls to final_fallback 'main', got {ref!r}")

    ref = git_run.resolve_default_branch_ref(bare, final_fallback="develop")
    check(ref == "develop", f"custom final_fallback is honored: got {ref!r}")

    # ---- candidates=() skips probing entirely, even when 'main' exists ----
    _git(bare, "checkout", "-q", "-b", "main")
    ref = git_run.resolve_default_branch_ref(bare, candidates=(), final_fallback="zzz-unprobed")
    check(
        ref == "zzz-unprobed",
        f"candidates=() returns final_fallback even though 'main' exists (no probing occurred): got {ref!r}",
    )

    # ---- run_git_checked: success returns stripped stdout, failure raises SystemExit ----
    sha = git_run.run_git_checked(["-C", str(bare), "rev-parse", "HEAD"])
    check(len(sha) == 40 and sha.strip() == sha,
          f"run_git_checked: success returns the stripped stdout (a 40-char sha), got {sha!r}")

    raised = False
    try:
        git_run.run_git_checked(["-C", str(bare), "rev-parse", "does-not-exist"])
    except SystemExit as exc:
        raised = True
        check("git" in str(exc) and "failed" in str(exc),
              f"run_git_checked: SystemExit message names the failed git command, got {exc!r}")
    check(raised, "run_git_checked: a non-zero exit raises SystemExit instead of returning")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_git_run")
