"""Box-wide advisory lock that keeps CPU-burn proofs and gate runs apart.

A red-under-load proof (``tests/_lib/cpu_burn.py``) saturates the shared box,
and a browser e2e gate running at the same time goes red on timeouts that have
nothing to do with its diff. Many gates may run together, but a burn must run
alone: gates take this lock in **shared** mode, a burn takes it **exclusive**.

The lock is a directory under the hooks-state root (``box-load-lock/``) with
one marker file per holder, ``<mode>.<pid>.<token>.json``, holding pid, repo,
mode and start time. Mode and pid live in the filename so deciding who holds
the lock is a directory listing, never a file read that could race the
holder's own write. Acquiring writes the marker *first* and then looks for a
conflicting holder, backing off (marker removed, jittered retry) if one is
there, so two racing acquirers can both back off but never both proceed.

The lock is **fair**: an acquirer that finds it busy takes a numbered queue
ticket (``wait.<mode>.<pid>.<ns>.<token>.json``) and keeps it until it holds
the lock or gives up. Every acquirer defers to an older ticket whose mode
conflicts with its own, and one without a ticket counts as newest. So a holder
that releases and immediately asks again (the perf sweep, one app after the
next) finds the waiting gate ahead of it and the gate gets the gap. A ticket
whose pid is confirmed gone is reclaimed like a dead holder; an unsettled pid
keeps its place.

A marker whose pid is confirmed gone is reclaimed: a crashed burn or gate
frees the lock on the next look. A pid whose state cannot be established, or a
lock directory that cannot be listed, counts as **held**: an unknown state is
never treated as free. Waiting is bounded; on timeout the caller gets
``BoxLockTimeout`` naming the holders, never an indefinite hang. A timeout
means the lock stayed busy, not that the gate failed: ``run`` reports it as an
``UNKNOWN`` line with exit 3, and the gate's own verdict is not established.

The lock is advisory and machine-local. CLI, for gates outside this repo:

    python skills/_lib/box_lock.py run --mode shared --repo <name>
                                       [--timeout-minutes M] -- <gate command>
    python skills/_lib/box_lock.py status

``run`` exits with the command's own code, or 3 (``UNKNOWN``, the command never
ran) when the lock could not be taken in time. ``status`` lists live holders.

stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from active_issue import pid_state  # noqa: E402
from fleet_core import runtime_files  # noqa: E402
from fleet_core.hooks_state import atomic_write_text, state_dir  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

LOCK_DIRNAME = "box-load-lock"
SHARED = "gate"
EXCLUSIVE = "burn"
MODES = (SHARED, EXCLUSIVE)
# A gate takes minutes and a burn proof up to its burners' self-exit deadline,
# so half an hour covers a normal wait while still ending a stuck one.
DEFAULT_TIMEOUT_MINUTES = 30.0
POLL_SECONDS = 5.0
# How often a waiter repeats its "waiting on ..." line, so a long wait shows
# it is alive without flooding the log.
REPORT_EVERY_SECONDS = 60.0
TIMEOUT_EXIT = 3
QUEUE = "wait"

log = logging.getLogger(__name__)


class BoxLockTimeout(RuntimeError):
    """The lock was not free (or its state not readable) before the deadline."""


def lock_dir() -> Path:
    return state_dir() / LOCK_DIRNAME


def _parse_marker(name: str) -> Optional[Dict[str, object]]:
    parts = name.split(".")
    if len(parts) != 4 or parts[0] not in MODES or not parts[1].isdigit() or parts[3] != "json":
        return None
    return {"mode": parts[0], "pid": int(parts[1]), "name": name}


def _parse_ticket(name: str) -> Optional[Dict[str, object]]:
    parts = name.split(".")
    if (len(parts) != 6 or parts[0] != QUEUE or parts[1] not in MODES
            or not parts[2].isdigit() or not parts[3].isdigit() or parts[5] != "json"):
        return None
    return {"mode": parts[1], "pid": int(parts[2]), "ns": int(parts[3]), "name": name, "queued": True}


def _describe(holder: Dict[str, object], folder: Path) -> Dict[str, object]:
    """Add repo and start time from the marker body, best effort (display only)."""
    try:
        body = json.loads((folder / str(holder["name"])).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    return {**holder, "repo": body.get("repo", "?"), "started": body.get("started", "?")}


def _scan(folder: Path, probe: Callable[[int], Optional[bool]]) -> Optional[tuple]:
    """``(live holders, live queue tickets oldest first)``, or ``None`` when unlistable.

    A marker or ticket whose pid is confirmed gone is reclaimed (unlinked). A pid
    the probe cannot settle stays: unknown is held, never free.
    """
    try:
        names = [entry.name for entry in folder.iterdir()]
    except FileNotFoundError:
        return [], []
    except OSError:
        return None
    live, queue = [], []
    for name in names:
        entry = _parse_marker(name) or _parse_ticket(name)
        if entry is None:
            continue
        if probe(int(entry["pid"])) is False:
            with contextlib.suppress(OSError):
                (folder / name).unlink()
            continue
        (queue if entry.get("queued") else live).append(_describe(entry, folder))
    queue.sort(key=lambda t: (t["ns"], t["name"]))
    return live, queue


def holders(folder: Optional[Path] = None,
            probe: Callable[[int], Optional[bool]] = pid_state) -> Optional[List[Dict[str, object]]]:
    """Live holders of the lock, or ``None`` when the directory can't be listed."""
    scanned = _scan(folder or lock_dir(), probe)
    return None if scanned is None else scanned[0]


