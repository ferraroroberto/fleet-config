"""SessionStart hook — hand the standing fleet chief its last written run log
back on every session start (fleet-config#442).

Chief used to be killed and respawned fresh daily, discarding everything it
had learned about how a run was going — which workers stall, which decisions
are already settled, which issues are deliberately parked. The insight behind
#442: chief doesn't want to be killed and restarted, it wants to be
*compacted and continued* — context is finite and must be shed, but the
*state of the run* should survive the shedding.

Chief itself owns the judgment of what to record (dense, decision-focused
prose — the log is chief-authored, not a mechanical transcript dump, per the
issue's own constraint) and writes it with the Write tool at natural
checkpoints (`.claude/skills/chief/SKILL.md`). This hook mechanizes only the
transport: whenever a fleet-config session starts — a fresh boot, a resume,
or continuing after an automatic compaction — it hands back whatever chief
last wrote, so it never has to remember to go read it. Live Board/GitHub
state still wins on facts; this log is the only thing that carries intent
and reasoning, which live state cannot express.

No `PreCompact` companion: Claude Code's `PreCompact` hook can only block
compaction (`decision: "block"`) or allow it silently — it has no documented
`additionalContext` injection, so a hook cannot hand content *into* the
post-compaction context at that point (https://code.claude.com/docs/en/hooks.md).
The write side is therefore chief's own discipline, not a hook.

Fires for every session cwd'd in fleet-config, not only chief's own —
harmless for an ordinary dev session (one extra FYI paragraph it can
ignore). No network call, no LLM call, no session-identity detection: cheap
and cwd-gated only.

Wired by the ``SessionStart`` hook in ``settings.template.json``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

# additionalContext has a ~10K-char ceiling (Claude Code docs); stay well
# under it and point at the full file instead of truncating silently mid-word.
MAX_INLINE_CHARS = 8000


def handover_path() -> Path:
    """Resolve the handover-log path at call time so tests can override its
    root (mirrors ``session_state.py``'s ``state_file()`` pattern). Lands
    under ``hooks/state/`` (gitignored, machine-local — fleet-config#442's
    "the log is gitignored and machine-local" criterion, for free)."""
    root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    base = Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
    return base / "chief-handover.md"


def build_context(content: str, path: Path) -> str:
    """The ``additionalContext`` string for a non-empty handover log.

    Truncates to the tail (the most recent entries) when the log would
    exceed the inline ceiling, pointing at the full file instead of
    silently dropping the older history.
    """
    if len(content) > MAX_INLINE_CHARS:
        content = (
            f"(showing the last {MAX_INLINE_CHARS} chars of a longer log; "
            f"the full history is at {path})\n\n{content[-MAX_INLINE_CHARS:]}"
        )
    return (
        "Prior fleet-chief run log (fleet-config#442) — if you are the "
        "standing chief, read this before re-deriving anything from live "
        "Board/GitHub state. Live state wins on facts; this log wins on "
        f"intent, reasoning, and what's deliberately parked.\n\n{content}"
    )


def main() -> int:
    payload = _lib.read_stdin_json()
    project = _lib.detect_project(_lib.cwd(payload))
    if project is None or project.name != "fleet-config":
        return 0  # not fleet-config -- chief only ever runs cwd'd here
    path = handover_path()
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0  # no log yet (first-ever run, or nothing written) -- silent no-op
    if not content:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": build_context(content, path),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
