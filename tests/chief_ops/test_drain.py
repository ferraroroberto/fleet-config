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
import time
import urllib.error
from pathlib import Path

import chief_plan  # noqa: E402
from _suite import _h, _session_card, check, co  # noqa: E402
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


def _run(fake, issues, dry_run=False, plan=None, states=None):
    """`states` maps `(repo, number)` to the issue state `gh` would report; unlisted refs are open."""
    plan_path = _TMP / "plan.json"
    if plan is not None:
        plan_path.write_text(json.dumps({**plan, "updated_at": "2026-11-06T00:00:00Z"}), encoding="utf-8")
    fetched = []

    def _fetch():
        fetched.append(True)
        return list(issues), []

    prior = (co.launcher._request, co.launcher.fetch_issue_state, co.chief_managed.mark)
    co.launcher._request = fake.request
    co.launcher.fetch_issue_state = lambda repo, number, owner=None: (
        (states or {}).get((repo, number)) or {"state": "OPEN", "labels": [{"name": "enhancement"}]})
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
check(all(b["repo"] != "app-z" for b in fake.posted), "a hand-queued row with no brief is not dispatched")
check("DRAIN_SKIPPED=app-z#9 reason=no-brief" in out, f"a hand-queued row with no brief says why (got {out!r})")
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

# ---- hand-queued rows (fleet-config#1925) ----------------------------------------------

_BRIEF = _TMP / "hand-brief.md"
_BRIEF.write_text("Issue: the hand-written scope for this lane.\n", encoding="utf-8")
_EMPTY_BRIEF = _TMP / "empty-brief.md"
_EMPTY_BRIEF.write_text("  \n", encoding="utf-8")


def _hand_plan(*rows, extra=None):
    """A plan of hand-queued rows: `(ref, fields)` pairs in order."""
    plan = chief_plan.empty_plan()
    for ref, fields in rows:
        chief_plan.add_item(plan, ref, f"title {ref}", **{k: v for k, v in fields.items() if k != "status"})
        if "status" in fields:
            plan["queue"][-1]["status"] = fields["status"]
    for fn in extra or []:
        fn(plan)
    return plan


def _hand(plan, states=None, cards=(), dry_run=False, settings=None):
    _reset()
    fake = FakeLauncher({"auto_drain": True, **(settings or {})}, cards=list(cards))
    rc, out, plan_path, _ = _run(fake, [], dry_run=dry_run, plan=plan, states=states)
    return fake, out, chief_plan.load(plan_path)


_brief = str(_BRIEF)

