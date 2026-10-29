"""The chief's waiter heartbeat: proof that a wake path is armed.

`hooks/chief_waiter_stop.py` refuses the chief's turn end while lanes run and no
waiter is armed. A waiter proves it is armed by holding one small file in
`state_dir()/chief-waiters/` for as long as it runs: `chief_ops.py wait-event`
does it itself, an ad-hoc shell waiter calls this module at start and exit.

    ID=$(python waiter_heartbeat.py start --seconds 3000)
    trap 'python waiter_heartbeat.py stop "$ID"' EXIT

One file per waiter, so concurrent waiters never rewrite each other's state and
removing one never disarms another. The hook trusts a file only while its
`expected_end` (plus the margin below) is ahead and, when a `pid` is recorded,
that process is alive: a killed waiter leaves a file that stops counting.
A shell waiter records no pid on purpose -- the interpreter that runs this
helper is a short-lived child of the venv launcher, so its parent pid is dead
the moment the helper returns.

The file layout is a contract with `hooks/chief_waiter_stop.py`, which reads it
on its own because `hooks/` and `skills/_lib/` stay independent trees.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional

# A direct run (`python waiter_heartbeat.py ...`) starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import hooks_state  # noqa: E402

logger = logging.getLogger("fleet_chief")

WAITERS_DIRNAME = "chief-waiters"
# A waiter's exit comes a settle window and a board read after its timeout.
EXPECTED_END_MARGIN_SECONDS = 60.0


def waiters_dir() -> Path:
    return hooks_state.state_dir() / WAITERS_DIRNAME


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start(
    expected_seconds: float, *, pid: Optional[int] = None, waiter_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Record a waiter expected to end within `expected_seconds`; returns its id."""
    moment = now or datetime.now(timezone.utc)
    waiter_id = waiter_id or uuid.uuid4().hex[:12]
    target = waiters_dir()
    target.mkdir(parents=True, exist_ok=True)
    row = {
        "pid": pid,
        "started": _iso(moment),
        "expected_end": _iso(moment + timedelta(seconds=expected_seconds + EXPECTED_END_MARGIN_SECONDS)),
    }
    hooks_state.atomic_write_text(target / f"{waiter_id}.json", json.dumps(row))
    return waiter_id


def stop(waiter_id: str) -> None:
    """Remove the waiter's file; a missing one is already stopped."""
    try:
        (waiters_dir() / f"{Path(waiter_id).name}.json").unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("⚠️ waiter heartbeat %s could not be removed (%s); it expires on its own", waiter_id, exc)


@contextlib.contextmanager
def armed(expected_seconds: float, *, pid: Optional[int] = None) -> Iterator[None]:
    """Hold a heartbeat for the body of the `with`. A heartbeat that cannot be
    written is logged and the waiter still runs: the hook then asks for a
    re-arm, which is the safe direction."""
    waiter_id: Optional[str] = None
    try:
        waiter_id = start(expected_seconds, pid=pid)
    except OSError as exc:
        logger.warning("⚠️ waiter heartbeat could not be written (%s); the Stop hook will not see this waiter", exc)
    try:
        yield
    finally:
        if waiter_id:
            stop(waiter_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start", help="record a heartbeat and print its id")
    st.add_argument("--seconds", type=float, required=True, help="how long the waiter may run")
    st.add_argument("--pid", type=int, default=None, help="a process whose death ends the waiter")
    sp = sub.add_parser("stop", help="remove a heartbeat by id")
    sp.add_argument("waiter_id")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "start":
            print(start(args.seconds, pid=args.pid))
        else:
            stop(args.waiter_id)
    except OSError as exc:
        print(f"WAITER_HEARTBEAT=failed reason={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
