"""chief_ops drain: the switch, every intake exclusion, dry-run writing nothing, and dispatch
filling free slots from the top of drain's own rows against a fake launcher (fleet-config#1881).

No live launcher, no `gh`, no real lane: the launcher transport, the issue fetch and the
owner-label read are replaced, and every state file lives in a throwaway directory.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_drain.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import urllib.error
from pathlib import Path

from _suite import _h, _session_card, check, co  # noqa: E402

import chief_plan  # noqa: E402
from chief import drain  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="chief_drain_"))
_TOML = _TMP / "projects.toml"
_TOML.write_text(
    '[site]\ncwd_prefix = "E:/automation/site"\nauto_ship = false\n\n'
    '[app-a]\ncwd_prefix = "E:/automation/app-a"\n',
    encoding="utf-8",
)
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_TMP / "state")
os.environ["FLEET_PROJECTS_TOML"] = str(_TOML)
os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)


_OPEN = "## Findings\n\n- [ ] an open finding\n- [x] a fixed one"
# The standing perf-review ledger with no work: tables, no checkbox (parking-manager's shape).
_PERF_CLEAN = ("## Phone-load budgets\n\n| Check | Budget | Measured | |\n|---|---|---|---|\n"
               "| Cold launch: ready (ms) | 3000 | 1212 | ✅ |\n\n## Fixes\n\n_Nothing over budget._\n\n"
               "## Run log\n- 2026-10-31 build `3cbaa9c`: 11 pass, 0 over budget")


def _issue(repo, number, kind="design-drift", labels=(), title="findings", findings=_OPEN):
    body = f"<!-- audit-managed: kind={kind} -->\n\n{findings}" if kind else "plain issue"
    return {"number": number, "title": title, "body": body,
            "labels": [{"name": name} for name in labels], "repository": {"name": repo}}


# ---- the switch ----------------------------------------------------------------

check(drain.drain_switch({"settings": {"auto_drain": True}}) is None, "auto_drain true is on")
check(drain.drain_switch({"settings": {"auto_drain": False}}) == "DRAIN=off reason=switch-off",
      "auto_drain false is off")
check(drain.drain_switch({"settings": {"worker_cap": 3}}) == "DRAIN=off reason=setting-missing",
      "a missing auto_drain key reads as off with its own reason")
check((drain.drain_switch({"settings": {"auto_drain": "yes"}}) or "").startswith(
      "DRAIN=off reason=setting-not-boolean"), "a non-boolean auto_drain is never on")
check(drain.drain_switch(["not", "a", "dict"]) == "DRAIN=off reason=settings-unreadable",
      "an unreadable settings reply is off")


# ---- intake: each exclusion ------------------------------------------------------

_plan = chief_plan.empty_plan()
chief_plan.add_item(_plan, "app-a#5", "hand-queued", status="merged")
chief_plan.set_lane(_plan, "app-a", "building", item="#6")
_issues = [
    _issue("app-a", 1, title="eligible\nsecond line"),
    _issue("app-a", 2, kind=None),
    _issue("app-a", 3, kind="cleanup-deferred"),
    _issue("app-a", 4, labels=("needs-owner",)),
    _issue("app-a", 5),
    _issue("app-a", 6),
    _issue("app-a", 7),
    _issue("site", 8),
    _issue("app-a", 9, labels=("needs-validation",)),
    _issue("app-a", 10, labels=("audit-meta",)),
    _issue("app-a", 11, kind="perf-review"),
    _issue("app-a", 12, kind="some-new-kind"),
    _issue("app-a", 13, kind="perf-review", findings=_PERF_CLEAN),
    _issue("app-a", 14, findings="## Findings\n\n- [x] fixed\n- [X] also fixed"),
    _issue("app-a", 15, kind="perf-review",
           findings=_PERF_CLEAN.replace("_Nothing over budget._", "- [ ] cold launch over budget")),
]
adds, skips = drain.select_intake(_issues, _plan, {"app-a#7"}, {"site"})
skip_reasons = dict(skips)
check(skip_reasons.get("app-a#13") == "no-open-findings",
      "a perf-review ledger whose Fixes reads 'Nothing over budget' is excluded")
check(skip_reasons.get("app-a#14") == "no-open-findings", "an issue whose findings are all checked is excluded")
check([a["ref"] for a in adds] == ["app-a#1", "app-a#11", "app-a#12", "app-a#15"],
      f"intake adds only the eligible audit issues, in order (got {[a['ref'] for a in adds]})")
check("app-a#2" not in skip_reasons, "an issue with no audit marker is ignored, not reported")
check(skip_reasons.get("app-a#3") == "kind-cleanup-deferred", "cleanup-deferred is excluded")
check(skip_reasons.get("app-a#4") == "label-needs-owner", "needs-owner is excluded")
check(skip_reasons.get("app-a#9") == "label-needs-validation", "needs-validation is excluded")
check(skip_reasons.get("app-a#10") == "label-audit-meta", "the audit-meta ledger is excluded")
check(skip_reasons.get("app-a#5") == "in-plan", "a row already in the plan (any status) is excluded")
check(skip_reasons.get("app-a#6") == "in-plan", "a lane's current item is excluded")
check(skip_reasons.get("app-a#7") == "removed-from-plan", "a ref drain queued before and the chief removed is excluded")
check(skip_reasons.get("site#8") == "auto-ship-off", "an auto_ship = false repo is excluded")
models = {a["ref"]: a["model"] for a in adds}
check(models == {"app-a#1": "sonnet", "app-a#11": "opus", "app-a#12": "sonnet", "app-a#15": "opus"},
      f"model comes from the kind table, defaulting to sonnet (got {models})")
check(adds[0]["title"] == "eligible second line", "a multi-line title is flattened for the plan")
check(drain.no_ship_repos({"site": {"auto_ship": False}, "x": {"auto_ship": True}, "y": {}}) == {"site"},
      "only an explicit auto_ship = false opts a repo out")
check(drain.marker_kind("  \n<!-- audit-managed: kind=stale -->\nbody") == "stale"
      and drain.marker_kind("see <!-- audit-managed: kind=stale -->") is None,
      "the marker counts only on the body's first line")


# ---- cmd_drain end to end against a fake launcher ------------------------------------

class FakeLauncher:
    def __init__(self, settings, cards=(), start_exc=None, cap=3):
        self.settings = settings
        self.cards = list(cards)
        self.start_exc = start_exc
        self.cap = cap
        self.posted = []

    def request(self, base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board/chief/settings":
            if isinstance(self.settings, Exception):
                raise self.settings
            # Scope `all`: every live card holds a slot, so the cap is decided by the fake board alone.
            return {"settings": {"worker_cap": self.cap, "worker_cap_scope": "all", **self.settings}}
        if path == "/api/board":
            return {"columns": {"claude_turn": list(self.cards), "your_turn": []}}
        if path == "/api/jobs":
            return {"jobs": []}
        if path == "/api/board/issues/start":
            self.posted.append(body)
            if self.start_exc is not None:
                raise self.start_exc
            sid = f"lane{len(self.posted)}"
            self.cards.append(_session_card(project=body["repo"], session_id=sid))
            return {"session": {"session_id": sid}, "launched": f"/issue-start {body['number']}"}
        raise AssertionError(f"unexpected path: {path}")


def _run(fake, issues, dry_run=False, plan=None):
    plan_path = _TMP / "plan.json"
    if plan is not None:
        plan_path.write_text(json.dumps({**plan, "updated_at": "2026-11-06T00:00:00Z"}), encoding="utf-8")
    fetched = []

    def _fetch():
        fetched.append(True)
        return list(issues), []

    prior = (co.launcher._request, co.launcher.fetch_issue_state, co.chief_managed.mark)
    co.launcher._request = fake.request
    co.launcher.fetch_issue_state = lambda repo, number, owner=None: {"labels": [{"name": "enhancement"}]}
    co.chief_managed.mark = lambda *a, **k: {}
    try:
        args = argparse.Namespace(dry_run=dry_run, plan_file=str(plan_path), base_url=co.DEFAULT_BASE_URL)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = drain.cmd_drain(args, fetch=_fetch)
    finally:
        co.launcher._request, co.launcher.fetch_issue_state, co.chief_managed.mark = prior
    return rc, out.getvalue(), plan_path, bool(fetched)


def _reset():
    shutil.rmtree(_TMP / "state", ignore_errors=True)
    (_TMP / "plan.json").unlink(missing_ok=True)


_ledger = _TMP / "state" / drain.LEDGER_FILENAME
_briefs = _TMP / "state" / drain.BRIEF_DIRNAME
_four = [_issue("app-a", 1), _issue("busy", 2), _issue("app-b", 3, kind="perf-review"), _issue("app-c", 4)]
_busy_card = _session_card(project="busy", session_id="busy0000")

# Off and unreadable: nothing is fetched, written or dispatched.
for label, settings in (("missing", {}), ("off", {"auto_drain": False}),
                        ("unreadable", urllib.error.URLError("refused"))):
    _reset()
    fake = FakeLauncher(settings)
    rc, out, plan_path, fetched = _run(fake, _four)
    check(rc == 0 and out.startswith("DRAIN=off"), f"switch {label}: prints DRAIN=off (got {out!r})")
    check(not fetched and not plan_path.exists() and not _ledger.exists() and not fake.posted,
          f"switch {label}: fetches, writes and dispatches nothing")
check("reason=setting-missing" in _run(FakeLauncher({}), _four)[1],
      "a launcher without auto_drain yet prints reason=setting-missing")

# Dry run: lists the adds and the dispatches it would make, writes nothing.
_reset()
fake = FakeLauncher({"auto_drain": True}, cards=[_busy_card])
rc, out, plan_path, _ = _run(fake, _four, dry_run=True)
check(rc == 0 and "DRAIN_WOULD_ADD=app-a#1" in out and "DRAIN_WOULD_ADD=app-c#4" in out,
      f"dry-run lists what it would add (got {out!r})")
check("DRAIN_WOULD_DISPATCH=app-a#1" in out and "DRAIN_WOULD_DISPATCH=app-b#3" in out
      and "DRAIN_WOULD_DISPATCH=app-c#4" not in out and "DRAIN_SKIPPED=busy#2 reason=repo-busy" in out,
      "dry-run previews dispatch into the free slots only, skipping the busy repo")
check("free_slots=2" in out, "dry-run reports the free slots (cap 3, one live session)")
check(not plan_path.exists() and not _ledger.exists() and not _briefs.exists() and not fake.posted,
      "dry-run writes no plan, ledger or brief and dispatches nothing")

# Live run: fills the two free slots top-down, skips the busy repo, stops on the cap refusal.
_reset()
_hand = chief_plan.empty_plan()
chief_plan.add_item(_hand, "app-z#9", "hand-queued row")
fake = FakeLauncher({"auto_drain": True}, cards=[_busy_card])
rc, out, plan_path, _ = _run(fake, _four, plan=_hand)
doc = chief_plan.load(plan_path)
posted = [(b["repo"], b["number"], b.get("model")) for b in fake.posted]
check(posted == [("app-a", 1, "sonnet"), ("app-b", 3, "opus")],
      f"dispatch fills free slots from the top of drain's rows with the kind's model (got {posted})")
check("DRAIN_SKIPPED=busy#2 reason=repo-busy" in out, "an occupied repo is skipped")
check("DRAIN_STOPPED=app-c#4 reason=refused" in out and "DRAIN=done added=4 dispatched=2" in out,
      f"the cap refusal stops the drain, then the summary line (got {out!r})")
check(all(b["repo"] != "app-z" for b in fake.posted), "a hand-queued row is never auto-dispatched")
lanes = {lane["repo"]: lane for lane in doc["lanes"]}
check(lanes.get("app-a", {}).get("session") == "lane1" and lanes["app-a"].get("item") == "#1"
      and lanes.get("app-b", {}).get("model") == "opus",
      f"each dispatch records its lane as `plan lane` does (got {doc['lanes']})")
status = {f"{r['repo']}{r['ref']}": r["status"] for r in doc["queue"]}
check(status == {"app-z#9": "queued", "app-a#1": "building", "busy#2": "queued",
                 "app-b#3": "building", "app-c#4": "queued"}, f"queue statuses after the drain (got {status})")
check("app-a#1" in fake.posted[0]["brief"] and "design-drift" in fake.posted[0]["brief"]
      and "no plan gate" in fake.posted[0]["brief"], "the per-kind brief names the issue and kind")
check(set(json.loads(_ledger.read_text(encoding="utf-8"))["refs"]) == {"app-a#1", "busy#2", "app-b#3", "app-c#4"},
      "the ledger records every ref drain queued")

# A row the chief removed is never re-added on a later turn.
chief_plan.update(plan_path, lambda d: chief_plan.remove_item(d, "app-c#4"))
fake = FakeLauncher({"auto_drain": True}, cards=list(fake.cards))
rc, out, plan_path, _ = _run(fake, _four)
check("DRAIN_SKIPPED=app-c#4 reason=removed-from-plan" in out and "added=0" in out,
      f"a removed row stays removed (got {out!r})")

# An unconfirmed dispatch stops the drain with no retry.
_reset()
fake = FakeLauncher({"auto_drain": True},
                    start_exc=urllib.error.HTTPError("u", 503, "unavailable", {}, None))
rc, out, plan_path, _ = _run(fake, _four)
check(len(fake.posted) == 1 and "DRAIN_STOPPED=app-a#1 reason=unconfirmed" in out
      and "dispatched=0" in out, f"an unconfirmed dispatch stops the drain, no retry (got {out!r})")
check(all(r["status"] == "queued" for r in chief_plan.load(plan_path)["queue"]),
      "an unconfirmed dispatch records no lane")

# The subcommand is wired into chief_ops.
check(co.cli.build_parser("x").parse_args(["drain", "--dry-run"]).func is drain.cmd_drain,
      "`chief_ops.py drain` reaches cmd_drain")

shutil.rmtree(_TMP, ignore_errors=True)
_h.report_and_exit("test_drain")