# Each skip reason, in one plan: every row is hand-queued and none is dispatched.
plan = _hand_plan(
    ("app-a#1", {}),                                                    # no brief
    ("app-a#2", {"brief": str(_TMP / "gone.md")}),                      # brief file missing
    ("app-a#3", {"brief": str(_EMPTY_BRIEF)}),                          # brief file empty
    ("app-a#4", {"brief": _brief, "after": "app-a#40"}),                # blocker still open
    ("app-a#5", {"brief": _brief, "after": "no-lane:busy"}),            # lane live in the named repo
    ("busy#6", {"brief": _brief}),                                      # its own repo is busy
    ("app-a#7", {"brief": _brief}),                                     # needs-owner
    ("app-a#8", {"brief": _brief}),                                     # needs-validation
    ("app-a#9", {"brief": _brief}),                                     # audit-meta
    ("app-a#10", {"brief": _brief}),                                    # closed
    ("app-a#11", {"brief": _brief}),                                    # gh could not say
    ("app-a#12", {"brief": _brief, "status": "parked"}),                # not queued: never considered
    ("app-a#13", {"brief": _brief, "status": "merged"}),
    ("app-a#14", {"brief": _brief, "status": "waiting-roberto"}),
    ("app-a#15", {"brief": _brief, "status": "building"}),
)
states = {
    ("app-a", 40): {"state": "OPEN", "labels": []},
    ("app-a", 7): {"state": "OPEN", "labels": [{"name": "needs-owner"}]},
    ("app-a", 8): {"state": "OPEN", "labels": [{"name": "needs-validation"}]},
    ("app-a", 9): {"state": "OPEN", "labels": [{"name": "audit-meta"}]},
    ("app-a", 10): {"state": "CLOSED", "labels": []},
    ("app-a", 11): {"error": "gh error"},
}
fake, out, doc = _hand(plan, states, cards=[_busy_card])
reasons = dict(line.split(" reason=", 1) for line in out.splitlines() if line.startswith("DRAIN_SKIPPED="))
reasons = {key.split("=", 1)[1]: value for key, value in reasons.items()}
check(not fake.posted and "dispatched=0" in out, f"no ineligible hand row is dispatched (got {out!r})")
check(reasons.get("app-a#1") == "no-brief", f"no brief is skipped (got {reasons.get('app-a#1')!r})")
check(reasons.get("app-a#2", "").startswith("brief-unreadable"), f"a missing brief file is skipped (got {reasons.get('app-a#2')!r})")
check(reasons.get("app-a#3", "").startswith("brief-unreadable"), f"an empty brief file is skipped (got {reasons.get('app-a#3')!r})")
check(reasons.get("app-a#4") == "after-unmet (app-a#40 (not merged))", f"an open blocker is after-unmet (got {reasons.get('app-a#4')!r})")
check(reasons.get("app-a#5") == "after-unmet (no-lane:busy (lane live))", f"no-lane with a live lane is after-unmet (got {reasons.get('app-a#5')!r})")
check(reasons.get("busy#6") == "repo-busy", f"a busy repo is skipped (got {reasons.get('busy#6')!r})")
check(reasons.get("app-a#7") == "label-needs-owner", f"needs-owner is skipped (got {reasons.get('app-a#7')!r})")
check(reasons.get("app-a#8") == "label-needs-validation", f"needs-validation is skipped (got {reasons.get('app-a#8')!r})")
check(reasons.get("app-a#9") == "label-audit-meta", f"audit-meta is skipped (got {reasons.get('app-a#9')!r})")
check(reasons.get("app-a#10") == "issue-closed", f"a closed issue is skipped (got {reasons.get('app-a#10')!r})")
check(reasons.get("app-a#11") == "state-unknown", f"an unreadable issue is its own state, never a pass (got {reasons.get('app-a#11')!r})")
check(not any(f"app-a#{n}" in reasons for n in (12, 13, 14, 15)),
      "parked, merged, waiting and in-flight rows are never considered or reported")
check(doc["lanes"] == [], "a run that dispatches nothing records no lane")

# The top eligible hand row goes out with its own brief and model, and its lane is recorded.
plan = _hand_plan(
    ("app-a#1", {}),
    ("app-a#2", {"brief": _brief, "model": "opus"}),
    ("app-a#3", {"brief": _brief}),
    ("app-b#4", {"brief": _brief, "after": "app-a#2"}),
    ("app-c#5", {"brief": _brief, "after": "no-lane:app-a no-lane:app-d"}),
)
fake, out, doc = _hand(plan, cards=[_busy_card])
posted = [(b["repo"], b["number"], b.get("model")) for b in fake.posted]
check(posted[:1] == [("app-a", 2, "opus")],
      f"plan order decides: the top eligible hand row goes first, with its model (got {posted})")
check(fake.posted[0]["brief"] == _BRIEF.read_text(encoding="utf-8"),
      "the lane is briefed with the row's own brief file, not the generic audit brief")
check("DRAIN_SKIPPED=app-a#1 reason=no-brief" in out and "DRAIN_SKIPPED=app-a#3 reason=repo-busy" in out,
      f"a row behind the dispatched one in the same repo is repo-busy this turn (got {out!r})")
check("DRAIN_SKIPPED=app-b#4 reason=after-unmet (app-a#2 (not merged))" in out,
      f"an after ref that was only just dispatched is not merged (got {out!r})")
check("DRAIN_SKIPPED=app-c#5 reason=after-unmet (no-lane:app-a (lane live))" in out,
      f"no-lane sees the lane dispatched earlier in the same run (got {out!r})")
check(len(posted) == 1, f"nothing else dispatched (got {posted})")
lanes = {lane["repo"]: lane for lane in doc["lanes"]}
status = {f"{r['repo']}{r['ref']}": r["status"] for r in doc["queue"]}
check(lanes.get("app-a", {}).get("session") == "lane1" and lanes["app-a"].get("item") == "#2"
      and lanes["app-a"].get("model") == "opus" and status["app-a#2"] == "building",
      f"the hand lane is recorded as `plan lane` would (got {doc['lanes']}, {status})")
