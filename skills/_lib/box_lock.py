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

A marker whose pid is confirmed gone is reclaimed: a crashed burn or gate
frees the lock on the next look. A pid whose state cannot be established, or a
lock directory that cannot be listed, counts as **held**: an unknown state is
never treated as free. Waiting is bounded; on timeout the caller gets
``BoxLockTimeout`` naming the holders, never an indefinite hang.

The lock is advisory and machine-local. CLI, for gates outside this repo:

    python skills/_lib/box_lock.py run --mode shared --repo <name>
                                       [--timeout-minutes M] -- <gate command>
    python skills/_lib/box_lock.py status

``run`` exits with the command's own code, or 3 when the lock could not be
taken in time. ``status`` lists live holders.

stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from active_issue import pid_state  # noqa: E402
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


def _describe(holder: Dict[str, object], folder: Path) -> Dict[str, object]:
    """Add repo and start time from the marker body, best effort (display only)."""
    try:
        body = json.loads((folder / str(holder["name"])).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    return {**holder, "repo": body.get("repo", "?"), "started": body.get("started", "?")}


def holders(folder: Optional[Path] = None,
            probe: Callable[[int], Optional[bool]] = pid_state) -> Optional[List[Dict[str, object]]]:
    """Live holders of the lock, or ``None`` when the directory can't be listed.

    A marker whose pid is confirmed gone is reclaimed (unlinked) here. A pid the
    probe cannot settle stays in the list: unknown is held, never free.
    """
    folder = folder or lock_dir()
    try:
        names = [entry.name for entry in folder.iterdir()]
    except FileNotFoundError:
        return []
    except OSError:
        return None
    live = []
    for name in names:
        holder = _parse_marker(name)
        if holder is None:
            continue
        if probe(int(holder["pid"])) is False:
            with contextlib.suppress(OSError):
                (folder / name).unlink()
            continue
        live.append(_describe(holder, folder))
    return live


def _conflicts(mode: str, live: List[Dict[str, object]], own: str) -> List[Dict[str, object]]:
    others = [h for h in live if h["name"] != own]
    if mode == EXCLUSIVE:
        return others
    return [h for h in others if h["mode"] == EXCLUSIVE]


def describe_holders(live: Optional[List[Dict[str, object]]]) -> str:
    if live is None:
        return "the lock state could not be read (treated as held)"
    if not live:
        return "no holders"
    return "; ".join(f"{h['mode']} pid {h['pid']} ({h['repo']}, since {h['started']})" for h in live)


def try_acquire(mode: str, repo: str, folder: Optional[Path] = None,
                probe: Callable[[int], Optional[bool]] = pid_state) -> tuple:
    """One attempt: ``(marker_path, None)`` on success, ``(None, blockers)`` if not.

    ``blockers`` is the conflicting holder list, or ``None`` for an unreadable
    lock state. The own marker is written before the look and removed again on
    a conflict, so a racing acquirer always sees it.
    """
    if mode not in MODES:
        raise ValueError(f"unknown lock mode {mode!r}")
    folder = folder or lock_dir()
    name = f"{mode}.{os.getpid()}.{uuid.uuid4().hex}.json"
    marker = folder / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker, json.dumps({
            "pid": os.getpid(), "repo": repo, "mode": mode,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
    except OSError:
        return None, None
    live = holders(folder, probe)
    blockers = None if live is None else _conflicts(mode, live, name)
    if blockers == []:
        return marker, None
    with contextlib.suppress(OSError):
        marker.unlink()
    return None, blockers


def release(marker: Path) -> None:
    with contextlib.suppress(OSError):
        marker.unlink()


@contextlib.contextmanager
def held(mode: str, repo: str, timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES,
         folder: Optional[Path] = None, poll_seconds: float = POLL_SECONDS,
         probe: Callable[[int], Optional[bool]] = pid_state,
         report: Callable[[str], None] = log.info) -> Iterator[Path]:
    """Hold the lock in ``mode`` for the body, waiting at most ``timeout_minutes``.

    Raises ``BoxLockTimeout`` when it is still held (or unreadable) at the deadline.
    """
    deadline = time.monotonic() + timeout_minutes * 60
    last_report = None
    while True:
        marker, blockers = try_acquire(mode, repo, folder, probe)
        if marker is not None:
            break
        now = time.monotonic()
        if now >= deadline:
            raise BoxLockTimeout(
                f"box load lock: no {mode} slot after {timeout_minutes:g} min -- "
                f"held by {describe_holders(blockers)}")
        if last_report is None or now - last_report >= REPORT_EVERY_SECONDS:
            report(f"ℹ️ box load lock: waiting for a {mode} slot -- {describe_holders(blockers)}")
            last_report = now
        time.sleep(min(poll_seconds * random.uniform(0.5, 1.5), max(0.0, deadline - now)))
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
            return subprocess.run([program, *command[1:]], stdout=sys.stdout.fileno(),
                                  stderr=sys.stderr.fileno(), creationflags=NO_WINDOW).returncode
    except BoxLockTimeout as exc:
        print(f"❌ {exc}", file=sys.stderr, flush=True)
        return TIMEOUT_EXIT


if __name__ == "__main__":
    sys.exit(main())
