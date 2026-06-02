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
from pathlib import Path
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
