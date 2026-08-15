"""Unit tests for the pure logic in skills/_lib/dirty_tree_check.py.

Exercises `evaluate` directly with synthetic facts, plus the `check` CLI
end-to-end against a throwaway local git repo (a real `upstream` + clone, so
`origin/HEAD` resolves like it would in a real fleet repo). No real fleet repo
is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_dirty_tree_check.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import dirty_tree_check as dtc  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- evaluate: merged mode ----

r = dtc.evaluate("merged", "main", "main", None, True, 0)
check(r.status == "CLEAN", "merged: on default branch + clean tree -> CLEAN")

r = dtc.evaluate("merged", "main", "main", None, False, 0)
check(r.status == "DIRTY" and "not clean" in r.reason, "merged: on default branch but dirty tree -> DIRTY")

r = dtc.evaluate("merged", "feat/23-x", "main", None, True, 0)
check(r.status == "DIRTY" and "still on feat/23-x" in r.reason, "merged: still on feature branch -> DIRTY")

# ---- evaluate: built mode ----

r = dtc.evaluate("built", "feat/23-x", "main", "feat/23-x", False, 0)
check(r.status == "CLEAN", "built: on expected branch + uncommitted changes -> CLEAN")

r = dtc.evaluate("built", "feat/23-x", "main", "feat/23-x", True, 3)
check(r.status == "CLEAN", "built: on expected branch + commits ahead -> CLEAN")

r = dtc.evaluate("built", "feat/23-x", "main", "feat/23-x", True, 0)
check(
    r.status == "DIRTY" and "nothing found" in r.reason,
    "built: clean tree + zero commits ahead -> DIRTY (agent saved nothing)",
)

r = dtc.evaluate("built", "main", "main", "feat/23-x", True, 0)
check(
    r.status == "DIRTY" and "unexpectedly back on main" in r.reason,
    "built: HEAD unexpectedly on default branch -> DIRTY",
)

r = dtc.evaluate("built", "feat/99-other", "main", "feat/23-x", True, 1)
check(
    r.status == "DIRTY" and "expected feat/23-x" in r.reason,
    "built: on the wrong feature branch -> DIRTY",
)

try:
    dtc.evaluate("bogus", "main", "main", None, True, 0)
    check(False, "evaluate: unknown mode should raise")
except ValueError:
    check(True, "evaluate: unknown mode raises ValueError")

# ---- evaluate: an unestablished commits-ahead count (fleet-config#570) ----
# It used to be coerced to 0, which reads as "the agent saved nothing".

r = dtc.evaluate("built", "feat/23-x", "main", "feat/23-x", True, None)
check(r.status == "UNKNOWN" and "could not be established" in r.reason,
      "built: clean tree + uncountable commits ahead -> UNKNOWN, not DIRTY")

r = dtc.evaluate("built", "feat/23-x", "main", "feat/23-x", False, None)
check(r.status == "CLEAN",
      "built: uncommitted changes present -> the count never mattered, still CLEAN")

r = dtc.evaluate("built", "main", "main", "feat/23-x", True, None)
check(r.status == "DIRTY" and "unexpectedly back on main" in r.reason,
      "built: a branch fact that WAS established still decides before the count")

r = dtc.evaluate("merged", "main", "main", None, True, None)
check(r.status == "CLEAN", "merged: the commits-ahead count is irrelevant to this mode")


# ---- check CLI end-to-end against a real throwaway repo ----

def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


tmp = Path(tempfile.mkdtemp(prefix="dirty_tree_check_"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)

    def run_check(mode: str, expect_branch: str | None = None, path: Path | None = None) -> str:
        args = [sys.executable, str(REPO / "skills" / "_lib" / "dirty_tree_check.py"),
                "check", str(path or work), "--mode", mode]
        if expect_branch:
            args += ["--expect-branch", expect_branch]
        proc = subprocess.run(args, capture_output=True, text=True)
        check(proc.returncode == 0, f"check CLI exits 0 ({proc.stderr.strip()})")
        return proc.stdout

    out = run_check("merged")
    check("STATUS=CLEAN" in out, "check CLI: merged mode, clean main -> STATUS=CLEAN")

    (work / "README.md").write_text("dirty\n", encoding="utf-8")
    out = run_check("merged")
    check("STATUS=DIRTY" in out and "REASON=" in out, "check CLI: merged mode, dirty tree -> STATUS=DIRTY + REASON")
    _git(work, "checkout", "-q", "--", "README.md")

    _git(work, "checkout", "-q", "-b", "feat/1-thing")
    out = run_check("built", "feat/1-thing")
    check(
        "STATUS=DIRTY" in out and "nothing found" in out,
        "check CLI: built mode, no commits/no changes -> STATUS=DIRTY (nothing found)",
    )

    (work / "README.md").write_text("changed on branch\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "wip")
    out = run_check("built", "feat/1-thing")
    check("STATUS=CLEAN" in out, "check CLI: built mode, committed change -> STATUS=CLEAN")

    _git(work, "checkout", "-q", "main")
    out = run_check("built", "feat/1-thing")
    check(
        "STATUS=DIRTY" in out and "unexpectedly back on main" in out,
        "check CLI: built mode, HEAD back on main -> STATUS=DIRTY",
    )

    # ---- a repo it could not read is never a verdict (fleet-config#570) ----
    # Pre-fix each of these printed a confident STATUS=DIRTY with an empty
    # BRANCH, about a repo no git command ever succeeded against.

    missing = tmp / "definitely-not-a-repo-xyz"
    for mode, expect in (("merged", None), ("built", "feat/x")):
        out = run_check(mode, expect, path=missing)
        check("STATUS=UNKNOWN" in out and "STATUS=DIRTY" not in out,
              f"check CLI: {mode} mode, nonexistent path -> STATUS=UNKNOWN, never DIRTY")
        check("no such path" in out, f"check CLI: {mode} mode, nonexistent path is named in REASON")

    not_a_repo = tmp / "plain-dir"
    not_a_repo.mkdir()
    for mode, expect in (("merged", None), ("built", "feat/x")):
        out = run_check(mode, expect, path=not_a_repo)
        check("STATUS=UNKNOWN" in out and "STATUS=DIRTY" not in out,
              f"check CLI: {mode} mode, a directory that is not a repo -> STATUS=UNKNOWN")
        check("REASON=" in out and "git " in out,
              f"check CLI: {mode} mode, the underlying git error reaches REASON")

    # A real repo with no remote at all: branch + porcelain read fine, only the
    # commits-ahead count is unestablishable. `merged` mode never needed it.
    lone = tmp / "no-remote"
    lone.mkdir()
    _git(lone, "init", "-q")
    _git(lone, "checkout", "-q", "-b", "main")
    _git(lone, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(lone, "config", "user.name", "Test")
    (lone / "a.txt").write_text("a\n", encoding="utf-8")
    _git(lone, "add", "a.txt")
    _git(lone, "commit", "-q", "-m", "initial")
    out = run_check("merged", path=lone)
    check("STATUS=CLEAN" in out, "check CLI: a readable repo with no remote still gets a real merged verdict")
    _git(lone, "checkout", "-q", "-b", "feat/2-thing")
    out = run_check("built", "feat/2-thing", path=lone)
    check("STATUS=UNKNOWN" in out and "could not be established" in out,
          "check CLI: built mode cannot count commits ahead with no remote -> UNKNOWN, not DIRTY")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_dirty_tree_check")
