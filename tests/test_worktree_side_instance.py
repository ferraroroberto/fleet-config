"""Unit tests for the side-instance staging guards (fleet-config#937).

Three behaviours, each traced to something that actually happened on
2026-09-19 while lanes validated changes by standing a second app instance up
out of a worktree:

  1. `side_instance_decision` / `side-instance-preflight` -- the refusal that
     did not exist. `worktree_forced` already refuses a primary checkout, but
     only inside `acquire`, which a free-text-dispatched lane never runs; the
     live app then served an hour of uncommitted work. Booting is the step
     such a lane cannot skip, so the check is asserted there, including its
     third state: `unknown` is not permission.
  2. `read_safe_config_keys` -- the fail-closed half. An undeclared
     machine-bound key stays blank, a declared one survives, and a key that is
     both declared read-safe and explicitly blank-listed is blanked, because
     the dangerous mistake is failing to blank.
  3. Port reservation -- two worktrees provisioned with nothing listening must
     not be written the same port, the loser must be told who holds it, and a
     torn-down lane must not strand one.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_worktree_side_instance.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Must precede the import: the registry resolves through
# `hooks_state.state_dir()` at call time, and this file runs as a bare
# subprocess with the ambient environment.
_STATE_DIR = tempfile.mkdtemp(prefix="wt-side-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = _STATE_DIR

_LIB = Path(__file__).resolve().parent.parent / "skills" / "_lib"
sys.path.insert(0, str(_LIB))
import worktree_claim as wc  # noqa: E402
import worktree_config as wcfg  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check

PYTHON = sys.executable
CLAIM = str(_LIB / "worktree_claim.py")


def _preflight(checkout: Path, extra_env: dict = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("WORKTREE_CLAIM_ALLOW_PRIMARY", None)
    env["CLAUDE_HOOKS_STATE_DIR"] = _STATE_DIR
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, CLAIM, "side-instance-preflight", str(checkout)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# ---- 1. the refusal, as pure logic ----------------------------------------

_NO_ENV: dict = {}

check(wc.side_instance_decision(False, _NO_ENV, False)[0] == wc.SIDE_OK,
      "side_instance_decision: a linked worktree is the sanctioned place to boot")
check(wc.side_instance_decision(True, _NO_ENV, False)[0] == wc.SIDE_REFUSED,
      "side_instance_decision: a primary checkout is REFUSED by default (#937)")
check(wc.side_instance_decision(None, _NO_ENV, False)[0] == wc.SIDE_UNKNOWN,
      "side_instance_decision: an unanswerable probe is 'unknown', never 'ok' (#937)")
check(wc.side_instance_decision(None, {"WORKTREE_CLAIM_ALLOW_PRIMARY": "1"}, True)[0]
      == wc.SIDE_UNKNOWN,
      "side_instance_decision: neither escape upgrades 'unknown' to a pass -- "
      "they permit a KNOWN primary, they do not establish the fact")
check(wc.side_instance_decision(True, {"WORKTREE_CLAIM_ALLOW_PRIMARY": "1"}, False)[0]
      == wc.SIDE_OK,
      "side_instance_decision: WORKTREE_CLAIM_ALLOW_PRIMARY=1 is honoured, same "
      "spelling worktree_forced uses")
check(wc.side_instance_decision(True, {"WORKTREE_CLAIM_ALLOW_PRIMARY": "yes"}, False)[0]
      == wc.SIDE_REFUSED,
      "side_instance_decision: only the literal '1' opens the env hatch")
check(wc.side_instance_decision(True, _NO_ENV, True)[0] == wc.SIDE_OK,
      "side_instance_decision: a repo may declare its own primary lanes legitimate "
      "(life-os, whose gitignored state exists only in the primary clone)")
check("primary" in wc.side_instance_decision(True, _NO_ENV, False)[1]
      and "worktree" in wc.side_instance_decision(True, _NO_ENV, False)[1],
      "side_instance_decision: the refusal says what is wrong AND what to do instead")


# ---- worktree_primary_instance_ok: only a literal true ---------------------

_toml_base = Path(tempfile.mkdtemp(prefix="wt-side-toml-"))
try:
    def _declare(body: str) -> Path:
        target = Path(tempfile.mkdtemp(dir=_toml_base))
        if body is not None:
            (target / ".fleet.toml").write_text(body, encoding="utf-8")
        return target

    check(wcfg.worktree_primary_instance_ok(_declare(None)) is False,
          "primary_instance_ok: no .fleet.toml -> False (refusal is the default)")
    check(wcfg.worktree_primary_instance_ok(_declare('layer = "governance"\n')) is False,
          "primary_instance_ok: no [worktree] table -> False")
    check(wcfg.worktree_primary_instance_ok(
        _declare('[worktree]\nprimary_instance_ok = true\n')) is True,
          "primary_instance_ok: a literal true opts the repo in")
    check(wcfg.worktree_primary_instance_ok(
        _declare('[worktree]\nprimary_instance_ok = "true"\n')) is False,
          "primary_instance_ok: the STRING \"true\" is not an opt-in -- fail closed")
    check(wcfg.worktree_primary_instance_ok(
        _declare('[worktree]\nprimary_instance_ok = true\n[[oops\n')) is False,
          "primary_instance_ok: an unparseable .fleet.toml -> False, never a pass")
finally:
    shutil.rmtree(_toml_base, ignore_errors=True)


# ---- 2. read_safe_config_keys: fail-closed exemption -----------------------

_blank_base = Path(tempfile.mkdtemp(prefix="wt-side-blank-"))
try:
    def _config(**keys) -> Path:
        target = Path(tempfile.mkdtemp(dir=_blank_base)) / "webapp_config.json"
        target.write_text(json.dumps(keys), encoding="utf-8")
        return target

    LIVE = {
        "projects_dir": "E:\\automation",
        "sessions_state_file": "C:\\Users\\rober\\.claude\\hooks\\state\\sessions-state.json",
        "nested": {"life_os_dir": "E:\\automation\\life-os"},
        "port": 8445,
    }

    dst = _config(**LIVE)
    wcfg.blank_machine_bound_config(dst, None, ["projects_dir"])
    got = json.loads(dst.read_text(encoding="utf-8"))
    check(got["projects_dir"] == "E:\\automation",
          "read_safe_config_keys: a declared read path survives the heuristic (#937)")
    check(got["sessions_state_file"] == "",
          "read_safe_config_keys: an UNDECLARED write path is still blanked -- "
          "an unknown key is unsafe, never assumed safe (#937)")
    check(got["nested"]["life_os_dir"] == "",
          "read_safe_config_keys: the exemption does not leak into nested tables")
    check(got["port"] == 8445,
          "read_safe_config_keys: non-path values are untouched, as before")

    dst = _config(**LIVE)
    wcfg.blank_machine_bound_config(dst, None, ["nested"])
    check(json.loads(dst.read_text(encoding="utf-8"))["nested"]["life_os_dir"]
          == "E:\\automation\\life-os",
          "read_safe_config_keys: a declared sub-table covers its children, the same "
          "spelling secret_config_keys uses for a whole table")

    dst = _config(**LIVE)
    wcfg.blank_machine_bound_config(dst, None, [])
    check(json.loads(dst.read_text(encoding="utf-8"))["projects_dir"] == "",
          "read_safe_config_keys: declaring nothing blanks everything, exactly as "
          "before this change -- an undeclared repo is unaffected")

    dst = _config(**LIVE)
    wcfg.blank_machine_bound_config(dst, ["projects_dir"], ["projects_dir"])
    check(json.loads(dst.read_text(encoding="utf-8"))["projects_dir"] == "",
          "read_safe_config_keys: an explicit blank_config_keys entry WINS over a "
          "read-safe one -- the dangerous mistake is failing to blank (#937)")

    # The preflight's own re-check of an already-provisioned config.
    clean = {"projects_dir": "E:\\automation", "sessions_state_file": ""}
    check(wc._unsafe_config_keys(dict(clean), None, ["projects_dir"]) == [],
          "_unsafe_config_keys: a declared read path in a provisioned config is clean")
    restored = {"projects_dir": "E:\\automation",
                "sessions_state_file": "C:\\Users\\rober\\.claude\\hooks\\state\\x.json"}
    check(wc._unsafe_config_keys(dict(restored), None, ["projects_dir"])
          == ["sessions_state_file"],
          "_unsafe_config_keys: a hand-restored WRITE path is reported unsafe -- the "
          "exact mistake that would point a test instance at live state (#937)")
    check(wc._unsafe_config_keys(dict(restored), ["mirror.dir"], []) == [],
          "_unsafe_config_keys: a repo that declared blank_config_keys is judged on "
          "ITS OWN keys -- the heuristic is not second-guessed over its choice")
    check(wc._unsafe_config_keys("not an object", None, []) == [],
          "_unsafe_config_keys: a config that isn't a JSON object is not a finding")
finally:
    shutil.rmtree(_blank_base, ignore_errors=True)


# ---- 3. port reservation: exclusive before anything binds ------------------

_port_base = Path(tempfile.mkdtemp(prefix="wt-side-port-"))
try:
    wt_a = _port_base / "repo-wt-701"
    wt_b = _port_base / "repo-wt-702"
    wt_a.mkdir()
    wt_b.mkdir()

    port_a = wcfg.worktree_port("701", owner=wt_a)
    check(wcfg.reserve_port(port_a, wt_a, "webapp_config.json") is None,
          "reserve_port: the first claimant gets the port")
    check(wcfg.reserve_port(port_a, wt_a, "webapp_config.json") is None,
          "reserve_port: idempotent for the same worktree -- a re-run of setup "
          "re-confirms its own reservation rather than colliding with itself")
    holder = wcfg.reserve_port(port_a, wt_b, "webapp_config.json")
    check(holder == str(wt_a.resolve()),
          "reserve_port: a second worktree is refused and TOLD WHO HOLDS IT, instead "
          "of a bind error that never mentions the other lane (#937)")
    check(wcfg.worktree_port("701", owner=wt_b) != port_a,
          "worktree_port: another live worktree's reservation is taken, even though "
          "nothing is listening yet -- the gap the probe alone could not close (#937)")
    check(wcfg.worktree_port("701", owner=wt_a) == port_a,
          "worktree_port: a lane's OWN reservation is not an obstacle, so re-running "
          "setup still reproduces the same port (#537 determinism preserved)")
    check(port_a in wcfg.port_reservations(),
          "port_reservations: a live reservation is visible to every lane on the box")

    # Liveness is the directory, not a clock.
    port_b = wcfg.worktree_port("702", owner=wt_b)
    wcfg.reserve_port(port_b, wt_b, "webapp_config.json")
    shutil.rmtree(wt_b)
    check(port_b not in wcfg.port_reservations(),
          "port_reservations: a reservation whose worktree is gone is pruned -- a "
          "crashed lane cannot strand a port (#937)")
    check(wcfg.reserve_port(port_b, wt_a, "other.json") is None,
          "reserve_port: the pruned port is immediately reservable by another lane")

    released = wcfg.release_ports(wt_a)
    check(released == sorted([port_a, port_b]),
          f"release_ports: teardown hands back every port the worktree held "
          f"(got {released}, wanted {sorted([port_a, port_b])})")
    check(wcfg.port_reservations() == {},
          "release_ports: nothing of that worktree's is left behind")

    # A registry that cannot be parsed is `unknown`, not `empty`.
    check(wcfg.port_registry_state() == "ok",
          "port_registry_state: a readable registry is 'ok'")
    wcfg.port_registry_path().write_text("{ not json", encoding="utf-8")
    check(wcfg.port_registry_state() == "unknown",
          "port_registry_state: an unparseable registry is 'unknown', never folded "
          "into 'nothing is reserved'")
    check(wcfg.port_reservations() == {},
          "port_reservations: still fail-open for provisioning -- a broken state "
          "file must not fail a worktree setup")
    wt_c = _port_base / "repo-wt-703"
    wt_c.mkdir()
    try:
        wcfg.reserve_port(8999, wt_c, "x.json")
        check(False, "reserve_port: an unreadable registry must NOT be overwritten -- "
                     "a whole-file write over a `{}` read would delete every other "
                     "lane's live reservation")
    except OSError:
        check(True, "reserve_port: refuses to overwrite an unreadable registry, so a "
                    "parse failure cannot silently evict other lanes")
    check(wcfg.port_registry_path().read_text(encoding="utf-8") == "{ not json",
          "reserve_port: the unreadable registry is left exactly as found, for a "
          "human to look at")
    wcfg.port_registry_path().unlink()
    check(wcfg.port_registry_state() == "ok",
          "port_registry_state: an absent registry is 'ok' -- nothing reserved yet "
          "is a fact, unlike a registry we could not read")
finally:
    shutil.rmtree(_port_base, ignore_errors=True)


# ---- the CLI, end to end over real git trees -------------------------------

_cli_base = Path(tempfile.mkdtemp(prefix="wt-side-cli-"))
try:
    if shutil.which("git") is None:
        _h.skip("side-instance-preflight CLI: git not on PATH, end-to-end NOT verified")
    else:
        # Cloned, not `git init`ed: `worktree_add_args` resolves `origin/<main>`
        # for a branch that exists nowhere yet, so a remote-less fixture cannot
        # exercise the real setup path. `make_upstream_and_clone` also owns the
        # commit identity this machine's global author-allowlist hook demands
        # -- hand-rolling it leaves the fixture silently commit-less.
        _upstream, primary = make_upstream_and_clone(_cli_base, check)
        (primary / ".gitignore").write_text("config/\n", encoding="utf-8")
        run_git(primary, "add", "-A", check=check)
        run_git(primary, "commit", "-q", "-m", "ignore config", check=check)

        res = _preflight(primary)
        check(res.returncode == 1 and "SIDE_INSTANCE=refused" in res.stdout,
              f"preflight CLI: a primary checkout exits 1 and says so "
              f"(rc={res.returncode}, out={res.stdout.strip()[:120]!r}) (#937)")

        res = _preflight(primary, {"WORKTREE_CLAIM_ALLOW_PRIMARY": "1"})
        check(res.returncode == 0 and "SIDE_INSTANCE=ok" in res.stdout,
              "preflight CLI: the env hatch is honoured end to end")

        (primary / ".fleet.toml").write_text(
            '[worktree]\nprimary_instance_ok = true\n', encoding="utf-8")
        run_git(primary, "add", "-A", check=check)
        run_git(primary, "commit", "-q", "-m", "declare", check=check)
        res = _preflight(primary)
        check(res.returncode == 0 and "primary_instance_ok" in res.stdout,
              "preflight CLI: a repo-declared exception is honoured end to end, so "
              "life-os-shaped operational lanes keep working")

        res = _preflight(_cli_base / "no-such-tree")
        check(res.returncode == 2 and "SIDE_INSTANCE=unknown" in res.stdout,
              f"preflight CLI: a path that resolves to nothing is 'unknown' (exit 2), "
              f"never a pass (rc={res.returncode})")

        res = _preflight(_cli_base)
        check(res.returncode == 2 and "SIDE_INSTANCE=unknown" in res.stdout,
              f"preflight CLI: a directory outside any git checkout is 'unknown' "
              f"(exit 2), never a pass (rc={res.returncode})")

        # A real worktree, provisioned the way setup_worktree provisions one.
        wt = wc.setup_worktree(primary, "937", "feat/937-probe")
        try:
            res = _preflight(wt)
            check(res.returncode == 0 and "SIDE_INSTANCE=ok" in res.stdout,
                  f"preflight CLI: a linked worktree passes "
                  f"(rc={res.returncode}, out={res.stdout.strip()[:160]!r})")
            check("STILL_YOURS:" in res.stdout,
                  "preflight CLI: a green run still states what the lane owns -- a "
                  "helper that hides its edges is the failure this issue exists to fix")

            # A config provisioned before reservations existed holds an
            # unreserved port. The preflight must CLAIM it, not report it as
            # an eternal `unknown` -- otherwise the check only names the risk.
            (wt / "config").mkdir(exist_ok=True)
            cfg = wt / "config" / "webapp_config.json"
            cfg.write_text(json.dumps({"port": 8987}), encoding="utf-8")
            check(8987 not in wcfg.port_reservations(),
                  "preflight CLI fixture: the port starts unreserved")
            res = _preflight(wt)
            check(res.returncode == 0 and "claimed now" in res.stdout,
                  f"preflight CLI: an unreserved port is CLAIMED, so the answer is "
                  f"total rather than observational "
                  f"(rc={res.returncode}, out={res.stdout.strip()[:200]!r}) (#937)")
            row = wcfg.port_reservations().get(8987)
            check(row is not None and row.get("worktree") == str(wt.resolve()),
                  "preflight CLI: the reservation is recorded against this checkout")
            res = _preflight(wt)
            check(res.returncode == 0 and "claimed now" not in res.stdout,
                  "preflight CLI: re-running is idempotent -- the lane's own "
                  "reservation is not a collision with itself")

            # A sibling lane holding the same port is a real collision.
            rival = _cli_base / "rival-wt-1"
            rival.mkdir()
            wcfg.release_ports(wt)
            wcfg.reserve_port(8987, rival, "webapp_config.json")
            res = _preflight(wt)
            check(res.returncode == 1 and "HELD BY" in res.stdout
                  and str(rival.resolve()) in res.stdout,
                  f"preflight CLI: a port another live worktree holds is refused and "
                  f"the holder is NAMED, instead of a bind error that never mentions "
                  f"the other lane (rc={res.returncode}) (#937)")
            wcfg.release_ports(rival)

            # Hand-restore a write path, exactly as a lane did on 2026-09-19.
            cfg.write_text(json.dumps({
                "port": 8987,
                "sessions_state_file": "C:\\Users\\rober\\.claude\\hooks\\state\\x.json",
            }), encoding="utf-8")
            res = _preflight(wt)
            check(res.returncode == 1 and "CONFIG_UNSAFE=" in res.stdout
                  and "sessions_state_file" in res.stdout,
                  f"preflight CLI: a restored WRITE path is refused by name "
                  f"(rc={res.returncode}, out={res.stdout.strip()[:200]!r}) (#937)")
        finally:
            wc.remove_worktree(wt)
finally:
    shutil.rmtree(_cli_base, ignore_errors=True)
    shutil.rmtree(_STATE_DIR, ignore_errors=True)


_h.report_and_exit("test_worktree_side_instance")
