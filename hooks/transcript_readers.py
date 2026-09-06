"""Native transcript readers; hook-envelope normalization remains in _lib.

Readers return ordered conversational text, never model context/tool output.
Unknown readers and damaged sources cannot silently become successful captures.
See docs/conversation-capture.md for the versioned reader/lineage contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Optional


@dataclass(frozen=True)
class Transcript:
    status: str  # ok | unavailable | unsupported | parse_failure
    harness: str = ""
    session_id: str = ""
    parent_session_id: str = ""
    source_format: str = ""
    source_version: str = ""
    messages: list[tuple[str, str]] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    detail: str = ""


def native_id(value: object) -> str:
    """Keep opaque native IDs safe in metadata and native resume commands."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value) else ""


def _normalize_newlines(text: str) -> str:
    """CRLF/CR -> LF, so a Windows-stored rollout renders identically to a LF one.

    Applied at the reader boundary so every consumer (capture, index, search)
    sees the same line endings regardless of which harness or OS wrote the
    stored transcript (fleet-config#785).
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_content(content: object) -> str:
    """Text blocks only; tool, image and reasoning blocks are not conversation."""
    if isinstance(content, str):
        return _normalize_newlines(content)
    if not isinstance(content, list):
        return ""
    joined = "\n".join(
        block["text"] for block in content
        if isinstance(block, dict) and block.get("type") in {"text", "Text", "input_text", "output_text"}
        and isinstance(block.get("text"), str)
    )
    return _normalize_newlines(joined)


def read_transcript(path: Path, *, harness: Optional[str] = None, session_id: str = "") -> Transcript:
    """Read one stored native JSONL source. Reject partial reads before any write.

    Native append order is chronological (not wall-clock sorting, which would
    move replayed/forked turns). Codex event mirrors are deliberately ignored.
    No scanning of other sessions, SQLite mutation or content-based lineage.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return Transcript("unavailable", detail=type(exc).__name__)
    except UnicodeError:
        return Transcript("parse_failure", detail="invalid UTF-8")
    entries = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return Transcript("parse_failure", detail=f"invalid JSON at line {number}")
        if not isinstance(entry, dict):
            return Transcript("parse_failure", detail=f"non-object at line {number}")
        entries.append(entry)
    if not entries:
        return Transcript("unavailable", detail="empty source")
    if harness and harness not in {"claude", "codex"}:
        return Transcript("unsupported", harness=harness, detail="reader not implemented")
    is_codex = any(e.get("type") == "session_meta" for e in entries)
    is_claude = any(e.get("type") in {"user", "assistant"} and "sessionId" in e for e in entries)
    detected = "codex" if is_codex else "claude" if is_claude else ""
    if harness and detected and harness != detected:
        return Transcript("parse_failure", detail="harness/source identity conflict")
    selected = harness or detected
    try:
        if selected == "codex":
            return _read_codex(entries, session_id)
        if selected == "claude":
            return _read_claude(entries, session_id)
    except (TypeError, ValueError, KeyError):
        return Transcript("parse_failure", detail="invalid native record shape")
    return Transcript("unsupported", detail="unrecognized stored transcript format")


def _read_claude(entries: list[dict], session_id: str) -> Transcript:
    messages = []
    ids = set()
    for entry in entries:
        if entry.get("type") not in {"user", "assistant"}:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            return Transcript("parse_failure", detail="invalid Claude message")
        sid = native_id(entry.get("sessionId"))
        if sid:
            ids.add(sid)
        text = text_content(message.get("content")).strip()
        if text:
            messages.append((entry["type"], text))
    sid = native_id(session_id)
    if len(ids) > 1 or (sid and ids and sid not in ids):
        return Transcript("parse_failure", detail="ambiguous Claude session identity")
    sid = sid or next(iter(ids), "")
    return Transcript("ok", "claude", sid, source_format="claude-jsonl-v1",
                      messages=messages, entries=entries)


def _read_codex(entries: list[dict], session_id: str) -> Transcript:
    metas = [e.get("payload") for e in entries if e.get("type") == "session_meta"]
    if not metas or not all(isinstance(m, dict) for m in metas):
        return Transcript("unsupported", detail="missing Codex session_meta (stream JSON is not a rollout)")
    ids = {native_id(m.get("id")) for m in metas}
    parents = {native_id(m.get("forked_from_id")) for m in metas}
    if len(ids) != 1 or len(parents) != 1:
        return Transcript("parse_failure", detail="ambiguous Codex session metadata")
    sid = ids.pop()
    if session_id and native_id(session_id) != sid:
        return Transcript("parse_failure", detail="hook/rollout session identity conflict")
    parent = parents.pop()
    messages = []
    seen = set()
    events = [e.get("payload") for e in entries if e.get("type") == "event_msg"]
    if any(not isinstance(e, dict) for e in events):
        return Transcript("parse_failure", detail="invalid Codex event")
    # Two explicitly supported generations; never read response_item mirrors
    # (which also contain injected instructions masquerading as user messages).
    modern = any(e.get("type") == "item_completed" for e in events)
    if modern and any(e.get("type") in {"user_message", "agent_message"} for e in events):
        return Transcript("unsupported", detail="mixed Codex event generations require a reader upgrade")
    for event in events:
        role, text = "", ""
        if modern and event.get("type") == "item_completed":
            item = event.get("item")
            if not isinstance(item, dict):
                return Transcript("parse_failure", detail="invalid completed Codex item")
            role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(item.get("type"), "")
            if not role:
                continue
            # If copied parent events are present, their thread_id describes
            # that turn, not the current resume identity from session_meta.
            key = (event.get("thread_id"), event.get("turn_id"), item.get("id"))
            if item.get("id") and key in seen:
                continue
            seen.add(key)
            text = text_content(item.get("content"))
        elif not modern:
            role = {"user_message": "user", "agent_message": "assistant"}.get(event.get("type"), "")
            text = event.get("message", "")
        if role:
            if not isinstance(text, str) or not text.strip():
                return Transcript("parse_failure", detail="missing Codex conversational text")
            messages.append((role, text.strip()))
    if not messages:
        return Transcript("unsupported", detail="no supported Codex conversational events")
    return Transcript("ok", "codex", sid, parent, "codex-rollout-events-v1",
                      str(metas[-1].get("cli_version") or ""), messages, entries)
