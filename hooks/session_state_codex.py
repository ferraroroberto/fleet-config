"""Codex adapter for the Fleet Board session-state writer (fleet-config#349).

Wires Codex CLI's ``UserPromptSubmit`` / ``Stop`` / ``PermissionRequest`` hooks
(``codex-hooks.json``) into the same ``sessions-state.json`` row
:mod:`session_state` already maintains for Claude Code, so a Codex terminal
shows ``working``/``needs-you`` on the Fleet Board instead of ``unknown``.

Codex's hook payload shares the same field names as Claude Code's
(``session_id``, ``cwd``, ``hook_event_name``, ``transcript_path`` —
confirmed against the current Codex hooks doc, developers.openai.com/codex/hooks)
so this module is a thin event-map wrapper around
:func:`session_state.upsert_from_payload`, not a separate parser.

``PermissionRequest`` maps to ``needs-you`` — the Codex analog of how
Claude's ``Notification`` hook piggybacks ``needs-you`` on a permission gate
(:mod:`notify_on_idle`). Codex has no session-end-shaped hook, so a Codex row
has no explicit removal path; it ages out via :mod:`session_state`'s existing
24h prune, same as a hard-killed Claude session.

Composes with, rather than replaces, Codex's separate ``notify`` mechanism
(the CUA ``codex-computer-use.exe`` "turn-ended" notifier in
``~/.codex/config.toml``) by never touching it — this hook fires through the
completely separate ``hooks.json`` subsystem.

Advisory-only like every hook here: any failure is swallowed and the hook
exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import session_state  # noqa: E402

_EVENT_STATUS = {
    "UserPromptSubmit": "working",
    "Stop": "needs-you",
    "PermissionRequest": "needs-you",
}


def main() -> None:
    try:
        payload = _lib.read_stdin_json()
        event = str(payload.get("hook_event_name") or "")
        status = _EVENT_STATUS.get(event)
        if status:
            session_state.upsert_from_payload(payload, status, default_agent="codex")
    except Exception:  # noqa: BLE001 — state is advisory; never disturb the session
        pass
    _lib.allow()


if __name__ == "__main__":
    main()
