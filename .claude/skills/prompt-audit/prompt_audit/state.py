"""Cadence state (`~/.claude/prompt-audit/state.json`).

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import json
import os
import datetime as dt
from pathlib import Path
from typing import Optional, Tuple


# ---- state ----------------------------------------------------------------------

def state_path() -> Path:
    override = os.environ.get("PROMPT_AUDIT_STATE_DIR")
    base = Path(override) if override else Path.home() / ".claude" / "prompt-audit"
    return base / "state.json"


def load_state(path: Optional[Path] = None) -> dict:
    p = path or state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or corrupt -> everything due; costs one extra fetch pass
    return data if isinstance(data, dict) and isinstance(data.get("sources", {}), dict) else {}


def save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def source_due(record: Optional[dict], every: int, today: dt.date) -> Tuple[bool, str]:
    """A source is fresh only if checked within the cadence AND last seen unchanged."""
    if not record or not record.get("last"):
        return True, "now"
    try:
        last = dt.date.fromisoformat(record["last"])
    except (TypeError, ValueError):
        return True, "now"
    nxt = last + dt.timedelta(days=every)
    if record.get("verdict") != "unchanged" or today >= nxt:
        return True, "now"
    return False, nxt.isoformat()
