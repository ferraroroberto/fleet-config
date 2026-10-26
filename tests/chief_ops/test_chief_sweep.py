"""Unit tests for skills/_lib/chief/chief_sweep.py, the chief's worktree sweep (fleet-config#1496).

Every class (`finished`, `in-use`, `has-work`, `orphan-empty`, `unknown`) is
driven against real throwaway git worktrees; only the `gh` PR lookup, the board
and the issue lookup are faked. The squash-merged case is a local SHA ahead of
`origin/main` whose PR the fake reports MERGED -- the one `git log` alone gets
wrong. `--apply` runs the real `worktree_claim.remove_worktree` against the
throwaway tree, with the hooks state dir pointed at a temp folder so no live
registry is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_sweep.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

TMP = throwaway_dir("chief_sweep_")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(TMP / "state")

import chief_ops as co  # noqa: E402
from chief import chief_sweep as cs  # noqa: E402
import worktree_claim  # noqa: E402

_h = CheckHarness()
check = _h.check

upstream, work = make_upstream_and_clone(TMP, check)


def add_worktree(n: int, age_s: float = 3600) -> Path:
    """A real worktree whose `.git` file is backdated, so it is past the sweep's grace period."""
    wt = TMP / f"work-wt-{n}"
    run_git(work, "worktree", "add", "-q", "-b", f"feat/{n}", str(wt), "origin/main", check=check)
    stamp = time.time() - age_s
    os.utime(wt / ".git", (stamp, stamp))
    return wt


def commit(wt: Path, name: str = "x.txt") -> str:
    (wt / name).write_text("work\n", encoding="utf-8")
    run_git(wt, "add", name, check=check)
    run_git(wt, "commit", "-q", "-m", "work", check=check)
    return run_git(wt, "rev-parse", "HEAD", check=check)


MERGED_OID = {}


def pr_lookup(_remote, branch):
    return {
        "feat/2": [{"number": 52, "state": "MERGED", "headRefOid": MERGED_OID["feat/2"]}],
        "feat/3": [{"number": 53, "state": "MERGED", "headRefOid": "0" * 40}],
        "feat/5": [{"number": 55, "state": "OPEN", "headRefOid": "1" * 40}],
        "feat/6": None,
        "feat/12": [],
    }[branch]


# ---- the fixtures ------------------------------------------------------------

wt1 = add_worktree(1)                                   # fresh, nothing beyond origin/main
wt2 = add_worktree(2)                                   # squash-merged: local SHA ahead, PR merged
MERGED_OID["feat/2"] = commit(wt2)
wt3 = add_worktree(3)                                   # merged PR whose head is not our HEAD
commit(wt3)
wt4 = add_worktree(4)                                   # dirty: an untracked file
(wt4 / "scratch.txt").write_text("x", encoding="utf-8")
wt5 = add_worktree(5)                                   # unmerged: open PR
commit(wt5)
wt6 = add_worktree(6)                                   # PR state not established
commit(wt6)
wt7 = add_worktree(7)                                   # clean, but a live session sits in it
(wt7 / "sub").mkdir()
wt8 = add_worktree(8)                                   # only a gitignored file (a copied live config)
(work / ".git" / "info" / "exclude").write_text("secret.cfg\n", encoding="utf-8")
(wt8 / "secret.cfg").write_text("token", encoding="utf-8")
wt11 = add_worktree(11)                                 # clean, a live active-issue marker names it
wt12 = add_worktree(12)                                 # commits, no PR at all
commit(wt12)
wt13 = add_worktree(13, age_s=30)                       # clean, no marker, created seconds ago
wt14 = add_worktree(14)                                 # clean, no marker, a chief-managed lane is on work#14
(TMP / "work-wt-9").mkdir()                             # empty, not a worktree
(TMP / "work-wt-10").mkdir()                            # not a worktree, not empty
(TMP / "work-wt-10" / "left.txt").write_text("x", encoding="utf-8")
(TMP / "not-a-worktree").mkdir()                        # name does not match: never scanned

LIVE_MARKER = {"work#11": {"owner_host": "box", "owner_pid": 4242, "owner_session_id": "s11"}}


MANAGED = {"lane14": {"repo": "work", "number": 14}}


def sweep(dirs=(), markers=None, session_ids=frozenset(), pid_probe=lambda _p: False, managed=MANAGED):
    found = cs.scan(
        TMP,
        dirs=None if dirs is None else [cs._norm(d) for d in dirs],
        markers=LIVE_MARKER if markers is None else markers,
        session_ids=session_ids, pr_lookup=pr_lookup, host="box", pid_probe=pid_probe, managed=managed,
    )
    return {f.name: f for f in found}


