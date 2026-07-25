"""SessionStart hook — lazily index settled conversation captures.

``conversation_capture`` (the Stop hook) writes raw captures every turn-end
(cheap, no LLM). This hook fires once when a new session starts — by then the
*previous* conversation is over — and kicks off ``conversation_index`` for the
session's project, which digests any settled, not-yet-indexed capture via the
local hub and upserts each conversations dir's ``index.md``. This is how
digesting stays "once per conversation, after it ends" instead of "every turn".

Generic + ``projects.toml``-driven: it indexes only if the session's project
opted into capture (``capture = true``); otherwise a silent no-op. Detached and
fail-open — it spawns the indexer in the background so session start is never
delayed by hub latency, and never blocks or errors out.

Wired by the ``SessionStart`` hook in a project's ``.claude/settings.json``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent
INDEXER = HOOKS_DIR / "conversation_index.py"


def main() -> int:
    payload = _lib.read_stdin_json()
    project = _lib.detect_project(_lib.cwd(payload))
    if project is None or not project.extra.get("capture"):
        return 0  # project not opted into capture — silent no-op
    if not INDEXER.exists():
        return 0
    try:
        # Detached: don't make the user wait on hub round-trips at session start.
        py = sys.executable or "py"
        subprocess.Popen(
            [py, str(INDEXER), "--project", project.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(HOOKS_DIR),
            creationflags=_lib.NO_WINDOW,
        )
    except OSError:
        pass  # fail-open — a failed index must never break session start
    return 0


if __name__ == "__main__":
    sys.exit(main())
