"""Block `AskUserQuestion` from a chief-managed worker (fleet-config#463).

`AskUserQuestion` renders only in the calling session's own PTY. For a normal
interactive session that's fine -- a human is sitting there. For a worker
dispatched by chief (`chief_ops.py dispatch`), it's a dead end: chief's only
window into a worker is `chief_ops.py exchange` (the last assistant *text*
message), which never surfaces a `tool_use` block, so the question is
invisible and any answer that reaches the prompt is unattributable -- nobody
in the chain chose it. `notify_on_idle.py` already tried to route around this
after the fact (fleet-config#443: reword the notification and ping chief
instead of the human ping), but that's advisory, not enforcement -- a worker can still
open the prompt and something can still answer it blind.

This is the enforcement: refuse the tool outright for a chief-managed
session, before it ever renders, so the worker falls back to stating the
question as plain output text (which chief's `exchange` *can* see) and
waiting for an answer relayed via `chief_ops.py say`.

Reuses `notify_on_idle.is_chief_managed` -- the exact marker/read logic
`notify_on_idle.py` already has, not a re-derivation -- since both live in
`hooks/` (the tree-independence convention is about the hooks/skills_lib
boundary, not imports within hooks/ itself; `notify_on_idle.py` already
imports its own sibling `session_state` the same way).
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

    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        # Can't tell if this is chief-managed -- fail open rather than strand
        # an ordinary session over a missing/malformed field.
        _lib.allow()

    # is_chief_managed already fails open (missing/corrupt state file -> False)
    # on any read problem, so a bad marker file never strands a worker either.
    if not notify_on_idle.is_chief_managed(sid):
        _lib.allow()

    _lib.block(
        "Blocked: AskUserQuestion is disabled for this chief-managed session -- "
        "it renders only in your own PTY, so chief can never see the question "
        "or attribute an answer to it. Instead: state the question and its "
        "options as plain output text (chief's `exchange` can see that), then "
        "continue with your best judgment or wait -- chief will relay a "
        "decision via `chief_ops.py say` if one is needed."
    )


if __name__ == "__main__":
    main()
