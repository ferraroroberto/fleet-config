"""Chief-managed session marker.

`chief_ops.py dispatch` writes a marker here immediately after spawning a
worker session, so `hooks/notify_on_idle.py` can tell a chief-dispatched
worker apart from a human-started one and route its "blocked on input"
notification to chief instead of the human ping. A different concern from
`active_issue.py`'s issue-branch lifecycle (this is session identity for
notification routing, keyed by session id, not by repo#issue), so a
separate state file rather than overloading that schema — but the same
I/O idiom (24h TTL prune, atomic temp-file replace, a lock dir serializing
the read-modify-write) is reused directly from `fleet_core.state_rows`
(`state_file`, `utc_now`, `iso_z`, `prune_rows`), not re-derived, since
concurrent dispatches are exactly the race that module's locking already
solves.

The file is advisory and self-healing, same as `active-issues.json`: a
missing/corrupt read is empty, not fatal, and a marker that outlives its
session is harmless — `notify_on_idle` only ever *reads* it to decide
routing, it never trusts it as proof a session is alive.

This module is the **writer only**. The one reader is
`hooks/notify_on_idle.chief_managed_state()` (used by it and by
`block_askuserquestion_chief`), which keeps its own read logic per the
hooks/skills_lib tree-independence convention and returns a reason alongside
the verdict so "not managed" and "could not tell" stay distinct. A second
reader here was removed as dead — query the marker through
that function, not by adding one back.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_core import state_rows  # noqa: E402

STATE_FILENAME = "chief-managed.json"
STAMP_FIELD = "dispatched_at"


def state_file() -> Path:
    return state_rows.state_file(STATE_FILENAME)


def mark(
    sid: str,
    repo: str,
    number: int,
    *,
    dispatch_id: Optional[str] = None,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record `sid` as chief-managed. Returns the row written.

    `dispatch_id` is the per-dispatch handle
    `hooks/chief_inbox.py` copies into every mailbox event; a row without
    one (a launcher-direct dispatch) yields `dispatch_id: null` events.
    """
    if not sid.strip():
        raise ValueError("sid must not be empty")
    target = path or state_file()
    moment = now or state_rows.utc_now()
    row: Dict[str, Any] = {
        "repo": repo,
        "number": number,
        STAMP_FIELD: state_rows.iso_z(moment),
    }
    if dispatch_id:
        row["dispatch_id"] = dispatch_id
    with state_rows.state_lock(target):
        rows = state_rows.prune_rows(state_rows.read_rows(target), stamp_field=STAMP_FIELD, now=moment)
        rows[sid] = row
        state_rows.write_rows(target, rows)
    return row


def _main(argv: Optional[list[str]] = None) -> int:
    """Minimal CLI so a caller outside this tree (app-launcher, a different
    repo) can write a marker via subprocess instead of importing across the
    repo boundary -- the same hooks/skills_lib tree-independence convention
    the module docstring describes for the read side.

    ``mark <sid> <repo> <number>`` is the only subcommand; it mirrors
    `chief_ops.py cmd_dispatch`'s existing best-effort call so both the CLI
    dispatch path and a direct launcher-endpoint dispatch land in the same
    state file the same way.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="chief_managed.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mark_parser = sub.add_parser("mark", help="record sid as chief-managed")
    mark_parser.add_argument("sid")
    mark_parser.add_argument("repo")
    mark_parser.add_argument("number", type=int)
    args = parser.parse_args(argv)

    try:
        mark(args.sid, args.repo, args.number)
    except ValueError as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        return 1
    print(f"MARKED sid={args.sid} repo={args.repo} number={args.number}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
