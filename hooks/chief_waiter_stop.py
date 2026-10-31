"""Stop hook: the chief may not end a turn with work in flight and no waiter armed.

The chief's wake-up is a one-shot background waiter it relaunches at the end of
every turn; a turn that ends without one silently ends all supervision, and a
finished lane then sits unnoticed until a human looks at the Board. Re-arming
was a prose rule the model had to remember on every turn. This is the
mechanism: refuse the turn end through `_lib.block()` when something the chief
supervises is running and no waiter heartbeat is live.

"Something is running" is a chief-managed lane whose Board row says `working`
(read from `sessions-state.json` + `chief-managed.json`, no network), else a
running fleet-wide job (one `chief_ops.py fleet-jobs` read). "A waiter is armed"
is a live file in `state_dir()/chief-waiters/` (written by
`skills/_lib/chief/waiter_heartbeat.py`): its `expected_end` is ahead and, when
it records a pid, that process is alive.

Cheapest check first, so every other session pays almost nothing:

1. a print-mode / scheduled child, or a turn already continuing after a refusal
   (`stop_hook_active`), is allowed before any file is read -- a headless
   `claude -p` has no waiter to arm and must never be held open;
2. a session that is not the chief (`sessions-state.json` row named `chief`) is
   allowed with one small file read;
3. only the chief reaches the context gate (hold the turn end once per window
   when its context is at or over the compact threshold), the heartbeat read,
   the lane read and, when it is about to be refused, the launcher reads
   (`wake`, `fleet-jobs`).

The context gate replaces a skill step the chief kept skipping: it asks
`chief_ops.py compact-due` (the line `chief_ops.py inbox` prints too) and holds
the turn end only on `COMPACT_DUE percent=.. threshold=..`; "not due", "off" and
"unknown" allow, and unknown is logged, never reported as fine. Two markers in
the hooks state dir debounce it to one hold per window so it can never loop:
`chief-compact-nudge` (written here) and `chief-compact-queued` (written by
`chief_ops.py self-compact`; the compaction runs when the turn ends, while the
percentage is still over the threshold).

It fails open everywhere it cannot establish a fact -- an unreadable session
state, an unreachable launcher -- and says so in the log; the launcher's
doorbell mode (`wake` -> `WAKE=doorbell`) needs no waiter, so it is allowed too.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import chief_handover_sessionstart  # noqa: E402
import notify_on_idle  # noqa: E402
import session_state  # noqa: E402

logger = logging.getLogger("fleet_hooks")

WAITERS_DIRNAME = "chief-waiters"
MANAGED_FILENAME = "chief-managed.json"
WORKING = "working"
# A `working` row is stamped at prompt submit only; a lane whose process died
# without a SessionEnd would otherwise hold the chief open for the 24h prune.
LANE_WORKING_MAX_AGE = timedelta(hours=12)
REFUSAL = ("No waiter armed while lanes are live: launch the poll (run_in_background) "
           "before ending the turn.")
COMPACT_NUDGE_MARKER = "chief-compact-nudge"
COMPACT_QUEUED_MARKER = "chief-compact-queued"
# Equals skills/_lib/chief/compact.py COMPACT_NUDGE_SECONDS (hooks do not import skills).
COMPACT_DEBOUNCE_S = 600
_COMPACT_DUE = re.compile(r"^COMPACT_DUE percent=(\d+) threshold=(\d+)\s*$")
_PROBE_TIMEOUT_S = 25.0
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _parse_stamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pid_alive(pid: int) -> bool:
    """Is process `pid` running? Never `os.kill(pid, 0)`: on Windows that terminates it."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def live_waiter(now: datetime, waiters_dir: Optional[Path] = None) -> bool:
    """Is any waiter heartbeat live? Unreadable or malformed files do not count."""
    directory = waiters_dir or _lib.state_dir() / WAITERS_DIRNAME
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return False
    for entry in entries:
        try:
            row = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        end = _parse_stamp(row.get("expected_end")) if isinstance(row, dict) else None
        if end is None or end < now:
            continue
        pid = row.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and not pid_alive(pid):
            continue
        return True
    return False


