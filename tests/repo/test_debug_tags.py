"""Unit tests for skills/_lib/debug_tags/check.py.

The diff parser is pure; `scan` runs against a throwaway upstream + clone so `origin/main`
resolves like a real fleet repo. No real repo is touched. Tag literals are built by
concatenation so this file never carries a tagged line itself.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_debug_tags.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from debug_tags import check as dt  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check

TAG = "[DEBUG-" + "a4f2]"

# ---- parse_added ----

diff = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    f"@@ -3,0 +4,2 @@\n+x = 1\n+log.info('{TAG} x=%s', x)\n"
    f"@@ -9 +11 @@\n-log.info('{TAG} old')\n+y = 2\n"
)
check(dt.parse_added(diff) == [("app.py", 5)], f"an added tagged line is found at its new line number, got {dt.parse_added(diff)}")
check(dt.parse_added("+++ b/a.py\n@@ -1 +1 @@\n+print('[DEBUG-<id>]')\n") == [], "a placeholder id is not a tag")
check(dt.parse_added("+++ b/a.py\n@@ -1 +1 @@\n+print('DEBUG-a4f2')\n") == [], "an unbracketed word is not a tag")
check(dt.parse_added("") == [], "an empty diff has no hits")

# ---- scan against a throwaway repo ----

tmp = Path(tempfile.mkdtemp(prefix="debug-tags-"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)
    run_git(work, "checkout", "-qb", "fix/x", check=check)
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    run_git(work, "add", "-A", check=check)
    run_git(work, "commit", "-qm", "clean change", check=check)
    check(dt.scan(work, "origin/main") == [], f"a clean branch passes, got {dt.scan(work, 'origin/main')}")

    (work / "app.py").write_text(f"x = 1\nprint('{TAG}', x)\n", encoding="utf-8")
    run_git(work, "commit", "-qam", "seed tag", check=check)
    check(dt.scan(work, "origin/main") == [("app.py", 2)], "a committed tagged line fails")

    run_git(work, "reset", "-q", "--hard", "HEAD~1", check=check)
    (work / "app.py").write_text(f"x = 1\n# {TAG}\n", encoding="utf-8")
    check(dt.scan(work, "origin/main") == [("app.py", 2)], "an uncommitted tagged line fails")

    run_git(work, "checkout", "-q", "--", "app.py", check=check)
    (work / "new.py").write_text(f"a = 0\nb = 0\nprint('{TAG}')\n", encoding="utf-8")
    check(dt.scan(work, "origin/main") == [("new.py", 3)], "a tagged line in a new untracked file fails")

    got = dt.scan(work, "origin/nope")
    check(isinstance(got, str) and "merge-base" in got, f"an unresolvable base is a reason, never a pass, got {got!r}")

    check(dt.main([str(work), "--base", "origin/main"]) == 1, "a hit exits 1")
    (work / "new.py").unlink()
    check(dt.main([str(work), "--base", "origin/main"]) == 0, "a clean tree exits 0")
    check(dt.main([str(work), "--base", "origin/nope"]) == 2, "not confirmed exits 2, distinct from a hit")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_debug_tags")
