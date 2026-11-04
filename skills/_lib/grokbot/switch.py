"""The app-launcher Settings > Chief "Grok Bot" switch, read fresh on every call.

    on       the launcher answered and `settings.grokbot_enabled` is the boolean true
    off      the launcher answered and it is the boolean false
    unknown  anything else: unreachable, a non-2xx, a missing field, a non-boolean value

`unknown` is never `on`: a reader that cannot confirm the owner's switch must not start, ping or send.
The value changes the moment the owner flips the switch, so nothing here caches it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chief import launcher  # noqa: E402

logger = logging.getLogger("fleet_chief")

ON, OFF, UNKNOWN = "on", "off", "unknown"
SETTINGS_PATH = "/api/board/chief/settings"


def read_switch(
    base_url: str = launcher.DEFAULT_BASE_URL,
    request: Callable[..., Dict[str, Any]] = launcher._request,
) -> Tuple[str, str]:
    """`(state, reason)`; reason is empty unless the state is `unknown`. Never raises."""
    try:
        body = request(base_url, SETTINGS_PATH)
    except Exception as exc:  # noqa: BLE001 - any failure to read means "not confirmed", never "on"
        logger.warning("⚠️ grokbot switch unreadable: %s", type(exc).__name__)
        return UNKNOWN, f"the launcher settings could not be read ({type(exc).__name__})"
    settings = body.get("settings") if isinstance(body, dict) else None
    value = settings.get("grokbot_enabled") if isinstance(settings, dict) else None
    if value is True:
        return ON, ""
    if value is False:
        return OFF, ""
    return UNKNOWN, "settings.grokbot_enabled is missing or not a boolean"