def cls(found, n):
    return found[f"work-wt-{n}"].cls


# ---- classification ------------------------------------------------------------

found = sweep(dirs=[wt7 / "sub"], pid_probe=lambda _p: True)
check("not-a-worktree" not in found and "work" not in found and "upstream" not in found,
      "scan: only <repo>-wt-<N> folders are classified")
check(cls(found, 1) == cs.FINISHED and "nothing beyond origin/main" in found["work-wt-1"].evidence,
      "finished: a clean worktree with no commits beyond origin/main")
check(cls(found, 2) == cs.FINISHED and "pr=#52 merged" in found["work-wt-2"].evidence
      and "ahead=1" in found["work-wt-2"].evidence,
      "finished: a squash-merged branch (local SHA ahead, PR merged) is finished")
check(cls(found, 3) == cs.HAS_WORK, "has-work: a merged PR whose head is not the local HEAD (commits it never saw)")
check(cls(found, 4) == cs.HAS_WORK and "dirty=1" in found["work-wt-4"].evidence,
      "has-work: a dirty tree, with the dirty count as evidence")
check(cls(found, 5) == cs.HAS_WORK and "#55 open" in found["work-wt-5"].evidence,
      "has-work: commits in an open PR are not finished")
check(cls(found, 6) == cs.UNKNOWN and "PR state not established" in found["work-wt-6"].evidence,
      "unknown: a failed PR lookup is its own state, never finished")
check(cls(found, 7) == cs.IN_USE and "live session cwd" in found["work-wt-7"].evidence,
      "in-use: a live session's cwd inside the worktree")
check(cls(found, 8) == cs.FINISHED, "finished: a gitignored copied config does not make the tree dirty")
check(cls(found, 9) == cs.ORPHAN_EMPTY, "orphan-empty: an empty non-git folder")
check(cls(found, 10) == cs.UNKNOWN and "not empty" in found["work-wt-10"].evidence,
      "unknown: a non-empty non-git folder is not removed on a guess")
check(cls(found, 11) == cs.IN_USE and "work#11" in found["work-wt-11"].evidence,
      "in-use: a live active-issue marker names the worktree")
check(cls(found, 12) == cs.HAS_WORK and "pr=none" in found["work-wt-12"].evidence,
      "has-work: commits with no PR at all")

dead = sweep(markers=LIVE_MARKER, pid_probe=lambda _p: False)
check(cls(dead, 11) == cs.FINISHED, "finished: a marker whose owner is dead no longer holds the worktree")
unknown_owner = sweep(markers={"work#11": {"owner_host": "elsewhere", "owner_pid": 1}})
check(cls(unknown_owner, 11) == cs.UNKNOWN, "unknown: a marker owner on another host cannot be called dead")

check(cls(found, 13) == cs.IN_USE and "younger than 10 min" in found["work-wt-13"].evidence,
      "in-use: a tree younger than the grace period is never finished, marker or not")
check(cls(found, 14) == cs.FINISHED,
      "finished: a chief-managed row whose session is not live does not hold the tree")
live_lane = sweep(session_ids=frozenset({"lane14"}), pid_probe=lambda _p: True)
check(cls(live_lane, 14) == cs.IN_USE and "live session lane14" in live_lane["work-wt-14"].evidence,
      "in-use: a live chief-managed session for the tree's repo#N holds it without a marker")
check(cls(sweep(session_ids=frozenset({"lane14"}), managed={"lane14": {"repo": "work", "number": 15}}), 14) == cs.FINISHED,
      "finished: a live chief-managed session on a different issue does not hold the tree")
check(cs.classify_worktree(wt13, dirs=[], markers={}, session_ids=set(), pr_lookup=pr_lookup, grace_s=0).cls == cs.FINISHED,
      "finished: the same fresh tree past a zero grace period, so only the grace period held it")

blind = sweep(dirs=None)
check(cls(blind, 1) == cs.UNKNOWN and "live sessions not established" in blind["work-wt-1"].evidence,
      "unknown: an unreadable board never lets a worktree read as finished")
check(cls(blind, 9) == cs.UNKNOWN, "unknown: an unreadable board also holds back orphan-empty")
check(cls(blind, 4) == cs.HAS_WORK, "has-work stands on git evidence alone, board or not")

# ---- apply ---------------------------------------------------------------------

