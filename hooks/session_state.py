"""Persist per-session state for the Fleet Board (fleet-config#91).

Maintains ``sessions-state.json`` — one row per recent Claude Code session
(``project``, ``status``, ``transcript_path``, ``cwd``, ``updated_at``) keyed by
the hook payload's ``session_id`` — so the app-launcher Board tab
(app-launcher#164) can render a "what needs me now" column without owning any
hook plumbing. The board only *reads* the file; this module is the only writer.

Wired into two Claude Code events (``settings.template.json``):

* ``UserPromptSubmit`` → status ``working`` (the user handed Claude the turn).
* ``Stop`` → status ``needs-you`` (Claude finished; the ball is back with you).

``notify_on_idle`` (the ``Notification`` hook) additionally upserts
``needs-you`` on a permission prompt and ``idle`` on the idle nag, so a blocked
session surfaces even mid-turn — Slack pings are unchanged.

Status meanings the board relies on: ``working`` | ``needs-you`` | ``idle``.

The join key caveat: this ``session_id`` is Claude Code's transcript UUID, which
never matches the launcher session-host's own session ids — consumers join rows
to live sessions by normalized ``cwd`` prefix, so ``cwd`` is the load-bearing
field, not the key.

Like every hook here this is advisory-only: any failure is swallowed and the
hook exits 0. The state file lives under ``~/.claude/hooks/state/`` (a junction
into this repo's working tree — the directory is gitignored);
``CLAUDE_HOOKS_STATE_DIR`` overrides the directory so acceptance tests stay
hermetic. Rows untouched for 24h are pruned on each write.
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
) -> None:
    """Write/refresh one session row and prune rows stale past 24h."""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(path)
    rows[str(session_id)] = {
        "project": project,
        "status": status,
        "transcript_path": transcript_path,
        "cwd": cwd_path,
        "updated_at": _isoformat(_now()),
    }

    cutoff = _now() - _PRUNE_AFTER
    kept: Dict[str, Any] = {}
    for sid, row in rows.items():
        stamp = _parse_updated_at(row)
        if stamp is not None and stamp >= cutoff:
            kept[sid] = row

    _write_rows(path, kept)


def upsert_from_payload(payload: Dict[str, Any], status: str) -> None:
    """Upsert straight from a hook payload; silent no-op without a session_id."""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    cwd_path = _lib.cwd(payload)
    project = _lib.detect_project(cwd_path)
    transcript = payload.get("transcript_path")
    upsert(
        session_id,
        status=status,
        project=project.name if project else cwd_path.name,
        transcript_path=transcript if isinstance(transcript, str) and transcript else None,
        cwd_path=str(cwd_path),
    )


def main() -> None:
    try:
        payload = _lib.read_stdin_json()
        status = _EVENT_STATUS.get(str(payload.get("hook_event_name") or ""))
        if status:
            upsert_from_payload(payload, status)
    except Exception:  # noqa: BLE001 — state is advisory; never disturb the session
        pass
    _lib.allow()


if __name__ == "__main__":
    main()
