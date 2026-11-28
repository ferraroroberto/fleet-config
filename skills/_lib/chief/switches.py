"""One reader for the chief's on/off switches in the launcher's chief settings.

Each switch is read on every call and only a literal `true` is on: off, missing
or unreadable yields the caller's `<PREFIX>=off reason=...` line.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Optional, Tuple

from . import launcher
from .timings import SETTINGS_PATH


def settings_switch(reply: Any, key: str, prefix: str) -> Optional[str]:
    """None when the switch is on, else the `<prefix>=off` line. Unknown is never on."""
    settings = reply.get("settings") if isinstance(reply, dict) else None
    if not isinstance(settings, dict):
        return f"{prefix}=off reason=settings-unreadable"
    if key not in settings:
        return f"{prefix}=off reason=setting-missing"
    value = settings[key]
    if value is True:
        return None
    if value is False:
        return f"{prefix}=off reason=switch-off"
    return f"{prefix}=off reason=setting-not-boolean value={json.dumps(value)}"


def read_switch(base_url: str, key: str, prefix: str) -> Tuple[Any, Optional[str]]:
    """`(settings reply, off line)` as the launcher serves them now; the off line is None when on."""
    try:
        reply = launcher._request(base_url, SETTINGS_PATH)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, f"{prefix}=off reason=settings-unreadable ({exc})"
    return reply, settings_switch(reply, key, prefix)
