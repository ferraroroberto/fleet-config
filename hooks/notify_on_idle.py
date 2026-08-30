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
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import slack_notify  # noqa: E402

logger = logging.getLogger("notify_on_idle")

# fleet-config#443: hooks/ and skills/_lib/ are two independent trees by
# convention (a hook must stay importable with nothing but its own directory
# on sys.path), so chief_ops.py is reached via subprocess here, never a
# Python import -- same cross-tier pattern chief_ops.py's own `escalate`
# subcommand uses in reverse to reach hooks/slack_notify.py. `hooks/` is
# physically inside the fleet-config repo (junctioned elsewhere too), so
# this resolves the fleet-config root without hardcoding a machine path.
FLEET_CONFIG_ROOT = Path(__file__).resolve().parent.parent
CHIEF_OPS = FLEET_CONFIG_ROOT / "skills" / "_lib" / "chief_ops.py"
CHIEF_OPS_PYTHON = FLEET_CONFIG_ROOT / ".venv" / "Scripts" / "python.exe"
_CHIEF_OPS_TIMEOUT_S = 10.0

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


def is_chief_managed(sid: str, path: Optional[Path] = None) -> bool:
    """True if `sid` has a live chief-managed marker (fleet-config#443).

    A tiny independent reader of the same `hooks/state/chief-managed.json`
    `skills/_lib/chief_managed.py` writes -- deliberately its own read
    logic, not an import, per the hooks/skills_lib tree-independence
    convention. Tolerant of a missing/corrupt file (not managed, never a
    hook-breaking error); no TTL re-check here since a marker outliving its
    session is harmless -- worst case is one extra `chief-sid` lookup that
    finds nothing.
    """
    if path is None:
        root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
        base = Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
        path = base / "chief-managed.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and sid in data


def parse_chief_sid(stdout: str) -> str:
    """Pull the sid out of `chief_ops.py chief-sid`'s `CHIEF_SID=...` line.

    Returns `""` for "no chief live" (`CHIEF_SID=none`) or a missing/absent
    line -- pure string logic, split out so it's unit-testable without a
    live launcher or a subprocess call.
    """
    sid_line = next((l for l in stdout.splitlines() if l.startswith("CHIEF_SID=")), "")
    chief_sid = sid_line.split("=", 1)[1].strip() if sid_line else ""
    return "" if chief_sid == "none" else chief_sid


def notify_chief(text: str) -> bool:
    """Push `text` into the live standing chief's session via `chief_ops.py`.

    Returns True only on confirmed delivery. The caller must fall back to
    the normal human ping on False -- never retry (fleet-config#443's
    cross-repo constraint note: a retry here would mask a real app-launcher
    delivery defect rather than surface it). Two short subprocess calls,
    each bounded and never inheriting stdin, reusing `chief_ops.py`'s own
    vetted transport (`chief-sid` then `say`) rather than hand-rolling a
    second HTTP client here.
    """
    if not CHIEF_OPS_PYTHON.is_file() or not CHIEF_OPS.is_file():
        return False
    try:
        sid_proc = subprocess.run(
            [str(CHIEF_OPS_PYTHON), str(CHIEF_OPS), "chief-sid"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=_lib.NO_WINDOW,
            cwd=str(FLEET_CONFIG_ROOT), timeout=_CHIEF_OPS_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Couldn't even invoke chief_ops.py -- distinct from "no chief found"
        # below, since the board was never reached to check (fleet-config#456).
        logger.warning("notify_chief: chief-sid lookup failed to run (%s) -- falling back to Slack ping", exc)
        return False
    if sid_proc.returncode == 1:
        # cmd_chief_sid's own "board reachable, no live chief-labeled session"
        # exit -- this is the routing-miss case fleet-config#456 is about: a
        # chief-managed worker is blocked and chief-sid genuinely found
        # nothing wearing the chief label. Distinguishable at a glance from
        # the rc==2 branch below (couldn't even query the board) and from
        # ordinary silence (this line only appears when the fallback fires,
        # not on every ping).
        logger.info("notify_chief: chief-sid found no live chief session -- falling back to Slack ping")
        return False
    if sid_proc.returncode != 0:
        # rc==2 is chief_ops.py's own ValueError/URLError catch (app-launcher
        # unreachable, board fetch failed, ...) -- a query failure, not "no
        # chief running", so it gets its own message.
        stderr_tail = (sid_proc.stderr or "").strip()
        logger.warning(
            "notify_chief: chief-sid query errored (rc=%d): %s -- falling back to Slack ping",
            sid_proc.returncode, stderr_tail,
        )
        return False
    chief_sid = parse_chief_sid(sid_proc.stdout)
    if not chief_sid:
        # Defense in depth: cmd_chief_sid's contract is exit 0 only when sid
        # is truthy, so this would be a parse/contract mismatch, not the
        # routing-miss above.
        logger.warning("notify_chief: chief-sid exited 0 but printed no sid -- falling back to Slack ping")
        return False

    fd, tmp_name = tempfile.mkstemp(prefix="chief-ping-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        say_proc = subprocess.run(
            [str(CHIEF_OPS_PYTHON), str(CHIEF_OPS), "say", chief_sid, "--file", tmp_name],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=_lib.NO_WINDOW,
            cwd=str(FLEET_CONFIG_ROOT), timeout=_CHIEF_OPS_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    return say_proc.returncode == 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
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
    # the Board's "Your turn" column (fleet-config#354). agent_needs_input and
    # agent_completed are the same shape of non-event, but for the *parent*
    # row: they fire per Task/Agent sub-agent spawn, not for the parent
    # session's own state, so persisting "needs-you" here stamped a mid-turn
    # parent row as blocked on evidence that says nothing about the parent
    # (fleet-config#718). All three are no-ops here, same as the Slack-ping
    # side already treats them (_NOOP_TYPES below).
    if payload.get("notification_type") not in _NOOP_TYPES:
        try:
            import session_state
            session_state.upsert_from_payload(payload, "needs-you")
        except Exception:  # noqa: BLE001
            pass

    # A chief-dispatched worker's "come look, I'm blocked" is chief's problem
    # first, not the human's (fleet-config#443) — chief wrote the brief and
    # can usually unblock it without paging Roberto. Tried *before* the
    # channel-configured check below, since this path doesn't need Slack at
    # all. Falls through to the normal human ping on any failure (session not
    # found, delivery failed) — never silently drops a real blocked-worker
    # notification, and never retries (a retry here would mask a real #607
    # delivery defect rather than surface it).
    sid = payload.get("session_id")
    if (
        payload.get("notification_type") == "permission_prompt"
        and isinstance(sid, str)
        and sid
        and is_chief_managed(sid)
    ):
        icon, text = classify(payload)
        chief_message = f"{icon} chief-managed worker needs input: {text}"
        chief_message = chief_message.replace("\n", " ")[:300]
        if notify_chief(chief_message):
            _lib.allow()  # delivered to chief -- no human ping for this one

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
