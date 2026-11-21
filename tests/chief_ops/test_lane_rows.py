"""chief_ops lane rows: `stop` and `plan set` retire the plan's lane row, `drain` reports stale ones.

Every case runs against a temp plan file and a stubbed launcher; the live chief plan is never touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_lane_rows.py`  (also invoked by tests/run_acceptance.py)
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _suite import _h, _session_card, check, co  # noqa: E402

# isort: split
import chief_plan  # noqa: E402
from chief import lane_rows, plan_cli  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="chief_lane_rows_"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_TMP / "state")
os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)
_PLAN = _TMP / "plan.json"

_SID_A = "aaaa1111" + "0" * 24
_SID_B = "bbbb2222" + "0" * 24


def _plan(lanes=(), queue=()):
    doc = chief_plan.empty_plan()
    for repo, status, fields in lanes:
        chief_plan.set_lane(doc, repo, status, **fields)
    for ref in queue:
        chief_plan.add_item(doc, ref, f"title {ref}")
    _PLAN.write_text(json.dumps({**doc, "updated_at": "2026-11-21T00:00:00Z"}), encoding="utf-8")


def _lane_repos():
    return [lane["repo"] for lane in chief_plan.load(_PLAN)["lanes"]]


def _stop(sid, *, columns, post_exc=None, managed=None):
    """Drive cmd_stop against a fake launcher; `columns=None` is an unreadable board."""
    def fake(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            if columns is None:
                raise urllib.error.URLError("refused")
            return {"columns": columns}
        if post_exc is not None:
            raise post_exc
        return {}

    prior = (co.launcher._request, co.commands._chief_managed_rows)
    co.launcher._request = fake
    co.commands._chief_managed_rows = lambda: (managed or {}, "")
    try:
        args = argparse.Namespace(sid=sid, kill=False, plan_file=str(_PLAN), base_url=co.DEFAULT_BASE_URL)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = co.cmd_stop(args)
    finally:
        co.launcher._request, co.commands._chief_managed_rows = prior
    return rc, out.getvalue()


def _cols(*cards):
    return {"claude_turn": list(cards), "your_turn": [], "other": []}


_card_a = _session_card(project="app-a", session_id=_SID_A)
_card_b = _session_card(project="app-b", session_id=_SID_B)
_two_lanes = (("app-a", "building", {"item": "#1", "session": _SID_A}),
              ("app-b", "building", {"item": "#2", "session": _SID_B}))

# ---- stop: the lane whose session was stopped goes, the others stay -----------------------

_plan(_two_lanes)
rc, out = _stop(_SID_A, columns=_cols(_card_b))
check(rc == 0 and "STOPPED sid=" in out and "PLAN_LANE=dropped repo=app-a" in out,
      f"stop: the stopped session's lane is dropped (got {out!r})")
check(_lane_repos() == ["app-b"], "stop: only that lane goes; the other lane is untouched")

_plan((("app-a", "building", {"item": "#1", "session": _SID_A[:8]}),))
rc, out = _stop(_SID_A, columns=_cols())
check("PLAN_LANE=dropped repo=app-a" in out and _lane_repos() == [],
      f"stop: a lane that recorded the 8-char board prefix matches the full session id (got {out!r})")

# ---- stop: no lane names the session -> repo fallback only when nothing else is live there ---

_plan((("app-a", "idle", {}),))
rc, out = _stop(_SID_A, columns=_cols(), managed={_SID_A: {"repo": "app-a", "number": 1}})
check("PLAN_LANE=dropped repo=app-a" in out and _lane_repos() == [],
      f"stop: no session on the row, repo free on the board -> the repo's lane goes (got {out!r})")

_plan((("app-a", "idle", {}),))
other = _session_card(project="app-a", session_id="cccc3333" + "0" * 24)
rc, out = _stop(_SID_A, columns=_cols(other), managed={_SID_A: {"repo": "app-a", "number": 1}})
check("PLAN_LANE=none" in out and _lane_repos() == ["app-a"],
      f"stop: another live session still holds the repo -> the lane stays (got {out!r})")

_plan((("app-a", "idle", {}),))
rc, out = _stop(_SID_A, columns=None, managed={_SID_A: {"repo": "app-a", "number": 1}})
check(rc == 0 and "PLAN_LANE=unknown reason=board-unreadable" in out and _lane_repos() == ["app-a"],
      f"stop: an unreadable board is unknown and keeps the row, never 'none' (got {out!r})")

_plan((("app-b", "building", {"item": "#2", "session": _SID_B}),))
rc, out = _stop(_SID_A, columns=_cols(_card_b))
check("PLAN_LANE=none" in out and _lane_repos() == ["app-b"],
      f"stop: a session no lane or repo maps to drops nothing (got {out!r})")

# ---- stop: only a confirmed stop retires the row -------------------------------------------

_plan(_two_lanes)
rc, out = _stop(_SID_A, columns=_cols(_card_a, _card_b), post_exc=TimeoutError("slow"))
check(rc == 1 and "STILL_LIVE" in out and "PLAN_LANE" not in out and len(_lane_repos()) == 2,
      f"stop: STILL_LIVE keeps the lane row (got {out!r})")

_plan(_two_lanes)
rc, out = _stop(_SID_A, columns=None, post_exc=TimeoutError("slow"))
check(rc == 1 and "UNCONFIRMED" in out and "PLAN_LANE" not in out and len(_lane_repos()) == 2,
      f"stop: UNCONFIRMED keeps the lane row (got {out!r})")

_plan(_two_lanes)
rc, out = _stop(_SID_A, columns=_cols(_card_b), post_exc=TimeoutError("slow"))
check(rc == 0 and "STOPPED sid=" in out and "PLAN_LANE=dropped repo=app-a" in out and _lane_repos() == ["app-b"],
      f"stop: a stop confirmed by the follow-up board read drops the lane (got {out!r})")

_PLAN.write_text("{not json", encoding="utf-8")
rc, out = _stop(_SID_A, columns=_cols())
check(rc == 0 and "STOPPED sid=" in out and "PLAN_LANE=unknown" in out,
      f"stop: an unreadable plan is reported unknown and does not undo the stop (got {out!r})")


# ---- plan set --status merged|parked ------------------------------------------------------

def _plan_set(ref, status):
    parser = co.cli.build_parser("x")
    args = parser.parse_args(["plan", "--plan-file", str(_PLAN), "set", ref, "--status", status,
                              *(["--kind", "umbrella"] if status == "parked" else [])])
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = plan_cli._run(args)
    return rc, out.getvalue()


_plan((("app-a", "building", {"item": "#1", "session": _SID_A}),
       ("app-b", "building", {"item": "#2", "session": _SID_B})), queue=("app-a#1", "app-b#2", "app-b#9"))
rc, out = _plan_set("app-a#1", "merged")
check(rc == 0 and out.splitlines()[0].startswith("PLAN=written") and "PLAN_LANE=dropped repo=app-a" in out
      and _lane_repos() == ["app-b"],
      f"plan set merged: the lane working that item is dropped, others stay (got {out!r})")

rc, out = _plan_set("app-b#9", "merged")
check("PLAN_LANE=none" in out and _lane_repos() == ["app-b"],
      f"plan set merged: a lane on a different item of the repo stays (got {out!r})")

chief_plan.update(_PLAN, lambda d: chief_plan.set_item(d, "app-b#2", status=chief_plan.QueueStatus.BUILDING))
rc, out = _plan_set("app-b#2", "parked")
check("PLAN_LANE=dropped repo=app-b" in out and _lane_repos() == [],
      f"plan set parked: also retires the lane (got {out!r})")

# ---- stale lane report ---------------------------------------------------------------------

_NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def _stale(lanes, columns, managed=None):
    doc = chief_plan.empty_plan()
    for repo, status, fields in lanes:
        chief_plan.set_lane(doc, repo, status, **fields)
    return lane_rows.stale_lines(doc, columns, managed, now=_NOW)


check(_stale(_two_lanes, _cols(_card_a)) == [f"PLAN_LANE_STALE=app-b session={_SID_B}"],
      "stale: a lane whose session is not live is reported, a live one is not")
check(_stale(_two_lanes, _cols(_card_a, _card_b)) == [], "stale: every lane backed by a live session reports nothing")
check(_stale((("app-a", "building", {"item": "#1", "session": _SID_A[:8]}),), _cols(_card_a)) == [],
      "stale: a prefix-only lane session matches its live card")
dead = _session_card(project="app-a", session_id=_SID_A, alive=False)
check(_stale(_two_lanes[:1], _cols(dead)) == [f"PLAN_LANE_STALE=app-a session={_SID_A}"],
      "stale: a dead card does not back its lane")
check(_stale((("app-a", "idle", {}),), _cols()) == ["PLAN_LANE_STALE=app-a session=none"],
      "stale: a lane with no session is stale when nothing live holds its repo")
check(_stale((("app-a", "idle", {}),), _cols(_card_a)) == [],
      "stale: a lane with no session is fine while a live session holds its repo")
fresh = {_SID_A: {"repo": "app-a", "number": 1, "dispatched_at": (_NOW - timedelta(seconds=60)).isoformat()}}
check(_stale(_two_lanes[:1], _cols(), fresh) == [],
      "stale: a lane dispatched a minute ago is not stale while its card is not on the board yet")
old = {_SID_A: {"repo": "app-a", "number": 1, "dispatched_at": (_NOW - timedelta(hours=2)).isoformat()}}
check(len(_stale(_two_lanes[:1], _cols(), old)) == 1, "stale: an old dispatch gets no grace")
check(_stale(_two_lanes, None) == ["PLAN_LANE_STALE=unknown reason=board-unreadable"],
      "stale: an unreadable board is one unknown line, never an empty (clean) list")

shutil.rmtree(_TMP, ignore_errors=True)
_h.report_and_exit("test_lane_rows")
