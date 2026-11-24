"""The log part of `chief_ops.py doctor`: recent errors, grouped by message.

Two sources are read, never written:

* the app-launcher's logs, every ``*.log`` in its checkout's ``webapp/`` folder
  (the webapp's warnings, the session-host, the doorbell, the watchdog). An
  ``ERROR``/``CRITICAL`` line or a traceback in the window is a failure.
* the hooks' own logs, every ``*.log`` in the shared hooks-state folder. Hooks
  report through their harness, not a file, so this folder holds only the logs
  a hook family keeps on purpose; the same error rule applies.

asyncio's own reports of a client that went away mid-connection (an SSL
shutdown that timed out, a reset pipe, a closed websocket), and the traceback
printed under one, are notes: the server library logs them at ERROR, but they
describe the client, not this machine's setup.

Messages are grouped once ids, hex runs and numbers are masked, so one fault
repeated a hundred times reads as one group with its count. The output shape
matches app-launcher-lite's doctor so one report template reads both.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

WINDOW = timedelta(hours=24)
# A busy warnings log grows a few MB a day; this tail covers far more than the window.
MAX_TAIL_BYTES = 2 * 1024 * 1024
# Logs a gate or a one-off verification writes for its own disposable instance, not this machine's launcher.
SKIPPED_PREFIXES = ("e2e-", "verify-", "visual-")
MESSAGE_CHARS = 160

FAIL = "fail"
NOTE = "note"

_TS = re.compile(r"^\[?(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_ERROR = re.compile(r"\[(?:ERROR|CRITICAL)\]|^\S+ \S+ (?:ERROR|CRITICAL) ")
_TRACEBACK = "Traceback (most recent call last)"
# A date-time or a bare time-of-day (the session-host's format), then an optional level word.
_LEADING_STAMP = re.compile(r"^\[?(?:\d{4}-\d{2}-\d{2}[ T])?\d{2}:\d{2}:\d{2}[\d,.]*\]?\s*(?:\[\w+\]\s*|[A-Z]+\s+)?")
_CLIENT_GONE = re.compile(
    r"asyncio: (?:Unhandled exception in client_connected_cb"
    r"|Exception in callback _ProactorBasePipeTransport\._call_connection_lost"
    r"|ConnectionClosedError exception in shielded future)"
)
_VOLATILE = (
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<id>"),
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<id>"),
    (re.compile(r"\d+(?:[.,]\d+)?"), "N"),
)


@dataclass
class Group:
    """One repeated message: where it came from, how bad, how often, and when last."""

    source: str
    kind: str
    severity: str
    message: str
    count: int
    last: datetime


def normalise(message: str) -> str:
    text = _LEADING_STAMP.sub("", message.strip())
    for pattern, mask in _VOLATILE:
        text = pattern.sub(mask, text)
    return text[:MESSAGE_CHARS]


def _tail_lines(path: Path) -> List[str]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - MAX_TAIL_BYTES))
        data = fh.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    # A tail that starts mid-file starts mid-line too.
    return lines[1:] if size > MAX_TAIL_BYTES else lines


def _log_files(folder: Path) -> List[Path]:
    """``*.log`` files directly in ``folder``; [] when it does not exist, OSError when unreadable."""
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return []
    return sorted(Path(e.path) for e in entries if e.name.endswith(".log") and e.is_file())


class _Groups:
    def __init__(self) -> None:
        self.rows: Dict[Tuple[str, str, str], Group] = {}

    def add(self, source: str, kind: str, severity: str, message: str, moment: datetime) -> None:
        key = (source, kind, normalise(message))
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = Group(source, kind, severity, key[2], 1, moment)
            return
        row.count += 1
        row.last = max(row.last, moment)


def _events(lines: Iterable[str], fallback: datetime) -> Iterable[Tuple[str, str, str, datetime]]:
    """``(kind, severity, message, local time)`` per error line or traceback.

    An untimed line takes the line above's time; a traceback takes the severity of the
    error line it was printed under.
    """
    moment: Optional[datetime] = None
    in_traceback = False
    under = FAIL
    for line in lines:
        stamp = _TS.match(line)
        if stamp:
            try:
                moment = datetime.strptime(f"{stamp.group(1)} {stamp.group(2)}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        if line.startswith(_TRACEBACK):
            in_traceback = True
            continue
        if in_traceback:
            # The traceback's own exception line is the first unindented line after the frames.
            if line and not line[0].isspace() and not line.startswith("During handling"):
                in_traceback = False
                yield "traceback", under, line, moment or fallback
                under = FAIL
            continue
        if _ERROR.search(line):
            under = NOTE if _CLIENT_GONE.search(line) else FAIL
            yield "error", under, line, moment or fallback
        elif stamp:
            under = FAIL


def scan_logs(log_dir: Path, source: str, now: datetime) -> List[Group]:
    """Errors and tracebacks in ``log_dir/*.log`` within the window (``now`` is naive local time)."""
    groups = _Groups()
    for path in _log_files(log_dir):
        if path.name.startswith(SKIPPED_PREFIXES):
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if now - modified > WINDOW:
            continue
        for kind, severity, message, moment in _events(_tail_lines(path), modified):
            if now - moment <= WINDOW:
                groups.add(f"{source}:{path.name}", kind, severity, message, moment)
    return list(groups.rows.values())
