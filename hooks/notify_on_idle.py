"""Ping Slack when a live session needs attention — so you can stop babysitting.

**Claude Code only.** This hook is wired *solely* into Claude Code's
``Notification`` event (``settings.template.json``) — no other agent has an
equivalent event surface (Codex's ``hooks.json`` is PreToolUse/PostToolUse only;
Pi exposes no hook surface), so the agent label below is the deterministic
constant ``"Claude Code"`` rather than something inferred. Extending the
"awaits your input" ping to Codex and Pi (and parametrizing the label once a
second caller exists) is tracked separately in fleet-config#213.

It pings only on the ``permission_prompt`` sub-type (a permission gate or an
``AskUserQuestion`` — the "come look, I'm blocked" push) and **no-ops on**
``idle_prompt`` (the 💤 "gone idle" nag is noise) **and on the background
sub-agent types** ``agent_needs_input`` / ``agent_completed`` (fleet-config#274
— fired per Task/Agent-tool spawn since a Claude Code update added them; a
fan-out skill like ``/issue-batch`` or ``/cleanup-fleet`` spawns many of these,
and only the parent session's own prompt is worth a phone push). It rides the
`slack_notify` transport, so an AFK human gets a real phone notification
instead of a desktop toast nobody sees.

**Opt-in, default off.** It does nothing unless the current project declares a
``slack_notify_channel`` in ``hooks/projects.toml`` (or a ``[global]
slack_notify_channel`` fallback is set). That keeps notification noise off by
default and lets you flip it on per project. See `docs/slack-workflow.md`.

**Board deep link (fleet-config#242).** When ``board_url`` is also configured,
the ping appends a second line linking straight to the session's Fleet-Board
card (`?board=<transcript_uuid>`) — see `board_link()` and
`docs/slack-workflow.md`. Unset by default, so this is a no-op until you
configure it.

A Notification hook only advises — it never blocks, and always exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import slack_notify  # noqa: E402

# Glanceable icon per notification kind. A real permission gate (action needed)
# reads differently from an idle wait; anything else falls back to the bell.
_ICONS = {"permission_prompt": "🔔", "idle_prompt": "💤"}

# Sub-types that never warrant a phone push (fleet-config#274): the idle nag,
# plus the background sub-agent lifecycle events a Claude Code update added
# (fired once per Task/Agent-tool spawn) — only the parent session's own
# permission_prompt is worth surfacing.
_NOOP_TYPES = {"idle_prompt", "agent_needs_input", "agent_completed"}

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
    The agent name is hardcoded "Claude Code" because this hook only ever fires
    from Claude Code (see module docstring; Codex/Pi are fleet-config#213).
    """
    if payload.get("notification_type") == "permission_prompt":
        return "🔔", "Claude Code awaits your input"
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


def board_link(payload: dict, registry: object | None = None) -> str | None:
    """Slack mrkdwn line deep-linking to the session's Fleet-Board card, or None.

    Needs both the payload's ``session_id`` (Claude's transcript UUID —
    ``session_state.py`` persists the same id as the board row's key) and a
    configured ``board_url`` (fleet-config#242, resolved via
    ``_lib.resolve_board_url`` — the real value comes from the
    ``FLEET_BOARD_URL`` env var, not `projects.toml`, since this repo is
    public — see fleet-config#271) — absent either, this is a silent no-op so
    the ping stays byte-identical to today.

    ``board_url`` may already carry its own query string — e.g. a bearer
    ``?token=...`` baked in the same way app-launcher's tray "Copy Tailscale
    URL" menu item does, so the tapped link authenticates without a login
    overlay (fleet-config#273). ``board=<session_id>`` is merged in via
    ``urllib.parse`` rather than concatenated, so an existing query string
    survives intact.
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    base_url = _lib.resolve_board_url(_lib.cwd(payload), registry)
    if not base_url:
        return None
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("board", session_id))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), ""))
    return f"📋 <{url}|Open on the Board>"


def main() -> None:
    payload = _lib.read_stdin_json()

    # Defensive re-entrancy guard. A Notification hook can't loop Claude, but if
    # this ever fires inside a Stop-loop, bail rather than ping repeatedly.
    if payload.get("stop_hook_active"):
        _lib.allow()

    # Persist the board state row (fleet-config#91) before any opt-in gating —
    # a blocked session must surface on the Fleet Board even for projects with
    # Slack pings off, and a persistence failure must never touch the ping.
    #
    # idle_prompt is a periodic "still waiting on you" re-announcement, not a
    # new state — Stop already wrote needs-you when the turn ended, and
    # idle_prompt fires ~60s later while nothing has changed. Writing "idle"
    # here silently downgraded that needs-you row until the user replied or a
    # fresh permission_prompt fired, dropping a genuinely-waiting session off
    # the Board's "Your turn" column (fleet-config#354). So it's a no-op here,
    # same as the Slack-ping side already treats it (_NOOP_TYPES below).
    if payload.get("notification_type") != "idle_prompt":
        try:
            import session_state
            session_state.upsert_from_payload(payload, "needs-you")
        except Exception:  # noqa: BLE001
            pass

    # A "come look, I'm blocked" prompt is action-needed → the attention channel.
    channel, user, name = _lib.resolve_slack_target(_lib.cwd(payload), category="attention")
    if not channel:
        _lib.allow()  # opt-in: not configured for this project → silent no-op

    # Only the "come look, I'm blocked" prompt is worth a phone push. The 💤
    # idle nag and the background sub-agent lifecycle events are noise — a
    # session left idle is rarely something you need to run back to, and a
    # sub-agent needing input or completing isn't the parent session asking
    # for you — so no-op on all of them (fleet-config#274).
    if payload.get("notification_type") in _NOOP_TYPES:
        _lib.allow()

    icon, text = classify(payload)
    link = session_link(payload.get("transcript_path"))
    suffix = f" · {link}" if link else ""
    message = f"{icon} [{name}] {text}{suffix}"
    board = board_link(payload)
    if board:
        message += f"\n{board}"
    # The @mention decision is single-sourced in slack_notify.notify() (off by
    # default); pass the resolved user id and let it decide.
    slack_notify.notify(message, channel=str(channel), user=user)
    _lib.allow()


if __name__ == "__main__":
    main()
