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
from typing import List, Optional

logger = logging.getLogger("slack_notify")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
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


def notify(
    text: str,
    channel: str,
    token: Optional[str] = None,
    thread_ts: Optional[str] = None,
) -> bool:
    """Post ``text`` to ``channel`` as the Slack bot. Return True on success.

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


def _read_text(arg_text: Optional[str]) -> str:
    """Message text from ``--text`` or, failing that, piped stdin."""
    if arg_text:
        return arg_text
    if not sys.stdin.isatty():
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
    parser.add_argument("--text", help="Message text. If omitted, read from stdin.")
    parser.add_argument(
        "--thread-ts", help="Optional parent message ts to reply in-thread."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    text = _read_text(args.text)
    if not text.strip():
        logger.error("❌ No message text (pass --text or pipe via stdin).")
        return 2

    ok = notify(text, channel=args.channel, thread_ts=args.thread_ts)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
