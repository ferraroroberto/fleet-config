"""Chief-managed session marker (fleet-config#443).

`chief_ops.py dispatch` writes a marker here immediately after spawning a
worker session, so `hooks/notify_on_idle.py` can tell a chief-dispatched
worker apart from a human-started one and route its "blocked on input"
notification to chief instead of Slack. A different concern from
`active_issue.py`'s issue-branch lifecycle (this is session identity for
notification routing, keyed by session id, not by repo#issue), so a
separate state file rather than overloading that schema — but the same
I/O idiom (24h TTL prune, atomic temp-file replace, a lock dir serializing
the read-modify-write), reused directly from `active_issue` rather than
re-derived, since concurrent dispatches are exactly the race that module's
locking already solves.

The file is advisory and self-healing, same as `active-issues.json`: a
missing/corrupt read is empty, not fatal, and a marker that outlives its
session is harmless — `notify_on_idle` only ever *reads* it to decide
routing, it never trusts it as proof a session is alive.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from active_issue import _write_rows, read_rows, state_lock  # noqa: E402

STATE_FILENAME = "chief-managed.json"
PRUNE_AFTER = timedelta(hours=24)


def state_file() -> Path:
    """Resolve the state path at call time so tests can override its root
    (mirrors `active_issue.py`'s own `state_file()`)."""
    root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    base = Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
    return base / STATE_FILENAME


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dispatched_at(row: Any) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    raw = row.get("dispatched_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def prune_rows(rows: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Keep only well-formed markers no older than :data:`PRUNE_AFTER`."""
    cutoff = (now or _now()) - PRUNE_AFTER
    kept: Dict[str, Any] = {}
    for sid, row in rows.items():
        stamp = _parse_dispatched_at(row)
        if stamp is not None and stamp >= cutoff:
            kept[str(sid)] = row
    return kept


def mark(
    sid: str,
    repo: str,
    number: int,
    *,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record `sid` as chief-managed. Returns the row written."""
    if not sid.strip():
        raise ValueError("sid must not be empty")
    target = path or state_file()
    moment = now or _now()
    row: Dict[str, Any] = {
        "repo": repo,
        "number": number,
        "dispatched_at": _iso_z(moment),
    }
    with state_lock(target):
        rows = prune_rows(read_rows(target), now=moment)
        rows[sid] = row
        _write_rows(target, rows)
    return row


def is_managed(sid: str, *, path: Optional[Path] = None) -> bool:
    """True if `sid` has a live (unpruned) chief-managed marker."""
    target = path or state_file()
    rows = prune_rows(read_rows(target))
    return sid in rows