check(status["app-a#3"] == "queued", "a skipped hand row stays queued")

# `after` met by a merged plan row, a closed issue, or a repo with no lane; the pick still follows plan order.
plan = _hand_plan(
    ("app-b#1", {"brief": _brief, "after": "app-x#5"}),                  # x#5 closed on GitHub
    ("app-c#2", {"brief": _brief, "after": "app-y#6, no-lane:busy2"}),   # y#6 merged in the plan
    ("app-a#3", {"brief": _brief}),
    extra=[lambda d: chief_plan.add_item(d, "app-y#6", "done", status="merged")],
)
fake, out, doc = _hand(plan, {("app-x", 5): {"state": "CLOSED", "labels": []}}, settings={"worker_cap": 2})
posted = [(b["repo"], b["number"]) for b in fake.posted]
check(posted == [("app-b", 1), ("app-c", 2)],
      f"met after conditions release rows, filling the cap in plan order (got {posted})")
check("DRAIN_STOPPED=app-a#3 reason=refused" in out,
      f"the worker cap still ends the drain through dispatch's own refusal (got {out!r})")

# A dispatch refusal on a hand row stops the drain, same as for drain's own rows.
plan = _hand_plan(("app-a#1", {"brief": _brief}), ("app-b#2", {"brief": _brief}))
fake, out, doc = _hand(plan, settings={"worker_cap": 0})
check(not fake.posted and "DRAIN_STOPPED=app-a#1 reason=refused" in out and "DRAIN_SKIPPED=app-b#2" not in out,
      f"a refused hand row stops the drain (got {out!r})")

# Dry run previews hand rows, honours the free slots and writes nothing.
plan = _hand_plan(
    ("app-a#1", {}),
    ("app-b#2", {"brief": _brief, "model": "sonnet"}),
    ("app-c#3", {"brief": _brief}),
)
before = json.dumps(plan, sort_keys=True)
fake, out, doc = _hand(plan, dry_run=True, settings={"worker_cap": 1})
check("DRAIN_WOULD_DISPATCH=app-b#2 model=sonnet" in out and "DRAIN_SKIPPED=app-a#1 reason=no-brief" in out
      and "app-c#3" not in out and "free_slots=1" in out,
      f"dry-run previews the top eligible hand row within the free slots (got {out!r})")
check(not fake.posted and not _ledger.exists() and not _briefs.exists()
      and json.dumps({k: v for k, v in doc.items() if k != "updated_at"}, sort_keys=True)
      == json.dumps({k: v for k, v in json.loads(before).items() if k != "updated_at"}, sort_keys=True),
      "dry-run dispatches nothing and writes no plan, ledger or brief")

# A plan file written before the new fields still loads and drains.
legacy = {"version": 1, "updated_at": "2026-11-06T00:00:00Z", "lanes": [],
          "queue": [{"repo": "app-a", "ref": "#1", "title": "old row", "status": "queued", "note": "x"}],
          "waiting_on_roberto": []}
_reset()
fake = FakeLauncher({"auto_drain": True})
rc, out, plan_path, _ = _run(fake, [], plan=legacy)
check(rc == 0 and "DRAIN_SKIPPED=app-a#1 reason=no-brief" in out, f"a row without the new fields loads (got {out!r})")

# ---- idle slots (fleet-config#1941) -------------------------------------------------------

_last = _TMP / "state" / drain.LAST_FILENAME


def _idle_lines(out):
    return [line for line in out.splitlines() if line.startswith("SLOTS_IDLE=")]


# Free slots and nothing eligible: the line names the count and why, and comes after the summary.
fake, out, doc = _hand(chief_plan.empty_plan(), cards=[_busy_card])
check(_idle_lines(out) == ["SLOTS_IDLE=2 reason=no-eligible-rows"] and out.splitlines()[-1] == _idle_lines(out)[0],
      f"free slots with an empty queue print SLOTS_IDLE=2 reason=no-eligible-rows, last (got {out!r})")
