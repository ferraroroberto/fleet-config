"""Fleet-wide Telegram notifier - fire a real, bot-identity notification.

This is the **transport** for machine->human alerts across the fleet. Any skill,
hook, or unattended job - in any project, with zero install - can reach it two
ways:

* As a CLI (e.g. from a skill's instructions or a `.bat` job) - invoke the
  resolved Python path directly, not a bare ``py``/``python`` (not reliably on
  ``PATH`` on this machine; see ``_lib.find_python_executable``)::

      E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category attention --text "stuck, come look"
      echo "long body" | E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category log

* As an import (from another hook / Python tool)::

      import notify_send
      notify_send.notify("done", chat="-1001234567890")

Replaces the Slack transport this repo carried until fleet-config#540. The
contract is deliberately unchanged - never raises, reports failure as ``False``
/ a non-zero exit, stdlib ``urllib`` only (hooks run on system Python with no
venv, so there is no ``requests`` to rely on).

The bot token is resolved in three steps: an explicit ``token=`` argument, then
the ``TELEGRAM_BOT_TOKEN`` environment variable, then - as a fallback - a direct
read of ``~/.claude/settings.json``'s ``env`` block (never committed). Claude
Code injects that ``env`` block into everything it spawns, but other launchers
(Pi, Codex, GitHub Copilot, a bare terminal, a scheduled ``.bat``) don't, so the
file fallback is what makes this transport truly launcher-agnostic.

**No ``parse_mode``.** Every message goes as plain text on purpose. Telegram
rejects a whole message whose HTML/Markdown does not parse, and the bodies this
carries are digests full of code fences, tables, angle brackets, ampersands and
unbalanced asterisks - exactly the input that would make a formatted send fail
as a unit. A notification that arrives unstyled beats one that silently does not
arrive, so Slack's link markup is flattened here (:func:`_flatten_markup`)
rather than translated into a second dialect that can also fail to parse.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("notify_send")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
SETTINGS_JSON_PATH = Path.home() / ".claude" / "settings.json"
SETTINGS_JSON_PATH_ENV_VAR = "CLAUDE_SETTINGS_JSON_PATH"

# Bot API hard limits. Exceeding either is a rejected send, not a truncated one,
# so both are enforced here in the transport rather than at 18 call sites.
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024

# Slack mrkdwn link: <https://example.com|label>
_SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)\|([^>]+)>")
# Slack bare autolink: <https://example.com>
_SLACK_BARE_LINK_RE = re.compile(r"<(https?://[^>|]+)>")


def _token_from_settings() -> Optional[str]:
    """Read ``TELEGRAM_BOT_TOKEN`` from ``~/.claude/settings.json``'s ``env`` block.

    The token lives in that file's ``env`` block, which Claude Code injects into
    the environment of everything it spawns - but other launchers (Pi, Codex,
    GitHub Copilot, a bare terminal, a scheduled ``.bat``) don't read it, so the
    env var is absent there. Reading the file directly makes the transport
    launcher-agnostic. Never raises: a missing/unreadable/malformed file or a
    blank token just means "no token from this source".

    ``CLAUDE_SETTINGS_JSON_PATH`` overrides the path so acceptance tests can
    point this at a nonexistent file and get a true graceful-fail. Stripping
    ``TELEGRAM_BOT_TOKEN`` from the subprocess env alone doesn't work here, since
    this fallback reads the file straight off disk via ``Path.home()``, which
    on Windows resolves through the OS profile API and ignores an env dict
    that merely omits ``USERPROFILE``.
    """
    path = Path(os.environ.get(SETTINGS_JSON_PATH_ENV_VAR) or SETTINGS_JSON_PATH)
    try:
        with path.open("r", encoding="utf-8") as fh:
            settings = json.load(fh)
        token = settings.get("env", {}).get(TOKEN_ENV_VAR)
        return token or None
    except (OSError, ValueError, AttributeError):
        return None


def _resolve_token(token: Optional[str]) -> Optional[str]:
    """Resolve the bot token: explicit arg -> env var -> settings.json fallback.

    Single source of token resolution for both :func:`notify` and
    :func:`upload_file` so every launcher (Claude Code, Pi, Codex, Copilot, a
    bare terminal, a scheduled ``.bat``) finds the token identically.
    """
    return token or os.getenv(TOKEN_ENV_VAR) or _token_from_settings()


def parse_chat(raw: str) -> str:
    """Normalise a chat reference to what the Bot API accepts.

    Accepts a numeric chat id (``-1001234567890`` for a supergroup/channel, a
    positive id for a private chat) or an ``@publicname``. Returns the stripped
    input; a ``https://t.me/<name>`` link is reduced to ``@<name>``.
    """
    raw = (raw or "").strip()
    match = re.fullmatch(r"https?://t\.me/([A-Za-z0-9_]{5,})", raw)
    if match:
        return "@" + match.group(1)
    return raw


def _flatten_markup(text: str) -> str:
    """Reduce Slack mrkdwn link syntax to plain text Telegram renders sanely.

    A labelled link becomes ``label: url`` and a bare autolink becomes the bare
    url. Without this, ``notify_on_idle``'s board deep link arrives as literal
    angle-bracket noise. Applied in the transport so no caller has to know which
    chat system it is talking to.
    """
    text = _SLACK_LINK_RE.sub(r"\2: \1", text)
    return _SLACK_BARE_LINK_RE.sub(r"\1", text)


def _chunks(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    """Split ``text`` into Bot-API-sized pieces, preferring line boundaries.

    A body under the limit is returned as a single unmarked chunk - the common
    case must look exactly like it always did. Anything longer is split on
    newlines where possible (hard-split only for a single line that is itself
    over the limit) and each piece carries an ASCII ``[i/n]`` marker so a
    truncated-looking digest is recognisable as continued rather than lost.
    """
    if len(text) <= limit:
        return [text]

    marker_budget = 12  # "\n\n[99/99]" with room to spare
    room = limit - marker_budget
    pieces: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > room:  # a single over-long line
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:room])
            line = line[room:]
        if len(current) + len(line) > room:
            pieces.append(current)
            current = line
        else:
            current += line
    if current:
        pieces.append(current)

    total = len(pieces)
    return ["{0}\n\n[{1}/{2}]".format(p.rstrip(), i, total) for i, p in enumerate(pieces, 1)]


def _api(method: str, token: str, payload: dict, timeout: int = 15) -> dict:
    """POST a JSON body to a Bot API method; return the parsed response.

    Raises on transport/JSON errors so callers convert them into a logged
    ``False`` - matching :func:`notify`'s never-raise contract. An HTTP error
    still carries a JSON body describing the rejection, so it is read rather
    than discarded.
    """
    request = urllib.request.Request(
        TELEGRAM_API.format(token=token, method=method),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8", errors="replace"))


def _log_rejection(method: str, body: dict) -> None:
    """Log a Bot API rejection, naming a group->supergroup migration explicitly.

    A basic group that Telegram upgrades to a supergroup gets a **new** chat id
    and every send to the old one fails from then on. Surfacing
    ``migrate_to_chat_id`` here is what makes that a one-line fix instead of a
    silent, permanent outage (fleet-config#540).
    """
    logger.error("[X] Telegram %s rejected: %s", method, body.get("description", "unknown"))
    migrated = (body.get("parameters") or {}).get("migrate_to_chat_id")
    if migrated:
        logger.error("[X] chat was upgraded to a supergroup - update the id to %s", migrated)


def notify(text: str, chat: str, token: Optional[str] = None) -> bool:
    """Post ``text`` to ``chat`` as the Telegram bot. Return True on success.

    Bodies over :data:`MESSAGE_LIMIT` are split into numbered messages rather
    than rejected. Returns True only if **every** chunk was accepted, so a
    partially-delivered digest reports failure.

    Never raises. A missing token, a malformed chat id, a network failure, or a
    Bot API error is logged and reported as ``False`` so an unattended caller
    keeps running instead of crashing mid-job.
    """
    token = _resolve_token(token)
    if not token:
        logger.error("[X] %s not set - cannot send Telegram notification.", TOKEN_ENV_VAR)
        return False

    chat = parse_chat(chat)
    if not chat:
        logger.error("[X] No Telegram chat given - cannot send notification.")
        return False
    if not text or not text.strip():
        logger.error("[X] Empty message text - nothing to send.")
        return False

    parts = _chunks(_flatten_markup(text))
    for part in parts:
        try:
            body = _api("sendMessage", token, {"chat_id": chat, "text": part})
        except urllib.error.URLError as exc:
            logger.error("[X] Telegram request failed: %s", exc)
            return False
        except (ValueError, OSError) as exc:  # unreadable / non-JSON response
            logger.error("[X] Telegram response unreadable: %s", exc)
            return False
        if not body.get("ok"):
            _log_rejection("sendMessage", body)
            return False

    suffix = " ({0} parts)".format(len(parts)) if len(parts) > 1 else ""
    logger.info("[OK] Telegram notification posted to %s%s", chat, suffix)
    return True


def _multipart(fields: dict, filename: str, payload: bytes) -> Tuple[bytes, str]:
    """Build a ``multipart/form-data`` body carrying one document.

    Returns ``(body, content_type)``.
    """
    boundary = "----notifysend" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for key, value in fields.items():
        header = "--{0}\r\nContent-Disposition: form-data; name=\"{1}\"\r\n\r\n{2}\r\n".format(
            boundary, key, value
        )
        chunks.append(header.encode("utf-8"))
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    doc_header = (
        "--{0}\r\n"
        "Content-Disposition: form-data; name=\"document\"; filename=\"{1}\"\r\n"
        "Content-Type: {2}\r\n\r\n"
    ).format(boundary, filename, ctype)
    chunks.append(doc_header.encode("utf-8"))
    chunks.append(payload)
    chunks.append("\r\n--{0}--\r\n".format(boundary).encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


def upload_file(
    path: str,
    chat: str,
    token: Optional[str] = None,
    *,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> bool:
    """Upload a file (e.g. the system-map PNG) to ``chat`` as the bot.

    Always ``sendDocument``, never ``sendPhoto``: ``sendPhoto`` re-encodes to
    JPEG and caps dimensions, which turns the small labels on
    ``architecture/config-map.png`` into unreadable mush. ``sendDocument`` is
    byte-exact (verified against that 1,138,704-byte PNG, in and out) and
    allows 50 MB.

    ``title`` and ``comment`` are joined into the document's caption. Telegram
    caps a caption at :data:`CAPTION_LIMIT` - a quarter of a message - and the
    weekly digests already exceed it, so an over-long body is sent as follow-up
    **messages** instead of being silently dropped by the API.

    Never raises - a missing token/file or any API error is logged and reported
    as ``False`` so an unattended caller keeps running.
    """
    token = _resolve_token(token)
    if not token:
        logger.error("[X] %s not set - cannot upload to Telegram.", TOKEN_ENV_VAR)
        return False
    chat = parse_chat(chat)
    if not chat:
        logger.error("[X] No Telegram chat given - cannot upload.")
        return False
    file_path = Path(path)
    if not file_path.is_file():
        logger.error("[X] File not found: %s", path)
        return False

    body_text = _flatten_markup("\n\n".join(p for p in (title, comment) if p and p.strip()))
    caption = body_text if len(body_text) <= CAPTION_LIMIT else ""

    fields = {"chat_id": chat}
    if caption:
        fields["caption"] = caption
    try:
        data, content_type = _multipart(fields, file_path.name, file_path.read_bytes())
        request = urllib.request.Request(
            TELEGRAM_API.format(token=token, method="sendDocument"),
            data=data,
            method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                done = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            done = json.loads(exc.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        logger.error("[X] Telegram upload request failed: %s", exc)
        return False
    except (ValueError, OSError, KeyError) as exc:
        logger.error("[X] Telegram upload response unreadable: %s", exc)
        return False

    if not done.get("ok"):
        _log_rejection("sendDocument", done)
        return False
    logger.info("[OK] Telegram file uploaded to %s", chat)

    # The caption did not fit - deliver the body as its own message(s) rather
    # than losing it. Reported as a failure if it does not land, because the
    # digest *is* the payload for insights-weekly / fleet-health.
    if body_text and not caption:
        return notify(body_text, chat=chat, token=token)
    return True


def _repair(text: str) -> str:
    """``_lib.repair_mojibake`` applied defensively.

    Imported lazily inside a try/except: this transport must stay usable with
    nothing but its own directory importable, so an absent ``_lib`` degrades to
    "send the text as received" rather than crashing a ping.
    """
    try:
        import _lib  # local import keeps the transport dependency-free at module load
        return _lib.repair_mojibake(text) or text
    except Exception:  # pragma: no cover - defensive: never break a ping on a repair error
        return text


def _read_text(arg_text: Optional[str]) -> str:
    """Message text from ``--text`` or, failing that, piped stdin.

    Reads piped stdin as raw bytes and decodes UTF-8 explicitly: on Windows
    ``sys.stdin``'s default cp1252 mis-decodes a UTF-8 pipe (emoji, em-dash,
    bullet), and the misread text then double-encodes on the way out.

    ``--text`` is decoded by the time argv reaches Python, but *what* it was
    decoded from is not ours to trust - the harness -> shell -> CreateProcess leg
    can hand us an already-mangled string (fleet-config#507), so it goes through
    :func:`_lib.repair_mojibake` for the recoverable half of exactly the same
    corruption. No separator-token expansion here: a ``--text`` body carries
    markdown, where a literal ``|`` is a table cell, not a separator.
    """
    if arg_text:
        return _repair(arg_text)
    if not sys.stdin.isatty():
        raw = getattr(sys.stdin, "buffer", None)
        if raw is not None:
            return raw.read().decode("utf-8", errors="replace")
        return sys.stdin.read()
    return ""


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Split out so the deprecated Slack shim can reuse it."""
    parser = argparse.ArgumentParser(
        description="Send a Telegram notification as the fleet bot."
    )
    parser.add_argument(
        "--chat",
        help="Telegram chat id (e.g. -1001234567890) or @publicname. "
             "Optional when --category is given.",
    )
    parser.add_argument(
        "--category",
        choices=["attention", "log"],
        help="Resolve the destination chat from projects.toml by intent "
             "(issue #139) instead of hardcoding an id: 'attention' (come-look) "
             "or 'log' (activity record). Ignored when --chat is given.",
    )
    parser.add_argument("--text", help="Message text (or caption with --file). If omitted, read from stdin.")
    parser.add_argument(
        "--file", help="Path to a file to upload (e.g. a PNG). --text becomes its caption.",
    )
    parser.add_argument("--title", help="Optional title line for an uploaded --file.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    # Resolve the destination chat from projects.toml so nothing is hardcoded.
    # --chat wins; otherwise route by --category through the shared resolver
    # (issue #139).
    chat: Optional[str] = args.chat
    try:
        import _lib  # local import keeps the transport import-safe if _lib is absent
        resolved_chat, _name = _lib.resolve_notify_target(
            Path(os.getcwd()), category=args.category
        )
        chat = chat or resolved_chat
    except Exception:  # pragma: no cover - defensive: still send with an explicit --chat
        pass

    if not chat:
        logger.error("[X] No chat: pass --chat or --category.")
        return 2

    if args.file:
        # Caption follows the same rule as a plain message: --text wins, else
        # piped stdin (UTF-8). Lets a multi-line digest ride along without
        # fragile shell quoting.
        ok = upload_file(
            args.file, chat=chat, title=args.title,
            comment=_read_text(args.text) or None,
        )
        return 0 if ok else 1

    text = _read_text(args.text)
    if not text.strip():
        logger.error("[X] No message text (pass --text or pipe via stdin).")
        return 2

    return 0 if notify(text, chat=chat) else 1


if __name__ == "__main__":
    sys.exit(main())
