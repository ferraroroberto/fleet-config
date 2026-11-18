"""Report each acceptance unit's outcome and seconds to the run history.

`gate_evidence run` sets `FLEET_GATE_UNIT_REPORT` to a temp file; this module appends one JSON line
per unit to it and publishes the running unit's name in `FLEET_GATE_UNIT` so the spawn probe can
attribute process spawns. With the env var unset (a plain `run_acceptance.py`) it does nothing.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Iterator

from fleet_core import runtime_files
from unit_history.ledger import CURRENT_UNIT_ENV, FAIL, PASS, SKIP, UNIT_REPORT_ENV


@contextlib.contextmanager
def unit(name: str) -> Iterator[dict]:
    """Time one unit; the body stores its `(failures, total, skipped)` in the yielded dict."""
    outcome: dict = {}
    os.environ[CURRENT_UNIT_ENV] = name
    started = time.perf_counter()
    try:
        yield outcome
    finally:
        seconds = round(time.perf_counter() - started, 3)
        os.environ.pop(CURRENT_UNIT_ENV, None)
        _write(name, outcome, seconds)


def _status(outcome: dict) -> str:
    if "result" not in outcome:
        return FAIL  # the unit raised before it reported
    failures, total, skipped = outcome["result"]
    if failures:
        return FAIL
    return SKIP if skipped and not total else PASS


def _write(name: str, outcome: dict, seconds: float) -> None:
    path = os.environ.get(UNIT_REPORT_ENV)
    if not path:
        return
    row = {"unit": name, "status": _status(outcome), "seconds": seconds}
    with contextlib.suppress(OSError):
        with runtime_files.open_for_write(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
