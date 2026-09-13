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
  check  [--threshold PCT] [--max-age SECONDS] [--state-dir PATH] [--unattended]
         Reads the cache and prints:
           DECISION=OK|PAUSE|UNKNOWN
           REASON=below_threshold|over_threshold|cache_missing|cache_stale|usage_missing
           MODE=interactive|unattended
           USED_PCT=<float|null>
           RESETS_AT=<iso|null>
           WAIT_SECONDS=<float|null>
           WATCHDOG_SECONDS=<float|off|unknown|null>

Unattended mode (fleet-config#825, #891)
----------------------------------------
The statusline only renders in an interactive session, so a scheduled
`claude -p` run with no session open on the machine never has a fresh cache.
Proceeding on `UNKNOWN` there cost the whole 2026-09-10 weekly chain: the audit
ran blind into the 5-hour limit. So a run launched by `scheduled_runner.py`
(which exports `FLEET_SCHEDULED_RUN=1` to its child), or one passing
`--unattended`, turns "no signal" into a bounded `PAUSE` that re-reads the
cache after `UNKNOWN_WAIT_SECONDS`; the calling skill's pause-cycle cap then
ends it as skipped rather than proceeding. Every unattended `PAUSE` wait, the
over-threshold one included, is also capped under the launcher's stall
watchdog (`STALL_TIMEOUT_ENV`), so waiting out a 5-hour reset re-checks in
cycles instead of being killed as a hung job. Interactive behaviour is unchanged.
There is deliberately no flag that makes an unattended run *less* cautious.

Like `audit_retry.py`, the correctness-critical decision (`decide`) is pure and
unit-tested (`tests/test_rate_gate.py`) independent of the file I/O around it.
stdlib + `hooks_state.state_dir()` only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooks_state import state_dir  # noqa: E402

DEFAULT_THRESHOLD_PCT = 70.0
DEFAULT_MAX_AGE_SECONDS = 1800  # 30 min — a statusline render is "recent" within this
DEFAULT_WAIT_SECONDS = 1800  # fallback wait when resets_at is missing but usage is over threshold
WAIT_BUFFER_SECONDS = 60  # small margin past the reported reset time
# Re-read interval when an unattended run has no signal at all. Short, because
# there is no reset to wait for, only a statusline render that may or may not
# come; and well under scheduled_runner's 45-min stall watchdog, so the caller's
# capped pause cycles end the run as skipped rather than as a killed job.
UNKNOWN_WAIT_SECONDS = 600
UNATTENDED_ENV = "FLEET_SCHEDULED_RUN"
# The effective stall watchdog of the process that launched this run, exported
# by scheduled_runner.py ("0" = watchdog off). A silent Monitor wait emits no
# stream record at all, so an over-threshold wait of hours is killed as a hung
# job (fleet-config#891 probe: --stall-timeout 120 fired at 02:10 into a silent
# 5-min wait). Each unattended wait is therefore capped at this fraction of the
# watchdog; the caller re-checks between cycles, and that re-check is stream
# activity the watchdog sees.
STALL_TIMEOUT_ENV = "FLEET_STALL_TIMEOUT_SECONDS"
WATCHDOG_WAIT_FRACTION = 0.5


class Decision(NamedTuple):
    status: str  # "OK" | "PAUSE" | "UNKNOWN"
    reason: str  # "below_threshold" | "over_threshold" | "cache_missing" | "cache_stale" | "usage_missing"
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


def is_unattended(environ: Mapping[str, str]) -> bool:
    """True when the environment marks a scheduled, nobody-attending run."""
    return environ.get(UNATTENDED_ENV) == "1"


def watchdog_seconds(environ: Mapping[str, str]) -> Optional[float]:
    """The launching runner's stall watchdog: seconds, ``0.0`` if off, ``None`` if unknown."""
    try:
        value = float(environ.get(STALL_TIMEOUT_ENV, ""))
    except ValueError:
        return None
    return value if value >= 0 else None  # NaN and negatives are not a watchdog


def max_silent_wait(watchdog: Optional[float]) -> Optional[float]:
    """Longest single silent wait the watchdog tolerates; ``None`` means uncapped.

    An unknown watchdog is never read as "no watchdog": it caps at the no-signal
    re-read interval, which is already sized under the runner's default.
    """
    if watchdog is None:
        return float(UNKNOWN_WAIT_SECONDS)
    if watchdog == 0:
        return None
    return watchdog * WATCHDOG_WAIT_FRACTION


def _no_signal(reason: str, unattended: bool) -> Decision:
    if unattended:
        return Decision("PAUSE", reason, None, None, float(UNKNOWN_WAIT_SECONDS))
    return Decision("UNKNOWN", reason, None, None, None)


def decide(
    cache: dict,
    now: _dt.datetime,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    unattended: bool = False,
    max_wait_seconds: Optional[float] = None,
) -> Decision:
    """Pure decision: given the parsed rate-limits cache and the current time,
    decide whether fan-out dispatch should proceed (OK) or pause (PAUSE). With
    no usable signal an interactive run gets UNKNOWN (proceed); an unattended
    run gets PAUSE with no `used_pct` and `UNKNOWN_WAIT_SECONDS`, so the caller
    re-reads instead of dispatching blind. `max_wait_seconds`, when given,
    caps a PAUSE's wait (see `max_silent_wait`). `now` must be timezone-aware.
    """
    decision = _decide(cache, now, threshold_pct, max_age_seconds, unattended)
    if max_wait_seconds is None or decision.wait_seconds is None:
        return decision
    return decision._replace(wait_seconds=min(decision.wait_seconds, max_wait_seconds))


def _decide(
    cache: dict,
    now: _dt.datetime,
    threshold_pct: float,
    max_age_seconds: int,
    unattended: bool,
) -> Decision:
    captured_at = _parse_iso(cache.get("captured_at")) if cache else None
    if captured_at is None:
        return _no_signal("cache_missing", unattended)
    if (now - captured_at).total_seconds() > max_age_seconds:
        return _no_signal("cache_stale", unattended)

    five_hour = cache.get("five_hour") or {}
    used_pct = five_hour.get("used_percentage")
    if used_pct is None:
        return _no_signal("usage_missing", unattended)

    if used_pct < threshold_pct:
        return Decision("OK", "below_threshold", used_pct, five_hour.get("resets_at"), None)

    resets_at_raw = five_hour.get("resets_at")
    resets_at = _parse_iso(resets_at_raw)
    if resets_at is not None:
        wait_seconds = max((resets_at - now).total_seconds(), 0) + WAIT_BUFFER_SECONDS
    else:
        wait_seconds = float(DEFAULT_WAIT_SECONDS)
    return Decision("PAUSE", "over_threshold", used_pct, resets_at_raw, wait_seconds)


def load_cache(path: Path) -> dict:
    """A missing/corrupt cache is treated as no signal (empty dict -> UNKNOWN)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _format_watchdog(unattended: bool, watchdog: Optional[float]) -> str:
    if not unattended:
        return "null"
    if watchdog is None:
        return "unknown"
    return "off" if watchdog == 0 else str(watchdog)


def cmd_check(threshold_pct: float, max_age_seconds: int, state_directory: Path, unattended: bool,
              environ: Mapping[str, str]) -> None:
    cache = load_cache(state_directory / "rate-limits.json")
    watchdog = watchdog_seconds(environ)
    max_wait = max_silent_wait(watchdog) if unattended else None
    result = decide(cache, _dt.datetime.now(_dt.timezone.utc), threshold_pct, max_age_seconds, unattended,
                    max_wait)
    print(f"DECISION={result.status}")
    print(f"REASON={result.reason}")
    print(f"MODE={'unattended' if unattended else 'interactive'}")
    print(f"USED_PCT={result.used_pct if result.used_pct is not None else 'null'}")
    print(f"RESETS_AT={result.resets_at if result.resets_at else 'null'}")
    print(f"WAIT_SECONDS={result.wait_seconds if result.wait_seconds is not None else 'null'}")
    print(f"WATCHDOG_SECONDS={_format_watchdog(unattended, watchdog)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Proactive session-rate-limit gate for fleet fan-out.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT, dest="threshold_pct")
    c.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_SECONDS, dest="max_age_seconds")
    c.add_argument("--state-dir", type=Path, default=None)
    c.add_argument("--unattended", action="store_true",
                   help=f"treat as a scheduled run even without {UNATTENDED_ENV}=1 (never the reverse)")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        cmd_check(args.threshold_pct, args.max_age_seconds, args.state_dir or state_dir(),
                  args.unattended or is_unattended(os.environ), os.environ)


if __name__ == "__main__":
    main()
