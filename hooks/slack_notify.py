"""DEPRECATED compatibility shim — forwards to :mod:`notify_send` (Telegram).

The fleet's notification transport moved from Slack to Telegram in
fleet-config#540. This file stays behind **only** because ``hooks/`` is
junctioned live into ``~/.claude/hooks``: the rename takes effect fleet-wide the
instant the PR merges, against already-running sessions and scheduled jobs, and
one sister repo reaches this module by absolute path rather than by import:

* ``content-management/reporting_pipeline.py`` — ``_load_slack_notify()`` loads
  ``~/.claude/hooks/slack_notify.py`` off disk and calls
  ``notify(message, channel=<slack id>)``.
* ``content-management``'s ``schedule-autoheal`` skill (and its duplicated
  ``.agents/`` copy) shells out to ``slack_notify.py --channel <id> --text``.

Both are tracked by the pointer issue filed on that repo. **Delete this file
once they are migrated** — it is scaffolding, not API.

A Slack channel id means nothing to Telegram, so rather than sending it and
getting a guaranteed ``chat not found``, a Slack-shaped id is dropped and the
ping is routed by category through the normal resolver. That is deliberately
*loud*: these are failure alerts, and dropping them silently would be worse than
delivering them to the fleet attention chat with a warning naming the ignored id.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify_send  # noqa: E402

logger = logging.getLogger("slack_notify")

# Slack ids: C=channel, G=private group, D=DM, U=user. Live ids are 9+ chars.
_SLACK_ID_RE = re.compile(r"^[CDGU][A-Z0-9]{7,}$")

_DEPRECATION = (
    "slack_notify.py is deprecated (fleet-config#540) - call notify_send.py instead"
)


def _translate(target: Optional[str]) -> Optional[str]:
    """Return a usable Telegram chat id from a legacy ``--channel`` value, or None.

    A Slack-shaped id resolves to ``None`` so the caller falls back to
    category routing; anything else (already a chat id) passes through.
    """
    raw = (target or "").strip()
    if not raw:
        return None
    if _SLACK_ID_RE.match(raw):
        logger.warning(
            "[!] ignoring Slack channel id %s - the fleet moved to Telegram "
            "(fleet-config#540); routing by category instead",
            raw,
        )
        return None
    return raw


def _fallback_chat(category: str = "attention") -> Optional[str]:
    """Resolve a chat by intent category, for a legacy caller that named a Slack id.

    ``attention`` is the right default: every legacy caller left is a
    pipeline-failure alert, which is come-look by definition.
    """
    try:
        import _lib
        chat, _name = _lib.resolve_notify_target(Path(os.getcwd()), category=category)
        return chat
    except Exception:  # pragma: no cover - defensive: never break a ping on config error
        return None


def notify(
    text: str,
    channel: Optional[str] = None,
    token: Optional[str] = None,
    thread_ts: Optional[str] = None,  # noqa: ARG001 - accepted and ignored (no Telegram equivalent)
    *,
    user: Optional[str] = None,  # noqa: ARG001 - accepted and ignored (mentions retired)
    mention: Optional[bool] = None,  # noqa: ARG001 - accepted and ignored (mentions retired)
    chat: Optional[str] = None,
) -> bool:
    """Legacy signature, Telegram delivery. Never raises; returns False on failure.

    ``thread_ts``, ``user`` and ``mention`` are accepted so an existing call site
    keeps working, and ignored: Telegram has no threads, and it pushes every
    message to a chat you are in, which is what made the ``@mention`` machinery
    redundant.
    """
    logger.warning("[!] %s", _DEPRECATION)
    target = _translate(chat or channel) or _fallback_chat()
    if not target:
        logger.error("[X] No Telegram chat resolved - alert not sent.")
        return False
    return notify_send.notify(text, chat=target, token=token)


def upload_file(
    path: str,
    channel: Optional[str] = None,
    token: Optional[str] = None,
    *,
    title: Optional[str] = None,
    comment: Optional[str] = None,
    chat: Optional[str] = None,
) -> bool:
    """Legacy signature, Telegram ``sendDocument`` delivery. Never raises."""
    logger.warning("[!] %s", _DEPRECATION)
    target = _translate(chat or channel) or _fallback_chat("log")
    if not target:
        logger.error("[X] No Telegram chat resolved - file not sent.")
        return False
    return notify_send.upload_file(path, chat=target, token=token, title=title, comment=comment)


def main(argv: Optional[List[str]] = None) -> int:
    """Legacy CLI: accept the old flags, deliver over Telegram."""
    parser = argparse.ArgumentParser(description=_DEPRECATION)
    parser.add_argument("--channel")
    parser.add_argument("--category", choices=["attention", "log"])
    parser.add_argument("--text")
    parser.add_argument("--file")
    parser.add_argument("--title")
    parser.add_argument("--thread-ts")  # accepted, ignored
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mention", dest="mention", action="store_true", default=None)
    group.add_argument("--no-mention", dest="mention", action="store_false")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logger.warning("[!] %s", _DEPRECATION)

    forwarded: List[str] = []
    chat = _translate(args.channel)
    if chat:
        forwarded += ["--chat", chat]
    # A legacy --channel that was a Slack id leaves no --chat, so --category (or
    # its absence, which the resolver answers with the [global] fallback) decides.
    if args.category:
        forwarded += ["--category", args.category]
    for flag, value in (("--text", args.text), ("--file", args.file), ("--title", args.title)):
        if value:
            forwarded += [flag, value]
    return notify_send.main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