check(json.loads(_last.read_text(encoding="utf-8"))["line"] == "SLOTS_IDLE=2 reason=no-eligible-rows",
      "a real drain records its idle-slot line for the Stop hook")

# Rows blocked only by a live lane in another repo: all-after-unmet, with the repos listed.
plan = _hand_plan(
    ("app-a#5", {"brief": _brief, "after": "no-lane:busy"}),
    ("app-b#6", {"brief": _brief, "after": "no-lane:busy app-x#40"}),   # also waits on an open issue
    ("app-c#7", {"brief": _brief, "after": "no-lane:busy2"}),
)
fake, out, doc = _hand(plan, cards=[_busy_card, _session_card(project="busy2", session_id="busy2000")],
                       settings={"worker_cap": 5})
check(_idle_lines(out) == ["SLOTS_IDLE=3 reason=all-after-unmet no_lane_blocked=busy,busy2"],
      f"rows blocked by no-lane alone are listed; a row that also waits on an issue is not (got {_idle_lines(out)})")

# A row that is not after-unmet makes the reason the general one.
plan = _hand_plan(("app-a#5", {"brief": _brief, "after": "no-lane:busy"}), ("busy#6", {"brief": _brief}))
fake, out, doc = _hand(plan, cards=[_busy_card])
check(_idle_lines(out) == ["SLOTS_IDLE=2 reason=no-eligible-rows no_lane_blocked=busy"],
      f"a repo-busy row is not all-after-unmet (got {_idle_lines(out)})")

# Cap unreadable: its own state, never silence.
fake, out, doc = _hand(chief_plan.empty_plan(), settings={"worker_cap_scope": "bogus"})
check(_idle_lines(out) == ["SLOTS_IDLE=unknown reason=cap-unreadable"],
      f"an unreadable cap prints SLOTS_IDLE=unknown (got {_idle_lines(out)})")

# Cap full, or every slot filled by this drain: no line, and the recorded result says 0.
fake, out, doc = _hand(chief_plan.empty_plan(), cards=[_busy_card], settings={"worker_cap": 1})
check(not _idle_lines(out) and json.loads(_last.read_text(encoding="utf-8"))["line"] == "SLOTS_IDLE=0",
      f"a full cap prints no SLOTS_IDLE line (got {out!r})")
fake, out, doc = _hand(_hand_plan(("app-a#1", {"brief": _brief})), settings={"worker_cap": 1})
check(len(fake.posted) == 1 and not _idle_lines(out), f"a drain that fills the last slot prints none (got {out!r})")

# A stopped dispatch leaves the slots idle and says so.
_reset()
fake = FakeLauncher({"auto_drain": True}, start_exc=urllib.error.HTTPError("u", 503, "unavailable", {}, None))
rc, out, plan_path, _ = _run(fake, [], plan=_hand_plan(("app-a#1", {"brief": _brief})))
check(_idle_lines(out) == ["SLOTS_IDLE=3 reason=dispatch-stopped"], f"a stopped dispatch is its own reason (got {out!r})")

# Dry run prints the line too, but records nothing.
_reset()
fake = FakeLauncher({"auto_drain": True}, cards=[_busy_card])
rc, out, plan_path, _ = _run(fake, [], dry_run=True)
check(_idle_lines(out) == ["SLOTS_IDLE=2 reason=no-eligible-rows"] and not _last.exists(),
      f"dry-run reports idle slots and writes no result (got {out!r})")

# Switch off: drain does nothing, so there is nothing to report.
fake, out, doc = _hand(chief_plan.empty_plan(), settings={"auto_drain": False})
check(not _idle_lines(out), "a drain that is switched off reports no idle slots")


# ---- slots-idle: the Stop hook's read ------------------------------------------------

class _NoLauncher(FakeLauncher):
    def request(self, base_url, path, method="GET", body=None, timeout=10.0):
        raise AssertionError(f"slots-idle read the launcher ({path}) although a fresh result exists")


