"""Retiring the plan's lane rows: a lane row is a claim that a worker is on a repo, so it goes when the worker does.

`drain` writes a row when it dispatches a lane (`drain.record_lane`); this module is the other half.
`chief_ops.py stop` drops the row of the session it stopped, `plan set --status merged|parked` drops the
row working that item, and `drain` reports any row whose session is not live so a leftover is visible
rather than silent. Every state a probe cannot establish (unreadable plan, unreadable board) is
reported as `unknown`, never as "no row" or "not stale".
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import chief_plan

from . import rails

# A fresh spawn is not on the board for a while (see drain's lag handling), so a lane dispatched
# this recently is not reported stale just because its card has not appeared yet.
STALE_GRACE_SECONDS = 600

Columns = Dict[str, Any]


def _same_session(lane_sid: str, sid: str) -> bool:
    """A lane's `session` is a full id when `drain` wrote it and often the 8-char board prefix when
    a hand-written `plan lane --session` did; either spelling names the same session."""
    a, b = lane_sid.strip().lower(), sid.strip().lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def drop_for_session(doc: Dict[str, Any], sid: str) -> Optional[str]:
    """Drop the lane whose session is `sid`; returns its repo, or None when no lane has it."""
    for lane in doc["lanes"]:
        if _same_session(str(lane.get("session") or ""), sid):
            chief_plan.drop_lane(doc, lane["repo"])
            return str(lane["repo"])
    return None


def drop_for_item(doc: Dict[str, Any], repo: str, local_ref: str) -> bool:
    """Drop `repo`'s lane when it is working `local_ref` (`#N`); a lane on another item stays."""
    lane = next((row for row in doc["lanes"] if row["repo"] == repo and row.get("item") == local_ref), None)
    if lane is None:
        return False
    chief_plan.drop_lane(doc, repo)
    return True


def _stopped_repos(columns: Optional[Columns], managed: Optional[Mapping[str, Any]], sid: str) -> List[str]:
    """Lowercased repos the stopped session could have held a lane for: the repo recorded when the
    chief dispatched it, and its card's folder while the board still lists one."""
    repos: List[str] = []
    for key, row in (managed or {}).items():
        if _same_session(str(key), sid) and isinstance(row, dict) and row.get("repo"):
            repos.append(str(row["repo"]))
    for bucket in (columns or {}).values():
        for card in bucket or []:
            if _same_session(str(card.get("session_id") or ""), sid) and card.get("project"):
                repos.append(str(card["project"]))
    return list(dict.fromkeys(r.strip().lower() for r in repos if r.strip()))


def _without_session(columns: Columns, sid: str) -> Columns:
    return {name: [card for card in bucket or [] if not _same_session(str(card.get("session_id") or ""), sid)]
            for name, bucket in columns.items()}


def retire_after_stop(
    plan_path: Path,
    sid: str,
    read_columns: Callable[[], Optional[Columns]],
    managed: Optional[Mapping[str, Any]],
) -> str:
    """The `PLAN_LANE=` line for a stop that is confirmed.

    The lane whose `session` is `sid` goes. With none, the stopped session's repo lane goes only
    when the board shows no other live session there; a board that cannot be read leaves the row
    and says `unknown`.
    """
    doc = chief_plan.load(plan_path)
    if not doc["lanes"]:
        return "PLAN_LANE=none"
    if any(_same_session(str(lane.get("session") or ""), sid) for lane in doc["lanes"]):
        dropped: List[str] = []
        chief_plan.update(plan_path, lambda d: dropped.append(drop_for_session(d, sid) or ""))
        return f"PLAN_LANE=dropped repo={dropped[0]}" if dropped[0] else "PLAN_LANE=none"

    columns = read_columns()
    if columns is None:
        return "PLAN_LANE=unknown reason=board-unreadable (no lane names this session; repo fallback needs the board)"
    candidates = _stopped_repos(columns, managed, sid)
    lane = next((row for row in doc["lanes"] if row["repo"].lower() in candidates), None)
    if lane is None:
        return "PLAN_LANE=none"
    still_held = rails.repo_occupancy(
        _without_session(columns, sid), exclude_sid=rails.caller_session_id(),
        managed={k: v for k, v in (managed or {}).items() if not _same_session(str(k), sid)})
    if lane["repo"].lower() in still_held:
        return "PLAN_LANE=none"
    repo = str(lane["repo"])
    chief_plan.update(plan_path, lambda d: chief_plan.drop_lane(d, repo))
    return f"PLAN_LANE=dropped repo={repo}"


def _recently_dispatched(managed: Optional[Mapping[str, Any]], sid: str, now: datetime) -> bool:
    for key, row in (managed or {}).items():
        if not (_same_session(str(key), sid) and isinstance(row, dict)):
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("dispatched_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if 0 <= (now - stamp).total_seconds() < STALE_GRACE_SECONDS:
            return True
    return False


def stale_lines(
    doc: Dict[str, Any],
    columns: Optional[Columns],
    managed: Optional[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> List[str]:
    """`PLAN_LANE_STALE=<repo> session=<sid|none>` for each lane no live session backs.

    A lane with a session is stale when no live card carries that id; one with none is stale when
    nothing live holds its repo. An unreadable board is one `unknown` line, never a clean list.
    """
    if columns is None:
        return ["PLAN_LANE_STALE=unknown reason=board-unreadable"]
    moment = now or datetime.now(timezone.utc)
    live = rails.live_session_ids(columns)
    held = rails.repo_occupancy(columns, exclude_sid=rails.caller_session_id(), managed=managed)
    lines: List[str] = []
    for lane in doc["lanes"]:
        sid = str(lane.get("session") or "")
        if sid:
            stale = not any(_same_session(sid, live_sid) for live_sid in live)
            stale = stale and not _recently_dispatched(managed, sid, moment)
        else:
            stale = lane["repo"].lower() not in held
        if stale:
            lines.append(f"PLAN_LANE_STALE={lane['repo']} session={sid or 'none'}")
    return lines
