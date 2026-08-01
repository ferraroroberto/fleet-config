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
    check(order and order[0][0] == "strip",
          "remove_worktree: junction strip happens FIRST, before any delete (#526)")

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


_h.report_and_exit("test_worktree_claim")
