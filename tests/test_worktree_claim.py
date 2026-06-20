"""Unit tests for the pure logic in skills/_lib/worktree_claim.py.

No git, no real worktrees — exercises the claim FSM (atomic acquire, the
worktree fallback when held, TTL-based stale reclaim) and the sibling-path
convention. The git/junction ops are Windows-side and proven by the live
two-terminal check; this guards the decision logic that decides primary vs
worktree.

Run: `py tests/test_worktree_claim.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import worktree_claim as wc  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


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


if _fails:
    print("FAIL test_worktree_claim:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_worktree_claim: all checks pass")
