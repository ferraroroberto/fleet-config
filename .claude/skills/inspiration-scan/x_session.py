"""The X reader's single Chrome launch helper: stealth kwargs + profile-lock backoff.

Every X page this skill opens goes through `open_context`, so the fleet's
"browser automation must not look like a bot" profile lives in one place
(docs/recurring-gotchas.md): real Chrome, the automation infobar stripped,
`navigator.webdriver` undefined, a persistent profile, 1280x900, and Chrome's
own sandbox kept on.

A persistent profile admits one live Chrome. A second launch fails, so the
helper waits for the holder with exponential backoff and never kills it: on
Windows the lock is a live-process kernel object, so there are no lock files
to clear.

Runs under a Python that has Playwright (app-launcher's `.venv`); Playwright is
imported only inside `open_context`, so the kwargs stay importable for tests.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Sequence, Tuple

from fleet_core import runtime_files

VIEWPORT = {"width": 1280, "height": 900}

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=Translate",
    "--no-default-browser-check",
    "--no-first-run",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation", "--enable-blink-features=IdleDetection"]
WEBDRIVER_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# The fleet's shared schedule for a held profile: a sibling job usually lets go
# within minutes, and a holder past about fifteen minutes is hung.
LOCK_BACKOFF_S: Tuple[int, ...] = (60, 120, 240, 480)
_LOCK_MARKERS = ("existing browser session", "user data directory is already in use", "profile in use",
                 "processsingleton")


class ProfileLocked(RuntimeError):
    """The profile stayed held by another Chrome through the whole backoff schedule."""


def launch_kwargs(headless: bool = False) -> Dict[str, Any]:
    """`launch_persistent_context` keyword arguments for a human-looking Chrome."""
    return dict(
        channel="chrome",
        headless=headless,
        chromium_sandbox=True,
        viewport=dict(VIEWPORT),
        args=list(LAUNCH_ARGS),
        ignore_default_args=list(IGNORE_DEFAULT_ARGS),
    )


def is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _LOCK_MARKERS)


def launch_with_backoff(launch: Callable[[], Any], schedule: Sequence[int] = LOCK_BACKOFF_S,
                        sleep: Callable[[float], None] = time.sleep) -> Any:
    """Call `launch` until the profile is free; `ProfileLocked` after the schedule."""
    for wait in list(schedule) + [None]:
        try:
            return launch()
        except Exception as exc:  # Playwright raises its own Error type; only lock errors retry
            if not is_lock_error(exc):
                raise
            if wait is None:
                total = sum(schedule)
                raise ProfileLocked(f"Chrome profile still held by another process after {total}s "
                                    "of backoff; is the login helper still open?") from exc
            sleep(wait)
    raise AssertionError("unreachable")


def open_context(playwright: Any, profile_dir: Path, headless: bool = False) -> Any:
    """A persistent Chrome context on `profile_dir`, stealth init script applied."""
    runtime_files.make_dirs(profile_dir, parents=True, exist_ok=True)
    context = launch_with_backoff(
        lambda: playwright.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs(headless)))
    context.add_init_script(WEBDRIVER_INIT_SCRIPT)
    return context