def _read_json_dict(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def working_lanes(now: datetime) -> Optional[Set[str]]:
    """Launcher ids of chief-managed lanes currently `working`; None when unreadable."""
    managed = _read_json_dict(_lib.state_dir() / MANAGED_FILENAME)
    rows = _read_json_dict(session_state.state_file())
    if managed is None or rows is None:
        return None
    lanes: Set[str] = set()
    for row in rows.values():
        if not isinstance(row, dict) or row.get("status") != WORKING:
            continue
        sid = row.get("launcher_session_id")
        stamp = _parse_stamp(row.get("updated_at"))
        if sid in managed and stamp is not None and now - stamp <= LANE_WORKING_MAX_AGE:
            lanes.add(str(sid))
    return lanes


def _chief_ops(*args: str) -> Optional[str]:
    """stdout of a `chief_ops.py` read, or None when it could not run or answer."""
    if not notify_on_idle.CHIEF_OPS_PYTHON.is_file() or not notify_on_idle.CHIEF_OPS.is_file():
        logger.warning("chief_waiter_stop: chief_ops.py or its venv is missing -- allowing the turn end")
        return None
    try:
        proc = subprocess.run(
            [str(notify_on_idle.CHIEF_OPS_PYTHON), str(notify_on_idle.CHIEF_OPS), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=_lib.NO_WINDOW,
            cwd=str(notify_on_idle.FLEET_CONFIG_ROOT), timeout=_PROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("chief_waiter_stop: `chief_ops.py %s` failed to run (%s) -- allowing the turn end", args[0], exc)
        return None
    return proc.stdout


def fleet_job_running() -> Optional[bool]:
    """True/False from the jobs API; None (logged) when it could not be read."""
    out = _chief_ops("fleet-jobs")
    line = next((l for l in (out or "").splitlines() if l.startswith("FLEET_JOBS=")), "")
    value = line.partition("=")[2].split()[0] if line else ""
    if value.isdigit():
        return int(value) > 0
    logger.warning("chief_waiter_stop: running fleet jobs unknown (%s) -- allowing the turn end", line or "no answer")
    return None


def doorbell_on() -> bool:
    """The launcher's doorbell wakes the chief with no waiter; unknown means arm one."""
    out = _chief_ops("wake") or ""
    return any(l.startswith("WAKE=doorbell") for l in out.splitlines())


def _marker_recent(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime < COMPACT_DEBOUNCE_S
    except OSError:
        return False


def compact_refusal(now: Optional[float] = None) -> Optional[str]:
    """The message that holds the chief's turn end at the compact threshold, or None."""
    clock = time.time() if now is None else now
    state = _lib.state_dir()
    if _marker_recent(state / COMPACT_NUDGE_MARKER, clock) or _marker_recent(state / COMPACT_QUEUED_MARKER, clock):
        return None
    line = next((l for l in (_chief_ops("compact-due") or "").splitlines() if l.startswith("COMPACT_")), "")
    match = _COMPACT_DUE.match(line)
    if match is None:
        if not line.startswith(("COMPACT_NOT_DUE", "COMPACT_OFF")):
            logger.warning("chief_waiter_stop: context use unknown (%s) -- allowing the turn end", line or "no answer")
        return None
    try:
        state.mkdir(parents=True, exist_ok=True)
        (state / COMPACT_NUDGE_MARKER).write_text(str(int(clock)), encoding="utf-8")
    except OSError as exc:
        # Without the marker every turn end would hold again: skip the hold rather than risk a loop.
        logger.warning("chief_waiter_stop: cannot write %s (%s) -- allowing the turn end", COMPACT_NUDGE_MARKER, exc)
        return None
    percent, threshold = match.groups()
    return (f"Context is at {percent}% (compact threshold {threshold}%). At this quiet point refresh the "
            "handover log and the plan, then run `chief_ops.py wake` and `chief_ops.py self-compact`, and end "
            "the turn on COMPACT_QUEUED. If a steer or decision is mid-flight, finish it first.")


def main() -> None:
    payload = _lib.read_stdin_json()
    if (os.environ.get(_lib.ENTRYPOINT_ENV_VAR, "").strip().lower().startswith("sdk")
            or _lib.is_scheduled_run() or payload.get("stop_hook_active")):
        _lib.allow()

    chief = chief_handover_sessionstart.is_chief_session(payload)
    if chief is None:
        logger.warning("chief_waiter_stop: %s unreadable -- chief-ness unknown, allowing the turn end",
                       session_state.state_file())
    if not chief:
        _lib.allow()

    compact_message = compact_refusal()
    if compact_message:
        _lib.block(compact_message)

    now = datetime.now(timezone.utc)
    if live_waiter(now):
        _lib.allow()

    lanes = working_lanes(now)
    if lanes is None:
        logger.warning("chief_waiter_stop: lane state unreadable -- allowing the turn end")
        _lib.allow()
    if not lanes and not fleet_job_running():
        _lib.allow()
    if doorbell_on():
        _lib.allow()
    _lib.block(REFUSAL)


if __name__ == "__main__":
    main()
