"""Tests for skills/_lib/worktree_residue.py (fleet-config#1077).

The 2026-09-27 `cleanup-fleet-all` run halted after 9 of 50 issues because
teardown read a foreign worktree -- created mid-run by another lane, clean,
its branch already squash-merged -- as the run's own residue. These checks pin
the classification that replaced that one-line rule:

  * the defer case: a foreign, clean worktree whose HEAD is squash-merged (or
    fast-forward merged, or detached on a merged commit) is `foreign-merged`,
    and a repo holding only such worktrees is `foreign-deferred`;
  * every still-halts case: the run's own worktree (matched by path or by
    branch, even when clean and merged), a foreign dirty one, a foreign
    unmerged one, and a squash later edited on the same lines each stay
    residue; an unreadable one is `unknown`, never a pass.

Real throwaway git repos (bare origin + primary clone + worktrees), no
network, nothing outside a temp dir.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_worktree_residue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import worktree_residue as wr  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from residue_fixtures import make_origin_and_primary  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- pure core ----------------------------------------------------------

E = wr.WorktreeEntry
porcelain = (
    "worktree E:/automation/alpha\nHEAD aaa\nbranch refs/heads/main\n\n"
    "worktree E:/automation/alpha-wt-7\nHEAD bbb\nbranch refs/heads/fix/7-x\n\n"
    "worktree E:/automation/alpha-wt-nav\nHEAD ccc\ndetached\n\n"
    "worktree E:/automation/alpha-wt-gone\nHEAD ddd\nbranch refs/heads/old\nprunable gitdir file points to non-existent location\n"
)
entries = wr.parse_worktree_porcelain(porcelain)
check([e.path for e in entries] == ["E:/automation/alpha-wt-7", "E:/automation/alpha-wt-nav", "E:/automation/alpha-wt-gone"],
      "parse: the primary is dropped, every other worktree kept in order")
check(entries[0].branch == "fix/7-x" and entries[1].branch is None and entries[1].head == "ccc",
      "parse: refs/heads/ is stripped; a detached worktree has no branch but keeps HEAD")
check(entries[2].prunable and not entries[0].prunable, "parse: prunable is recorded")

check(wr.is_own(entries[0], [r"E:\automation\alpha-wt-7"], []),
      "is_own: a backslash lane path matches git's forward-slash path")
check(wr.is_own(entries[0], [r"e:\AUTOMATION\alpha-wt-7\\"], []),
      "is_own: path match is case- and trailing-separator-insensitive")
check(wr.is_own(entries[0], [], ["fix/7-x"]), "is_own: a lane branch match is own, whatever the path")
check(not wr.is_own(entries[1], [r"E:\automation\alpha-wt-7"], ["fix/7-x"]),
      "is_own: a worktree matching neither path nor branch is foreign")
check(not wr.is_own(entries[1], [], [""]), "is_own: an empty branch never matches a detached worktree")

check(wr.classify_worktree(True, False, True)[0] == wr.OWN,
      "classify: own wins even when clean and merged -- the run's own leftover always halts")
check(wr.classify_worktree(False, True, True)[0] == wr.FOREIGN_DIRTY,
      "classify: dirty is checked before merged")
check(wr.classify_worktree(False, False, False)[0] == wr.FOREIGN_UNMERGED, "classify: clean but unmerged")
check(wr.classify_worktree(False, False, True)[0] == wr.FOREIGN_MERGED, "classify: clean and merged")
check(wr.classify_worktree(False, None, True)[0] == wr.UNKNOWN, "classify: unreadable status is unknown")
check(wr.classify_worktree(False, False, None)[0] == wr.UNKNOWN, "classify: unestablished merge state is unknown")

check(wr.overall_verdict([]) == wr.VERDICT_CLEAN, "verdict: no extra worktree is clean")
check(wr.overall_verdict([wr.FOREIGN_MERGED, wr.FOREIGN_MERGED]) == wr.VERDICT_FOREIGN_DEFERRED,
      "verdict: only foreign-merged worktrees defer")
for cls in (wr.OWN, wr.FOREIGN_DIRTY, wr.FOREIGN_UNMERGED):
    check(wr.overall_verdict([wr.FOREIGN_MERGED, cls]) == wr.VERDICT_RESIDUE,
          f"verdict: one {cls} beside a foreign-merged one is residue")
check(wr.overall_verdict([wr.FOREIGN_MERGED, wr.UNKNOWN]) == wr.VERDICT_UNKNOWN,
      "verdict: an unknown is never folded into foreign-deferred")
check(wr.overall_verdict([wr.UNKNOWN, wr.OWN]) == wr.VERDICT_RESIDUE, "verdict: residue outranks unknown")


# ---- real git fixtures --------------------------------------------------

base, origin, primary, git, commit = make_origin_and_primary("wr")


def add_worktree(name: str, branch: str, start: str = "main") -> Path:
    wt = base / name
    git(primary, "worktree", "add", "-q", "-b", branch, str(wt), start)
    return wt


def classify(own_paths=(), own_branches=()):
    rows, verdict, reason, _note = wr.classify_repo(primary, list(own_paths), list(own_branches), fetch=False)
    return {Path(e.path).name: (cls, why) for e, cls, why in rows}, verdict, reason


rows, verdict, _ = classify()
check(verdict == wr.VERDICT_CLEAN and rows == {}, "fixture: a lone primary is clean")

# The incident shape: another lane's worktree, its branch squash-merged into
# main, main moved on afterwards.
nav = add_worktree("alpha-wt-nav-41af40a", "chore/revendor-nav")
commit(nav, "nav.css", "pad: 0\n", "revendor nav part 1")
commit(nav, "nav.css", "pad: 0\nnav: pill\n", "revendor nav part 2")
(primary / "nav.css").write_text("pad: 0\nnav: pill\n", encoding="utf-8")
git(primary, "add", "nav.css")
git(primary, "commit", "-q", "-m", "chore: revendor nav (#45)")  # the squash
commit(primary, "b.txt", "later\n", "an unrelated later commit")
git(primary, "push", "-q", "origin", "main")
git(primary, "fetch", "-q", "origin")

rows, verdict, _ = classify()
check(rows.get("alpha-wt-nav-41af40a", ("",))[0] == wr.FOREIGN_MERGED,
      f"defer case: a foreign clean squash-merged worktree is foreign-merged (got {rows})")
check("squash-merged" in rows.get("alpha-wt-nav-41af40a", ("", ""))[1], "defer case: the reason names the squash proof")
check(verdict == wr.VERDICT_FOREIGN_DEFERRED, f"defer case: the repo verdict is foreign-deferred (got {verdict})")

# A fast-forward-merged foreign branch, and a detached foreign worktree on a merged commit.
ff = add_worktree("alpha-wt-ff", "feat/ff", "main~1")
detached = base / "alpha-wt-detached"
git(primary, "worktree", "add", "-q", "--detach", str(detached), "main~2")
rows, verdict, _ = classify()
check(rows.get("alpha-wt-ff", ("",))[0] == wr.FOREIGN_MERGED and "ancestor" in rows["alpha-wt-ff"][1],
      "defer case: a foreign branch whose tip is an ancestor of origin/main is foreign-merged")
check(rows.get("alpha-wt-detached", ("",))[0] == wr.FOREIGN_MERGED,
      "defer case: a detached foreign worktree on a merged commit is foreign-merged")
check(verdict == wr.VERDICT_FOREIGN_DEFERRED, "defer case: several foreign-merged worktrees still defer")

# Still-halts: the run's own worktree, clean and merged, matched by path and by branch.
own = add_worktree("alpha-wt-12", "fix/12-thing")
rows, verdict, _ = classify(own_paths=[str(own).replace("/", "\\")])
check(rows["alpha-wt-12"][0] == wr.OWN and verdict == wr.VERDICT_RESIDUE,
      "still halts: the run's own clean, merged worktree matched by path is residue")
rows, verdict, _ = classify(own_branches=["fix/12-thing"])
check(rows["alpha-wt-12"][0] == wr.OWN and verdict == wr.VERDICT_RESIDUE,
      "still halts: the run's own worktree matched by branch alone is residue")
rows, verdict, _ = classify()
check(rows["alpha-wt-12"][0] == wr.FOREIGN_MERGED,
      "control: the same worktree with no own-match is foreign-merged -- ownership is what halts it")
git(primary, "worktree", "remove", str(own))

# Still-halts: foreign but dirty (tracked edit, then untracked file alone).
(nav / "nav.css").write_text("pad: 0\nnav: pill\nwip\n", encoding="utf-8")
rows, verdict, _ = classify()
check(rows["alpha-wt-nav-41af40a"][0] == wr.FOREIGN_DIRTY and verdict == wr.VERDICT_RESIDUE,
      "still halts: a foreign merged worktree with a modified file is foreign-dirty residue")
git(nav, "checkout", "-q", "--", "nav.css")
(nav / "scratch.txt").write_text("x\n", encoding="utf-8")
rows, verdict, _ = classify()
check(rows["alpha-wt-nav-41af40a"][0] == wr.FOREIGN_DIRTY,
      "still halts: an untracked file makes a foreign worktree dirty")
(nav / "scratch.txt").unlink()

# Still-halts: foreign, clean, unmerged.
wip = add_worktree("alpha-wt-wip", "feat/wip")
commit(wip, "c.txt", "unshipped\n", "unshipped work")
rows, verdict, _ = classify()
check(rows["alpha-wt-wip"][0] == wr.FOREIGN_UNMERGED and verdict == wr.VERDICT_RESIDUE,
      "still halts: a foreign clean worktree with unmerged commits is foreign-unmerged residue")
check(rows["alpha-wt-nav-41af40a"][0] == wr.FOREIGN_MERGED,
      "still halts: one unmerged foreign worktree halts even beside a harmless one")
git(primary, "worktree", "remove", "--force", str(wip))

# Still-halts: a squash later edited on the same lines is not provably merged.
edit = add_worktree("alpha-wt-edit", "feat/edit")
commit(edit, "a.txt", "two\n", "change a")
(primary / "a.txt").write_text("two\n", encoding="utf-8")
git(primary, "commit", "-q", "-am", "squash of feat/edit")
(primary / "a.txt").write_text("three\n", encoding="utf-8")
git(primary, "commit", "-q", "-am", "later edit of the same line")
git(primary, "push", "-q", "origin", "main")
git(primary, "fetch", "-q", "origin")
rows, verdict, _ = classify()
check(rows["alpha-wt-edit"][0] == wr.FOREIGN_UNMERGED and verdict == wr.VERDICT_RESIDUE,
      "still halts: a merge that conflicts with later main is not proven merged -- conservative halt")
git(primary, "worktree", "remove", "--force", str(edit))

# Unknown: a registered worktree whose directory vanished.
gone = add_worktree("alpha-wt-gone", "feat/gone", "main~1")
shutil.rmtree(gone)
rows, verdict, _ = classify()
check(rows.get("alpha-wt-gone", ("",))[0] == wr.UNKNOWN and verdict == wr.VERDICT_UNKNOWN,
      f"unknown: a registered worktree whose directory is gone is unknown, never foreign-merged (got {rows.get('alpha-wt-gone')})")
git(primary, "worktree", "prune")

# Unknown: a status probe that fails.
rows, verdict, _reason = wr.classify_repo(primary, [], [], fetch=False, dirty_fn=lambda p: (None, "boom"))[0:3]
check(all(cls == wr.UNKNOWN for _, cls, _ in rows) and verdict == wr.VERDICT_UNKNOWN,
      "unknown: an unreadable status never reads as clean")

# Unreadable repo.
_, verdict, reason, _ = wr.classify_repo(base / "not-a-repo-dir", [], [], fetch=False)
check(verdict == wr.VERDICT_UNKNOWN and reason, "unknown: an unreadable worktree list is unknown with a reason")

# CLI shape.
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    wr.main(["classify", str(primary), "--no-fetch", "--own-worktree", str(base / "alpha-wt-99")])
out = buf.getvalue()
check("CLASS=foreign-merged" in out and "BRANCH=chore/revendor-nav" in out and "BRANCH=(detached)" in out,
      f"cli: one WORKTREE= line per extra worktree with its branch and class (got {out!r})")
check(out.strip().splitlines()[-1] == "VERDICT=foreign-deferred", "cli: VERDICT= is the last line")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    wr.main(["classify", str(base / "missing"), "--no-fetch"])
check(buf.getvalue().startswith("VERDICT=unknown") and "REASON=no such path" in buf.getvalue(),
      "cli: a missing repo path is unknown, not clean")

# Nothing was removed: the helper never touches a worktree.
check(nav.exists() and ff.exists() and detached.exists(), "the helper never removes a worktree")

# ---- the one `<repo>-wt-*` scanner (fleet-config#1556) --------------------------
scan_base = base / "scan"
scan_base.mkdir()
scan_primary = scan_base / "Gamma"
scan_primary.mkdir()
registered = scan_base / "Gamma-wt-1"
with_git = scan_base / "gamma-wt-2"
full = scan_base / "gamma-wt-name"
empty = scan_base / "GAMMA-wt-4"
other_repo = scan_base / "delta-wt-1"
a_file = scan_base / "gamma-wt-5"
for d in (registered, with_git, full, empty, other_repo):
    d.mkdir()
(with_git / ".git").write_text("gitdir: x\n")
(full / "f.txt").write_text("x")
a_file.write_text("x")
found = wr.unregistered_siblings(scan_primary, [str(registered).replace("\\", "/")])
check([p.name for p in found] == ["gamma-wt-2", "GAMMA-wt-4", "gamma-wt-name"],
      f"unregistered_siblings: this repo's unlisted dirs only, any suffix, case-insensitive (got {[p.name for p in found]})")
check(wr.dir_facts(with_git).has_git and not wr.dir_facts(full).has_git, "dir_facts: a .git entry is seen")
check(wr.dir_facts(full).empty is False and wr.dir_facts(empty).empty is True, "dir_facts: empty reads the top level")
check(wr.dir_facts(scan_base / "missing") == wr.DirFacts(False, None, None), "dir_facts: an unreadable folder is None, never empty")
check(wr.unregistered_siblings(scan_base / "no" / "such", []) == [], "unregistered_siblings: an unreadable parent is no hit")
check(wr.WT_SEP == "-wt-" and wr.classify_orphan(False, True)[0] == "orphan-has-content", "WT_SEP and classify_orphan live here")

_h.report_and_exit("test_worktree_residue")