found_list = list(sweep(dirs=[wt7 / "sub"], pid_probe=lambda _p: True).values())
stuck = cs.apply_findings(found_list, worktree_claim.remove_worktree)
check(stuck == 0, "apply: nothing stuck on a clean teardown")
gone = {n for n in (1, 2, 8, 9) if not (TMP / f"work-wt-{n}").exists()}
check(gone == {1, 2, 8, 9}, f"apply: finished and orphan-empty folders are removed (gone={sorted(gone)})")
kept = [n for n in (3, 4, 5, 6, 7, 10, 11, 12) if (TMP / f"work-wt-{n}").exists()]
check(kept == [3, 4, 5, 6, 7, 10, 11, 12],
      f"apply: has-work, in-use and unknown folders are never touched (kept={kept})")
check(all(f.outcome == "REMOVED" for f in found_list if f.cls in cs.REMOVABLE),
      "apply: every removed folder is recorded REMOVED")
check(all(f.outcome == "" for f in found_list if f.cls not in cs.REMOVABLE),
      "apply: a non-removable folder carries no outcome")
check(not re.search(r"work-wt-1\s", run_git(work, "worktree", "list", check=check)),
      "apply: the worktree is deregistered from the primary")

wt_busy = add_worktree(20)
(TMP / "work-wt-21").mkdir()
busy = [cs.Finding("work-wt-20", wt_busy, cs.FINISHED, "e"), cs.Finding("work-wt-21", TMP / "work-wt-21", cs.ORPHAN_EMPTY, "e")]


def held(_path):
    raise OSError("in use by another process")


stuck = cs.apply_findings(busy, lambda _wt: 1, rmdir=held)
check(stuck == 2 and busy[0].outcome.startswith("STAYS") and "busy" in busy[1].outcome
      and wt_busy.exists() and (TMP / "work-wt-21").exists(),
      "apply: a folder that will not go stays and is reported, not counted as removed")

# ---- stale lane sessions -------------------------------------------------------


def card(sid, status="idle-finished", alive=True):
    return {"session_id": sid, "status": status, "alive": alive}


managed = {"aaaa1111x": {"repo": "r", "number": 1}, "bbbb2222x": {"repo": "r", "number": 2},
           "cccc3333x": {"repo": "r", "number": 3}, "dddd4444x": {"repo": "r", "number": 4},
           "eeee5555x": {"repo": "r", "number": 5}}
issue_states = {1: {"state": "CLOSED"}, 2: {"state": "OPEN"}, 3: {"error": "gh error"}}
lines = cs.stale_sessions(
    [card("aaaa1111x"), card("bbbb2222x"), card("cccc3333x"), card("dddd4444x", status="working"),
     card("eeee5555x", alive=False), card("ffff6666x")],
    managed, lambda _repo, n: issue_states[n],
)
check(len(lines) == 3, f"sessions: only alive idle-finished chief-managed cards are listed ({lines})")
check("aaaa1111 r#1" in lines[0] and lines[0].endswith("-> stoppable"), "sessions: a closed issue is stoppable")
check(lines[1].endswith("-> idle-open"), "sessions: an open issue is the chief's call, not stoppable")
check(lines[2].endswith("-> unknown") and "gh error" in lines[2], "sessions: a failed issue lookup is unknown")

# ---- CLI ------------------------------------------------------------------------

board = {"columns": {"claude_turn": [{"session_id": "s", "alive": True, "project_dir": str(TMP / "work-wt-7")}]}}
saved_request = co.launcher._request
co.launcher._request = lambda *_a, **_k: board
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = co.main(["sweep", "--root", str(TMP)])
    text = out.getvalue()
    check(code == 0 and "in-use       work-wt-7" in text and "SUMMARY" in text and "read-only" in text,
          "CLI: read-only sweep prints one line per worktree plus a SUMMARY")
    check((TMP / "work-wt-20").exists(), "CLI: read-only touches nothing")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = co.main(["sweep", "--root", str(TMP), "--apply"])
    check(code == 0 and not (TMP / "work-wt-20").exists() and not (TMP / "work-wt-21").exists()
          and (TMP / "work-wt-7").exists() and "-> REMOVED" in out.getvalue(),
          "CLI: --apply removes the finished and orphan-empty folders and keeps the in-use one")

    def unreachable(*_a, **_k):
        raise OSError("connection refused")

    co.launcher._request = unreachable
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = co.main(["sweep", "--root", str(TMP), "--apply"])
    check(code == 0 and "work-wt-7" in out.getvalue() and (TMP / "work-wt-7").exists()
          and (TMP / "work-wt-4").exists() and "unknown      work-wt-7" in out.getvalue(),
          "CLI: with the board unreadable --apply removes nothing and says unknown")
finally:
    co.launcher._request = saved_request

_h.report_and_exit("test_chief_sweep")
