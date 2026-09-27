"""Chief inbox: a chief-managed worker's turn-end becomes a wake event (fleet-config#999).

The chief used to learn that a dispatched worker finished, asked a question,
or gave up only on its next timed poll -- up to ten minutes later. Workers
already emit the exact signal: their turn ending. `AskUserQuestion` is blocked
in chief-managed sessions (#463), so a worker asks in plain output and then
ends its turn; `Stop` covers finished, question and gave-up alike, and the
chief tells them apart by reading the exchange.

`record()` is called from the three Board adapters' turn-end/exit paths
(`session_state`, `session_state_codex`, `session_state_pi`), each already
wired on its harness, so no hook registration changes. It writes one small
event file per event into ``state_dir()/chief-inbox/``; the chief's
``chief_ops.py wait-event`` claims them by rename and exits, and that exit is
what wakes the chief (a background task's exit is the one wake it trusts).

Why a directory of files rather than one ``.jsonl``: on Windows a file another
process holds open for append cannot be renamed, so a single shared log would
make "claim atomically" and "append concurrently" fight. One file per event,
published by temp-file-then-rename, makes claim-exactly-once a plain rename.

Cheap and fail-open by construction: a session without
``APP_LAUNCHER_SESSION_ID`` (every human-started one) returns before any I/O;
the worker path is one small file write -- no subprocess, no network. An
unreadable registry is logged as undetermined and writes nothing: "could not
tell" is never folded into "not managed" (#835).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

logger = logging.getLogger("fleet_hooks")

INBOX_DIRNAME = "chief-inbox"
TURN_END = "Stop"
SESSION_END = "SessionEnd"


def inbox_dir() -> Path:
    """Resolved at call time so a ``CLAUDE_HOOKS_STATE_DIR`` override wins."""
    return _lib.state_dir() / INBOX_DIRNAME


def record(payload: Dict[str, Any], event: str, default_agent: str = "claude") -> Optional[Path]:
    """Write one inbox event for this session's ``event``; the file, or ``None``."""
    sid = _lib.launcher_session_id()
    if not sid:
        return None
    import notify_on_idle  # deferred: only launcher-spawned sessions pay for it

    row, reason = notify_on_idle.chief_managed_row()
    if reason == notify_on_idle.CHIEF_UNDETERMINED:
        logger.info("ℹ️ chief_inbox: chief-managed state undetermined for %s -- no %s event written",
                    sid, event)
        return None
    if row is None:
        return None
    agent = (os.environ.get("APP_LAUNCHER_AGENT", "").strip().lower()
             or _lib.payload_agent(payload) or default_agent)
    now = datetime.now(timezone.utc)
    entry = {
        "event": event,
        "launcher_sid": sid,
        "repo": row.get("repo"),
        "number": row.get("number"),
        "agent": agent,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    directory = inbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{int(now.timestamp() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    # Every event shares one temp stem, so the next writer's age sweep can
    # find a hard-killed writer's orphan (each target name is unique).
    stem = directory / "event"
    _lib.sweep_stale_atomic_temps(stem)
    fd, tmp_name = tempfile.mkstemp(prefix=_lib.atomic_tmp_prefix(stem), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entry, handle)
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def record_safely(payload: Dict[str, Any], event: str, default_agent: str = "claude") -> None:
    """``record`` for a hook's turn-end path: never raises, never blocks."""
    try:
        record(payload, event, default_agent)
    except Exception as exc:  # noqa: BLE001 -- advisory; a worker's Stop must never fail
        logger.info("ℹ️ chief_inbox: %s event not written (%s)", event, exc)
