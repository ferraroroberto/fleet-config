"""Hold a chief-dispatched lane that ends its turn with its own background work still running.

A lane is a top-level session nothing wakes: when it starts a gate with
`run_in_background`, arms a Monitor or launches a background agent and then
ends its turn "waiting for it", the work finishes and nobody reads the result.
`chief_waiter_stop` calls :func:`stop_refusal` for every non-chief session; it
acts only for a launcher session listed in `chief-managed.json` and holds the
turn end once with the instruction to block on that task's output.

The signal is the session's own transcript (`transcript_path` in the Stop
payload). Every background launch writes a tool result that names its task id
(`Command running in background with ID: <id>`, `Monitor started (task <id>`,
`Async agent launched successfully ... agentId: <id>`), and the harness records
how each one ended: a `<task-notification>` carrying `<status>`, a Monitor's
`[Monitor expired` event, or a TaskStop result `Successfully stopped task:
<id>`. A launch with none of those is still running. A Monitor past the expiry
its own start line names is over even without the event, and a launch older
than :data:`STALE_AFTER_S` is not counted: its process may have died with a
restarted session, and a guess must not hold the lane every turn.

One hold per turn: the follow-up stop carries `stop_hook_active`, which
`chief_waiter_stop` allows before reaching this module, so it can never loop. An
unreadable transcript allows and logs `unknown`; it is never read as "nothing
running".
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402

logger = logging.getLogger("fleet_hooks")

# A launch with no recorded end after this long is not counted: a restarted
# session loses its tasks without any notification, and a lane gate runs well
# under it.
STALE_AFTER_S = 4 * 3600
# A Monitor's expiry event is written when it fires; this slack covers that write.
MONITOR_GRACE_S = 120

_LAUNCHES = (
    ("background command", re.compile(r"^Command running in background with ID: (\w+)")),
    ("Monitor", re.compile(r"^Monitor started \(task (\w+)(?:, expires in (\d+)m)?")),
    ("background agent", re.compile(r"^Async agent launched successfully\..*?agentId: (\w+)", re.S)),
)
_STOPPED = re.compile(r'^(?:\{"message":")?Successfully stopped task: (\w+)')
_NOTIFICATION = re.compile(r"<task-notification>\s*<task-id>(\w+)</task-id>(.*?)</task-notification>", re.S)
# Cheap byte pre-filter: only lines that can carry a launch or an end are parsed.
_NEEDLES = (b"running in background with ID", b"Monitor started (task", b"Async agent launched",
            b"<task-notification>", b"Successfully stopped task")


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _stamp(record: Dict[str, Any]) -> Optional[float]:
    value = record.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _records(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("rb") as handle:
        for raw in handle:
            if not any(needle in raw for needle in _NEEDLES):
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and not record.get("isSidechain"):
                yield record


def _notification_text(record: Dict[str, Any]) -> str:
    """The text a `<task-notification>` can arrive in: a queue operation or a user message."""
    if record.get("type") == "queue-operation":
        return str(record.get("content") or "")
    if record.get("type") != "user":
        return ""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content
                       if isinstance(part, dict) and part.get("type") == "text")
    return ""


def running_tasks(path: Path, now: float) -> List[Tuple[str, str]]:
    """`(kind, task id)` of every background launch in the transcript with no recorded end.

    Raises OSError when the transcript cannot be read; the caller reports that as unknown.
    """
    # task id -> (kind, launch time, Monitor expiry deadline)
    launched: Dict[str, Tuple[str, Optional[float], Optional[float]]] = {}
    ended = set()
    for record in _records(path):
        for match in _NOTIFICATION.finditer(_notification_text(record)):
            body = match.group(2)
            if "<status>" in body or "[Monitor expired" in body:
                ended.add(match.group(1))
        content = (record.get("message") or {}).get("content") if record.get("type") == "user" else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            text = _result_text(block)
            stopped = _STOPPED.match(text)
            if stopped:
                ended.add(stopped.group(1))
                continue
            for kind, pattern in _LAUNCHES:
                found = pattern.match(text)
                if not found:
                    continue
                start = _stamp(record)
                deadline = None
                if kind == "Monitor" and found.group(2) and start is not None:
                    deadline = start + int(found.group(2)) * 60 + MONITOR_GRACE_S
                launched[found.group(1)] = (kind, start, deadline)
                break
    live = []
    for task_id, (kind, start, deadline) in launched.items():
        if task_id in ended or start is None or (deadline is not None and now > deadline):
            continue
        if now - start > STALE_AFTER_S:
            logger.info("lane_background: task %s launched over %ds ago with no recorded end -- not counted",
                        task_id, STALE_AFTER_S)
            continue
        live.append((kind, task_id))
    return live


def stop_refusal(payload: Dict[str, Any], now: Optional[float] = None) -> Optional[str]:
    """The message that holds a lane's turn end while its own background work runs, or None."""
    if not _lib.launcher_session_id():
        return None
    # Imported here, not at module level: only launcher sessions ever need it.
    import notify_on_idle  # noqa: PLC0415

    managed, reason = notify_on_idle.chief_managed_state()
    if not managed:
        if reason == notify_on_idle.CHIEF_UNDETERMINED:
            logger.warning("lane_background: chief-managed registry unreadable -- allowing the turn end")
        return None
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        logger.info("lane_background: background tasks unknown (no transcript path) -- allowing the turn end")
        return None
    clock = time.time() if now is None else now
    try:
        live = running_tasks(Path(transcript), clock)
    except OSError as exc:
        logger.warning("lane_background: background tasks unknown (%s) -- allowing the turn end", exc)
        return None
    if not live:
        return None
    named = ", ".join(f"{kind} {task_id}" for kind, task_id in live[:3])
    more = f" and {len(live) - 3} more" if len(live) > 3 else ""
    logger.info("lane_background: holding lane turn end -- %d background task(s) running", len(live))
    return (
        f"Your own background work is still running ({named}{more}), and nothing wakes this session once the "
        "turn ends. Block on it now: call TaskOutput with block=true (or BashOutput) until it finishes, then "
        "carry on from its result. If it is a server you mean to leave running, or no longer needed, stop it "
        "with TaskStop or say so and end the turn again."
    )
