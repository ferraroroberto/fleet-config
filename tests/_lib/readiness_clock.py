"""A formatter clock that counts a stall window from fixture readiness.

Shared by the runner suites (`tests/test_claude_progress.py`,
`tests/test_scheduled_runner.py`) so a loaded box's interpreter start is never
mistaken for the silence a stall watchdog is meant to judge (fleet-config#1056,
#1069).
"""
from __future__ import annotations

import time
from typing import Callable, Optional


class ReadinessClock:
    """A monotonic clock that stands at zero until ``ready()`` first holds.

    The formatter's clock starts when the formatter is built, before the child
    even exists, so on a loaded box the child's interpreter start alone could
    use up the stall window: the watchdog then killed the tree before the
    fixture had done anything worth judging (fleet-config#1056). Holding the
    clock until the fixture is ready counts the stall window from readiness, so
    the watchdog judges exactly the silence the fixture produces. The
    watchdog's limit and every assertion are unchanged. The hold is capped at
    ``max_hold`` seconds, so a fixture that never gets ready still fails the
    promptness checks instead of hanging the suite.
    """

    def __init__(self, ready: Callable[[], bool], max_hold: float = 30.0) -> None:
        self._ready = ready
        self._hold_until = time.monotonic() + max_hold
        self._ready_at: Optional[float] = None

    def __call__(self) -> float:
        if self._ready_at is None:
            if not self._ready() and time.monotonic() < self._hold_until:
                return 0.0
            self._ready_at = time.monotonic()
        return time.monotonic() - self._ready_at
