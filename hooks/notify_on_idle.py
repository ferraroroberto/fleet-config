"""Ping Slack when a live session needs attention — so you can stop babysitting.

Wired to Claude Code's ``Notification`` event (fires when Claude needs your
input / a permission / has gone idle). It rides the `slack_notify` transport, so
an AFK human gets a real phone notification instead of a desktop toast nobody
sees.

**Opt-in, default off.** It does nothing unless the current project declares a
``slack_notify_channel`` in ``hooks/projects.toml`` (or a ``[global]
slack_notify_channel`` fallback is set). That keeps notification noise off by
default and lets you flip it on per project. See `docs/slack-workflow.md`.

A Notification hook only advises — it never blocks, and always exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import slack_notify  # noqa: E402


def main() -> None:
    payload = _lib.read_stdin_json()

    # Defensive re-entrancy guard. A Notification hook can't loop Claude, but if
    # this ever fires inside a Stop-loop, bail rather than ping repeatedly.
    if payload.get("stop_hook_active"):
        _lib.allow()

    registry = _lib.load_registry()
    project = _lib.detect_project(_lib.cwd(payload), registry)

    channel = None
    if project is not None:
        channel = project.extra.get("slack_notify_channel")
    if not channel:
        channel = registry.globals.slack_notify_channel
    if not channel:
        _lib.allow()  # opt-in: not configured for this project → silent no-op

    name = project.name if project is not None else "claude"
    message = str(payload.get("message") or "needs your attention").strip()
    slack_notify.notify(f"🔔 [{name}] {message}", channel=str(channel))
    _lib.allow()


if __name__ == "__main__":
    main()
