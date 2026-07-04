"""Proactive session-rate-limit gate for fleet-wide sub-agent fan-out.

Why this exists
----------------
`/audit-fleet` and `/cleanup-fleet` dispatch many background sub-agents against
the shared, rolling 5-hour Claude Code session rate limit. `statusline-command.ps1`
now caches `rate_limits.five_hour`/`seven_day` (`used_percentage` + `resets_at`)
plus a `captured_at` stamp to `~/.claude/hooks/state/rate-limits.json` on every
statusline render (fleet-config#259 / app-launcher#326) — so a skill can read the
live session usage % and pause dispatch *before* hitting the wall, wait until the
window resets, then resume in the same still-alive process. This replaces the
older "dead-man's switch" (`audit_retry.py`, retired in fleet-config#261), which
armed an OS-level scheduled relaunch because there was no way to read the live %
from a headless `claude -p` run. See `docs/rate-gate.md` for the full design.

Subcommand
----------
  check  [--threshold PCT] [--max-age SECONDS] [--state-dir PATH]
         Reads the cache and prints:
           DECISION=OK|PAUSE|UNKNOWN
           USED_PCT=<float|null>
           RESETS_AT=<iso|null>
           WAIT_SECONDS=<float|null>

Like `audit_retry.py`, the correctness-critical decision (`decide`) is pure and
unit-tested (`tests/test_rate_gate.py`) independent of the file I/O around it.
stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple, Optional

DEFAULT_THRESHOLD_PCT = 70.0
DEFAULT_MAX_AGE_SECONDS = 1800  # 30 min — a statusline render is "recent" within this
DEFAULT_WAIT_SECONDS = 1800  # fallback wait when resets_at is missing but usage is over threshold
WAIT_BUFFER_SECONDS = 60  # small margin past the reported reset time


class Decision(NamedTuple):
    status: str  # "OK" | "PAUSE" | "UNKNOWN"
    used_pct: Optional[float]
    resets_at: Optional[str]
    wait_seconds: Optional[float]


def _parse_iso(value: object) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def decide(
    cache: dict,
    now: _dt.datetime,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> Decision:
    """Pure decision: given the parsed rate-limits cache and the current time,
    decide whether fan-out dispatch should proceed (OK), pause (PAUSE), or
    proceed for lack of any signal (UNKNOWN). `now` must be timezone-aware.
    """
    captured_at = _parse_iso(cache.get("captured_at")) if cache else None
    if captured_at is None or (now - captured_at).total_seconds() > max_age_seconds:
        return Decision("UNKNOWN", None, None, None)

    five_hour = cache.get("five_hour") or {}
    used_pct = five_hour.get("used_percentage")
    if used_pct is None:
        return Decision("UNKNOWN", None, None, None)

    if used_pct < threshold_pct:
        return Decision("OK", used_pct, five_hour.get("resets_at"), None)

    resets_at_raw = five_hour.get("resets_at")
    resets_at = _parse_iso(resets_at_raw)
    if resets_at is not None:
        wait_seconds = max((resets_at - now).total_seconds(), 0) + WAIT_BUFFER_SECONDS
    else:
        wait_seconds = float(DEFAULT_WAIT_SECONDS)
    return Decision("PAUSE", used_pct, resets_at_raw, wait_seconds)


def state_dir() -> Path:
    """Resolve the cache directory at call time, mirroring
    `hooks/session_state.py`'s `state_file()` override pattern."""
    root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    return Path(root) if root else Path.home() / ".claude" / "hooks" / "state"


def load_cache(path: Path) -> dict:
    """A missing/corrupt cache is treated as no signal (empty dict -> UNKNOWN)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def cmd_check(threshold_pct: float, max_age_seconds: int, state_directory: Path) -> None:
    cache = load_cache(state_directory / "rate-limits.json")
    result = decide(cache, _dt.datetime.now(_dt.timezone.utc), threshold_pct, max_age_seconds)
    print(f"DECISION={result.status}")
    print(f"USED_PCT={result.used_pct if result.used_pct is not None else 'null'}")
    print(f"RESETS_AT={result.resets_at if result.resets_at else 'null'}")
    print(f"WAIT_SECONDS={result.wait_seconds if result.wait_seconds is not None else 'null'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Proactive session-rate-limit gate for fleet fan-out.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT, dest="threshold_pct")
    c.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_SECONDS, dest="max_age_seconds")
    c.add_argument("--state-dir", type=Path, default=None)

    args = ap.parse_args(argv)
    if args.cmd == "check":
        cmd_check(args.threshold_pct, args.max_age_seconds, args.state_dir or state_dir())


if __name__ == "__main__":
    main()
