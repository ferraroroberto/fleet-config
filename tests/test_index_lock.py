"""Unit tests for skills/_lib/index_lock.py (fleet-config#667).

Two halves, the way `fleet_audit_scan`'s `is_fleet_repo` is tested:

  * `classify()` is pure, so the whole verdict lattice — including the two
    "could not establish" paths that must NOT collapse into `stale` or
    `fresh` — is driven by plain values, no filesystem and no `tasklist`.
  * `inspect()` is exercised against a real repo with a real planted lock,
    including the *reproduction* of the 2026-08-01 condition: a 0-byte
    `.git/index.lock` backdated fifteen days, on a repo that is clean,
    up to date, and reads perfectly healthy through `status`.

That last assertion is the point of the whole issue and is worth stating
twice: the test proves `git status --porcelain` still exits 0 with the right
answer while the repo is frozen against every write. Any future refactor that
tries to detect this by "just checking whether git commands fail" will fail
here, which is the intent.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_index_lock.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import git_run  # noqa: E402
import index_lock  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402

_h = CheckHarness()
check = _h.check

FIFTEEN_DAYS = 15 * 24 * 3600

# ---------------------------------------------------------------- classify()

check(index_lock.classify(False, None, None) == "absent",
      "classify: no lock file -> absent")
check(index_lock.classify(True, 5.0, False) == "fresh",
      "classify: a young lock is 'fresh' — a git op is legitimately in flight")
check(index_lock.classify(True, index_lock.STALE_AFTER_SECONDS - 1, False) == "fresh",
      "classify: just under the threshold is still fresh (boundary)")
check(index_lock.classify(True, index_lock.STALE_AFTER_SECONDS, False) == "stale",
      "classify: exactly at the threshold, with no git running, is stale (boundary)")
check(index_lock.classify(True, FIFTEEN_DAYS, False) == "stale",
      "classify: the 2026-08-01 shape — old lock, no git process — is 'stale'")

# The two unestablished-fact paths. Neither may be folded into a verdict that
# reads as settled; this repo's standing rule is that a check which cannot
# establish a fact reports that as its own state.
check(index_lock.classify(True, FIFTEEN_DAYS, True) == "stale_unconfirmed",
      "classify: old lock but a git process IS running -> stale_unconfirmed, not stale")
check(index_lock.classify(True, FIFTEEN_DAYS, None) == "stale_unconfirmed",
      "classify: old lock and the process probe failed -> stale_unconfirmed, not stale")
check(index_lock.classify(True, None, False) == "stale_unconfirmed",
      "classify: lock present but its age is unreadable -> stale_unconfirmed, never assumed young")
check(index_lock.classify(True, None, None) != "fresh",
      "classify: an unreadable age is never silently treated as an in-flight lock")

# A probe result must never suppress a report, only downgrade its confidence.
for running in (True, False, None):
    check(index_lock.classify(True, FIFTEEN_DAYS, running) in index_lock.REPORTABLE_VERDICTS,
          f"classify: a lock past the threshold is reported regardless of the probe (git_running={running})")

check(index_lock.classify(True, 5.0, None) == "fresh",
      "classify: the probe is irrelevant below the threshold")
check(index_lock.classify(True, 100.0, None, stale_after=50) == "stale_unconfirmed",
      "classify: an explicit stale_after overrides the module default")
check("absent" not in index_lock.REPORTABLE_VERDICTS and "fresh" not in index_lock.REPORTABLE_VERDICTS,
      "REPORTABLE_VERDICTS: only the two stale verdicts are surfaced to callers")

# ----------------------------------------------------------------- inspect()

tmp = Path(tempfile.mkdtemp(prefix="test_index_lock_"))
try:
    repo = tmp / "fixture"
    repo.mkdir()
    run_git(repo, "init", "-q", check=check)
    run_git(repo, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com", check=check)
    run_git(repo, "config", "user.name", "Test", check=check)
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    run_git(repo, "add", "a.txt", check=check)
    run_git(repo, "commit", "-q", "-m", "initial", check=check)

    lock = repo / ".git" / "index.lock"

    # ---- clean repo, no lock ----
    info = index_lock.inspect(repo)
    check(info["verdict"] == "absent", f"inspect: a healthy repo reports absent, got {info['verdict']!r}")
    check(info["path"] == str(lock), f"inspect: names where the lock would be, got {info['path']!r}")
    check(info["detail"] is None, "inspect: absent carries no detail")

    # The no-spawn fast path for an ordinary checkout must agree exactly with
    # what git itself reports, or the sweep reads the wrong file 39 times a week.
    check(Path(index_lock.index_lock_path(repo)) ==
          Path(git_run.run_git_checked(["-C", str(repo), "rev-parse", "--absolute-git-dir"])) / "index.lock",
          "index_lock_path: the `.git is a dir` fast path agrees with --absolute-git-dir")

    # ---- not a git repo at all: 'unreadable', never 'absent' ----
    notrepo = tmp / "not_a_repo"
    notrepo.mkdir()
    info = index_lock.inspect(notrepo)
    check(info["verdict"] == "unreadable",
          f"inspect: an unresolvable git dir is 'unreadable', not a clean bill of health, got {info['verdict']!r}")
    check(info["detail"] and "git dir" in info["detail"], "inspect: unreadable says why")

    # ---- a fresh lock: in flight, not a fault ----
    lock.write_bytes(b"")
    info = index_lock.inspect(repo)
    check(info["verdict"] == "fresh",
          f"inspect: a just-created lock is 'fresh', got {info['verdict']!r}")
    check(info["size"] == 0 and info["age_seconds"] is not None, "inspect: fresh carries size + age")
    check("in flight" in (info["detail"] or ""), "inspect: fresh detail says a git op is in flight")

    # ---- REPRODUCE 2026-08-01: a 0-byte lock backdated fifteen days ----
    old = time.time() - FIFTEEN_DAYS
    os.utime(lock, (old, old))

    # The heart of the issue: every read the fleet sweep performs still passes.
    st = git_run.run_git(["-C", str(repo), "status", "--porcelain"])
    check(st.returncode == 0 and st.stdout.strip() == "",
          f"repro: `git status --porcelain` STILL exits 0 and reads clean with a stale lock "
          f"(exit {st.returncode}, out {st.stdout!r}) — this is why nine repos hid for fifteen days")
    check(git_run.run_git(["-C", str(repo), "rev-list", "--count", "HEAD"]).returncode == 0,
          "repro: `git rev-list` also still exits 0 with a stale lock")
    # ...while every write is frozen. Both halves matter: a detector keyed on
    # "does git fail" would see nothing wrong.
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    write = git_run.run_git(["-C", str(repo), "add", "a.txt"])
    check(write.returncode != 0,
          f"repro: but a WRITE is frozen (exit {write.returncode}) — reads pass, writes don't")
    # Restore by hand, not with `git checkout --` : that is a write too, and
    # it is frozen for exactly the same reason. Fifteen days of this.
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")

    info = index_lock.inspect(repo)
    check(info["verdict"] in index_lock.REPORTABLE_VERDICTS,
          f"repro: the 2026-08-01 condition IS reported, got {info['verdict']!r}")
    check(info["size"] == 0, f"repro: reported as the 0-byte lock it is, got size {info['size']!r}")
    check(info["age_seconds"] > FIFTEEN_DAYS - 60,
          f"repro: age is reported in full, got {info['age_seconds']!r}")
    check("15.0" in (info["detail"] or "") or "360" in (info["detail"] or ""),
          f"repro: the detail line states the age a human acts on, got {info['detail']!r}")

    # ---- report only: inspect() must never delete ----
    check(lock.exists(), "inspect: REPORTS ONLY — the lock is still there afterwards, never auto-deleted")

    # ---- a generous threshold reclassifies the same lock as in-flight ----
    info = index_lock.inspect(repo, stale_after=FIFTEEN_DAYS * 2)
    check(info["verdict"] == "fresh", f"inspect: stale_after is honoured, got {info['verdict']!r}")

    lock.unlink()
    check(index_lock.inspect(repo)["verdict"] == "absent",
          "inspect: back to absent once the lock is cleared")

    # ---- a linked worktree reports on ITS OWN index, not the primary's ----
    # `--absolute-git-dir`, not `--git-common-dir`: a worktree keeps its index
    # under .git/worktrees/<name>/, so the shared dir would report the wrong
    # tree's lock.
    wt = tmp / "fixture-wt-1"
    run_git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt), check=check)
    try:
        wt_lock = Path(index_lock.index_lock_path(wt))
        check("worktrees" in wt_lock.as_posix(),
              f"inspect: a linked worktree resolves to its own git dir, got {wt_lock}")
        check(wt_lock != lock, "inspect: a worktree's lock path is not the primary's")
        wt_lock.write_bytes(b"")
        os.utime(wt_lock, (old, old))
        check(index_lock.inspect(wt)["verdict"] in index_lock.REPORTABLE_VERDICTS,
              "inspect: a stale lock inside a linked worktree is reported")
        check(index_lock.inspect(repo)["verdict"] == "absent",
              "inspect: ...and does NOT bleed into the primary checkout's verdict")
        wt_lock.unlink()
    finally:
        run_git(repo, "worktree", "remove", "-f", str(wt), check=check)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_index_lock")
