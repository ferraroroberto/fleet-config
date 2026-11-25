"""The chief's tunable timings, read from the launcher's chief settings.

The launcher serves four timings on `GET /api/board/chief/settings`; each
default here equals the constant it replaced, so an unreadable launcher or a
launcher without the setting behaves exactly as before. A value that cannot be
used is reported as `default` with its reason, never passed off as configured.
Ranges are the launcher's own: a stored value outside them is not trusted.
"""

from __future__ import annotations

import argparse
import urllib.error
from typing import Any, Dict, NamedTuple, Optional

from . import launcher

SETTINGS_PATH = "/api/board/chief/settings"
# A short budget: these reads ride hot paths (the Stop hook, the inbox), and a
# slow launcher must cost a default, not a stall.
FETCH_TIMEOUT = 3.0


class Timing(NamedTuple):
    key: str
    label: str
    default: int
    minimum: int
    maximum: int
    unit: str  # "s" or "m"

    @property
    def seconds_per_unit(self) -> int:
        return 60 if self.unit == "m" else 1


class Resolved(NamedTuple):
    value: int
    seconds: float
    source: str  # "settings" or "default"
    reason: str  # why the default is in use; "" when read from settings


TIMINGS: Dict[str, Timing] = {t.key: t for t in (
    # The launcher's doorbell back-off before it rings an idle chief again.
    Timing("chief_rering_seconds", "rering", 60, 30, 600, "s"),
    # An unacknowledged mailbox claim is offered again after this long.
    Timing("chief_reoffer_minutes", "reoffer", 15, 10, 120, "m"),
    # The Stop hook holds the chief's turn end at most once per this window.
    Timing("chief_hold_minutes", "hold", 10, 5, 60, "m"),
    # A headless run silent this long is killed by the stall watchdog.
    Timing("chief_stall_minutes", "stall", 45, 15, 240, "m"),
)}


def _resolve_one(timing: Timing, settings: Optional[Dict[str, Any]], unread_reason: str) -> Resolved:
    def fallback(reason: str) -> Resolved:
        return Resolved(timing.default, float(timing.default * timing.seconds_per_unit), "default", reason)

    if settings is None:
        return fallback(unread_reason)
    value = settings.get(timing.key)
    if value is None:
        return fallback("setting missing")
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback("setting is not a whole number")
    if not timing.minimum <= value <= timing.maximum:
        return fallback(f"setting {value} outside {timing.minimum}-{timing.maximum}")
    return Resolved(value, float(value * timing.seconds_per_unit), "settings", "")


def resolve(settings: Optional[Dict[str, Any]], unread_reason: str = "launcher unreachable") -> Dict[str, Resolved]:
    """Every timing from a settings block; `None` means the block could not be read."""
    return {key: _resolve_one(timing, settings, unread_reason) for key, timing in TIMINGS.items()}


def fetch(base_url: str = launcher.DEFAULT_BASE_URL) -> Dict[str, Resolved]:
    """Read the launcher's chief settings once and resolve every timing; never raises."""
    try:
        reply = launcher._request(base_url, SETTINGS_PATH, timeout=FETCH_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return resolve(None, f"launcher chief settings unreadable (HTTP {exc.code})")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return resolve(None, f"launcher unreachable ({type(exc).__name__})")
    block = reply.get("settings") if isinstance(reply, dict) else None
    if not isinstance(block, dict):
        return resolve(None, "launcher chief settings reply had no settings block")
    return resolve(block)


def seconds(key: str, base_url: str = launcher.DEFAULT_BASE_URL) -> float:
    """One timing in seconds (the default when it cannot be read)."""
    return fetch(base_url)[key].seconds


def format_line(resolved: Dict[str, Resolved]) -> str:
    """`TIMINGS rering=60s(settings) reoffer=15m(default: why) ...`: the values in use and where each came from."""
    parts = []
    for key, timing in TIMINGS.items():
        item = resolved[key]
        origin = "settings" if item.source == "settings" else f"default: {item.reason}"
        parts.append(f"{timing.label}={item.value}{timing.unit}({origin})")
    return "TIMINGS " + " ".join(parts)


def stale_after_seconds(explicit: Optional[float], base_url: str = launcher.DEFAULT_BASE_URL) -> float:
    """Seconds before an unacked mailbox claim is re-offered: `--stale-after`, else the setting, else 900."""
    return explicit if explicit is not None else fetch(base_url)["chief_reoffer_minutes"].seconds


def cmd_timings(args: argparse.Namespace) -> int:
    """Print the timings in use and where each came from. Exit 1 when any fell back to its default."""
    resolved = fetch(args.base_url)
    print(format_line(resolved))
    return 0 if all(item.source == "settings" for item in resolved.values()) else 1
