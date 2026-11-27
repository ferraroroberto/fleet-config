"""Shared JSON-row state files under the hooks-state root, plus the clock and pid helpers they rely on.

Several writers keep a small advisory JSON mapping there (`active_issue.py`,
`chief_managed.py`, the chief's journal, plan and proposal stores, the worktree
config, `inspiration-scan`). They all need the same four things, so they share
one home instead of importing them out of whichever feature module wrote them
first:

* a lock directory that serializes a whole read-modify-write cycle
  (`state_lock`), whose stale-reclaim needs a tri-state pid probe (`pid_state`);
* tolerant or checked reads and atomic writes of the mapping (`read_rows`,
  `read_rows_checked`, `write_rows`);
* a TTL prune keyed on a row's own timestamp field (`prune_rows`);
* UTC clock helpers (`utc_now`, `iso_z`) so every row stamps time the same way.

stdlib only; the state root itself resolves through `hooks_state.state_dir()`.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from fleet_core import runtime_files
from fleet_core.hooks_state import atomic_write_text, state_dir

PRUNE_AFTER = timedelta(hours=24)
LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_AFTER_SECONDS = 30.0
_REPLACE_ATTEMPTS = 3
_LOCK_OWNER_PREFIX = "owner"
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_STILL_ACTIVE = 259
_WIN_ERROR_ACCESS_DENIED = 5
_WIN_ERROR_INVALID_PARAMETER = 87


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def pid_state(pid: int) -> Optional[bool]:
    """Tri-state pid probe for claim-owner liveness: True alive, False
    confirmed gone, None when the probe cannot tell.

    A claim's ``dead`` needs positive evidence, so "could
    not open the process" stays ``None`` rather than folding into dead; the
    state lock reads ``pid_state(pid) is True``, where unknown counts as not
    alive because the lock's age must also exceed the stale horizon. Access
    denied means the process exists. A pid reused by an unrelated process
    reads as alive: the failure to avoid is displacing a live holder.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(_WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            if error == _WIN_ERROR_INVALID_PARAMETER:
                return False
            if error == _WIN_ERROR_ACCESS_DENIED:
                return True
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == _WIN_STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def read_lock_owner(lock_dir: Path) -> Optional[Dict[str, Any]]:
    """Identify the marker file by name only -- never open it.

    The owner's pid and token live in the filename itself (``owner.<pid>.
    <token>``), so a staleness check is a directory listing, not a file
    open. A file read here would race the true holder's own open/close of
    that same name on Windows (``WinError 32``, "used by another process"),
    which is exactly the flake this design avoids.
    """
    try:
        entries = list(lock_dir.iterdir())
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return None
    for entry in entries:
        parts = entry.name.split(".", 2)
        if len(parts) == 3 and parts[0] == _LOCK_OWNER_PREFIX and parts[1].isdigit():
            return {"pid": int(parts[1]), "token": parts[2]}
    return None


def _write_lock_owner(lock_dir: Path, token: str) -> None:
    marker = lock_dir / f"{_LOCK_OWNER_PREFIX}.{os.getpid()}.{token}"
    runtime_files.touch_file(marker, exist_ok=False)


def state_file(filename: str) -> Path:
    """Resolve a state path under the shared hooks-state root at call time so
    tests can override it (`CLAUDE_HOOKS_STATE_DIR`, via `hooks_state.state_dir()`)."""
    return state_dir() / filename


def _parse_timestamp(row: Any, field: str) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    raw = row.get(field)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def read_rows(path: Path) -> Dict[str, Any]:
    """Return the current mapping, or an empty mapping for invalid input."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_rows_checked(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(rows, error)` for a caller that must not act on an unread file: a missing file is no rows
    (nothing written yet), an unreadable one is `None` with the reason, never empty."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path.name} unreadable: {exc}"
    try:
        rows = json.loads(text)
    except ValueError as exc:
        return None, f"{path.name} is not JSON: {exc}"
    if not isinstance(rows, dict):
        return None, f"{path.name} is not a mapping"
    return rows, ""


def prune_rows(
    rows: Dict[str, Any],
    *,
    stamp_field: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Keep only well-formed records whose `stamp_field` timestamp is no
    older than :data:`PRUNE_AFTER`."""
    cutoff = (now or utc_now()) - PRUNE_AFTER
    kept: Dict[str, Any] = {}
    for key, row in rows.items():
        stamp = _parse_timestamp(row, stamp_field)
        if stamp is not None and stamp >= cutoff:
            kept[str(key)] = row
    return kept


def write_rows(path: Path, rows: Dict[str, Any]) -> None:
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
    payload = json.dumps(rows, indent=2, sort_keys=True) + "\n"
    last_error: Optional[OSError] = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            atomic_write_text(path, payload, newline="\n")
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    assert last_error is not None
    raise last_error


@contextmanager
def state_lock(
    path: Path,
    *,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
    stale_after_seconds: float = STALE_LOCK_AFTER_SECONDS,
) -> Iterator[None]:
    """Serialize one state-file read-modify-write transaction.

    ``mkdir`` is atomic on Windows and POSIX.  The holder's pid and a random
    token are recorded inside the lock directory on acquire.  A contender
    only reclaims the directory once it is *both* older than
    ``stale_after_seconds`` *and* its recorded pid is confirmed dead --  age
    alone can't tell a crashed holder from one that is merely slow, and a
    slow-but-alive holder must never be displaced.  Release verifies the
    lock directory still carries this call's own token before removing it,
    so a holder that was reclaimed while it slept past the stale horizon
    can't turn around and delete the reclaimer's lock out from under it.
    """
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
    lock_dir = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_seconds
    token = uuid.uuid4().hex
    while True:
        try:
            runtime_files.make_dirs(lock_dir)
            break
        except (FileExistsError, PermissionError):
            # Windows can surface PermissionError instead of FileExistsError
            # when this mkdir() races another thread's rmdir() on the same
            # path (NTFS reports "access denied" for a directory mid-deletion
            # rather than "already exists") — treat it identically: retry.
            try:
                age = time.time() - lock_dir.stat().st_mtime
                owner = read_lock_owner(lock_dir)
                holder_alive = owner is not None and pid_state(owner.get("pid", -1)) is True
                if age > stale_after_seconds and not holder_alive:
                    shutil.rmtree(lock_dir)
                    continue
            except (FileNotFoundError, PermissionError):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for state lock: {lock_dir}")
            time.sleep(0.025)
    try:
        _write_lock_owner(lock_dir, token)
        yield
    finally:
        owner = read_lock_owner(lock_dir)
        if owner is None or owner.get("token") == token:
            try:
                shutil.rmtree(lock_dir)
            except FileNotFoundError:
                pass
        # else: someone else reclaimed this lock directory while we held it
        # past the stale horizon (should not happen given the liveness check
        # above, but this is the last line of defense) -- removing it here
        # would delete the reclaimer's live lock, not ours.
