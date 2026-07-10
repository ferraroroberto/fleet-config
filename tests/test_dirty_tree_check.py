"""Unit tests for the pure logic in skills/_lib/dirty_tree_check.py.

Exercises `evaluate` directly with synthetic facts, plus the `check` CLI
end-to-end against a throwaway local git repo (a real `upstream` + clone, so
`origin/HEAD` resolves like it would in a real fleet repo). No real fleet repo
is touched.

Run: `C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_dirty_tree_check.py`  (also invoked by tests/run_acceptance.py)
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


# ---- check CLI end-to-end against a real throwaway repo ----

def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    check(proc.returncode == 0, f"git {' '.join(args)} in {cwd} failed: {proc.stderr}")
    return proc.stdout.strip()


tmp = Path(tempfile.mkdtemp(prefix="dirty_tree_check_"))
try:
    upstream = tmp / "upstream"
    work = tmp / "work"
    upstream.mkdir()
    _git(upstream, "init", "-q")
    _git(upstream, "checkout", "-q", "-b", "main")
    _git(upstream, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(upstream, "config", "user.name", "Test")
    (upstream / "README.md").write_text("hello\n", encoding="utf-8")
    _git(upstream, "add", "README.md")
    _git(upstream, "commit", "-q", "-m", "initial")

    _git(tmp, "clone", "-q", str(upstream), str(work))
    _git(work, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(work, "config", "user.name", "Test")

    def run_check(mode: str, expect_branch: str | None = None) -> str:
        args = [sys.executable, str(REPO / "skills" / "_lib" / "dirty_tree_check.py"),
                "check", str(work), "--mode", mode]
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
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_dirty_tree_check")
