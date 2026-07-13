"""Persist per-session state for the Fleet Board (fleet-config#91).

Maintains ``sessions-state.json`` — one row per recent Claude Code session
(``project``, ``status``, ``transcript_path``, ``cwd``, ``updated_at``, explicit
``agent``, optional ``launcher_session_id``, plus the live session
``name``/``name_source`` when available — see below) keyed by the
hook payload's ``session_id`` — so the app-launcher Board tab (app-launcher#164)
can render a "what needs me now" column without owning any hook plumbing. The
board only *reads* the file; this module is the only writer.

**Session name (fleet-config#302).** Claude Code maintains a live per-process
registry file at ``~/.claude/sessions/<pid>.json`` (one per running ``claude``
process), whose ``sessionId`` field is the exact same UUID as the hook
payload's ``session_id``. On each write this module scans that directory for
the matching entry and, when found, copies its ``name`` (the title shown in the
prompt box / ``/resume`` picker / terminal title) and ``nameSource`` (present
as ``"derived"`` only for the generic ``<project>-N`` fallback; absent once the
session has a real title) into the row as ``name`` / ``name_source``. Advisory
like everything else here: a missing directory, a missing/malformed per-PID
file, or no matching ``sessionId`` all just leave both fields ``None`` — never
raises, never blocks.

Wired into three Claude Code events (``settings.template.json``):

* ``UserPromptSubmit`` → status ``working`` (the user handed Claude the turn).
* ``Stop`` → status ``needs-you`` (Claude finished; the ball is back with you).
* ``SessionEnd`` → **deletes** the row (the session is gone, not waiting on
  anyone). Fires on clean exit (``/exit``, ``clear``, ``logout``, prompt-input
  exit); a hard kill (taskkill, crash) never fires it, so those rows still age
  out via the 24h prune below (#241).

``notify_on_idle`` (the ``Notification`` hook) additionally upserts
``needs-you`` on a permission prompt and ``idle`` on the idle nag, so a blocked
session surfaces even mid-turn — Slack pings are unchanged.

Status meanings the board relies on: ``working`` | ``needs-you`` | ``idle``.

The hook payload's ``session_id`` is Claude Code's transcript UUID, not the
launcher session-host id. App Launcher injects its exact identity as inherited
``APP_LAUNCHER_SESSION_ID`` / ``APP_LAUNCHER_AGENT`` values; when present this
writer persists them for an exact agent-aware consumer join. External sessions
have no launcher id and retain the normalized-cwd fallback.

Like every hook here this is advisory-only: any failure is swallowed and the
hook exits 0. The state file lives under ``~/.claude/hooks/state/`` (a junction
into this repo's working tree — the directory is gitignored);
``CLAUDE_HOOKS_STATE_DIR`` overrides the directory so acceptance tests stay
hermetic, and ``CLAUDE_SESSIONS_DIR`` likewise overrides the
``~/.claude/sessions/`` registry directory the name lookup reads. Rows
untouched for 24h are pruned on each write.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

STATE_FILENAME = "sessions-state.json"

_PRUNE_AFTER = timedelta(hours=24)
_REPLACE_ATTEMPTS = 3  # os.replace can hit a transient PermissionError under a concurrent Windows reader

# Claude Code event → the board status it evidences. Anything else is ignored.
_EVENT_STATUS = {
    "UserPromptSubmit": "working",
    "Stop": "needs-you",
}


def state_file() -> Path:
    """Resolve the state-file path at call time so the env override always wins."""
    root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    base = Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
    return base / STATE_FILENAME


def sessions_registry_dir() -> Path:
    """Resolve Claude Code's live per-process session registry directory at call
    time so the env override always wins (same pattern as ``state_file()``).

    Defaults to ``~/.claude/sessions``, one ``<pid>.json`` file per running
    ``claude`` process; ``CLAUDE_SESSIONS_DIR`` overrides it so acceptance
    tests stay hermetic.
    """
    root = os.environ.get("CLAUDE_SESSIONS_DIR")
    return Path(root) if root else Path.home() / ".claude" / "sessions"


def _lookup_session_name(session_id: str) -> "tuple[Optional[str], Optional[str]]":
    """Best-effort ``(name, name_source)`` for ``session_id`` from the live
    per-process session registry (fleet-config#302). Scans every
    ``<pid>.json`` file in ``sessions_registry_dir()`` for the entry whose
    ``sessionId`` matches; returns ``(None, None)`` on any miss — missing
    directory, unreadable/malformed file, or no matching session — never
    raises."""
    try:
        registry_dir = sessions_registry_dir()
        if not registry_dir.is_dir():
            return None, None
        for entry in registry_dir.glob("*.json"):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("sessionId") != session_id:
                continue
            name = data.get("name")
            name_source = data.get("nameSource")
            return (
                name if isinstance(name, str) and name else None,
                name_source if isinstance(name_source, str) and name_source else None,
            )
    except Exception:  # noqa: BLE001 — advisory only, never break the write path
        pass
    return None, None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_updated_at(row: Any) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    raw = row.get("updated_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_rows(path: Path) -> Dict[str, Any]:
    """Current rows, or {} on a missing/corrupt file — the writer self-heals."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_rows(path: Path, rows: Dict[str, Any]) -> None:
    """Atomic tmp+replace write, retried because a concurrent reader on Windows
    can hold the target and fail ``os.replace`` with a transient PermissionError."""
    payload = json.dumps(rows, indent=2, sort_keys=True)
    for attempt in range(_REPLACE_ATTEMPTS):
        tmp_name: Optional[str] = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
            return
        except OSError:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            time.sleep(0.05 * (attempt + 1))


def upsert(
    session_id: str,
    *,
    status: str,
    project: Optional[str],
    transcript_path: Optional[str],
    cwd_path: Optional[str],
    name: Optional[str] = None,
    name_source: Optional[str] = None,
    agent: str = "claude",
    launcher_session_id: Optional[str] = None,
) -> None:
    """Write/refresh one session row and prune rows stale past 24h.

    ``name``/``name_source`` (fleet-config#302) are the live Claude Code
    session title looked up from ``~/.claude/sessions/<pid>.json``, when a
    caller has one; both default to ``None`` so existing callers are unaffected.
    """
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(path)
    rows[str(session_id)] = {
        "project": project,
        "status": status,
        "transcript_path": transcript_path,
        "cwd": cwd_path,
        "name": name,
        "name_source": name_source,
        "agent": agent,
        "launcher_session_id": launcher_session_id,
        "updated_at": _isoformat(_now()),
    }

    cutoff = _now() - _PRUNE_AFTER
    kept: Dict[str, Any] = {}
    for sid, row in rows.items():
        stamp = _parse_updated_at(row)
        if stamp is not None and stamp >= cutoff:
            kept[sid] = row

    _write_rows(path, kept)


def upsert_from_payload(
    payload: Dict[str, Any], status: str, *, default_agent: str = "claude"
) -> None:
    """Upsert straight from a hook payload; silent no-op without a session_id.

    ``default_agent`` (fleet-config#349) is the agent to record when the
    process carries no ``APP_LAUNCHER_AGENT`` — true for a Codex/Pi session
    opened outside App Launcher, which would otherwise misreport as
    ``claude`` (the historical default, kept for Claude's own callers).
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    cwd_path = _lib.cwd(payload)
    project = _lib.detect_project(cwd_path)
    transcript = payload.get("transcript_path")
    name, name_source = _lookup_session_name(session_id)
    launcher_session_id = (
        os.environ.get("APP_LAUNCHER_SESSION_ID", "").strip() or None
    )
    agent = os.environ.get("APP_LAUNCHER_AGENT", "").strip().lower() or default_agent
    upsert(
        session_id,
        status=status,
        project=project.name if project else cwd_path.name,
        transcript_path=transcript if isinstance(transcript, str) and transcript else None,
        cwd_path=str(cwd_path),
        name=name,
        name_source=name_source,
        agent=agent,
        launcher_session_id=launcher_session_id,
    )


def remove(session_id: str) -> None:
    """Delete one session's row (SessionEnd); silent no-op if the row is absent."""
    path = state_file()
    if not path.exists():
        return
    rows = _read_rows(path)
    if str(session_id) not in rows:
        return
    del rows[str(session_id)]
    _write_rows(path, rows)


def remove_from_payload(payload: Dict[str, Any]) -> None:
    """Delete the payload's session row; silent no-op without a session_id."""
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id:
        remove(session_id)


def main() -> None:
    try:
        payload = _lib.read_stdin_json()
        event = str(payload.get("hook_event_name") or "")
        if event == "SessionEnd":
            remove_from_payload(payload)
        else:
            status = _EVENT_STATUS.get(event)
            if status:
                upsert_from_payload(payload, status)
    except Exception:  # noqa: BLE001 — state is advisory; never disturb the session
        pass
    _lib.allow()


if __name__ == "__main__":
    main()
