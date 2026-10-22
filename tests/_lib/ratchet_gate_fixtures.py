"""Shared assertions for the tests of a ratchet acceptance stage.

A ratchet stage (`tests/acceptance/<name>_ratchet_gate.py`) exposes
`classify_exit(code)` and one `_<name>_ratchet_check()`; what must hold for every
one of them is the same, so the assertions live here once: exit 0 is the only
pass, exit 3 is NOT CONFIRMED and never a skip, and the check function maps a run
(or a run that could not happen) to one reported check.

Usage:
    from ratchet_gate_fixtures import assert_exit_mapping, assert_check_function
    assert_exit_mapping(gate, check)
    assert_check_function(gate, "_structure_ratchet_check", check,
                          finding="[file size] src/a.py has 1200 lines")
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from typing import Callable

from acceptance.shared import SKIP_EXIT


def assert_exit_mapping(gate: object, check: Callable[[bool, str], None]) -> None:
    """`classify_exit`: only 0 passes; 3 is its own failing state."""
    check(gate.classify_exit(0)[0], "exit 0 passes")
    for code in (1, 2, 3, 4, -1):
        check(not gate.classify_exit(code)[0], f"exit {code} fails")
    check("NOT CONFIRMED" in gate.classify_exit(3)[1], "exit 3 is labelled NOT CONFIRMED")
    check("SKIP" not in gate.classify_exit(3)[1].upper(), "exit 3 is never labelled a skip")
    # The collision the dedicated stage exists for: the gate's own SKIP code is
    # the ratchet's NOT CONFIRMED code, so only a dedicated mapping keeps it a failure.
    check(SKIP_EXIT == 3, "shared.SKIP_EXIT is the ratchet's exit 3 (why the check is dedicated)")
    check(not gate.classify_exit(SKIP_EXIT)[0], "shared.SKIP_EXIT fails through the dedicated mapping")


def _run_check_with(gate: object, fn_name: str, result: object) -> tuple[tuple[int, int], str]:
    """`gate.<fn_name>()` with `subprocess.run` replaced by `result` -- a
    CompletedProcess to return, or an exception to raise."""
    real = gate.subprocess.run

    def fake(*_a: object, **_k: object) -> object:
        if isinstance(result, BaseException):
            raise result
        return result

    buf = io.StringIO()
    gate.subprocess.run = fake  # type: ignore[assignment]
    try:
        with contextlib.redirect_stdout(buf):
            counts = getattr(gate, fn_name)()
    finally:
        gate.subprocess.run = real  # type: ignore[assignment]
    return counts, buf.getvalue()


def assert_check_function(gate: object, fn_name: str, check: Callable[[bool, str], None], finding: str) -> None:
    """The check function with the script's run stubbed: the reported line and
    the `(failures, total)` it hands the gate, for every exit and failure mode.
    `finding` is script output that must be echoed on a non-zero exit."""
    def done(code: int, out: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr="")

    counts, out = _run_check_with(gate, fn_name, done(0))
    check(counts == (0, 1) and out.startswith("OK"), f"exit 0 -> one passing check, got {counts} {out!r}")

    counts, out = _run_check_with(gate, fn_name, done(1, finding))
    check(counts == (1, 1) and out.startswith("FAIL") and finding in out,
          f"exit 1 -> failing check that echoes the script's finding, got {counts} {out!r}")

    counts, out = _run_check_with(gate, fn_name, done(3, finding))
    check(counts == (1, 1), f"exit 3 counts as a failure, not a skip, got {counts}")
    check("NOT CONFIRMED" in out and "SKIP" not in out and finding in out,
          f"exit 3 prints NOT CONFIRMED plus the script's reason, got {out!r}")

    counts, out = _run_check_with(gate, fn_name, subprocess.TimeoutExpired(cmd="x", timeout=1))
    check(counts == (1, 1) and "NOT CONFIRMED" in out, f"a timed-out run is NOT CONFIRMED, got {counts} {out!r}")

    counts, out = _run_check_with(gate, fn_name, OSError("no interpreter"))
    check(counts == (1, 1) and "NOT CONFIRMED" in out, f"an unstartable run is NOT CONFIRMED, got {counts} {out!r}")
