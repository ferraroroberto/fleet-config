"""Unit tests for skills/_lib/instruction_delta/delta.py.

The numstat parser and file filter are pure; `measure` runs against a throwaway
upstream + clone so `origin/main` resolves like a real fleet repo. No real repo is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_instruction_delta.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from instruction_delta import delta as idelta  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check

# ---- which paths are instruction files ----

for path in ("CLAUDE.md", "global-CLAUDE.md", "skills/issue-add/SKILL.md", ".claude/skills/x/SKILL.md",
             ".claude/rules/a.md", "sub/CLAUDE.md"):
    check(idelta.is_instruction_file(path), f"{path} is an instruction file")
for path in ("README.md", "docs/skills.md", "skills/_lib/git_run.py", "skills/issue-add/notes.md"):
    check(not idelta.is_instruction_file(path), f"{path} is not an instruction file")

# ---- parse_numstat ----

d = idelta.parse_numstat("3\t1\tskills/a/SKILL.md\n10\t0\tREADME.md\n-\t-\tCLAUDE.md\n2\t5\tCLAUDE.md\n")
check(d == idelta.Delta(5, 6, 3), f"sums instruction rows only, binary row counts zero, got {d}")
check(d.net == -1, "net is added minus removed")
check(idelta.parse_numstat("") == idelta.Delta(0, 0, 0), "empty diff is a real +0 -0")
check(idelta.render(idelta.Delta(8, 2, 3)) == "INSTRUCTION_DELTA=+8 -2 net=+6 files=3", "render shape")

# ---- measure against a throwaway repo ----

tmp = Path(tempfile.mkdtemp(prefix="instruction-delta-"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)
    (upstream / "skills" / "a").mkdir(parents=True)
    (upstream / "skills" / "a" / "SKILL.md").write_text("one\ntwo\n", encoding="utf-8")
    run_git(upstream, "add", "-A", check=check)
    run_git(upstream, "commit", "-qm", "base skill", check=check)
    run_git(work, "pull", "-q", "origin", "main", check=check)
    skill = work / "skills" / "a" / "SKILL.md"

    check(idelta.measure(work, "origin/main") == idelta.Delta(0, 0, 0), "no change since the base is +0 -0")

    run_git(work, "checkout", "-qb", "feat/x", check=check)
    skill.write_text("one\nthree\nfour\n", encoding="utf-8")
    (work / "README.md").write_text("not counted\n", encoding="utf-8")
    run_git(work, "add", "-A", check=check)
    run_git(work, "commit", "-qm", "edit", check=check)
    got = idelta.measure(work, "origin/main")
    check(got == idelta.Delta(2, 1, 1), f"a committed edit counts added and removed lines, got {got}")

    (work / "CLAUDE.md").write_text("a\nb\nc\n", encoding="utf-8")
    got = idelta.measure(work, "origin/main")
    check(got == idelta.Delta(5, 1, 2), f"a new untracked instruction file counts its lines, got {got}")

    skill.write_text("one\nthree\nfour\nfive\n", encoding="utf-8")
    got = idelta.measure(work, "origin/main")
    check(got == idelta.Delta(6, 1, 2), f"an unstaged edit counts too, got {got}")

    got = idelta.measure(work, "origin/nope")
    check(isinstance(got, str) and "merge-base" in got, f"an unresolvable base is a reason, never +0, got {got!r}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_instruction_delta")
