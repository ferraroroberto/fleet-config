"""Report each acceptance unit's outcome and seconds to the run history.

`gate_evidence run` sets `FLEET_GATE_UNIT_REPORT` to a temp file; this module appends one JSON line
per unit to it and publishes the running unit's name in `FLEET_GATE_UNIT` so the spawn probe can
attribute process spawns. A failing unit also reports the bounded tail of what it printed (stdout and
stderr, or the traceback it raised), so a red unit can be diagnosed from the ledger after the temp run
directory is gone. Under the probe's coverage half, each unit's executed lines are flushed under its
name when it ends. With the env var unset (a plain `run_acceptance.py`) it does nothing.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import traceback
from typing import Iterator, Optional, TextIO

from fleet_core import runtime_files
from unit_history.ledger import (
    CURRENT_UNIT_ENV,
    FAIL,
    OUTPUT_TAIL_CHARS,
    PASS,
    SKIP,
    UNIT_REPORT_ENV,
)


class _Tail:
    """Pass writes through to the real stream and keep the last `OUTPUT_TAIL_CHARS` of them."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._chunks: list[str] = []
        self._size = 0
        self.cut = False

    def write(self, text: str) -> int:
        self._chunks.append(text)
        self._size += len(text)
        if self._size > 2 * OUTPUT_TAIL_CHARS:  # trim in batches so a chatty unit stays cheap
            kept = "".join(self._chunks)[-OUTPUT_TAIL_CHARS:]
            self._chunks, self._size, self.cut = [kept], len(kept), True
        return self._stream.write(text)

    def text(self) -> str:
        return "".join(self._chunks)

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


@contextlib.contextmanager
def unit(name: str) -> Iterator[dict]:
    """Time one unit; the body stores its `(failures, total, skipped)` in the yielded dict."""
    outcome: dict = {}
    reporting = bool(os.environ.get(UNIT_REPORT_ENV))
    out, err = sys.stdout, sys.stderr
    if reporting:
        sys.stdout, sys.stderr = _Tail(out), _Tail(err)
    os.environ[CURRENT_UNIT_ENV] = name
    _coverage_flush("")  # lines run between units belong to none of them
    started = time.perf_counter()
    raised: Optional[str] = None
    try:
        yield outcome
    except BaseException:
        raised = traceback.format_exc()
        raise
    finally:
        seconds = round(time.perf_counter() - started, 3)
        _coverage_flush(name)
        os.environ.pop(CURRENT_UNIT_ENV, None)
        tails = [s for s in (sys.stdout, sys.stderr) if isinstance(s, _Tail)]
        sys.stdout, sys.stderr = out, err
        printed = "".join(t.text() for t in tails) + (raised or "")
        _write(name, outcome, seconds, _bound(printed, any(t.cut for t in tails)))


def _coverage_flush(name: str) -> None:
    """Hand the probe's recorded lines to `name` (or drop them for ""); a no-op without the probe."""
    flush = getattr(sys.modules.get("sitecustomize"), "fleet_coverage_flush", None)
    if callable(flush):
        flush(name)


def _bound(text: str, already_cut: bool) -> str:
    """The last `OUTPUT_TAIL_CHARS` of `text`, led by a marker when anything older was cut."""
    if len(text) <= OUTPUT_TAIL_CHARS and not already_cut:
        return text
    marker = "[... earlier output cut]\n"
    return marker + text[-(OUTPUT_TAIL_CHARS - len(marker)):]


def _status(outcome: dict) -> str:
    if "result" not in outcome:
        return FAIL  # the unit raised before it reported
    failures, total, skipped = outcome["result"]
    if failures:
        return FAIL
    return SKIP if skipped and not total else PASS


def _write(name: str, outcome: dict, seconds: float, output: str) -> None:
    path = os.environ.get(UNIT_REPORT_ENV)
    if not path:
        return
    status = _status(outcome)
    row = {"unit": name, "status": status, "seconds": seconds}
    if status == FAIL and output:
        row["output"] = output
    with contextlib.suppress(OSError):
        with runtime_files.open_for_write(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
