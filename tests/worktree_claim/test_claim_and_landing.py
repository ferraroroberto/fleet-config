"""worktree_claim's decision logic: the claim FSM, owner and land-primary guards, the live-service probe and worktree_add args.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/worktree_claim/test_claim_and_landing.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from _suite import _h, check, sp, wc, wclaim, wgit  # noqa: E402


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

    wclaim.write_meta = paced_write_meta
    try:
        ta = threading.Thread(target=racer_a)
        tb = threading.Thread(target=racer_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)
    finally:
        wclaim.write_meta = real_write_meta

    check(not ta.is_alive() and not tb.is_alive(), "race: both racers finished (no deadlock)")
    modes = list(race_results.values())
    check(modes.count("primary") == 1,
          "race: concurrent acquirers -> exactly one primary (#334)")
    check(sorted(modes) == ["primary", "worktree"],
          "race: the loser falls back to worktree, not a second primary (#334)")
finally:
    wclaim.write_meta = real_write_meta
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

# `service` is the fourth guard's verdict (#665), passed in like every other
# fact so the decision stays pure; NO_SERVICE is "nothing runs out of this
# tree", the fleet-config case these three guards were written for.
NO_SERVICE = (True, "no long-running service declared")

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="main", main_branch="main", behind=1,
                                   service=NO_SERVICE)
check(ok is True, "land_primary_check: clean primary on main, free claim -> pass")
check("main" in reason, "land_primary_check: pass reason names the branch")
check(wc.land_primary_check({"issue": "647"}, "647", dirty=False,
                            current_branch="main", main_branch="main", behind=1,
                            service=NO_SERVICE)[0] is True,
      "land_primary_check: claim owned by this issue -> pass")

ok, reason = wc.land_primary_check(None, "647", dirty=True,
                                   current_branch="main", main_branch="main", behind=1,
                                   service=NO_SERVICE)
check(ok is False and reason == "working tree has uncommitted changes",
      "land_primary_check: dirty primary -> refuse (never stash, never force)")

ok, reason = wc.land_primary_check(
    {"issue": "640", "branch": "fix/640-x", "created_iso": "2026-08-16T09:00:00"},
    "647", dirty=False, current_branch="main", main_branch="main", behind=1,
    service=NO_SERVICE)
check(ok is False and "640" in reason,
      "land_primary_check: another issue's live claim -> refuse, naming it")

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="fix/599-y", main_branch="main", behind=1,
                                   service=NO_SERVICE)
check(ok is False and "fix/599-y" in reason and "main" in reason,
      "land_primary_check: primary parked off its default branch -> refuse, no checkout")
check(wc.land_primary_check(None, "647", dirty=False,
                            current_branch="master", main_branch="master", behind=1,
                            service=NO_SERVICE)[0] is True,
      "land_primary_check: non-'main' default branch (life-os) is detected, not assumed")

ok, reason = wc.land_primary_check(None, "647", dirty=False,
                                   current_branch="", main_branch="main", behind=1,
                                   service=NO_SERVICE)
check(ok is False and "detached HEAD" in reason,
      "land_primary_check: detached-HEAD primary refuses by name, not as an empty branch")


# ---- the live-service capability is reachable without the claim FSM (#680) ----
#
# It moved to `service_probe.py` precisely so `/issue-finish`, the webapp
# restart helper, and any future health sweep can ask "is this repo's declared
# service up?" without importing the 1400-line worktree module -- so the checks
# below drive it as `sp.`, and the new module importing standalone is itself
# asserted (a broken import is an ImportError before any check runs). Two more
# structural facts, both of which would otherwise fail silently: `land-primary`
# reaches the *same object* rather than a second copy, and the probe never
# imports the claim module back -- the dependency runs one way.
check(wc.service_state is sp.service_state,
      "service_probe: worktree_claim calls service_probe's own service_state, not a copy")
for _name in ("SERVICE_LIVE", "live_service_check", "declared_service",
              "listening_ports", "probe_service"):
    check(not hasattr(wc, _name),
          f"service_probe: {_name} has exactly one home -- worktree_claim no longer carries it")
check("worktree_claim" not in sys.modules["worktree.service_probe"].__dict__,
      "service_probe: does not import worktree_claim back -- the dependency runs one way")


# ---- live_service_check: the fourth guard -- don't land a tree in use (#665) ----
#
# `#647`'s three questions were all satisfied when the app-launcher#773 lane
# fast-forwarded a primary whose tray was serving it: disk moved to 1705558
# while the running Python stayed at 9af573f, so jobs-row.js gained a control
# the live API did not understand. The asymmetry is the whole design -- not
# landing is behind-but-coherent, landing is behind-AND-skewed -- so the
# declared-and-running case refuses, and there is no "landed anyway" variant.

ok, reason = sp.live_service_check(True, sp.SERVICE_LIVE, port=8445, running_sha="9af573f")
check(ok is False, "live_service_check: declared tray + live webapp -> refuse (the #665 case)")
check("8445" in reason and "9af573f" in reason,
      "live_service_check: refusal names the port and the commit actually running")
check("restart" in reason,
      "live_service_check: refusal names the restart as the remedy, not a repair")

check(sp.live_service_check(False, sp.SERVICE_UNKNOWN,
                            detail="fleet-config declares no tray_cmd")[0] is True,
      "live_service_check: no tray_cmd -> lands unconditionally (the #647 junction case)")
check(sp.live_service_check(True, sp.SERVICE_ABSENT,
                            port=8445, detail="nothing listening on 127.0.0.1:8445")[0] is True,
      "live_service_check: declared but not running -> nothing is served, landing is safe")

for probe in (sp.SERVICE_UNKNOWN, "", "garbled"):
    ok, reason = sp.live_service_check(True, probe, port=8445, detail="probe timed out")
    check(ok is False and "cannot confirm" in reason,
          f"live_service_check: unestablished probe ({probe!r}) refuses -- never folded into a pass")

ok, reason = sp.live_service_check(True, sp.SERVICE_UNKNOWN,
                                   detail="no webapp_port declared to probe")
check(ok is False and "no webapp_port" in reason,
      "live_service_check: tray declared with nothing to probe -> refuse, saying why")

# Composition: the new guard is fourth, so the older refusals still speak first.
ok, reason = wc.land_primary_check(None, "665", dirty=False,
                                   current_branch="main", main_branch="main", behind=1,
                                   service=sp.live_service_check(True, sp.SERVICE_LIVE, port=8445))
check(ok is False and "live process serving this tree" in reason,
      "land_primary_check: clean, on main, claim free -- and still refused for the live service")
check(wc.format_primary_state(*wc.land_primary_check(
          None, "665", dirty=False, current_branch="main", main_branch="main", behind=1,
          service=sp.live_service_check(True, sp.SERVICE_LIVE, port=8445, running_sha="9af573f")))
      == ("PRIMARY=stale reason=live process serving this tree (webapp :8445 at 9af573f); "
          "restart required, not a fast-forward"),
      "land_primary_check: the live-service refusal reaches the summary as one PRIMARY=stale line")
check(wc.land_primary_check(None, "665", dirty=True,
                            current_branch="main", main_branch="main", behind=1,
                            service=sp.live_service_check(True, sp.SERVICE_LIVE, port=8445)
                            )[1] == "working tree has uncommitted changes",
      "land_primary_check: a dirty tree still reports its own reason, not the service one")


# ---- "is there anything to pull?" comes before the service guard (#671) ----
#
# grocery's tree sat at 6f8f641, its webapp reported 6f8f641, zero commits
# behind -- and land-primary still said `PRIMARY=stale ... restart required`,
# exit 1. Nothing was stale and no restart would change anything: the #665
# guard is right about fast-forwarding a tree out from under a running server,
# but a landing that is a no-op cannot skew one. Reporting stale there
# downgrades an *established* fact, the mirror of the unknown-is-not-pass rule,
# and teaches the reader to ignore the one line #647 exists to produce.

LIVE_SERVICE = sp.live_service_check(True, sp.SERVICE_LIVE, port=8502, running_sha="6f8f641")

ok, reason = wc.land_primary_check(None, "671", dirty=False,
                                   current_branch="main", main_branch="main", behind=0,
                                   service=LIVE_SERVICE)
check(ok is True,
      "land_primary_check: level tree + live service -> pass (nothing to fast-forward, #671)")
check(wc.format_primary_state(ok, reason, 0) == "PRIMARY=live behind=0",
      "land_primary_check: the in-sync pass reaches the summary as PRIMARY=live behind=0")
check(wc.land_primary_check(None, "671", dirty=False,
                            current_branch="main", main_branch="main", behind=0,
                            service=NO_SERVICE)[0] is True,
      "land_primary_check: level tree with no service declared still passes")
for probe in (sp.SERVICE_UNKNOWN, "garbled"):
    check(wc.land_primary_check(None, "671", dirty=False,
                                current_branch="main", main_branch="main", behind=0,
                                service=sp.live_service_check(True, probe, port=8502))[0] is True,
          f"land_primary_check: level tree passes even on an unestablished probe ({probe!r})")

# The #665 guard itself is untouched: genuinely behind + live service refuses.
ok, reason = wc.land_primary_check(None, "671", dirty=False,
                                   current_branch="main", main_branch="main", behind=2,
                                   service=LIVE_SERVICE)
check(ok is False and "live process serving this tree" in reason,
      "land_primary_check: genuinely behind + live service -> still refuses (#665 unchanged)")

# ...and an *unknown* distance is not zero -- it takes the ordinary path.
ok, reason = wc.land_primary_check(None, "671", dirty=False,
                                   current_branch="main", main_branch="main", behind=None,
                                   service=LIVE_SERVICE)
check(ok is False and "live process serving this tree" in reason,
      "land_primary_check: uncountable behind is not a level tree -- no shortcut past the guard")
check(wc.land_primary_check(None, "671", dirty=False,
                            current_branch="main", main_branch="main", behind=None,
                            service=NO_SERVICE)[0] is True,
      "land_primary_check: uncountable behind with no service still lands (pull decides)")

# Behind-with-no-service is the plain fast-forward case, untouched by all this.
check(wc.land_primary_check(None, "671", dirty=False,
                            current_branch="main", main_branch="main", behind=3,
                            service=NO_SERVICE)[0] is True,
      "land_primary_check: genuinely behind, nothing serving -> fast-forwards as before")

# The earlier guards still speak first: level does not excuse a dirty or
# off-branch primary, because those refusals are about the tree, not the pull.
check(wc.land_primary_check(None, "671", dirty=True,
                            current_branch="main", main_branch="main", behind=0,
                            service=LIVE_SERVICE)[1] == "working tree has uncommitted changes",
      "land_primary_check: behind=0 does not short-circuit the dirty-tree refusal")
check(wc.land_primary_check(None, "671", dirty=False,
                            current_branch="fix/671-x", main_branch="main", behind=0,
                            service=NO_SERVICE)[0] is False,
      "land_primary_check: behind=0 does not short-circuit the off-branch refusal")

# The new input is a required argument, exactly like `service`: a call site
# that forgets it must be a TypeError, never a silently skipped guard.
try:
    wc.land_primary_check(None, "671", dirty=False, current_branch="main",
                          main_branch="main", service=NO_SERVICE)  # type: ignore[call-arg]
    check(False, "land_primary_check: omitting `behind` must raise TypeError")
except TypeError:
    check(True, "land_primary_check: omitting `behind` raises TypeError, never skips the check")

# count_behind supplies that input, and its failure value is the whole safety
# margin: a git that cannot answer must read as unknown, never as level.
_nogit = Path(tempfile.mkdtemp(prefix="wc-nogit-"))
try:
    check(wc.count_behind(_nogit, "origin/main") is None,
          "count_behind: a path git cannot answer for -> None, never 0")
    check(wc.count_behind(_nogit, "origin/main", fetch=False) is None,
          "count_behind: fetch=False on an unanswerable path is still None")
finally:
    shutil.rmtree(_nogit, ignore_errors=True)


# declared_service reads the fleet's existing membership table -- and an
# unreadable one is nothing established, so it refuses rather than reading a
# failed parse as "this repo declares no tray".
_toml_base = Path(tempfile.mkdtemp(prefix="wc-toml-"))
try:
    missing = _toml_base / "nope.toml"
    check(sp.declared_service(Path("E:/automation/app-launcher"), missing)[0] is True,
          "declared_service: unreadable projects.toml -> declared, i.e. refuse (never a silent pass)")
    ok, reason = sp.service_state(Path("E:/automation/app-launcher"), missing)
    check(ok is False and "could not read hooks/projects.toml" in reason,
          f"service_state: a table it cannot read names that as the reason (got {reason!r})")

    broken = _toml_base / "broken.toml"
    broken.write_text("[unclosed\n", encoding="utf-8")
    ok, reason = sp.service_state(Path("E:/automation/app-launcher"), broken)
    check(ok is False and "could not read hooks/projects.toml" in reason,
          "service_state: a malformed table refuses too -- a failed parse is not a declaration")

    declaring = _toml_base / "ok.toml"
    declaring.write_text(
        '[app-launcher]\ncwd_prefix = "E:/automation/app-launcher"\n'
        'webapp_port = 8445\ntray_cmd = "tray.bat"\napi_version_path = "/api/version"\n'
        '[fleet-config]\ncwd_prefix = "E:/automation/fleet-config"\n', encoding="utf-8")
    check(sp.declared_service(Path("E:/automation/app-launcher"), declaring)
          == (True, 8445, "/api/version", ""),
          "declared_service: tray_cmd + port + api path are read straight off the table")
    check(sp.declared_service(Path("E:\\automation\\app-launcher"), declaring)[0] is True,
          "declared_service: a backslash path matches a forward-slash cwd_prefix (same tree)")
    check(sp.declared_service(Path("E:/automation/fleet-config"), declaring)[0] is False,
          "declared_service: no tray_cmd -> no service, whatever else the table says (#647 intact)")

    # fleet-config#686: a repo *absent from the table entirely* is not the
    # same fact as one that is registered and declares no tray_cmd -- the
    # first cut conflated both under `declared=False`, so an undeclared repo
    # (task-os, live on :8448 while unregistered for days) landed exactly
    # like a genuine known absence instead of refusing as unknown.
    check(sp.declared_service(Path("E:/automation/task-os"), declaring)[0] is True,
          "declared_service: absent from the table -> unknown (routes to refuse), not a known absence")
    ok, reason = sp.service_state(Path("E:/automation/task-os"), declaring)
    check(ok is False and "not declared in hooks/projects.toml" in reason,
          f"service_state: an undeclared repo refuses, naming the missing declaration (got {reason!r})")
    ok, reason = wc.land_primary_check(None, "686", dirty=False,
                                       current_branch="main", main_branch="main", behind=1,
                                       service=sp.service_state(Path("E:/automation/task-os"), declaring))
    check(ok is False and "not declared in hooks/projects.toml" in reason,
          "land_primary_check: an undeclared repo never reaches PRIMARY=live")

    # The genuine known-absence case (fleet-config: registered, no tray_cmd)
    # must keep landing unconditionally through the same service_state path,
    # or every merge to this repo stops deploying through the ~/.claude
    # junctions (#647).
    check(sp.service_state(Path("E:/automation/fleet-config"), declaring)[0] is True,
          "service_state: registered with no tray_cmd still lands (#647 regression guard)")
finally:
    shutil.rmtree(_toml_base, ignore_errors=True)

# The one line the finish summary quotes -- 'merged' and 'live' are two facts.
check(wc.format_primary_state(True, "clean, on main, claim free", behind=0)
      == "PRIMARY=live behind=0",
      "format_primary_state: landed -> PRIMARY=live behind=0")
check(wc.format_primary_state(False, "working tree has uncommitted changes")
      == "PRIMARY=stale reason=working tree has uncommitted changes",
      "format_primary_state: refusal carries the reason verbatim")
check(wc.format_primary_state(True, "ok", behind=2)
      == "PRIMARY=stale reason=still 2 behind after the fast-forward",
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

    wgit.common_dir = _dead_common_dir

    check(wc.primary_for_worktree(leftover) == primary,
          "primary_for_worktree: falls back to the <repo>-wt-<N> convention when git is dead (#526)")
    check(wc.primary_for_worktree(deregistered_base / "no-separator-here") is None,
          "primary_for_worktree: returns None rather than guessing when nothing resolves (#526)")

    # Full teardown over the leftover: must not raise, must strip first, must go.
    order = []
    wgit._strip_junction = lambda p: order.append(("strip", p))
    wgit._git = lambda repo, *a, **k: order.append(("git", a[0])) or _sp.CompletedProcess([], 0, "", "")

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
    wgit.common_dir = real_common_dir
    wgit._git = real_git
    wgit._strip_junction = real_strip
    shutil.rmtree(deregistered_base, ignore_errors=True)

_h.report_and_exit("test_claim_and_landing")
