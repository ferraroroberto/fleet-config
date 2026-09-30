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

Fires for every session cwd'd in a project that sets ``chief_handover = true``
in ``hooks/projects.toml`` (fleet-config today), not only chief's own —
harmless for an ordinary dev session (one extra FYI paragraph it can
ignore). No network call, no LLM call, no session-identity detection: cheap
and cwd-gated only.

Chief skill refresh (fleet-config#1102): a chief that loaded ``/chief``
days ago keeps running on that copy across compactions, so a rule added to
``.claude/skills/chief/SKILL.md`` since never reaches it. On any non-startup
start (``compact``, ``resume``, ``clear``) of the chief's own session -- its
``sessions-state.json`` row, by the payload's session id or the launcher
session id, is named ``chief`` -- this hook also tells it to re-read the skill,
naming the file's content hash. The skill (~37K chars) is over the
``additionalContext`` ceiling, so it is pointed at, never inlined. An
unreadable state file is its own case: the pointer goes out conditionally
("if you are the standing chief"), never folded into "not the chief".

Wired by the ``SessionStart`` hook in ``settings.template.json``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import session_state  # noqa: E402

CHIEF_NAME = "chief"

# additionalContext has a ~10K-char ceiling (Claude Code docs); stay well
# under it and point at the full file instead of truncating silently mid-word.
MAX_INLINE_CHARS = 8000


def handover_path() -> Path:
    """Resolve the handover-log path at call time so tests can override its
    root (mirrors ``session_state.py``'s ``state_file()`` pattern). Lands
    under ``hooks/state/`` (gitignored, machine-local — fleet-config#442's
    "the log is gitignored and machine-local" criterion, for free)."""
    return _lib.state_dir() / "chief-handover.md"


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


def skill_path() -> Path:
    """The chief skill in this checkout (``hooks/`` resolves through its junction)."""
    return Path(__file__).resolve().parent.parent / ".claude" / "skills" / "chief" / "SKILL.md"


def is_chief_session(payload: Dict[str, Any]) -> Optional[bool]:
    """True/False from ``sessions-state.json``; None when that file can't be read."""
    path = session_state.state_file()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None
    if not isinstance(rows, dict):
        return None
    sid, launcher_sid = str(payload.get("session_id") or ""), _lib.launcher_session_id()
    return any(
        isinstance(row, dict) and row.get("name") == CHIEF_NAME
        and ((sid and key == sid) or (launcher_sid and row.get("launcher_session_id") == launcher_sid))
        for key, row in rows.items())


def skill_refresh(payload: Dict[str, Any]) -> Optional[str]:
    """The re-read instruction for a compacted/resumed chief, or None (#1102)."""
    source = str(payload.get("source") or "")
    if source in ("", "startup"):
        return None  # a fresh chief loads the current skill through /chief itself
    chief = is_chief_session(payload)
    if chief is False:
        return None
    path = skill_path()
    try:
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None
    if chief is None:
        return (f"If you are the standing chief: re-read {path} (sha256 {version}) now -- whether this "
                f"session is the chief could not be read from {session_state.state_file()}.")
    return (f"Chief skill refresh (fleet-config#1102): this chief session is continuing after a {source}, "
            f"and skill rules added since you first loaded /chief do not reach you otherwise. Re-read "
            f"{path} in full now (Read tool; version sha256 {version}) and follow its current text over "
            f"anything you remember of it.")


def main() -> int:
    payload = _lib.read_stdin_json()
    project = _lib.detect_project(_lib.cwd(payload))
    if project is None or not project.extra.get("chief_handover"):
        return 0  # project does not host the chief (`chief_handover` in projects.toml)
    path = handover_path()
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""  # no log yet (first-ever run, or nothing written)
    parts = [build_context(content, path)] if content else []
    refresh = skill_refresh(payload)
    if refresh:
        parts.append(refresh)
    if not parts:
        return 0
    # `_lib.warn()` owns the per-harness SessionStart dialect (fleet-config#818):
    # Claude gets the hookSpecificOutput.additionalContext envelope this hook
    # used to hand-roll unconditionally; a non-Claude harness -- e.g. Grok,
    # which loads this repo's hooks by default -- gets the plain-stdout
    # fallback it actually reads, instead of a Claude-shaped envelope it
    # silently drops.
    _lib.warn("\n\n".join(parts))


if __name__ == "__main__":
    sys.exit(main())
