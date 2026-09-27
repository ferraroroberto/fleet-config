"""Pi adapter for the Fleet Board session-state writer (fleet-config#349).

Small stdin-JSON CLI the Pi extension (``pi/extensions/session_state.ts``)
shells out to on each lifecycle event it observes — keeps the one-writer,
atomic-write invariant :mod:`session_state` documents ("this module is the
only writer") in Python rather than duplicating tmp+replace/prune logic in
TypeScript.

Reads a single JSON object from stdin shaped like::

    {"event": "input"|"agent_settled"|"session_shutdown", "session_id": "...",
     "cwd": "...", "transcript_path": null}

Pi's extension API events (pi.dev/docs/latest/extensions) map onto Claude
Code's three wired hooks: ``input`` (user submits a prompt) ↔
``UserPromptSubmit``, ``agent_settled`` ("fires when Pi will not continue
running automatically") ↔ ``Stop``, ``session_shutdown`` ↔ ``SessionEnd``.

Advisory-only like every hook here: any failure is swallowed and the process
always exits 0 — a broken adapter must never disturb a live Pi session.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import session_state  # noqa: E402

_EVENT_STATUS = {
    "input": "working",
    "agent_settled": "needs-you",
}


def _payload_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape the extension's event JSON into the shared hook-payload shape
    :func:`session_state.upsert_from_payload` / :func:`session_state.remove_from_payload`
    already parse (``session_id`` / ``cwd`` / ``transcript_path``)."""
    return {
        "session_id": event.get("session_id"),
        "cwd": event.get("cwd"),
        "transcript_path": event.get("transcript_path"),
    }


def main() -> None:
    # Same stderr wiring as session_state, so chief_inbox's breadcrumbs land.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        event = _lib.read_stdin_json()
        name = str(event.get("event") or "")
        payload = _payload_from_event(event)
        if name == "session_shutdown":
            session_state.remove_from_payload(payload)
        else:
            status = _EVENT_STATUS.get(name)
            if status:
                session_state.upsert_from_payload(
                    payload,
                    status,
                    default_agent="pi",
                    allow_reopen=name == "input",
                )
        # Pi's turn-end and exit, in the inbox's harness-neutral vocabulary
        # (fleet-config#999).
        inbox_event = {"agent_settled": "Stop", "session_shutdown": "SessionEnd"}.get(name)
        if inbox_event:
            import chief_inbox
            chief_inbox.record_safely(payload, inbox_event, default_agent="pi")
    except Exception:  # noqa: BLE001 — state is advisory; never disturb the session
        pass
    _lib.allow()


if __name__ == "__main__":
    main()