def _conflicts(mode: str, live: List[Dict[str, object]], own: str) -> List[Dict[str, object]]:
    others = [h for h in live if h["name"] != own]
    if mode == EXCLUSIVE:
        return others
    return [h for h in others if h["mode"] == EXCLUSIVE]


def _ahead(mode: str, queue: List[Dict[str, object]], ticket: Optional[str]) -> List[Dict[str, object]]:
    """Queued tickets of a conflicting mode that an acquirer holding ``ticket`` must let go first.

    Without a ticket the acquirer has not queued yet, so every conflicting ticket is older.
    """
    older = []
    for entry in queue:
        if entry["name"] == ticket:
            break
        older.append(entry)
    return _conflicts(mode, older, "")


def describe_holders(live: Optional[List[Dict[str, object]]]) -> str:
    if live is None:
        return "the lock state could not be read (treated as held)"
    if not live:
        return "no holders"
    return "; ".join(
        f"{'queued ' if h.get('queued') else ''}{h['mode']} pid {h['pid']} ({h['repo']}, since {h['started']})"
        for h in live)


def try_acquire(mode: str, repo: str, folder: Optional[Path] = None,
                probe: Callable[[int], Optional[bool]] = pid_state,
                ticket: Optional[str] = None) -> tuple:
    """One attempt: ``(marker_path, None)`` on success, ``(None, blockers)`` if not.

    ``blockers`` is the conflicting holders plus the older queued tickets this
    acquirer defers to, or ``None`` for an unreadable lock state. ``ticket`` is
    the caller's own queue ticket name from ``enqueue``, if it has one. The own
    marker is written before the look and removed again on a conflict, so a
    racing acquirer always sees it.
    """
    if mode not in MODES:
        raise ValueError(f"unknown lock mode {mode!r}")
    folder = folder or lock_dir()
    name = f"{mode}.{os.getpid()}.{uuid.uuid4().hex}.json"
    marker = folder / name
    try:
        runtime_files.make_dirs(folder, parents=True, exist_ok=True)
        atomic_write_text(marker, json.dumps({
            "pid": os.getpid(), "repo": repo, "mode": mode,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
    except OSError:
        return None, None
    scanned = _scan(folder, probe)
    blockers = None if scanned is None else _conflicts(mode, scanned[0], name) + _ahead(mode, scanned[1], ticket)
    if blockers == []:
        return marker, None
    with contextlib.suppress(OSError):
        marker.unlink()
    return None, blockers


def enqueue(mode: str, repo: str, folder: Optional[Path] = None,
            now_ns: Callable[[], int] = time.time_ns) -> Optional[str]:
    """Take a queue ticket and return its file name, or ``None`` if it cannot be written.

    No ticket only costs the fairness: the caller still polls for the lock.
    """
    folder = folder or lock_dir()
    name = f"{QUEUE}.{mode}.{os.getpid()}.{now_ns()}.{uuid.uuid4().hex}.json"
    try:
        runtime_files.make_dirs(folder, parents=True, exist_ok=True)
        atomic_write_text(folder / name, json.dumps({
            "pid": os.getpid(), "repo": repo, "mode": mode,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
    except OSError:
        return None
    return name


def dequeue(ticket: Optional[str], folder: Optional[Path] = None) -> None:
    if ticket is not None:
        with contextlib.suppress(OSError):
            ((folder or lock_dir()) / ticket).unlink()


def release(marker: Path) -> None:
    with contextlib.suppress(OSError):
        marker.unlink()


@contextlib.contextmanager
def held(mode: str, repo: str, timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES,
         folder: Optional[Path] = None, poll_seconds: float = POLL_SECONDS,
         probe: Callable[[int], Optional[bool]] = pid_state,
         report: Callable[[str], None] = log.info,
         clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> Iterator[Path]:
    """Hold the lock in ``mode`` for the body, waiting at most ``timeout_minutes``.

    The first busy look queues a ticket (see the module doc), so the wait is in
    arrival order. Raises ``BoxLockTimeout`` when it is still held (or unreadable)
    at the deadline. ``clock`` and ``sleep`` are injectable so a wait is testable
    without real time.
    """
    deadline = clock() + timeout_minutes * 60
    last_report = None
    ticket = None
    try:
        while True:
            marker, blockers = try_acquire(mode, repo, folder, probe, ticket)
            if marker is not None:
                break
            if ticket is None:
                ticket = enqueue(mode, repo, folder)
            now = clock()
            if now >= deadline:
                raise BoxLockTimeout(
                    f"box load lock: no {mode} slot after {timeout_minutes:g} min -- "
                    f"held by {describe_holders(blockers)}")
            if last_report is None or now - last_report >= REPORT_EVERY_SECONDS:
                report(f"ℹ️ box load lock: waiting for a {mode} slot -- {describe_holders(blockers)}")
                last_report = now
            sleep(min(poll_seconds * random.uniform(0.5, 1.5), max(0.0, deadline - now)))
    finally:
        dequeue(ticket, folder)
    try:
        yield marker
    finally:
        release(marker)


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="box_lock.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run a command holding the lock")
    run.add_argument("--mode", choices=("shared", "exclusive"), default="shared")
    run.add_argument("--repo", default=Path.cwd().name)
    run.add_argument("--timeout-minutes", type=float, default=DEFAULT_TIMEOUT_MINUTES)
    run.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("status", help="list live holders")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.action == "status":
        print(describe_holders(holders()), file=sys.stdout)
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given (put it after --)")
    program = shutil.which(command[0])
    if program is None:
        parser.error(f"command not found: {command[0]}")
    mode = SHARED if args.mode == "shared" else EXCLUSIVE
    try:
        with held(mode, args.repo, args.timeout_minutes):
            # A windowless child given no std handles writes to its own hidden
            # console, so the gate's output is handed over explicitly.
            sys.stdout.flush()
            sys.stderr.flush()
            # subprocess-timeout-exempt: foreground runner for the caller's own gate command, whose length is the caller's to set
            return subprocess.run([program, *command[1:]], stdout=sys.stdout.fileno(),
                                  stderr=sys.stderr.fileno(), creationflags=NO_WINDOW).returncode
    except BoxLockTimeout as exc:
        print(f"UNKNOWN box_load_lock: {exc}; the command did not run, this is not a failure of it",
              file=sys.stderr, flush=True)
        return TIMEOUT_EXIT


if __name__ == "__main__":
    sys.exit(main())
