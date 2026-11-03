"""A machine-wide claim so only one /propagate-vendored wave runs at a time.

Two wave drivers at once collide on the same adopters' branches and worktrees. The claim is
the lock primitive `/issue-start` already uses (`worktree.claim`: an atomically published
lock directory, a TTL for a holder that crashed), pointed at one directory under the hooks
state root instead of a repo's git dir.

A wave takes it with `plan.py plan --claim --holder <wave-id>` (a re-run by the same holder
renews it) and drops it with `wave_claim.py release --holder <wave-id>` when the wave ends.
A driver that never asks holds nothing and is never refused, so a wave already running when
this landed keeps working.

Every answer is one of three states: `held` (this holder owns it), `refused` (another wave
does), or `unknown` (the claim could not be read or written). Unknown is never treated as
free: the caller stops and says so.

CLI (exit 0 held/free/released, 1 refused or not the holder, 3 unknown):

  acquire --holder <id> [--session <sid>] [--ttl-hours H] [--lock-dir D]
  release --holder <id> [--lock-dir D]
  status [--lock-dir D]

stdlib + `worktree.claim` only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core.hooks_state import state_dir  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from worktree.claim import DEFAULT_TTL_HOURS, is_stale, read_meta, try_acquire, write_meta  # noqa: E402

ensure_utf8_stdio()

HELD, REFUSED, UNKNOWN, FREE = "held", "refused", "unknown", "free"
RELEASED, ABSENT, NOT_HOLDER = "released", "absent", "not-holder"
LOCK_PARENT = "propagate-vendored"
LOCK_NAME = "wave.lock"
EXIT_REFUSED = 1
EXIT_UNKNOWN = 3  # the fleet's "could not establish it" code


@dataclass
class Claim:
    state: str
    holder: dict = field(default_factory=dict)
    reason: str = ""


def default_lock_dir() -> Path:
    return state_dir() / LOCK_PARENT / LOCK_NAME


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))


def acquire(lock: Path, holder: str, now: float, ttl_hours: float = DEFAULT_TTL_HOURS,
            session: Optional[str] = None) -> Claim:
    meta = {"holder": holder, "session": session, "created": now, "created_iso": _stamp(now)}
    try:
        # runtime-write-exempt: the lock directory is the caller's, by default under the hooks state root, never a code folder
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            current = read_meta(lock)
            if current is None:
                # A published claim always carries its meta, so a present-but-unreadable one is a
                # fault, not a free claim; only age (the crashed-holder valve) may reclaim it.
                if (now - lock.stat().st_mtime) <= ttl_hours * 3600.0:
                    return Claim(UNKNOWN, reason=f"claim at {lock} is present but its holder could not be read")
            elif current.get("holder") == holder and not is_stale(current, now, ttl_hours):
                renewed = {**meta, "created_iso": current.get("created_iso", meta["created_iso"])}
                write_meta(lock, renewed)
                return Claim(HELD, renewed)
        mode, other = try_acquire(lock, meta, now, ttl_hours)
    except OSError as exc:
        return Claim(UNKNOWN, reason=f"claim at {lock} could not be read or written: {exc}")
    if mode == "primary":
        return Claim(HELD, meta)
    return Claim(REFUSED, other)


def status(lock: Path, now: Optional[float] = None, ttl_hours: float = DEFAULT_TTL_HOURS) -> Claim:
    try:
        if not lock.exists():
            return Claim(FREE)
        current = read_meta(lock)
    except OSError as exc:
        return Claim(UNKNOWN, reason=f"claim at {lock} could not be read: {exc}")
    if current is None:
        return Claim(UNKNOWN, reason=f"claim at {lock} is present but its holder could not be read")
    if is_stale(current, time.time() if now is None else now, ttl_hours):
        return Claim(FREE, current, "expired")
    return Claim(HELD, current)


def release(lock: Path, holder: str) -> str:
    """Drop the claim only for the wave that holds it, so a finishing wave cannot free another's."""
    try:
        if not lock.exists():
            return ABSENT
        current = read_meta(lock)
        if current is None:
            return UNKNOWN
        if current.get("holder") != holder:
            return NOT_HOLDER
        shutil.rmtree(lock)
        return RELEASED
    except OSError:
        return UNKNOWN


def _who(claim: Claim) -> str:
    h = claim.holder
    line = f"holder={h.get('holder', '?')} since={h.get('created_iso', '?')}"
    return f"{line} session={h['session']}" if h.get("session") else line


def refusal_line(claim: Claim) -> str:
    if claim.state == REFUSED:
        return f"REFUSED=wave-running {_who(claim)}"
    return f"WAVE_CLAIM=unknown reason={claim.reason}"


def report(claim: Claim) -> int:
    """Print the claim's one line and return the exit code a caller branches on."""
    if claim.state == HELD:
        sys.stdout.write(f"WAVE_CLAIM=held {_who(claim)}\n")
        return 0
    sys.stdout.write(refusal_line(claim) + "\n")
    return EXIT_REFUSED if claim.state == REFUSED else EXIT_UNKNOWN


def holder_session() -> Optional[str]:
    """The launcher session id, recorded so a refusal says which session to look at."""
    return os.environ.get("APP_LAUNCHER_SESSION_ID") or None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One /propagate-vendored wave at a time.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire", help="take the wave claim, or print why it was refused")
    r = sub.add_parser("release", help="drop the claim this holder owns")
    s = sub.add_parser("status", help="print who holds the claim")
    for p in (a, r, s):
        p.add_argument("--lock-dir", default=None, help="claim directory (default: under the hooks state root)")
    for p in (a, r):
        p.add_argument("--holder", required=True, help="the wave id, stable across the wave's calls")
    a.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    args = ap.parse_args(argv)

    lock = Path(args.lock_dir) if args.lock_dir else default_lock_dir()
    if args.cmd == "acquire":
        return report(acquire(lock, args.holder, time.time(), args.ttl_hours, holder_session()))
    if args.cmd == "release":
        outcome = release(lock, args.holder)
        sys.stdout.write(f"WAVE_CLAIM={outcome}\n")
        return {NOT_HOLDER: EXIT_REFUSED, UNKNOWN: EXIT_UNKNOWN}.get(outcome, 0)
    claim = status(lock)
    if claim.state == HELD:
        sys.stdout.write(f"WAVE_CLAIM=held {_who(claim)}\n")
    elif claim.state == UNKNOWN:
        sys.stdout.write(f"WAVE_CLAIM=unknown reason={claim.reason}\n")
        return EXIT_UNKNOWN
    else:
        sys.stdout.write("WAVE_CLAIM=free" + (f" ({claim.reason})" if claim.reason else "") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
