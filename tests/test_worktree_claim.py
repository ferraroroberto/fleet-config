"""Unit tests for the pure logic in skills/_lib/worktree_claim.py.

No git, no real worktrees — exercises the claim FSM (atomic acquire, the
worktree fallback when held, TTL-based stale reclaim) and the sibling-path
convention. The git/junction ops are Windows-side and proven by the live
two-terminal check; this guards the decision logic that decides primary vs
worktree.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_worktree_claim.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import worktree_claim as wc  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- worktree_path: sibling convention, prefix-matches cwd_prefix ----

repo = Path("E:/automation/fleet-config")
check(wc.worktree_path(repo, "143") == Path("E:/automation/fleet-config-wt-143"),
      "worktree_path sibling <repo>-wt-<N>")
check(str(wc.worktree_path(repo, "7")).startswith(str(repo)),
      "worktree_path prefix-matches the repo cwd_prefix (notify_on_idle naming)")

# ---- is_stale: TTL boundary + unreadable meta ----

now = 1_000_000.0
check(wc.is_stale(None, now, 8) is True, "is_stale: no meta -> stale")
check(wc.is_stale({}, now, 8) is True, "is_stale: empty meta -> stale")
check(wc.is_stale({"created": "garbage"}, now, 8) is True, "is_stale: bad created -> stale")
check(wc.is_stale({"created": now - 3600}, now, 8) is False, "is_stale: 1h old, 8h ttl -> fresh")
check(wc.is_stale({"created": now - 9 * 3600}, now, 8) is True, "is_stale: 9h old, 8h ttl -> stale")
check(wc.is_stale({"created": now - 8 * 3600 - 1}, now, 8) is True, "is_stale: just past ttl -> stale")

# ---- is_stale: branch-existence reclaim (#174, injected predicate; no git) ----

gone = lambda b: False   # branch no longer exists on the remote
alive = lambda b: True   # branch still exists
fresh_with_branch = {"created": now - 3600, "branch": "fix/174-x"}
check(wc.is_stale(fresh_with_branch, now, 8, branch_exists=gone) is True,
      "is_stale: fresh but branch gone -> stale (#174)")
check(wc.is_stale(fresh_with_branch, now, 8, branch_exists=alive) is False,
      "is_stale: fresh and branch alive -> fresh")
check(wc.is_stale(fresh_with_branch, now, 8) is False,
      "is_stale: no predicate -> branch check skipped, TTL only")
check(wc.is_stale({"created": now - 3600}, now, 8, branch_exists=gone) is False,
      "is_stale: no recorded branch -> branch check skipped")
# TTL still wins regardless of branch state
check(wc.is_stale({"created": now - 9 * 3600, "branch": "fix/174-x"}, now, 8, branch_exists=alive) is True,
      "is_stale: aged-out beats a live branch -> stale")
# try_acquire honors the predicate: a held-but-branch-gone claim is reclaimed
_b = Path(tempfile.mkdtemp(prefix="wtclaim_branch_"))
try:
    _lock = _b / wc.LOCK_NAME
    wc.try_acquire(_lock, {"created": time.time(), "branch": "fix/174-x"}, time.time(), 8)
    mode, _ = wc.try_acquire(_lock, {"created": time.time(), "branch": "fix/174-y"},
                             time.time(), 8, branch_exists=gone)
    check(mode == "primary", "try_acquire: branch-gone holder reclaimed -> primary (#174)")
finally:
    shutil.rmtree(_b, ignore_errors=True)

# ---- try_acquire FSM (hermetic tempdir as the git-common-dir) ----

base = Path(tempfile.mkdtemp(prefix="wtclaim_"))
try:
    lock = base / wc.LOCK_NAME

    def meta(issue: str, age_h: float = 0.0) -> dict:
        return {"created": time.time() - age_h * 3600, "created_iso": "iso", "issue": issue, "branch": f"feat/{issue}"}

    # first session wins primary
    mode, _ = wc.try_acquire(lock, meta("143"), time.time(), 8)
    check(mode == "primary", "acquire: first session -> primary")
    check(lock.exists() and (lock / wc.META_NAME).exists(), "acquire: lock dir + meta written")
    check(wc.read_meta(lock).get("issue") == "143", "acquire: holder meta readable")

    # second concurrent session falls back to worktree, holder reported
    mode, holder = wc.try_acquire(lock, meta("144"), time.time(), 8)
    check(mode == "worktree", "acquire: second session -> worktree")
    check(holder.get("issue") == "143", "acquire: worktree path reports the live holder")

    # release frees it; next session reclaims primary
    shutil.rmtree(lock, ignore_errors=True)  # mirrors cmd_release
    mode, _ = wc.try_acquire(lock, meta("145"), time.time(), 8)
    check(mode == "primary", "release+reacquire: next session -> primary")

    # a stale (crashed) claim is auto-reclaimed without an explicit release
    shutil.rmtree(lock, ignore_errors=True)
    wc.try_acquire(lock, meta("146", age_h=9), time.time(), 8)  # plant a 9h-old claim
    mode, _ = wc.try_acquire(lock, meta("147"), time.time(), 8)
    check(mode == "primary", "stale reclaim: 9h-old claim taken over -> primary")
    check(wc.read_meta(lock).get("issue") == "147", "stale reclaim: new holder recorded")
finally:
    shutil.rmtree(base, ignore_errors=True)


# ---- _resolve_path_arg: bare-name tolerance (#162 repo name, #165 sibling) ----

# Build a real on-disk parent with a repo dir and a sibling worktree dir, then
# resolve from inside the repo. No git needed — _resolve_path_arg is pure path
# logic over Path.cwd().
rbase = Path(tempfile.mkdtemp(prefix="wtresolve_"))
_prev_cwd = os.getcwd()
try:
    repo_dir = rbase / "fleet-config"
    sibling_wt = rbase / "fleet-config-wt-7"
    repo_dir.mkdir()
    sibling_wt.mkdir()
    os.chdir(repo_dir)

    # #162: the repo's own name from inside it -> CWD
    check(wc._resolve_path_arg("fleet-config") == repo_dir.resolve(),
          "_resolve_path_arg: repo's own name -> CWD (#162)")
    # #165: a sibling worktree name from inside the repo -> parent/<name>
    check(wc._resolve_path_arg("fleet-config-wt-7") == sibling_wt.resolve(),
          "_resolve_path_arg: sibling worktree name -> CWD.parent/<name> (#165)")
    # "." still resolves to CWD
    check(wc._resolve_path_arg(".") == repo_dir.resolve(),
          "_resolve_path_arg: '.' -> CWD")
    # absolute path that exists still wins
    check(wc._resolve_path_arg(str(sibling_wt)) == sibling_wt.resolve(),
          "_resolve_path_arg: absolute existing path -> itself")
    # a genuinely missing bare name -> None (no double-append rescue)
    check(wc._resolve_path_arg("does-not-exist-anywhere") is None,
          "_resolve_path_arg: missing bare name -> None")
    # a name with directory components does NOT get the bare-name fallback
    check(wc._resolve_path_arg("sub/fleet-config-wt-7") is None,
          "_resolve_path_arg: name with dir components -> None")
finally:
    os.chdir(_prev_cwd)
    shutil.rmtree(rbase, ignore_errors=True)


# ---- try_acquire: concurrent-acquire race (fleet-config#334) --------------
# Two sessions racing `acquire` on the same repo must never both win "primary"
# -- that's the exact collision that silently wiped a session's uncommitted
# work. Force the precise interleave that let the pre-fix mkdir-then-write_meta
# implementation double-win: pause the first racer's meta write (simulating the
# window between claiming the slot and populating it) until the second racer
# has fully run its own acquire attempt against the still-meta-less claim, then
# release the first. Against the fixed `_publish_claim` (temp-dir + rename),
# the first racer's meta is invisible to the second until the rename publishes
# it, so this same interleave still yields exactly one primary.
race_base = Path(tempfile.mkdtemp(prefix="wtclaim_race_"))
try:
    race_lock = race_base / wc.LOCK_NAME
    first_meta_pending = threading.Event()
    release_first = threading.Event()
    real_write_meta = wc.write_meta

    def paced_write_meta(lock_dir: Path, meta: dict) -> None:
        if meta.get("issue") == "A" and not first_meta_pending.is_set():
            first_meta_pending.set()
            release_first.wait(timeout=5)
        real_write_meta(lock_dir, meta)

    race_results: dict = {}

    def racer_a() -> None:
        race_results["a"] = wc.try_acquire(
            race_lock, {"created": time.time(), "issue": "A"}, time.time(), 8)[0]

    def racer_b() -> None:
        first_meta_pending.wait(timeout=5)
        race_results["b"] = wc.try_acquire(
            race_lock, {"created": time.time(), "issue": "B"}, time.time(), 8)[0]
        release_first.set()

    wc.write_meta = paced_write_meta
    try:
        ta = threading.Thread(target=racer_a)
        tb = threading.Thread(target=racer_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)
    finally:
        wc.write_meta = real_write_meta

    check(not ta.is_alive() and not tb.is_alive(), "race: both racers finished (no deadlock)")
    modes = list(race_results.values())
    check(modes.count("primary") == 1,
          "race: concurrent acquirers -> exactly one primary (#334)")
    check(sorted(modes) == ["primary", "worktree"],
          "race: the loser falls back to worktree, not a second primary (#334)")
finally:
    wc.write_meta = real_write_meta
    shutil.rmtree(race_base, ignore_errors=True)


# ---- owner_check: assert-owner guard decision (#473) -----------------------

check(wc.owner_check(None, "473", dirty=False) == (True, "free"),
      "owner_check: free claim -> pass")
check(wc.owner_check({"issue": "473", "branch": "fix/473-x"}, "473", dirty=False) == (True, "owned"),
      "owner_check: matching-issue claim -> pass")
check(wc.owner_check({"issue": 473, "branch": "fix/473-x"}, "473", dirty=False) == (True, "owned"),
      "owner_check: matching-issue claim, int vs str -> pass")
ok, reason = wc.owner_check(
    {"issue": "469", "branch": "fix/469-x", "created_iso": "2026-07-27T21:33:02"}, "473", dirty=False)
check(ok is False, "owner_check: different-issue claim -> refuse")
check("469" in reason and "fix/469-x" in reason,
      "owner_check: refusal names the holder issue + branch")
check(wc.owner_check(None, "473", dirty=True) == (False, "working tree has uncommitted changes"),
      "owner_check: dirty tree, free claim -> refuse")
check(wc.owner_check({"issue": "473"}, "473", dirty=True)[0] is False,
      "owner_check: dirty tree beats a matching-issue claim -> refuse")


# ---- land_primary_check / format_primary_state: worktree lane lands the primary (#647) ----
#
# A worktree lane's merge is authoritative on the remote but not *live* until
# the primary is fast-forwarded -- in this repo `hooks/` and `skills/` reach
# ~/.claude through junctions rooted there. The guard inherits owner_check and
# adds "already on the default branch", because the fix is `pull --ff-only` on
# a tree already sitting on main -- never a `git checkout` from a worktree.

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="main", main_branch="main")
check(ok is True, "land_primary_check: clean primary on main, free claim -> pass")
check("main" in reason, "land_primary_check: pass reason names the branch")
check(wc.land_primary_check({"issue": "647"}, "647", dirty=False,
                            current_branch="main", main_branch="main")[0] is True,
      "land_primary_check: claim owned by this issue -> pass")

ok, reason = wc.land_primary_check(None, "647", dirty=True,
                                   current_branch="main", main_branch="main")
check(ok is False and reason == "working tree has uncommitted changes",
      "land_primary_check: dirty primary -> refuse (never stash, never force)")

ok, reason = wc.land_primary_check(
    {"issue": "640", "branch": "fix/640-x", "created_iso": "2026-08-16T09:00:00"},
    "647", dirty=False, current_branch="main", main_branch="main")
check(ok is False and "640" in reason,
      "land_primary_check: another issue's live claim -> refuse, naming it")

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="fix/599-y", main_branch="main")
check(ok is False and "fix/599-y" in reason and "main" in reason,
      "land_primary_check: primary parked off its default branch -> refuse, no checkout")
check(wc.land_primary_check(None, "647", dirty=False,
                            current_branch="master", main_branch="master")[0] is True,
      "land_primary_check: non-'main' default branch (life-os) is detected, not assumed")

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="", main_branch="main")
check(ok is False and "detached HEAD" in reason,
      "land_primary_check: detached-HEAD primary refuses by name, not as an empty branch")

# The one line the finish summary quotes -- 'merged' and 'live' are two facts.
check(wc.format_primary_state(True, "clean, on main, claim free", behind=0)
      == "PRIMARY=live behind=0",
      "format_primary_state: landed -> PRIMARY=live behind=0")
check(wc.format_primary_state(False, "working tree has uncommitted changes")
      == "PRIMARY=stale reason=working tree has uncommitted changes",
      "format_primary_state: refusal carries the reason verbatim")
check(wc.format_primary_state(True, "ok", behind=2)
      == "PRIMARY=stale reason=still 2 behind after pull --ff-only",
      "format_primary_state: pull ran but tree still behind -> stale, not live")
for unknown in (None, -1):
    check(wc.format_primary_state(True, "ok", behind=unknown)
          == "PRIMARY=stale reason=could not count commits behind origin",
          f"format_primary_state: uncountable behind ({unknown!r}) -> stale, never folded into live")
check(all(wc.format_primary_state(o, r, b).startswith("PRIMARY=")
          for o, r, b in ((True, "x", 0), (True, "x", 3), (False, "y", None), (True, "x", None))),
      "format_primary_state: every outcome emits a PRIMARY= line (never absent, never implied)")


# ---- worktree_add_args: branch reuse, no `-b` on an existing branch (#602) ----
#
# `-b` means *create*. Applied unconditionally it crashed with a raw
# CalledProcessError on a local branch, and -- silently, the worse half --
# started the lane at `main` when the branch existed only on origin, dropping
# the commits already pushed to it.

WT = Path("C:/tmp/repo-wt-602")

check(wc.worktree_add_args(WT, "fix/602-x", local_exists=False, remote_exists=False,
                           main_ref_value="origin/main")
      == ["worktree", "add", str(WT), "-b", "fix/602-x", "origin/main"],
      "worktree_add_args: brand-new branch still created off the default branch")
check(wc.worktree_add_args(WT, "fix/602-x", local_exists=False, remote_exists=False,
                           main_ref_value="origin/master")
      == ["worktree", "add", str(WT), "-b", "fix/602-x", "origin/master"],
      "worktree_add_args: non-'main' default branch passes through untouched")

check(wc.worktree_add_args(WT, "fix/602-x", local_exists=True, remote_exists=False,
                           main_ref_value="origin/main")
      == ["worktree", "add", str(WT), "fix/602-x"],
      "worktree_add_args: existing local branch is checked out, never re-created")
check("-b" not in wc.worktree_add_args(WT, "fix/602-x", local_exists=True, remote_exists=True,
                                       main_ref_value="origin/main"),
      "worktree_add_args: local branch wins over remote -- still no `-b`")

check(wc.worktree_add_args(WT, "fix/602-x", local_exists=False, remote_exists=True,
                           main_ref_value="origin/main")
      == ["worktree", "add", str(WT), "-b", "fix/602-x", "origin/fix/602-x"],
      "worktree_add_args: pushed-but-not-local branch resumes from origin/<branch>, not main")
check("origin/main" not in wc.worktree_add_args(WT, "fix/602-x", local_exists=False,
                                                remote_exists=True, main_ref_value="origin/main"),
      "worktree_add_args: remote-only branch never silently starts at main")


# ---- acquire --force-worktree: unattended fanout never wins a primary (#515) ----
#
# The whole point is that it must NOT consult, publish, or reclaim a claim: a
# live app or a live junction isn't a claim holder, so "is the claim free?" is
# the wrong question for unattended dispatch. Assert MODE=worktree *and* that
# the lock dir is untouched, which also proves the primary stays free for a
# human session.

force_base = Path(tempfile.mkdtemp(prefix="wc-force-"))
try:
    fake_repo = force_base / "repo"
    fake_repo.mkdir()
    fake_lock = force_base / "lock"

    real_lock_dir_for = wc.lock_dir_for
    real_try_acquire = wc.try_acquire
    try_acquire_calls = []

    wc.lock_dir_for = lambda repo: fake_lock  # noqa: E731
    wc.try_acquire = lambda *a, **k: (try_acquire_calls.append(a) or ("primary", {}))  # noqa: E731

    import argparse as _argparse
    import contextlib as _contextlib
    import io as _io

    def _run_acquire(**kw) -> str:
        ns = _argparse.Namespace(
            repo_root=str(fake_repo), issue="515", branch="fix/515-x",
            ttl_hours=wc.DEFAULT_TTL_HOURS, **kw)
        buf = _io.StringIO()
        with _contextlib.redirect_stdout(buf):
            wc.cmd_acquire(ns)
        return buf.getvalue()

    out = _run_acquire(force_worktree=True)
    check("MODE=worktree" in out, "acquire --force-worktree -> MODE=worktree (#515)")
    check("MODE=primary" not in out,
          "acquire --force-worktree never prints MODE=primary, even with a free claim (#515)")
    check(try_acquire_calls == [],
          "acquire --force-worktree short-circuits before try_acquire (no claim published) (#515)")
    check(not fake_lock.exists(),
          "acquire --force-worktree leaves the primary claim free for a human session (#515)")

    # Control the environment explicitly: this suite itself usually runs inside
    # a launcher-dispatched session, whose APP_LAUNCHER_SESSION_ID would now
    # force worktree mode (#525 take 2) and mask the interactive default.
    _saved_env = {k: os.environ.pop(k) for k in
                  ("APP_LAUNCHER_SESSION_ID", "WORKTREE_CLAIM_ALLOW_PRIMARY")
                  if k in os.environ}
    try:
        out = _run_acquire(force_worktree=False)
        check("MODE=primary" in out and len(try_acquire_calls) == 1,
              "acquire without the flag, outside a dispatched session, keeps claim-or-worktree")

        os.environ["APP_LAUNCHER_SESSION_ID"] = "sess-525"
        out = _run_acquire(force_worktree=False)
        check("MODE=worktree" in out and "MODE=primary" not in out,
              "acquire inside a launcher-dispatched session is forced to worktree "
              "with no flag passed -- enforced in the tool, not in skill prose (#525)")
        check(len(try_acquire_calls) == 1,
              "acquire: the forced path still never publishes a claim (#525)")
    finally:
        os.environ.pop("APP_LAUNCHER_SESSION_ID", None)
        os.environ.update(_saved_env)
finally:
    wc.lock_dir_for = real_lock_dir_for
    wc.try_acquire = real_try_acquire
    shutil.rmtree(force_base, ignore_errors=True)



# ---- worktree_forced: enforcement, not instruction (#525 take 2) -----------
#
# #525 first shipped as a line of /issue-start SKILL.md prose telling the agent
# to pass --force-worktree. A dispatched worker landed in a primary checkout
# within the hour with every precondition satisfied. These checks pin the
# decision in code, where an agent cannot skip it by not reading carefully.

check(wc.worktree_forced(True, {}) == (True, "--force-worktree"),
      "worktree_forced: the explicit flag forces worktree mode")
forced, why = wc.worktree_forced(False, {"APP_LAUNCHER_SESSION_ID": "abc123"})
check(forced is True, "worktree_forced: a launcher-dispatched session is forced even without the flag (#525)")
check("APP_LAUNCHER_SESSION_ID" in why,
      "worktree_forced: the reason names the trigger, so the stderr note is diagnosable")
check(wc.worktree_forced(False, {}) == (False, ""),
      "worktree_forced: an ordinary interactive session is NOT forced (claim-or-worktree preserved)")
check(wc.worktree_forced(False, {"APP_LAUNCHER_SESSION_ID": ""})[0] is False,
      "worktree_forced: an empty session id is not a dispatch signal")
check(wc.worktree_forced(False, {"APP_LAUNCHER_SESSION_ID": "x",
                                 "WORKTREE_CLAIM_ALLOW_PRIMARY": "1"})[0] is False,
      "worktree_forced: WORKTREE_CLAIM_ALLOW_PRIMARY=1 is the deliberate escape hatch")
check(wc.worktree_forced(True, {"APP_LAUNCHER_SESSION_ID": "x",
                                "WORKTREE_CLAIM_ALLOW_PRIMARY": "1"})[0] is True,
      "worktree_forced: an explicit --force-worktree still wins over the escape hatch")
check(wc.worktree_forced(False, {"APP_LAUNCHER_SESSION_ID": "x",
                                 "WORKTREE_CLAIM_ALLOW_PRIMARY": "yes"})[0] is True,
      "worktree_forced: only the literal '1' opens the hatch, not any truthy string")


# ---- primary_for_worktree / remove_worktree: the deregistered leftover (#526) ----
#
# The state that used to crash the helper with an unhandled CalledProcessError:
# git has deregistered the worktree (its .git file is gone, so rev-parse exits
# 128) but the directory survived because a live process held a file inside it.
# That is exactly the leftover teardown is called to clean, so it must degrade,
# not trap -- and the junction strip must still happen FIRST on every path.

deregistered_base = Path(tempfile.mkdtemp(prefix="wc-dereg-"))
try:
    primary = deregistered_base / "myrepo"
    (primary / ".git").mkdir(parents=True)
    leftover = deregistered_base / ("myrepo" + wc.WT_SEP + "526")
    leftover.mkdir()

    real_common_dir = wc.common_dir
    real_git = wc._git
    real_strip = wc._strip_junction

    import subprocess as _sp

    def _dead_common_dir(_wt):
        raise _sp.CalledProcessError(128, ["git", "rev-parse"])

    wc.common_dir = _dead_common_dir

    check(wc.primary_for_worktree(leftover) == primary,
          "primary_for_worktree: falls back to the <repo>-wt-<N> convention when git is dead (#526)")
    check(wc.primary_for_worktree(deregistered_base / "no-separator-here") is None,
          "primary_for_worktree: returns None rather than guessing when nothing resolves (#526)")

    # Full teardown over the leftover: must not raise, must strip first, must go.
    order = []
    wc._strip_junction = lambda p: order.append(("strip", p))
    wc._git = lambda repo, *a, **k: order.append(("git", a[0])) or _sp.CompletedProcess([], 0, "", "")

    rc = wc.remove_worktree(leftover)
    check(rc == 0, "remove_worktree: deregistered-but-present leftover removed, exit 0 (#526)")
    check(not leftover.exists(), "remove_worktree: the leftover directory is actually gone (#526)")
    # The #589 primary-checkout guard now runs a read-only `git rev-parse`
    # before the strip, so "strip" is no longer literally order[0] -- the
    # invariant that actually matters is that it precedes any `git worktree`
    # removal call (nothing may recurse into the .venv junction before it's
    # stripped).
    strip_idx = next(i for i, entry in enumerate(order) if entry[0] == "strip")
    worktree_git_idx = next(i for i, entry in enumerate(order)
                             if entry[0] == "git" and entry[1] == "worktree")
    check(strip_idx < worktree_git_idx,
          "remove_worktree: junction strip happens before any git worktree removal (#526)")

    check(wc.remove_worktree(deregistered_base / "never-existed") == 0,
          "remove_worktree: absent path still exits 0 ('already gone' fast path)")

    # A tree that survives every attempt must fail loudly, never report success.
    stubborn = deregistered_base / ("myrepo" + wc.WT_SEP + "999")
    stubborn.mkdir()
    real_rmtree = shutil.rmtree
    shutil.rmtree = lambda *a, **k: None  # simulate a held directory
    try:
        check(wc.remove_worktree(stubborn) == 1,
              "remove_worktree: a directory that survives teardown exits 1, never a false clean (#526)")
    finally:
        shutil.rmtree = real_rmtree
finally:
    wc.common_dir = real_common_dir
    wc._git = real_git
    wc._strip_junction = real_strip
    shutil.rmtree(deregistered_base, ignore_errors=True)



# ---- copy_runtime_config: the worktree gets its OWN port, not the primary's (#537) ----
#
# Carrying the primary's port across is what made every worktree lane's e2e
# suite report a collision with the live tray and refuse to run. Secrets and
# every other field must still copy verbatim -- the worktree is a faithful
# runtime twin, differing only where sharing is the bug.

import json as _json

port_base = Path(tempfile.mkdtemp(prefix="wc-port-"))
try:
    primary = port_base / "myrepo"
    (primary / "config").mkdir(parents=True)
    (primary / "config" / "webapp_config.json").write_text(
        _json.dumps({"host": "0.0.0.0", "port": 8447, "auth_token": "s3cret"}), encoding="utf-8")
    # A second ported config: app-launcher's webapp + session-host shape.
    (primary / "config" / "hosts.json").write_text(
        _json.dumps({"port": 8447, "name": "session-host"}), encoding="utf-8")
    # No top-level port -- must stay byte-identical.
    plain = {"names": {"a": "b"}, "nested": {"port": 8447}}
    (primary / "config" / "display_names.json").write_text(_json.dumps(plain), encoding="utf-8")
    # Not an object, and not parseable -- neither may break setup.
    (primary / "config" / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (primary / "config" / "broken.json").write_text("{not json", encoding="utf-8")
    (primary / "config" / "webapp_config.sample.json").write_text("{}", encoding="utf-8")

    wt = port_base / ("myrepo" + wc.WT_SEP + "579")
    wt.mkdir()
    copied = wc.copy_runtime_config(primary, wt)

    names = sorted(q.name for q in copied)
    check("webapp_config.sample.json" not in names,
          "copy_runtime_config: *.sample.json still excluded")

    got = _json.loads((wt / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(got["port"] != 8447, "copy_runtime_config: worktree port differs from the primary's (#537)")
    check(wc.WT_PORT_BASE <= got["port"] < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
          "copy_runtime_config: repointed port lands in the 8500-8999 band (#537)")
    check(got["auth_token"] == "s3cret" and got["host"] == "0.0.0.0",
          "copy_runtime_config: every other field copies verbatim, secrets included (#537)")

    got2 = _json.loads((wt / "config" / "hosts.json").read_text(encoding="utf-8"))
    check(got2["port"] != got["port"],
          "copy_runtime_config: two ported configs get two DISTINCT ports (#537)")
    check(got2["name"] == "session-host", "copy_runtime_config: sibling config keeps its other fields")

    check(_json.loads((wt / "config" / "display_names.json").read_text(encoding="utf-8")) == plain,
          "copy_runtime_config: no top-level port -> untouched, nested 'port' not walked (#537)")
    check((wt / "config" / "list.json").read_text(encoding="utf-8") == "[1, 2, 3]",
          "copy_runtime_config: a non-object JSON copies verbatim (#537)")
    check((wt / "config" / "broken.json").read_text(encoding="utf-8") == "{not json",
          "copy_runtime_config: an unparseable config copies verbatim, never breaks setup (#537)")

    # The primary must come out of this untouched.
    src_now = _json.loads((primary / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(src_now["port"] == 8447, "copy_runtime_config: the PRIMARY's own port is never rewritten (#537)")

    # Deterministic: same repo + same issue -> same port when it is still free.
    shutil.rmtree(wt / "config")
    wc.copy_runtime_config(primary, wt)
    again = _json.loads((wt / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(again["port"] == got["port"],
          "copy_runtime_config: same lane re-setup reproduces the same port (#537)")

    # An already-present destination is still left alone.
    wt2 = port_base / ("myrepo" + wc.WT_SEP + "580")
    (wt2 / "config").mkdir(parents=True)
    (wt2 / "config" / "webapp_config.json").write_text('{"port": 1}', encoding="utf-8")
    wc.copy_runtime_config(primary, wt2)
    check(_json.loads((wt2 / "config" / "webapp_config.json").read_text(encoding="utf-8"))["port"] == 1,
          "copy_runtime_config: an existing destination file is not overwritten or repointed")
finally:
    shutil.rmtree(port_base, ignore_errors=True)


# ---- worktree_port: band, determinism, avoids what is taken ----

check(wc.worktree_port("579") == wc.worktree_port("579"),
      "worktree_port: deterministic for the same issue (#537)")
_p = wc.worktree_port("579")
check(wc.worktree_port("579", {_p}) != _p,
      "worktree_port: skips a port already handed out in this worktree (#537)")
check(wc.WT_PORT_BASE <= wc.worktree_port("fix-no-digits") < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
      "worktree_port: a non-numeric issue still lands in band (#537)")


# ---- _looks_like_worktree_name: the <repo>-wt-<N> naming convention (#589) ----

check(wc._looks_like_worktree_name("fleet-config" + wc.WT_SEP + "143") is True,
      "_looks_like_worktree_name: a real <repo>-wt-<N> name matches (#589)")
check(wc._looks_like_worktree_name("fleet-config") is False,
      "_looks_like_worktree_name: a bare repo name (no separator) does not match (#589)")
check(wc._looks_like_worktree_name("fleet-config" + wc.WT_SEP) is False,
      "_looks_like_worktree_name: an empty issue suffix does not match (#589)")
check(wc._looks_like_worktree_name(wc.WT_SEP + "143") is False,
      "_looks_like_worktree_name: an empty stem does not match (#589)")


# ---- _is_primary_checkout_safe: tolerates a non-git target (#589) ----

def _raise_not_a_repo(_p):
    raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

_real_is_primary = wc.is_primary_checkout
try:
    wc.is_primary_checkout = _raise_not_a_repo
    check(wc._is_primary_checkout_safe(Path("not-a-git-repo")) is False,
          "_is_primary_checkout_safe: swallows CalledProcessError -> False, never raises (#589)")
    wc.is_primary_checkout = lambda p: True
    check(wc._is_primary_checkout_safe(Path("whatever")) is True,
          "_is_primary_checkout_safe: passes through a real True (#589)")
finally:
    wc.is_primary_checkout = _real_is_primary


# ---- remove_worktree: refuses a primary checkout, never deletes it (#589) -----
#
# The exact confusion that destroyed life-os's gitignored personal data: an
# agent passed remove-worktree the PRIMARY repo root (the shape every sibling
# subcommand accepts as repo_root) instead of a worktree path. This must now
# refuse loudly and touch nothing, both for a detected primary checkout and
# for a path whose basename doesn't even look like a worktree.

guard_base = Path(tempfile.mkdtemp(prefix="wc-guard-"))
try:
    # Detected as a primary checkout -> hard refusal, exit 2, nothing touched.
    primary_like = guard_base / "myrepo"
    primary_like.mkdir()
    (primary_like / "sentinel.txt").write_text("still here", encoding="utf-8")

    real_is_primary = wc.is_primary_checkout
    wc.is_primary_checkout = lambda p: True
    try:
        rc = wc.remove_worktree(primary_like)
        check(rc == 2, "remove_worktree: refuses a primary checkout, exit 2 (#589)")
        check(primary_like.exists() and (primary_like / "sentinel.txt").exists(),
              "remove_worktree: a primary checkout is left completely untouched (#589)")
    finally:
        wc.is_primary_checkout = real_is_primary

    # Not a primary checkout, but the basename doesn't match <repo>-wt-<N> ->
    # the naming-convention guard refuses too.
    wc.is_primary_checkout = lambda p: False
    try:
        weird_name = guard_base / "not-a-worktree-name"
        weird_name.mkdir()
        rc = wc.remove_worktree(weird_name)
        check(rc == 2, "remove_worktree: refuses a non-<repo>-wt-<N> basename (#589)")
        check(weird_name.exists(),
              "remove_worktree: the mismatched-name path is left untouched (#589)")

        # --force-nonstandard-name is the deliberate override.
        weird_name2 = guard_base / "another-nonstandard-dir"
        weird_name2.mkdir()
        rc = wc.remove_worktree(weird_name2, force_nonstandard_name=True)
        check(rc == 0,
              "remove_worktree: --force-nonstandard-name overrides the naming guard (#589)")
        check(not weird_name2.exists(),
              "remove_worktree: with the override, a nonstandard-named path is removed (#589)")

        # A real <repo>-wt-<N> worktree (correctly not flagged as primary)
        # still tears down normally -- the guard must not break the happy path.
        ok_wt = guard_base / ("myrepo" + wc.WT_SEP + "589")
        ok_wt.mkdir()
        rc = wc.remove_worktree(ok_wt)
        check(rc == 0, "remove_worktree: a real <repo>-wt-<N> worktree still tears down (#589)")
        check(not ok_wt.exists(), "remove_worktree: the real worktree is actually gone (#589)")
    finally:
        wc.is_primary_checkout = real_is_primary
finally:
    shutil.rmtree(guard_base, ignore_errors=True)


# ---- worktree_junction_targets: declarative extra paths from .fleet.toml (#620) ----

wjt_base = Path(tempfile.mkdtemp(prefix="wc-wjt-"))
try:
    bare = wjt_base / "no-toml"
    bare.mkdir()
    check(wc.worktree_junction_targets(bare) == [".venv"],
          "worktree_junction_targets: no .fleet.toml -> .venv-only default (#620)")

    no_table = wjt_base / "no-table"
    no_table.mkdir()
    (no_table / ".fleet.toml").write_text(
        'layer = "enabling"\nicon = "x"\ndescription = "d"\n', encoding="utf-8")
    check(wc.worktree_junction_targets(no_table) == [".venv"],
          "worktree_junction_targets: .fleet.toml without [worktree] -> .venv-only default (#620)")

    invalid = wjt_base / "invalid-toml"
    invalid.mkdir()
    (invalid / ".fleet.toml").write_text("not [ valid toml", encoding="utf-8")
    check(wc.worktree_junction_targets(invalid) == [".venv"],
          "worktree_junction_targets: unparseable .fleet.toml -> .venv-only default, no crash (#620)")

    wrong_type = wjt_base / "wrong-type"
    wrong_type.mkdir()
    (wrong_type / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = "vendor/comfyui"\n', encoding="utf-8")
    check(wc.worktree_junction_targets(wrong_type) == [".venv"],
          "worktree_junction_targets: extra_junctions not a list -> .venv-only default (#620)")

    declared = wjt_base / "declared"
    declared.mkdir()
    (declared / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ["vendor/comfyui", "  /models/cache/  ", 42, "", "../escape"]\n',
        encoding="utf-8")
    check(wc.worktree_junction_targets(declared) == [".venv", "vendor/comfyui", "models/cache"],
          "worktree_junction_targets: declared extras appended after .venv; "
          "non-strings, blanks, and '..'-escaping entries dropped, slashes trimmed (#620)")

    # An unreadable-but-present .fleet.toml (permission error, vanished mid-read,
    # a leftover worktree with a half-deleted checkout) must degrade the same
    # way a MISSING file does -- never raise. If this raised, remove_worktree's
    # `for rel in worktree_junction_targets(wt): _strip_junction(...)` loop would
    # never even start, and the exception would propagate uncaught out of
    # remove_worktree, skipping the .venv strip entirely (fleet-config#620).
    unreadable = wjt_base / "unreadable"
    unreadable.mkdir()
    (unreadable / ".fleet.toml").write_text('[worktree]\nextra_junctions = ["x"]\n', encoding="utf-8")
    real_read_text = Path.read_text

    def _boom(self, *a, **k):
        if self.name == ".fleet.toml":
            raise OSError("simulated: permission denied / vanished mid-read")
        return real_read_text(self, *a, **k)

    Path.read_text = _boom
    try:
        check(wc.worktree_junction_targets(unreadable) == [".venv"],
              "worktree_junction_targets: OSError reading .fleet.toml -> .venv-only "
              "default, never raises (#620)")
    finally:
        Path.read_text = real_read_text
finally:
    shutil.rmtree(wjt_base, ignore_errors=True)


# ---- real junction + reparse-safe teardown: the target survives (#620) ------
#
# Proof this branch exists for: create a REAL junction to a directory holding a
# sentinel file, tear it down the exact way setup_worktree / remove_worktree do
# (strip via _strip_junction BEFORE any recursive delete of the worktree dir),
# and assert the sentinel still exists afterwards. Proven for BOTH junction
# shapes this branch handles: the existing top-level '.venv' junction and the
# NEW declarative nested extra junction ('vendor/comfyui', whose parent dir
# does not pre-exist in a fresh worktree). No git, no real worktree -- a
# throwaway temp-dir fixture only, per the branch's own safety rule.

jt_base = Path(tempfile.mkdtemp(prefix="wc-junction-proof-"))
try:
    primary = jt_base / "primary"
    (primary / ".venv").mkdir(parents=True)
    (primary / ".venv" / "sentinel.txt").write_text("venv-survives", encoding="utf-8")
    (primary / "vendor" / "comfyui").mkdir(parents=True)
    (primary / "vendor" / "comfyui" / "sentinel.txt").write_text("vendor-survives", encoding="utf-8")
    (primary / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ["vendor/comfyui"]\n', encoding="utf-8")

    wt = jt_base / "primary-wt-620"
    wt.mkdir()
    # A real `git worktree add` checks out tracked files, and .fleet.toml is
    # tracked -- mirror that so the teardown side reads targets from the
    # worktree's OWN .fleet.toml, exactly as remove_worktree does.
    shutil.copy2(primary / ".fleet.toml", wt / ".fleet.toml")

    targets = wc.worktree_junction_targets(primary)
    check(targets == [".venv", "vendor/comfyui"],
          "junction proof: fixture declares both junction shapes (#620)")

    for rel in targets:
        link = wt / rel
        ok, err = wc._junction(link, primary / rel)
        check(ok, f"junction proof: real mklink /J succeeded for {rel!r} ({err})")

    # Prove the junctions actually work before tearing anything down.
    check((wt / ".venv" / "sentinel.txt").read_text(encoding="utf-8") == "venv-survives",
          "junction proof: .venv junction reads through to the primary's real file")
    check((wt / "vendor" / "comfyui" / "sentinel.txt").read_text(encoding="utf-8") == "vendor-survives",
          "junction proof: nested extra-junction reads through to the primary's real file")

    # Tear down exactly as remove_worktree does: strip every declared target
    # BEFORE any recursive delete of the worktree directory.
    for rel in wc.worktree_junction_targets(wt):
        wc._strip_junction(wt / rel)
    shutil.rmtree(wt, ignore_errors=True)

    check(not wt.exists(), "junction proof: the worktree directory itself is gone")
    check((primary / ".venv" / "sentinel.txt").read_text(encoding="utf-8") == "venv-survives",
          "junction proof: .venv junction TARGET survives teardown intact (#620)")
    check((primary / "vendor" / "comfyui" / "sentinel.txt").read_text(encoding="utf-8") == "vendor-survives",
          "junction proof: nested extra-junction TARGET survives teardown intact (#620)")
finally:
    shutil.rmtree(jt_base, ignore_errors=True)


# ---- land-primary CLI, git-backed: the worktree lane's landing step (#647) ----
#
# End-to-end over real repos, because the CLI *is* what a lane invokes and the
# whole defect was a missing step rather than a wrong computation. Asserts the
# three outcomes a finish summary can carry, and -- the safety property that
# must not regress -- that a refusal leaves the tree exactly as it found it:
# nothing stashed, nothing checked out, nothing forced.

CLI = str(Path(__file__).resolve().parent.parent / "skills" / "_lib" / "worktree_claim.py")


def _land(repo: Path, issue: str = "647") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, "land-primary", str(repo), issue],
                          capture_output=True, text=True)


def _git_t(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # `core.hooksPath` -> an empty dir: this machine carries a global commit
    # hook that rejects non-allowlisted author emails, which would otherwise
    # silently leave the fixture repos commit-less.
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                          "-c", f"core.hooksPath={_no_hooks}", *args],
                         cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0 and args[0] in ("init", "clone", "commit", "add", "checkout"):
        raise AssertionError(f"fixture git {args[0]} failed: {res.stderr.strip()}")
    return res


land_base = Path(tempfile.mkdtemp(prefix="wc-land-"))
_no_hooks = land_base / "nohooks"
_no_hooks.mkdir()
try:
    upstream = land_base / "upstream"
    upstream.mkdir()
    _git_t(upstream, "init", "-b", "main")
    (upstream / "a.txt").write_text("one", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "one")
    primary = land_base / "myrepo"
    _git_t(land_base, "clone", str(upstream), str(primary))

    # Upstream moves ahead -- exactly the post-merge state a worktree lane is in.
    (upstream / "b.txt").write_text("two", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "two")

    res = _land(primary)
    check(res.returncode == 0 and res.stdout.strip() == "PRIMARY=live behind=0",
          f"land-primary: clean primary behind origin -> fast-forwards, PRIMARY=live (got {res.stdout.strip()!r})")
    check((primary / "b.txt").exists(),
          "land-primary: the merged file is actually present in the primary afterwards")

    res = _land(primary)
    check(res.returncode == 0 and "PRIMARY=live" in res.stdout,
          "land-primary: idempotent -- an already-current primary is live, not an error")

    # Refusal 1: dirty tree. The reason a lane must report stale, never recover.
    (primary / "untracked.txt").write_text("a human's work in progress", encoding="utf-8")
    res = _land(primary)
    check(res.returncode == 1 and
          res.stdout.strip() == "PRIMARY=stale reason=working tree has uncommitted changes",
          f"land-primary: dirty primary -> PRIMARY=stale, exit 1 (got {res.stdout.strip()!r})")
    check((primary / "untracked.txt").read_text(encoding="utf-8") == "a human's work in progress",
          "land-primary: a refused tree is untouched -- never stashed, never forced (#647)")
    (primary / "untracked.txt").unlink()

    # Refusal 2: primary parked off its default branch. The fix is `pull
    # --ff-only` on a tree already on main -- never a checkout from a worktree.
    _git_t(primary, "checkout", "-q", "-b", "fix/599-someone-elses-work")
    res = _land(primary)
    check(res.returncode == 1 and "not 'main'" in res.stdout and "fix/599" in res.stdout,
          f"land-primary: primary off its default branch -> stale, no checkout (got {res.stdout.strip()!r})")
    check(_git_t(primary, "branch", "--show-current").stdout.strip() == "fix/599-someone-elses-work",
          "land-primary: refusing did not switch the primary's branch (#647)")
    _git_t(primary, "checkout", "-q", "main")

    # Refusal 3: a live claim held by a *different* issue.
    lock = wc.lock_dir_for(primary)
    lock.mkdir(parents=True, exist_ok=True)
    wc.write_meta(lock, {"issue": "640", "branch": "fix/640-x", "created_iso": "2026-08-16T09:00:00"})
    res = _land(primary, issue="647")
    check(res.returncode == 1 and "640" in res.stdout,
          f"land-primary: another issue's live claim -> stale, naming the holder (got {res.stdout.strip()!r})")
    check(_land(primary, issue="640").returncode == 0,
          "land-primary: the claim's own issue is still allowed to land")
    shutil.rmtree(lock, ignore_errors=True)

    # Wrong target: a linked worktree, not the primary. Refuses, changes nothing.
    wt = wc.setup_worktree(primary, "647", "fix/647-x")
    res = _land(wt)
    check(res.returncode == 2 and "not a primary checkout" in res.stdout,
          f"land-primary: pointed at a worktree -> exit 2, no landing attempted (got {res.stdout.strip()!r})")
    check(wt.exists(), "land-primary: the mistargeted worktree is left intact (never a teardown)")
    wc.remove_worktree(wt)

    # Every outcome emitted exactly one PRIMARY= line -- the summary is never silent.
    check(all(len([l for l in _land(p, i).stdout.strip().splitlines() if l.startswith("PRIMARY=")]) == 1
              for p, i in ((primary, "647"), (primary, "999"))),
          "land-primary: exactly one PRIMARY= line on stdout in every outcome (#647)")
finally:
    shutil.rmtree(land_base, ignore_errors=True)


# ---- mode CLI, git-backed: answers about the cwd, not the argument (#652) ----
#
# Over a REAL linked worktree, deliberately: the whole defect lives in path
# resolution, so a mocked filesystem would reproduce nothing. From inside
# `<repo>-wt-<N>`, `_resolve_path_arg`'s sibling fallback (#165) turns the bare
# repo name every skill documents into the *primary*, and the pre-fix
# implementation answered `primary` to a worktree lane -- silently, and always
# toward the teardown path that fails with "'main' is already used by worktree".

# Pure half first: reconciliation is at repo granularity, never tree granularity.
_g = Path("E:/automation/fleet-config/.git")
check(wc.mode_check(_g, _g) == (True, "repo argument and cwd agree on the repo"),
      "mode_check: same shared git dir -> answerable (a worktree naming its primary)")
check(wc.mode_check(None, _g)[0] is False and "cwd is not inside" in wc.mode_check(None, _g)[1],
      "mode_check: cwd outside git -> not answerable, with a reason")
check(wc.mode_check(_g, None)[0] is False,
      "mode_check: argument not a checkout -> not answerable")
check(wc.mode_check(_g, Path("E:/automation/app-launcher/.git"))[0] is False,
      "mode_check: a genuinely different repo -> not answerable")


def _mode(cwd: Path, arg: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, "mode", arg],
                          cwd=str(cwd), capture_output=True, text=True)


mode_base = Path(tempfile.mkdtemp(prefix="wc-mode-"))
_no_hooks = mode_base / "nohooks"
_no_hooks.mkdir()
try:
    # Cloned, not `init`ed: `setup_worktree` branches off `main_ref`, which
    # resolves to `origin/main` -- a bare init has no such ref.
    upstream = mode_base / "upstream"
    upstream.mkdir()
    _git_t(upstream, "init", "-b", "main")
    (upstream / "a.txt").write_text("one", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "one")

    primary = mode_base / "myrepo"
    _git_t(mode_base, "clone", str(upstream), str(primary))
    other = mode_base / "otherrepo"
    _git_t(mode_base, "clone", str(upstream), str(other))

    wt = wc.setup_worktree(primary, "652", "fix/652-x")
    check(wt.exists() and wt.name == "myrepo-wt-652",
          "mode fixture: a real linked worktree exists (not a mock)")
    # The disagreement that *is* the bug, asserted directly.
    check(_git_t(wt, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip()
          != _git_t(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(),
          "mode fixture: inside the worktree --git-dir and --git-common-dir differ")

    # THE regression: the bare-name form every SKILL.md documents, from the worktree.
    res = _mode(wt, "myrepo")
    check(res.returncode == 0 and res.stdout.strip() == "worktree",
          f"mode: bare repo name from inside <repo>-wt-<N> -> 'worktree' (got {res.stdout.strip()!r}) (#652)")

    res = _mode(primary, "myrepo")
    check(res.returncode == 0 and res.stdout.strip() == "primary",
          f"mode: bare repo name from the primary still -> 'primary' (got {res.stdout.strip()!r})")

    check(_mode(wt, ".").stdout.strip() == "worktree" and
          _mode(primary, ".").stdout.strip() == "primary",
          "mode: the explicit '.' form agrees with the bare-name form in both trees")

    # Naming the *other* tree of the same repo is not a mismatch -- the argument
    # identifies the repo, the cwd selects the tree.
    check(_mode(primary, str(wt)).stdout.strip() == "primary" and
          _mode(wt, str(primary)).stdout.strip() == "worktree",
          "mode: naming the sibling tree still answers about the cwd, not the argument")

    # Non-answers: distinct, reasoned, never a confident wrong answer.
    unknowns = [
        (_mode(wt, str(other)), "a genuinely different repo"),
        (_mode(wt, "no-such-repo-anywhere"), "a name that resolves to nothing"),
        (_mode(mode_base, str(primary)), "a cwd outside any git checkout"),
    ]
    for res, what in unknowns:
        out = res.stdout.strip()
        check(res.returncode == 2 and out.startswith("UNKNOWN reason=") and len(out) > len("UNKNOWN reason="),
              f"mode: {what} -> 'UNKNOWN reason=<why>', exit 2 (got {out!r}, rc={res.returncode})")
    check(all(r.stdout.strip() not in ("primary", "worktree") for r, _ in unknowns),
          "mode: an unreconcilable argument NEVER produces a confident primary/worktree answer (#652)")

    # Audit of the sibling subcommands (#652 criterion): `status` is repo-scoped
    # -- its lock lives in the shared git dir and `worktree list` is identical
    # from every tree -- so argument-vs-cwd resolution cannot change its answer.
    st_from_wt = _git_t(wt, "worktree", "list").stdout.strip()
    st_from_primary = _git_t(primary, "worktree", "list").stdout.strip()
    check(st_from_wt == st_from_primary,
          "status audit: `git worktree list` is identical from the primary and the worktree")
    check(wc.lock_dir_for(wt) == wc.lock_dir_for(primary),
          "status audit: the claim lock resolves to one shared path from either tree (repo-scoped)")

    wc.remove_worktree(wt)
finally:
    shutil.rmtree(mode_base, ignore_errors=True)


_h.report_and_exit("test_worktree_claim")
