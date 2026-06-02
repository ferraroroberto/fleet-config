"""Ping Slack when a live session needs attention — so you can stop babysitting.

Wired to Claude Code's ``Notification`` event. It pings only on the
``permission_prompt`` sub-type (a permission gate or an ``AskUserQuestion`` —
the "come look, I'm blocked" push) and **no-ops on ``idle_prompt``** (the 💤
"gone idle" nag is noise). It rides the `slack_notify` transport, so an AFK
human gets a real phone notification instead of a desktop toast nobody sees.

**Opt-in, default off.** It does nothing unless the current project declares a
``slack_notify_channel`` in ``hooks/projects.toml`` (or a ``[global]
slack_notify_channel`` fallback is set). That keeps notification noise off by
default and lets you flip it on per project. See `docs/slack-workflow.md`.

A Notification hook only advises — it never blocks, and always exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import slack_notify  # noqa: E402

# Glanceable icon per notification kind. A real permission gate (action needed)
# reads differently from an idle wait; anything else falls back to the bell.
_ICONS = {"permission_prompt": "🔔", "idle_prompt": "💤"}

# Only the head of the transcript is read for the bridge link — the bridge-session
# metadata is written at session start, so it's always near the top.
_TRANSCRIPT_HEAD_BYTES = 65536


def classify(payload: dict) -> tuple[str, str]:
    """Map a Notification payload to an (icon, text) pair for the Slack ping.

    The payload only reliably carries ``notification_type`` and a generic
    ``message`` — in a remote/bridge session the tool being gated lives in the
    cloud transcript, not locally, so finer classification (question vs
    permission) isn't possible here. Icon by type; idle/other pass the message
    through, but a permission prompt is reworded to "awaits your input" because
    it's just as often a question (AskUserQuestion) as a real permission gate.
    """
    if payload.get("notification_type") == "permission_prompt":
        return "🔔", "Claude awaits your input"
    raw = str(payload.get("message") or "needs your attention").strip()
    return _ICONS.get(payload.get("notification_type"), "🔔"), raw


def session_link(transcript_path: object) -> str | None:
    """Web URL for a remote-control session, or None for a local one.

    A bridged (phone / claude.ai) session records a ``bridge-session`` entry
    near the top of its local transcript with ``bridgeSessionId`` like
    ``cse_01H…``; the web session lives at
    ``https://claude.ai/code/session_01H…`` (the ``cse_`` prefix dropped). Lets
    the ping deep-link straight back into the conversation. Returns None for a
    local terminal session (no bridge entry) or on any read error.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as handle:
            head = handle.read(_TRANSCRIPT_HEAD_BYTES).decode("utf-8", "ignore")
    except (OSError, TypeError, ValueError):
        return None

    for line in head.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # partial trailing line from the head cut, or non-JSON
        if entry.get("type") == "bridge-session":
            bridge_id = entry.get("bridgeSessionId") or ""
            session_id = bridge_id[4:] if bridge_id.startswith("cse_") else bridge_id
            if session_id:
                return f"https://claude.ai/code/session_{session_id}"
    return None


def main() -> None:
    payload = _lib.read_stdin_json()

    # Defensive re-entrancy guard. A Notification hook can't loop Claude, but if
    # this ever fires inside a Stop-loop, bail rather than ping repeatedly.
    if payload.get("stop_hook_active"):
        _lib.allow()

    channel, user, name = _lib.resolve_slack_target(_lib.cwd(payload))
    if not channel:
        _lib.allow()  # opt-in: not configured for this project → silent no-op

    # Only the "come look, I'm blocked" prompt is worth a phone push. The 💤
    # idle nag is noise — a session left idle is rarely something you need to
    # run back to — so no-op on it.
    if payload.get("notification_type") == "idle_prompt":
        _lib.allow()

    icon, text = classify(payload)
    link = session_link(payload.get("transcript_path"))
    suffix = f" · {link}" if link else ""
    # The @mention decision is single-sourced in slack_notify.notify() (off by
    # default); pass the resolved user id and let it decide.
    slack_notify.notify(f"{icon} [{name}] {text}{suffix}", channel=str(channel), user=user)
    _lib.allow()


if __name__ == "__main__":
    main()
