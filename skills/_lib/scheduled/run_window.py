"""Wall-clock window for an unattended fleet run that works a long queue.

`/cleanup-fleet-all` ships one issue after another for as many hours as the
queue is long, and its Job is chained into a weekly block that has the next
scheduled work behind it. Nothing bounded that: a run is only as short as its
queue, and a workflow script cannot read a clock (`Date.now()` throws there), so
the bound has to be a fact a lane agent reads from here before it starts.

Subcommands
-----------
  arm   [--hours H] [--state-dir PATH] [--unattended]
        Records when this run's window closes. Only an unattended run (launched
        by `scheduled_runner.py`, which exports `FLEET_SCHEDULED_RUN=1`, or
        passing `--unattended`) is bounded: an attended run clears any earlier
        state and stays unbounded, because a human is watching it. Prints:
          WINDOW_ARMED=yes|no
          WINDOW_ENDS_AT=<ISO UTC|null>
          WINDOW_SECONDS=<float|null>
  check [--state-dir PATH]
        Prints:
          WINDOW=open|closed|unarmed|unknown
          REMAINING_SECONDS=<float|null>
          WINDOW_ENDS_AT=<ISO UTC|null>
        `unarmed` is "no window was armed" (an attended run); `unknown` is "a
        window may exist but the state could not be read", which a caller must
        never fold into `open`.

The decision (`window_state`) is pure and unit-tested with a fixed clock in
`tests/claude_progress/test_run_window.py`. stdlib + `hooks_state.state_dir()`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core.hooks_state import state_dir  # noqa: E402
from fleet_core.runtime_files import make_dirs, save_text  # noqa: E402
from rate_gate import UNATTENDED_ENV, is_unattended  # noqa: E402

# The run starts after Thursday's audit chain and the next scheduled block opens
# about seven hours later; a lane that starts just inside the window can run for
# up to an hour, so the window closes well before that block.
DEFAULT_WINDOW_HOURS = 5.0
STATE_FILE = "run-window.json"


def _parse_iso(value: object) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


def window_state(state: Optional[Mapping[str, object]], now: _dt.datetime) -> tuple[str, Optional[float]]:
    """``(open|closed|unarmed|unknown, seconds left)`` for one read of the state file.

    ``state`` is ``None`` when no file exists (nothing armed). A file that is
    present but lacks a readable deadline is ``unknown``, never ``open``.
    """
    if state is None:
        return "unarmed", None
    ends = _parse_iso(state.get("ends_at"))
    if ends is None:
        return "unknown", None
    remaining = (ends - now).total_seconds()
    return ("open", remaining) if remaining > 0 else ("closed", remaining)


def load_state(path: Path) -> Optional[Mapping[str, object]]:
    """The parsed state, ``None`` if absent, and a non-mapping marker if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return {"unreadable": True}
    try:
        data = json.loads(text)
    except ValueError:
        return {"unreadable": True}
    return data if isinstance(data, dict) else {"unreadable": True}


def _fmt(value: Optional[float]) -> str:
    return "null" if value is None else str(round(value, 1))


def cmd_arm(hours: float, directory: Path, unattended: bool, now: _dt.datetime) -> None:
    path = directory / STATE_FILE
    if not unattended:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        print("WINDOW_ARMED=no")
        print("WINDOW_ENDS_AT=null")
        print("WINDOW_SECONDS=null")
        return
    ends = now + _dt.timedelta(hours=hours)
    make_dirs(directory, parents=True, exist_ok=True)
    save_text(path, json.dumps({"armed_at": now.isoformat(), "ends_at": ends.isoformat()}), encoding="utf-8")
    print("WINDOW_ARMED=yes")
    print(f"WINDOW_ENDS_AT={ends.isoformat()}")
    print(f"WINDOW_SECONDS={hours * 3600}")


def cmd_check(directory: Path, now: _dt.datetime) -> None:
    state = load_state(directory / STATE_FILE)
    status, remaining = window_state(state, now)
    ends = state.get("ends_at") if state else None
    print(f"WINDOW={status}")
    print(f"REMAINING_SECONDS={_fmt(remaining)}")
    print(f"WINDOW_ENDS_AT={ends if isinstance(ends, str) and status != 'unknown' else 'null'}")


def main(argv: list[str] | None = None, now: Optional[_dt.datetime] = None) -> None:
    ap = argparse.ArgumentParser(description="Wall-clock window for an unattended long-queue run.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("arm")
    a.add_argument("--hours", type=float, default=DEFAULT_WINDOW_HOURS)
    a.add_argument("--state-dir", type=Path, default=None)
    a.add_argument("--unattended", action="store_true",
                   help=f"treat as a scheduled run even without {UNATTENDED_ENV}=1 (never the reverse)")
    c = sub.add_parser("check")
    c.add_argument("--state-dir", type=Path, default=None)

    args = ap.parse_args(argv)
    when = now or _dt.datetime.now(_dt.timezone.utc)
    directory = args.state_dir or state_dir()
    if args.cmd == "arm":
        if not args.hours > 0:
            ap.error("--hours must be positive")
        cmd_arm(args.hours, directory, args.unattended or is_unattended(os.environ), when)
    else:
        cmd_check(directory, when)


if __name__ == "__main__":
    main()