def _slots(fake, plan=None, last=None, states=None):
    """Run `slots-idle` against `fake`; `last` is a recorded drain result as `(age_seconds, line)`."""
    _reset()
    plan_path = _TMP / "plan.json"
    if plan is not None:
        plan_path.write_text(json.dumps({**plan, "updated_at": "2026-11-06T00:00:00Z"}), encoding="utf-8")
    if last is not None:
        _last.parent.mkdir(parents=True, exist_ok=True)
        _last.write_text(json.dumps({"at": time.time() - last[0], "line": last[1]}), encoding="utf-8")
    prior = (co.launcher._request, co.launcher.fetch_issue_state, co.chief_managed.mark)
    co.launcher._request = fake.request
    co.launcher.fetch_issue_state = lambda repo, number, owner=None: (
        (states or {}).get((repo, number)) or {"state": "OPEN", "labels": [{"name": "enhancement"}]})
    co.chief_managed.mark = lambda *a, **k: {}
    try:
        args = argparse.Namespace(plan_file=str(plan_path), base_url=co.DEFAULT_BASE_URL)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = drain.cmd_slots_idle(args)
    finally:
        co.launcher._request, co.launcher.fetch_issue_state, co.chief_managed.mark = prior
    return rc, buf.getvalue().splitlines(), plan_path


def _ask_plan(ask_id):
    plan = chief_plan.empty_plan()
    chief_plan.add_question(plan, "Which items fill the slots?", item_id=ask_id)
    return plan


rc, lines, _ = _slots(_NoLauncher({}), last=(30, "SLOTS_IDLE=2 reason=no-eligible-rows"))
check(rc == 0 and lines == ["SLOTS_IDLE=2 reason=no-eligible-rows"],
      f"a drain result under two minutes old is read back without asking the launcher (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card]), last=(300, "SLOTS_IDLE=0"))
check(lines == ["SLOTS_IDLE=2 reason=no-eligible-rows"], f"a stale result is re-checked live (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card]))
check(lines == ["SLOTS_IDLE=2 reason=no-eligible-rows"], f"no recorded result is checked live (got {lines})")

fake = FakeLauncher({"auto_drain": True})
rc, lines, plan_path = _slots(fake, plan=_hand_plan(("app-a#1", {"brief": _brief})))
check(lines == ["SLOTS_IDLE=3 reason=dispatchable-rows"] and not fake.posted
      and not _ledger.exists() and chief_plan.load(plan_path)["lanes"] == [],
      f"a row drain has not started counts as idle, and the check dispatches nothing (got {lines})")

rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card], cap=1))
check(lines == ["SLOTS_IDLE=0"], f"a full cap is SLOTS_IDLE=0 (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True, "worker_cap_scope": "bogus"}))
check(lines == ["SLOTS_IDLE=unknown reason=cap-unreadable"], f"an unreadable cap is unknown (got {lines})")
rc, lines, _ = _slots(FakeLauncher(urllib.error.URLError("refused")))
check(lines == ["SLOTS_IDLE=unknown reason=settings-unreadable"], f"an unreachable launcher is unknown (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": False}))
check(lines == ["SLOTS_IDLE=off reason=switch-off"], f"auto_drain off is not a hold (got {lines})")

# An open ask named slots-idle covers the idle slots; any other ask does not.
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card]), plan=_ask_plan("slots-idle"))
check(lines == ["SLOTS_IDLE=2 reason=no-eligible-rows", "SLOTS_COVERED=slots-idle"],
      f"an open slots-idle ask covers the idle slots (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card]), plan=_ask_plan("q1"))
check(lines == ["SLOTS_IDLE=2 reason=no-eligible-rows"], f"another open ask does not cover them (got {lines})")
rc, lines, _ = _slots(FakeLauncher({"auto_drain": True}, cards=[_busy_card], cap=1), plan=_ask_plan("slots-idle"))
check(lines == ["SLOTS_IDLE=0"], "a full cap prints no cover line")

# The subcommands are wired into chief_ops.
check(co.cli.build_parser("x").parse_args(["drain", "--dry-run"]).func is drain.cmd_drain,
      "`chief_ops.py drain` reaches cmd_drain")
check(co.cli.build_parser("x").parse_args(["slots-idle"]).func is drain.cmd_slots_idle,
      "`chief_ops.py slots-idle` reaches cmd_slots_idle")

shutil.rmtree(_TMP, ignore_errors=True)
_h.report_and_exit("test_drain")
