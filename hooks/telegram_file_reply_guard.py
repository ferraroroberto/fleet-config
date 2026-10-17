"""Refuse a Telegram file reply outside the sender's private chat (fleet-config#1478).

A project can let its Telegram channel sessions send files without a
permission prompt, through an allow rule on the channel plugin's ``reply``
tool (life-os#265). An allow rule can't see arguments, so on its own nothing
stops a ``reply`` carrying ``files`` from going to a group or to someone
else's chat. This guard enforces the private-chat half: a ``reply`` with
``files`` passes only when its ``chat_id`` is a positive id equal to the
``user_id`` of the inbound channel message that triggered the turn, and that
message itself came from a private chat (``chat_id == user_id``; a group's
``chat_id`` is a different, negative number). Text-only replies pass
untouched. Opt-in per project: ``telegram_file_guard = true`` in
``hooks/projects.toml``; every other project is a silent no-op.

Where the sender comes from. The ``PreToolUse`` payload carries no channel
metadata (its keys: ``session_id``, ``transcript_path``, ``cwd``,
``hook_event_name``, ``permission_mode``, ``tool_name``, ``tool_input``,
``tool_use_id``, ``prompt_id``, ``effort``). The harness records each inbound
channel message in the transcript as a ``user`` entry with ``origin: {"kind":
"channel", "server": "plugin:telegram:telegram"}`` and a string content that
opens with the plugin's tag, ``<channel source="plugin:telegram:telegram"
chat_id="…" message_id="…" user="…" user_id="…" ts="…">``. The plugin fills
``chat_id``/``user_id`` from Telegram's own ``ctx.chat.id``/``ctx.from.id``;
``origin`` is set by the harness, so a terminal prompt or a message body that
types a fake tag can't pass as one. The trigger is the newest entry whose
``origin.kind`` is ``channel`` or ``human`` — a terminal prompt after the
channel message makes the sender unknown, and unknown is refused.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

REPLY_TOOL = "mcp__plugin_telegram_telegram__reply"
CHANNEL_SERVER = "plugin:telegram:telegram"
PROJECT_KEY = "telegram_file_guard"

_POSITIVE_ID = re.compile(r"[1-9][0-9]*")
_CHANNEL_TAG = re.compile(r"\A\s*<channel\s([^>]*)>")
_TAG_ATTR = re.compile(r'([a-z_]+)="([^"]*)"')
_READ_BLOCK = 1 << 16


class Sender(NamedTuple):
    chat_id: str
    user_id: str


def _reverse_lines(path: Path) -> Iterator[bytes]:
    """The file's lines, newest first, read in blocks from the end — a channel
    session's transcript runs to megabytes and the trigger sits near its end."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        tail = b""
        while position > 0:
            step = min(_READ_BLOCK, position)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + tail).split(b"\n")
            tail = lines.pop(0)
            yield from reversed(lines)
        yield tail


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def trigger_sender(transcript_path: object) -> tuple[Optional[Sender], str]:
    """The Telegram sender of the newest person-sent prompt, or (None, why not)."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None, "the hook payload carries no transcript_path"
    try:
        for raw in _reverse_lines(Path(transcript_path)):
            try:
                entry = json.loads(raw)
            except ValueError:
                continue  # blank or partially written line
            if not isinstance(entry, dict) or entry.get("type") != "user":
                continue
            origin = entry.get("origin")
            kind = origin.get("kind") if isinstance(origin, dict) else None
            if kind == "human":
                return None, "the newest prompt was typed in the session, not sent from Telegram"
            if kind != "channel":
                continue
            if origin.get("server") != CHANNEL_SERVER:
                return None, f"the newest prompt came from channel {origin.get('server')!r}, not Telegram"
            tag = _CHANNEL_TAG.match(_text((entry.get("message") or {}).get("content")))
            attrs = dict(_TAG_ATTR.findall(tag.group(1))) if tag else {}
            chat_id, user_id = attrs.get("chat_id", ""), attrs.get("user_id", "")
            if attrs.get("source") != CHANNEL_SERVER or not chat_id or not _POSITIVE_ID.fullmatch(user_id):
                return None, "the newest Telegram message has no readable chat_id/user_id tag"
            return Sender(chat_id, user_id), ""
    except OSError as exc:
        return None, f"the transcript could not be read ({exc.__class__.__name__})"
    return None, "no Telegram message was found in this session's transcript"


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != REPLY_TOOL:
        _lib.allow()
    project = _lib.detect_project(_lib.cwd(payload))
    if project is None or not project.extra.get(PROJECT_KEY):
        _lib.allow()  # project has not opted in (`telegram_file_guard` in projects.toml)
    args = _lib.tool_input(payload)
    if not args.get("files"):
        _lib.allow()  # text-only reply

    refuse = ("Blocked: a Telegram reply with files goes only to the requester's own private chat "
              "(fleet-config#1478). {why}. Send no file; if the request came from a group, answer there "
              "in one line that files are sent only in a private chat.")
    chat_id = str(args.get("chat_id", "")).strip()
    if not _POSITIVE_ID.fullmatch(chat_id):
        _lib.block(refuse.format(why="This chat_id is not a private chat (a group's id is negative)"))
    sender, why_unknown = trigger_sender(payload.get("transcript_path"))
    if sender is None:
        _lib.block(refuse.format(why=f"The sender could not be established: {why_unknown}"))
    if sender.chat_id != sender.user_id:
        _lib.block(refuse.format(why="The request came from a group chat, not a private one"))
    if chat_id != sender.user_id:
        _lib.block(refuse.format(why="This chat_id is not the chat of the person who sent the request"))
    _lib.allow()


if __name__ == "__main__":
    main()
