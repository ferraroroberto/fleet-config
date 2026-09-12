"""Block `AskUserQuestion` from a chief-managed worker (fleet-config#463).

`AskUserQuestion` renders only in the calling session's own PTY. For a normal
interactive session that's fine -- a human is sitting there. For a worker
dispatched by chief (`chief_ops.py dispatch`), it's a dead end: chief's only
window into a worker is `chief_ops.py exchange` (the last assistant *text*
message), which never surfaces a `tool_use` block, so the question is
invisible and any answer that reaches the prompt is unattributable -- nobody
in the chain chose it. Worse than invisible, in fact: the rendered modal puts
the PTY into a state that rejects further input, so `chief_ops.py say` -- the
documented fallback for relaying a decision -- stops working precisely when it
is needed, and the only recovery is killing the worker and re-dispatching it
from scratch (fleet-config#835).

This is the enforcement: refuse the tool outright for a chief-managed
session, before it ever renders, so the worker falls back to stating the
question as plain output text (which chief's `exchange` *can* see) and
waiting for an answer relayed via `chief_ops.py say`.

The chief-managed decision comes from `notify_on_idle.chief_managed_state()`
-- the exact marker/read logic `notify_on_idle.py` already has, not a
re-derivation -- since both live in `hooks/` (the tree-independence convention
is about the hooks/skills_lib boundary, not imports within hooks/ itself;
`notify_on_idle.py` already imports its own sibling `session_state` the same
way). That helper keys on the **launcher** session id from the environment,
not the hook payload's `session_id`: keying on the payload is what left this
guard inert from the day it shipped (fleet-config#835).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import notify_on_idle  # noqa: E402


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "AskUserQuestion":
        _lib.allow()

    managed, reason = notify_on_idle.chief_managed_state()
    if managed:
        _lib.block(
            "Blocked: AskUserQuestion is disabled for this chief-managed session -- "
            "it renders only in your own PTY, where nobody can read it, and the "
            "open modal then rejects further input, so chief cannot even steer you "
            "out of it. Instead: state the question and its options as plain output "
            "text (chief's `exchange` can see that), then continue with your best "
            "judgment or wait -- chief will relay a decision via `chief_ops.py say` "
            "if one is needed."
        )

    if reason == notify_on_idle.CHIEF_UNDETERMINED:
        # Launcher-spawned, but `chief-managed.json` could not be read. Still
        # fail open -- stranding an ordinary launcher session over a bad read is
        # the worse outcome -- but never silently: "couldn't tell" gets its own
        # reported state rather than being folded into the quiet allow below
        # (fleet-config#835). `warn()` exits 0, so the tool call proceeds.
        _lib.warn(
            "AskUserQuestion allowed but UNVERIFIED: this session is launcher-spawned "
            f"({_lib.launcher_session_id()}) and the chief-managed registry at "
            f"{_lib.state_dir() / 'chief-managed.json'} could not be read, so whether "
            "chief dispatched you is unknown. If you are a chief-dispatched worker, "
            "state the question as plain output text instead -- a rendered modal will "
            "strand this session."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
