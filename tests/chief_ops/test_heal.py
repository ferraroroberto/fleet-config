"""chief_ops heal: the self-heal switch, the ledger, and the one guarded re-run against a fake
launcher, a fake `gh` and a real throwaway git checkout (fleet-config#1897).

No live launcher, no `gh`, no real Job: the launcher transport and the PR read are replaced,
the primary checkout is a temp repo named in a temp projects.toml, and every state file lives
in a throwaway directory.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_heal.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path

from _suite import _h, check, co  # noqa: E402
from chief import heal  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="chief_heal_"))
_REPO = init_repo()


def _git(*args):
    return subprocess.run(["git", "-C", str(_REPO), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


_LIVE_SHA = _git("rev-parse", "HEAD")
_git("checkout", "-q", "-b", "side")
_git("commit", "-q", "--allow-empty", "-m", "not yet in the primary checkout")
_UNLANDED_SHA = _git("rev-parse", "HEAD")
_git("checkout", "-q", "main")

_TOML = _TMP / "projects.toml"
_TOML.write_text(f'[app-a]\ncwd_prefix = "{_REPO.as_posix()}"\n', encoding="utf-8")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_TMP / "state")
os.environ["FLEET_PROJECTS_TOML"] = str(_TOML)
os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)
_LEDGER = _TMP / "state" / heal.LEDGER_FILENAME
_PRIVATE = "PRIVATE-OUTPUT-LINE"


# ---- the switch: only a literal true is on -----------------------------------------

check(heal.heal_switch({"settings": {"chief_job_self_heal": True}}) is None, "chief_job_self_heal true is on")
check(heal.heal_switch({"settings": {"chief_job_self_heal": False}}) == "HEAL=off reason=switch-off",
      "chief_job_self_heal false is off")
check(heal.heal_switch({"settings": {"auto_drain": True}}) == "HEAL=off reason=setting-missing",
      "a missing switch reads as off with its own reason")
check((heal.heal_switch({"settings": {"chief_job_self_heal": "true"}}) or "").startswith(
      "HEAL=off reason=setting-not-boolean"), "a non-boolean switch is never on")
check(heal.heal_switch(None) == "HEAL=off reason=settings-unreadable", "an unreadable reply is off")


# ---- rerun_safe: only a literal true on the launcher's job is safe ------------------------

def _jobs(**fields):
    return {"jobs": [{"id": "job-a", **fields}]}


check(heal.job_rerun_safe(_jobs(rerun_safe=True), "job-a") == (True, ""), "rerun_safe true is safe")
check(heal.job_rerun_safe(_jobs(rerun_safe=False), "job-a") == (False, "not-rerun-safe"), "false is not")
check(heal.job_rerun_safe(_jobs(), "job-a") == (False, "rerun-safe-absent"),
      "a launcher without the field (before app-launcher ships it) refuses every job")
check(heal.job_rerun_safe(_jobs(rerun_safe="true"), "job-a") == (False, "rerun-safe-not-boolean"),
      "a string 'true' is not safe")
check(heal.job_rerun_safe(_jobs(rerun_safe=True), "job-b") == (False, "job-not-found"), "an unknown job refuses")
check(heal.job_rerun_safe({"oops": 1}, "job-a") == (None, "jobs-unreadable"), "an unreadable jobs list is unknown")
check([heal.run_outcome({"status": s}) for s in ("success", "failed", "running", "weird")]
      == ["success", "failed", "pending", "unknown"], "run status maps to outcome, an odd one to unknown")


# ---- end to end against a fake launcher ---------------------------------------------------

class FakeLauncher:
    def __init__(self, switch=True, rerun_safe=True, run_exc=None, run_status="success"):
        self.switch = switch
        self.rerun_safe = rerun_safe
        self.run_exc = run_exc
        self.run_status = run_status
        self.runs = []

    def request(self, base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board/chief/settings":
            if isinstance(self.switch, Exception):
                raise self.switch
            settings = {} if self.switch is None else {"chief_job_self_heal": self.switch}
            return {"settings": {"worker_cap": 3, **settings}}
        if path == "/api/jobs":
            job = {"id": "job-a"}
            if self.rerun_safe is not None:
                job["rerun_safe"] = self.rerun_safe
            return {"jobs": [job]}
        if path == "/api/jobs/job-a/run" and method == "POST":
            self.runs.append({"path": path, "body": body})
            if self.run_exc is not None:
                raise self.run_exc
            return {"run_id": f"rerun{len(self.runs)}", "job_id": "job-a"}
        if path.startswith("/api/jobs/job-a/runs/"):
            return {"status": self.run_status, "output_tail": _PRIVATE}
        raise AssertionError(f"unexpected {method} {path}")


_PR = {"app-a#7": ("merged", _LIVE_SHA), "app-a#8": ("merged", _UNLANDED_SHA),
       "app-a#9": ("open", None), "app-a#10": ("gh-error", None), "app-a#11": ("merged", "f" * 40),
       "other#7": ("merged", _LIVE_SHA)}


def _fake_pr(repo, number):
    state, sha = _PR[f"{repo}#{number}"]
    if state == "merged":
        return sha, ""
    return (None, "not-merged") if state == "open" else (None, "unknown: gh exit 1")


def _run(fake, *argv):
    prior = (co.launcher._request, heal.fetch_pr_merge)
    co.launcher._request = fake.request
    heal.fetch_pr_merge = _fake_pr
    try:
        args = co.cli.build_parser("x").parse_args(["heal", *argv])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = args.func(args)
    finally:
        co.launcher._request, heal.fetch_pr_merge = prior
    return rc, out.getvalue()


def _reset():
    shutil.rmtree(_TMP / "state", ignore_errors=True)


def _ledger():
    return json.loads(_LEDGER.read_text(encoding="utf-8"))["entries"]


# Switch off, missing or unreadable: every verb writes nothing and fires nothing.
for switch, reason in ((False, "switch-off"), (None, "setting-missing"),
                       (urllib.error.URLError("down"), "settings-unreadable")):
    _reset()
    fake = FakeLauncher(switch=switch)
    outs = [_run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--pr", "app-a#7")[1],
            _run(fake, "rerun", "job-a", "--run", "r1")[1],
            _run(fake, "outcome", "job-a", "--run", "r1")[1]]
    check(all(f"HEAL=off reason={reason}" in o for o in outs) and not _LEDGER.exists() and not fake.runs,
          f"switch {reason}: no ledger, no re-run (got {outs!r})")

# Record, then a repeated cause class is flagged.
_reset()
fake = FakeLauncher()
rc, out = _run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--issue", "app-a#6")
check(rc == 0 and "HEAL_RECORDED=job-a run=r1 cause=defect issue=app-a#6" in out and "HEAL_REPEAT" not in out,
      f"a first failure is recorded, not flagged (got {out!r})")
rc, out = _run(fake, "record", "job-a", "--run", "r1", "--pr", "app-a#7")
check(len(_ledger()) == 1 and _ledger()[0]["pr"] == "app-a#7" and _ledger()[0]["cause"] == "defect",
      "a second record of the same run updates the entry in place")
rc, out = _run(fake, "record", "job-a", "--run", "r2", "--cause", "defect")
check("HEAL_REPEAT=job-a cause=defect count=2" in out, f"the same cause class twice is flagged (got {out!r})")
rc, out = _run(fake, "record", "job-a", "--run", "r3", "--cause", "config")
check("HEAL_REPEAT" not in out, "a different cause class is not a repeat")
check(heal.repeat_count([{"job_id": "j", "cause": "defect", "recorded_at": "2020-01-01T00:00:00Z"}],
                        "j", "defect", heal._now()) == 0, "a failure older than the window is not counted")
check(heal.repeat_count([{"job_id": "j", "cause": "defect",
                          "recorded_at": heal._iso_z(heal._now() - timedelta(days=29))}],
                        "j", "defect", heal._now()) == 1, "a failure inside the window counts")
rc, out = _run(fake, "record", "job-a", "--run", "r4", "--pr", "nonsense")
check(rc == 2 and "reason=bad-ref" in out, "a malformed ref is refused before anything is written")

# A live fix on a rerun_safe job re-runs exactly once, with no confirm flag.
rc, out = _run(fake, "rerun", "job-a", "--run", "r1")
check(rc == 0 and f"HEAL_RERUN=job-a failed_run=r1 rerun=rerun1 sha={_LIVE_SHA[:12]}" in out,
      f"a live fix on a rerun_safe job re-runs (got {out!r})")
check(len(fake.runs) == 1 and fake.runs[0]["body"] == {} and "confirmed" not in fake.runs[0]["path"],
      "the re-run POSTs an empty body and never confirmed=1")
rc, out = _run(fake, "rerun", "job-a", "--run", "r1")
check(rc == 2 and "reason=already-rerun" in out and len(fake.runs) == 1, "a second re-run of one failure is refused")

# Outcome: success heals; a failed re-run needs the owner.
rc, out = _run(fake, "outcome", "job-a", "--run", "r1")
check("HEAL_OUTCOME=job-a rerun=rerun1 outcome=success" in out and _ledger()[0]["outcome"] == "success",
      f"a green re-run is recorded (got {out!r})")
fake.run_status = "running"
rc, out = _run(fake, "outcome", "job-a", "--run", "r1")
check("outcome=pending" in out and _ledger()[0]["outcome"] == "success", "a run in flight writes no outcome")

_reset()
fake = FakeLauncher(run_status="failed")
_run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--pr", "app-a#7")
_run(fake, "rerun", "job-a", "--run", "r1")
rc, out = _run(fake, "outcome", "job-a", "--run", "r1")
check("outcome=failed needs_owner=yes" in out and _ledger()[0]["needs_owner"] is True,
      f"a red re-run marks the failure needs-owner (got {out!r})")

# The re-run's own job-failed event: needs-owner, no new entry, no new re-run.
_reset()
fake = FakeLauncher()
_run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--pr", "app-a#7")
_run(fake, "rerun", "job-a", "--run", "r1")
rc, out = _run(fake, "record", "job-a", "--run", "rerun1", "--cause", "defect")
check("HEAL_NEEDS_OWNER=job-a failed_run=r1 rerun=rerun1" in out and len(_ledger()) == 1
      and _ledger()[0]["needs_owner"] is True, f"the re-run failing again goes to the owner (got {out!r})")
rc, out = _run(fake, "rerun", "job-a", "--run", "rerun1")
check(rc == 2 and "reason=not-in-ledger" in out and len(fake.runs) == 1, "the re-run is never itself re-run")


def _refused(fake_kwargs, pr, expect, label):
    _reset()
    fake = FakeLauncher(**fake_kwargs)
    record = ["record", "job-a", "--run", "r1", "--cause", "defect"] + (["--pr", pr] if pr else [])
    _run(fake, *record)
    rc, out = _run(fake, "rerun", "job-a", "--run", "r1")
    check(expect in out and rc != 0 and not fake.runs and not _ledger()[0].get("rerun_run_id"),
          f"{label}: no re-run, the one re-run is not used up (got {out!r})")


_refused({"rerun_safe": False}, "app-a#7", "HEAL_REFUSED=job-a reason=not-rerun-safe", "a job marked unsafe")
_refused({"rerun_safe": None}, "app-a#7", "HEAL_REFUSED=job-a reason=rerun-safe-absent", "a job with no flag")
_refused({}, None, "HEAL_REFUSED=job-a reason=no-fix-pr", "no fix PR recorded")
_refused({}, "app-a#9", "HEAL_REFUSED=job-a reason=not-merged", "an unmerged fix PR")
_refused({}, "app-a#8", "HEAL_REFUSED=job-a reason=fix-not-in-primary-head", "a merge not in the primary HEAD")
_refused({}, "app-a#10", "HEAL=unknown job=job-a reason=pr app-a#10 unknown", "an unreadable PR is unknown")
_refused({}, "app-a#11", "HEAL=unknown job=job-a reason=liveness", "a merge sha git cannot resolve is unknown")
_refused({}, "other#7", "HEAL=unknown job=job-a reason=no-primary-checkout repo=other",
         "a PR in a repo with no primary checkout")
_reset()
fake = FakeLauncher()
rc, out = _run(fake, "rerun", "job-a", "--run", "r9")
check(rc == 2 and "reason=not-in-ledger" in out and not fake.runs, "a failure never recorded is never re-run")

# An unanswered POST uses up the one re-run: it may have started.
_reset()
fake = FakeLauncher(run_exc=TimeoutError("slow"))
_run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--pr", "app-a#7")
rc, out = _run(fake, "rerun", "job-a", "--run", "r1")
check(rc == 1 and "HEAL=unconfirmed" in out and _ledger()[0]["rerun_run_id"] == heal.RERUN_IN_FLIGHT,
      f"an unconfirmed POST is its own state (got {out!r})")
fake.run_exc = None
rc, out = _run(fake, "rerun", "job-a", "--run", "r1")
check(rc == 2 and "reason=already-rerun" in out and len(fake.runs) == 1, "an unconfirmed re-run is never retried")

# No job output ever reaches the ledger, and nothing here writes a job definition.
_reset()
fake = FakeLauncher()
_run(fake, "record", "job-a", "--run", "r1", "--cause", "defect", "--pr", "app-a#7")
_run(fake, "rerun", "job-a", "--run", "r1")
_run(fake, "outcome", "job-a", "--run", "r1")
rc, out = _run(fake, "show")
check(_PRIVATE not in _LEDGER.read_text(encoding="utf-8") and _PRIVATE not in out and "HEAL_ENTRIES=1" in out,
      "the run's output tail is never stored or printed")
check("jobs.json" not in Path(heal.__file__).read_text(encoding="utf-8"),
      "heal never names the owner's jobs.json, so it cannot write it")
check(fake.runs == [{"path": "/api/jobs/job-a/run", "body": {}}], "the only POST heal makes is the bare run")

check(co.cli.build_parser("x").parse_args(["heal", "show"]).func is heal.cmd_show,
      "`chief_ops.py heal` is wired into chief_ops")

shutil.rmtree(_TMP, ignore_errors=True)
shutil.rmtree(_REPO, ignore_errors=True)
_h.report_and_exit("test_heal")
