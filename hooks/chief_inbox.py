"""Chief inbox: a chief-managed worker's turn-end becomes a wake event (fleet-config#999).

The chief used to learn that a dispatched worker finished, asked a question,
or gave up only on its next timed poll -- up to ten minutes later. Workers
already emit the exact signal: their turn ending. `AskUserQuestion` is blocked
in chief-managed sessions (#463), so a worker asks in plain output and then
ends its turn; `Stop` covers finished, question and gave-up alike, and the
chief tells them apart by reading the exchange.

`record()` is called from the three Board adapters' turn-end/exit paths
(`session_state`, `session_state_codex`, `session_state_pi`), each already
wired on its harness, so no hook registration changes. A worker blocked on a
permission gate writes a ``needs-input`` event the same way, through
``notify_on_idle.chief_needs_input`` (fleet-config#1456). It writes one small
event file per event into ``state_dir()/chief-inbox/``; the chief's
``chief_ops.py wait-event`` claims them by rename and exits, and that exit is
what wakes the chief (a background task's exit is the one wake it trusts).

Why a directory of files rather than one ``.jsonl``: on Windows a file another
process holds open for append cannot be renamed, so a single shared log would
make "claim atomically" and "append concurrently" fight. One file per event,
published by temp-file-then-rename, makes claim-exactly-once a plain rename.

Mailbox v2 (fleet-config#1455) adds a stable ``id`` (also the file name's
suffix: ``<published_ms>-<id>.json``), a ``kind``, the dispatch's
``dispatch_id`` and a bounded ``excerpt`` of the worker's final reply, so a
re-delivered event can be deduped and the chief rarely needs ``exchange``.
``event`` stays for the v1 reader. The cross-repo contract -- layout, schema,
the pending rule a second reader counts by -- is ``docs/chief-mailbox.md``.

Cheap and fail-open by construction: a session without
``APP_LAUNCHER_SESSION_ID`` (every human-started one), or a print-mode child
that only inherited it (fleet-config#1281), returns before any I/O;
the worker path is one small file write -- no subprocess, no network -- plus,
only when the payload carries no reply text, one bounded transcript tail read
(``TRANSCRIPT_TAIL_BYTES``). An
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
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

logger = logging.getLogger("fleet_hooks")

INBOX_DIRNAME = "chief-inbox"
TURN_END = "Stop"
SESSION_END = "SessionEnd"
SCHEMA_VERSION = 2
# v1 `event` -> v2 `kind`. A needs-input event keeps its harness's own `event`
# (`Notification` / `PermissionRequest`) and passes `kind=NEEDS_INPUT` (#1456).
KINDS = {TURN_END: "turn-end", SESSION_END: "session-end"}
NEEDS_INPUT = "needs-input"
EXCERPT_CHARS = 1500
# A needs-input excerpt is the hook's classified prompt line, bounded as the
# typed `say` message it replaces was.
NEEDS_INPUT_EXCERPT_CHARS = 300
# A transcript tail read on the worker's Stop stays bounded however long the
# session ran; an assistant record larger than this is reported unavailable.
TRANSCRIPT_TAIL_BYTES = 256 * 1024


def inbox_dir() -> Path:
    """Resolved at call time so a ``CLAUDE_HOOKS_STATE_DIR`` override wins."""
    return _lib.state_dir() / INBOX_DIRNAME


def _content_text(content: Any) -> str:
    """Text of a message's ``content``: a string, or the ``text`` of its text blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [block.get("text") for block in content
             if isinstance(block, dict) and block.get("type") in ("text", "output_text")]
    return "\n".join(part for part in parts if isinstance(part, str) and part)


def _assistant_text(entry: Any) -> str:
    """Assistant reply text of one transcript line, or ``""``.

    One reader for the three on-disk shapes: Claude (``type: assistant``,
    ``message``), Pi (``type: message``, ``message.role``) and a Codex rollout
    (``type: response_item``, ``payload.role``).
    """
    if not isinstance(entry, dict):
        return ""
    for key in ("message", "payload"):
        message = entry.get(key)
        if isinstance(message, dict) and message.get("role") == "assistant":
            return _content_text(message.get("content")).strip()
    return ""


def _transcript_excerpt(path: str) -> Tuple[Optional[str], str]:
    """``(text, "")`` from the transcript's last assistant reply, or ``(None, reason)``."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            tail = handle.read()
    except OSError as exc:
        return None, f"transcript unreadable ({type(exc).__name__})"
    for line in reversed(tail.decode("utf-8", errors="replace").splitlines()):
        try:
            text = _assistant_text(json.loads(line))
        except ValueError:
            continue  # the window's cut first line, or junk
        if text:
            return text, ""
    return None, f"no assistant reply in the transcript's last {TRANSCRIPT_TAIL_BYTES // 1024} KiB"


def excerpt_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The event's excerpt fields: the last ``EXCERPT_CHARS`` of the final reply.

    The harness's own last-assistant field first, else the tail of
    ``transcript_path``. Neither readable -> ``excerpt: None`` plus an
    ``excerpt_unavailable`` reason -- never an empty string, which would read
    as "the worker said nothing".
    """
    text = payload.get("last_assistant_message")
    if isinstance(text, str) and text.strip():
        return {"excerpt": text.strip()[-EXCERPT_CHARS:], "excerpt_source": "payload"}
    transcript = payload.get("transcript_path")
    if not (isinstance(transcript, str) and transcript):
        return {"excerpt": None, "excerpt_source": None,
                "excerpt_unavailable": "no last-assistant field and no transcript_path"}
    found, reason = _transcript_excerpt(transcript)
    if found:
        return {"excerpt": found[-EXCERPT_CHARS:], "excerpt_source": "transcript"}
    return {"excerpt": None, "excerpt_source": None, "excerpt_unavailable": reason}


def record(payload: Dict[str, Any], event: str, default_agent: str = "claude", *,
           kind: Optional[str] = None, prompt: Optional[str] = None) -> Optional[Path]:
    """Write one inbox event for this session's ``event``; the file, or ``None``.

    ``kind`` overrides the ``KINDS`` mapping; ``prompt`` is a needs-input
    event's excerpt (``excerpt_source: prompt``) in place of the final reply.
    """
    sid = _lib.own_launcher_session_id()
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
    event_id = uuid.uuid4().hex
    entry = {
        "v": SCHEMA_VERSION,
        "id": event_id,
        "kind": kind or KINDS.get(event, event),
        "event": event,
        "launcher_sid": sid,
        "repo": row.get("repo"),
        "number": row.get("number"),
        "agent": agent,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dispatch_id": row.get("dispatch_id"),
        **(excerpt_fields(payload) if prompt is None else
           {"excerpt": prompt[:NEEDS_INPUT_EXCERPT_CHARS], "excerpt_source": "prompt"}),
    }
    directory = inbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # `<published_ms>-<id>.json`: the millisecond prefix keeps the unchanged v1
    # reader's name-sorted claim oldest first; the id is the suffix.
    target = directory / f"{int(now.timestamp() * 1000)}-{event_id}.json"
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
