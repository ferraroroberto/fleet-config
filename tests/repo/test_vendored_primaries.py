"""Landing and restarting fleet primaries after a wave (skills/_lib/vendored_wave/primaries.py).

Synthetic throughout: probes, restarts and counts are fakes, and the landing cases run against
throwaway git clones, so the gate never restarts anything or touches a real checkout. Covers a
repo with a recipe (landed, then restarted), one without (stale, not restarted), the hub and
session-host ports (owner-restart), a down or unknown service, a failed or unconfirmed restart,
a level tree, `--dry-run`, and the single `STALE_PRIMARIES=` line.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_vendored_primaries.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from vendored_wave import primaries as pr  # noqa: E402
from worktree import service_probe as sp  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_h = CheckHarness()
check = _h.check

NEVER = pr.never_kill_ports({"global": {"never_kill_ports": [8000, 8090, 8446]}})
RECIPE = '& .\\.venv\\Scripts\\python.exe -c "start"'


def tbl(path: Path, **extra) -> dict:
    return {"cwd_prefix": str(path), "webapp_port": 8447, "tray_cmd": "tray.bat", "restart_cmd": RECIPE, **extra}


def probe_as(state: str):
    return lambda _port, _api: (state, None, "")


class Recorder:
    """Stands in for the restart hook and records whether it ran."""

    def __init__(self, code: int = 0, last: str = "ok") -> None:
        self.calls, self.code, self.last = [], code, last

    def __call__(self, repo: Path):
        self.calls.append(repo)
        return self.code, self.last


def behind_clone(root_name: str):
    """A clone one commit behind its upstream: the state a merged wave leaves a primary in."""
    root = throwaway_dir(root_name)
    upstream, work = make_upstream_and_clone(root, check)
    (upstream / "new.txt").write_text("x\n", encoding="utf-8")
    run_git(upstream, "add", "new.txt", check=check)
    run_git(upstream, "commit", "-q", "-m", "wave", check=check)
    return work


def land(name: str, table: dict, state: str, restart, **kw) -> pr.Outcome:
    return pr.land_one(name, table, NEVER, "wave-1", probe=probe_as(state), restart=restart, **kw)


# ---- recipe_kind / never_kill_ports ----
check(pr.never_kill_ports({}) == frozenset({8000, 8090, 8446}), "never_kill_ports: a registry without the key still protects the hub, whisper and the session host")
check(pr.recipe_kind({"webapp_port": 8000, "tray_cmd": "t", "restart_cmd": "r"}, NEVER) == pr.OWNER, "recipe_kind: the hub port is owner-restart even with a recipe declared")
check(pr.recipe_kind({"webapp_port": 8445, "session_host_port": 8446, "tray_cmd": "t", "restart_cmd": "r"}, NEVER) == pr.OWNER, "recipe_kind: a repo hosting the session host is owner-restart")
check(pr.recipe_kind({"webapp_port": 8447, "tray_cmd": "t"}, NEVER) == pr.NO_RECIPE, "recipe_kind: a tray with no restart_cmd is no-restart-recipe")
check(pr.recipe_kind({"webapp_port": 8447, "tray_cmd": "t", "restart_cmd": "r"}, NEVER) == pr.RESTART, "recipe_kind: a tray with a restart_cmd is restartable")
check(pr.recipe_kind({}, NEVER) == pr.NO_SERVICE, "recipe_kind: no tray declared means no service")

# ---- a repo with a recipe: landed, then restarted ----
work = behind_clone("primaries-restart-")
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_LIVE, rec)
check(out.live and out.line.startswith("PRIMARY=live behind=0 restarted=yes build="), f"recipe + live service: landed and restarted ({out.line})")
check(len(rec.calls) == 1 and rec.calls[0] == work, "recipe + live service: the restart ran once, in that repo")
check((work / "new.txt").exists(), "recipe + live service: the primary was fast-forwarded")
build = out.line.split("build=")[1].split()[0]
check(run_git(work, "rev-parse", "HEAD", check=check).startswith(build), "recipe + live service: the reported build is the new HEAD")

# ---- level tree: nothing to land, nothing to restart ----
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_LIVE, rec)
check(out.live and out.line == "PRIMARY=live behind=0" and not rec.calls, f"level tree: live, and the service is not restarted ({out.line})")

# ---- service down: landed, never started ----
work = behind_clone("primaries-down-")
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_ABSENT, rec)
check(out.live and out.line == "PRIMARY=live behind=0" and not rec.calls and (work / "new.txt").exists(),
      f"service down: landed without starting anything ({out.line})")

# ---- liveness unknown: refused, never assumed idle ----
work = behind_clone("primaries-unknown-")
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_UNKNOWN, rec)
check(not out.live and out.line.startswith("PRIMARY=stale reason=") and not rec.calls and not (work / "new.txt").exists(),
      f"unknown liveness: stale, not landed, not restarted ({out.line})")

# ---- no recipe: listed stale, not landed, not restarted ----
work = behind_clone("primaries-norecipe-")
rec = Recorder()
table = tbl(work)
del table["restart_cmd"]
out = land("grocery", table, sp.SERVICE_LIVE, rec)
check(not out.live and out.line == "PRIMARY=stale reason=no-restart-recipe" and not rec.calls and not (work / "new.txt").exists(),
      f"no restart_cmd: reported stale, tree untouched ({out.line})")

# ---- the hub and the session host are the owner's ----
for label, extra in (("hub :8000", {"webapp_port": 8000}), ("session host :8446", {"webapp_port": 8445, "session_host_port": 8446})):
    work = behind_clone("primaries-owner-")
    rec = Recorder()
    out = land("owner", tbl(work, **extra), sp.SERVICE_LIVE, rec)
    check(not out.live and out.line == "PRIMARY=stale reason=owner-restart" and not rec.calls and not (work / "new.txt").exists(),
          f"{label}: stale reason=owner-restart, never restarted ({out.line})")
work = behind_clone("primaries-owner-down-")
out = land("hub", tbl(work, webapp_port=8000), sp.SERVICE_ABSENT, Recorder())
check(out.live and (work / "new.txt").exists(), "hub with nothing listening: landed (there is no process to skew)")

# ---- restart outcomes beyond success ----
for code, want in ((2, "restart failed (exit 2)"), (3, "restart not confirmed")):
    work = behind_clone("primaries-restartfail-")
    rec = Recorder(code, "boom")
    out = land("app", tbl(work), sp.SERVICE_LIVE, rec)
    check(not out.live and want in out.line and "landed" in out.line and out.line.startswith("PRIMARY=stale reason="),
          f"restart exit {code}: stale, says the tree landed, never counted live ({out.line})")

# ---- a dirty primary is the landing guard's call, and nothing restarts ----
work = behind_clone("primaries-dirty-")
(work / "README.md").write_text("edited\n", encoding="utf-8")
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_LIVE, rec)
check(not out.live and out.line.startswith("PRIMARY=stale reason=") and not rec.calls, f"dirty primary: stale, not restarted ({out.line})")

# ---- dry run changes nothing ----
work = behind_clone("primaries-dry-")
rec = Recorder()
out = land("app", tbl(work), sp.SERVICE_LIVE, rec, dry_run=True, behind=lambda _r: 1)
check(out.live and out.line == "WOULD=land 1 commit(s) then restart" and not rec.calls and not (work / "new.txt").exists(),
      f"dry run: reports the plan and changes nothing ({out.line})")
out = land("hub", tbl(work, webapp_port=8000), sp.SERVICE_LIVE, rec, dry_run=True, behind=lambda _r: 1)
check(not out.live and out.line == "WOULD=stale reason=owner-restart", f"dry run: the hub would be stale ({out.line})")
out = land("app", tbl(work), sp.SERVICE_LIVE, rec, dry_run=True, behind=lambda _r: None)
check(not out.live and out.line.startswith("WOULD=stale"), "dry run: an uncountable distance is stale, not level")
out = land("app", tbl(work), sp.SERVICE_LIVE, rec, dry_run=True, behind=lambda _r: 0)
check(out.live and out.line.startswith("WOULD=nothing"), "dry run: a level tree needs nothing")

# ---- the single stale line ----
fine = pr.Outcome("a", "PRIMARY=live behind=0", True)
bad1 = pr.Outcome("b", "PRIMARY=stale reason=owner-restart", False)
bad2 = pr.Outcome("c", "PRIMARY=stale reason=no-restart-recipe", False)
check(pr.stale_line([fine]) == "STALE_PRIMARIES=none (checked 1)", "stale_line: all live reads as an explicit none")
check(pr.stale_line([fine, bad1, bad2]) == "STALE_PRIMARIES=2: b (owner-restart); c (no-restart-recipe)", f"stale_line: every stale primary on one line ({pr.stale_line([fine, bad1, bad2])})")

# ---- the read-only listing sweep prints ----
report = pr.stale_report({"x": 0, "y": 3, "z": None})
check(report == ["BEHIND y  3 commit(s) behind origin", "UNKNOWN z  could not count commits behind origin", "STALE_PRIMARIES=2: y, z"],
      f"stale_report: behind and uncountable repos listed, level ones not ({report})")
check(pr.stale_report({"x": 0}) == ["STALE_PRIMARIES=none (checked 1)"], "stale_report: nothing behind says none with the count checked")

root = throwaway_dir("primaries-counts-")
(root / "has" / ".git").mkdir(parents=True)
counts = pr.behind_counts({"has": {"cwd_prefix": str(root / "has")}, "gone": {"cwd_prefix": str(root / "gone")}},
                          ["has", "gone"], lambda _r: 2)
check(counts == {"has": 2}, f"behind_counts: a repo with no checkout is skipped, not reported level ({counts})")

_h.report_and_exit("test_vendored_primaries")
