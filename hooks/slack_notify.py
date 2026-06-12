"""Fleet-wide Slack notifier — fire a real, bot-identity Slack notification.

This is the **transport** for machine→human alerts across the fleet. Any skill,
hook, or unattended job — in any project, with zero install — can reach it two
ways:

* As a CLI (e.g. from a skill's instructions or a `.bat` job)::

      py ~/.claude/hooks/slack_notify.py --channel C0123ABCD --text "stuck, come look"
      echo "long body" | py ~/.claude/hooks/slack_notify.py --channel C0123ABCD

* As an import (from another hook / Python tool)::

      import slack_notify
      slack_notify.notify("done", channel="C0123ABCD")

Why a bot and not the claude.ai Slack MCP connector: that connector posts *as
the user*, and Slack never notifies you about your own messages — so escalation
pings land silently. A separate bot identity (`chat.postMessage` with an
`xoxb-` token) actually triggers a notification. See `docs/slack-workflow.md`.

The bot token is read from the ``SLACK_BOT_TOKEN`` environment variable, which
lives in ``~/.claude/settings.json``'s ``env`` block (never committed). The HTTP
call uses **stdlib urllib** on purpose: hooks run on system Python with no venv,
so there is no `requests` to rely on.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

logger = logging.getLogger("slack_notify")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
SLACK_COMPLETE_UPLOAD_URL = "https://slack.com/api/files.completeUploadExternal"
TOKEN_ENV_VAR = "SLACK_BOT_TOKEN"
_ARCHIVE_RE = re.compile(r"/archives/([A-Z0-9]+)", re.IGNORECASE)


def parse_channel(raw: str) -> str:
    """Normalise a channel reference to a bare Slack id.

    Accepts a bare channel id (``C0123ABCD``), a user id for a DM
    (``U0123ABCD``), or a pasted archive URL
    (``https://x.slack.com/archives/C0123ABCD``) and returns the id. Returns the
    stripped input unchanged when no archive pattern matches.
    """
    raw = (raw or "").strip()
    match = _ARCHIVE_RE.search(raw)
    return match.group(1) if match else raw


def _global_mention_toggle() -> bool:
    """Read the ``[global] slack_notify_mention`` toggle (default off).

    Imported lazily inside a try/except so this transport module stays
    import-safe and never crashes a ping if ``_lib`` / the config is unreadable —
    a missing toggle simply means "don't mention".
    """
    try:
        import _lib  # local import keeps the transport dependency-free at module load
        return bool(_lib.load_registry().globals.slack_notify_mention)
    except Exception:  # pragma: no cover - defensive: never break a ping on config error
        return False


def _resolve_mention(override: Optional[bool]) -> bool:
    """Whether to @mention: an explicit ``override`` wins, else the global toggle."""
    return override if override is not None else _global_mention_toggle()


def _mention_prefix(user: Optional[str], enabled: bool) -> str:
    """``'<@user> '`` when mentioning is enabled and a user id is known, else ``''``.

    The single source of the @mention decision: every fleet ping flows through
    :func:`notify`, so no caller hand-assembles ``<@U…>``. Mentioning defaults
    **off** (the ``[global] slack_notify_mention`` toggle) because the target
    channel delivers a mobile push regardless — the tag was redundant noise.
    """
    return f"<@{user}> " if (enabled and user) else ""


def notify(
    text: str,
    channel: str,
    token: Optional[str] = None,
    thread_ts: Optional[str] = None,
    *,
    user: Optional[str] = None,
    mention: Optional[bool] = None,
) -> bool:
    """Post ``text`` to ``channel`` as the Slack bot. Return True on success.

    When ``user`` is given and mentioning is enabled, a ``<@user> `` prefix is
    prepended **here** — the one place the mention decision lives. ``mention``
    overrides per-call (``True``/``False``); left ``None`` it follows the
    ``[global] slack_notify_mention`` toggle (default off).

    Never raises. A missing token, a malformed channel, a network failure, or a
    Slack API error is logged and reported as ``False`` so an unattended caller
    keeps running instead of crashing mid-job.
    """
    token = token or os.getenv(TOKEN_ENV_VAR)
    if not token:
        logger.error("❌ %s not set — cannot send Slack notification.", TOKEN_ENV_VAR)
        return False

    channel = parse_channel(channel)
    if not channel:
        logger.error("❌ No Slack channel given — cannot send notification.")
        return False
    if not text or not text.strip():
        logger.error("❌ Empty message text — nothing to send.")
        return False

    text = _mention_prefix(user, _resolve_mention(mention)) + text
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    request = urllib.request.Request(
        SLACK_POST_MESSAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logger.error("❌ Slack request failed: %s", exc)
        return False
    except (ValueError, OSError) as exc:  # unreadable / non-JSON response
        logger.error("❌ Slack response unreadable: %s", exc)
        return False

    if not body.get("ok"):
        logger.error("❌ Slack API error: %s", body.get("error", "unknown"))
        return False

    logger.info("✅ Slack notification posted to %s", channel)
    return True


def _slack_api(url: str, token: str, payload: dict) -> dict:
    """POST a JSON body to a Slack Web API method; return the parsed response.

    Raises on transport/JSON errors so :func:`upload_file` can convert them into
    a logged ``False`` — matching :func:`notify`'s never-raise contract.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(
    path: str,
    channel: str,
    token: Optional[str] = None,
    *,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> bool:
    """Upload a file (e.g. the system-map PNG) to ``channel`` as the bot.

    Uses Slack's current external-upload flow (``files.upload`` is retired):
    ``getUploadURLExternal`` → POST the bytes → ``completeUploadExternal``. The
    optional ``comment`` becomes the message that carries the file. Never raises —
    a missing token/file or any API error is logged and reported as ``False`` so
    an unattended caller keeps running.
    """
    token = token or os.getenv(TOKEN_ENV_VAR)
    if not token:
        logger.error("❌ %s not set — cannot upload to Slack.", TOKEN_ENV_VAR)
        return False
    channel = parse_channel(channel)
    if not channel:
        logger.error("❌ No Slack channel given — cannot upload.")
        return False
    file_path = Path(path)
    if not file_path.is_file():
        logger.error("❌ File not found: %s", path)
        return False

    data = file_path.read_bytes()
    filename = file_path.name
    try:
        # 1. reserve an upload URL
        q = urlencode({"filename": filename, "length": len(data)})
        req = urllib.request.Request(
            f"{SLACK_GET_UPLOAD_URL}?{q}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            got = json.loads(resp.read().decode("utf-8"))
        if not got.get("ok"):
            logger.error("❌ getUploadURLExternal failed: %s", got.get("error"))
            return False
        upload_url, file_id = got["upload_url"], got["file_id"]

        # 2. POST the raw bytes to the one-time URL (multipart/form-data)
        boundary = f"----slacknotify{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        up = urllib.request.Request(
            upload_url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(up, timeout=60):
            pass  # a 200 with body "OK" means the bytes landed

        # 3. publish the uploaded file into the channel
        payload = {
            "files": [{"id": file_id, "title": title or filename}],
            "channel_id": channel,
        }
        if comment and comment.strip():
            payload["initial_comment"] = comment
        done = _slack_api(SLACK_COMPLETE_UPLOAD_URL, token, payload)
    except urllib.error.URLError as exc:
        logger.error("❌ Slack upload request failed: %s", exc)
        return False
    except (ValueError, OSError, KeyError) as exc:
        logger.error("❌ Slack upload response unreadable: %s", exc)
        return False

    if not done.get("ok"):
        logger.error("❌ completeUploadExternal failed: %s", done.get("error", "unknown"))
        return False
    logger.info("✅ Slack file uploaded to %s", channel)
    return True


def _read_text(arg_text: Optional[str]) -> str:
    """Message text from ``--text`` or, failing that, piped stdin.

    Reads piped stdin as raw bytes and decodes UTF-8 explicitly: on Windows
    ``sys.stdin``'s default cp1252 mis-decodes a UTF-8 pipe (emoji, em-dash,
    bullet), and the misread text then double-encodes on the way to Slack
    (🧠 -> ``ðŸ§ ``, — -> ``â€"``). ``--text`` comes from argv already decoded,
    so it is left alone.
    """
    if arg_text:
        return arg_text
    if not sys.stdin.isatty():
        raw = getattr(sys.stdin, "buffer", None)
        if raw is not None:
            return raw.read().decode("utf-8", errors="replace")
        return sys.stdin.read()
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a Slack notification as the fleet bot."
    )
    parser.add_argument(
        "--channel",
        required=True,
        help="Channel id, user id (for a DM), or a pasted archive URL.",
    )
    parser.add_argument("--text", help="Message text (or caption with --file). If omitted, read from stdin.")
    parser.add_argument(
        "--file", help="Path to a file to upload (e.g. a PNG). --text becomes its caption.",
    )
    parser.add_argument("--title", help="Optional title for an uploaded --file.")
    parser.add_argument(
        "--thread-ts", help="Optional parent message ts to reply in-thread."
    )
    mention = parser.add_mutually_exclusive_group()
    mention.add_argument(
        "--mention", dest="mention", action="store_true", default=None,
        help="@mention the configured user (id resolved from projects.toml). "
             "Default follows the [global] slack_notify_mention toggle.",
    )
    mention.add_argument(
        "--no-mention", dest="mention", action="store_false",
        help="Never @mention, regardless of the global toggle.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    if args.file:
        # Caption follows the same rule as a plain message: --text wins, else
        # piped stdin (UTF-8). Lets a multi-line digest ride along as the file's
        # comment without fragile shell quoting. Existing callers pass --text, so
        # _read_text returns immediately and never touches stdin for them.
        ok = upload_file(
            args.file, channel=args.channel, title=args.title,
            comment=_read_text(args.text) or None,
        )
        return 0 if ok else 1

    text = _read_text(args.text)
    if not text.strip():
        logger.error("❌ No message text (pass --text or pipe via stdin).")
        return 2

    # Resolve the user id to mention from projects.toml — never hardcoded — so a
    # manual caller can't drift by hand-typing a stale ``<@U…>`` into --text.
    user: Optional[str] = None
    try:
        import _lib  # local import keeps the transport import-safe if _lib is absent
        _channel, user, _name = _lib.resolve_slack_target(Path(os.getcwd()))
    except Exception:  # pragma: no cover - defensive: still send without a mention
        user = None

    ok = notify(
        text, channel=args.channel, thread_ts=args.thread_ts,
        user=user, mention=args.mention,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
