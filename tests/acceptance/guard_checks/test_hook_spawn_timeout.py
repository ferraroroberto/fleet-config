"""Tests for the shared hook-spawn timeout in tests/acceptance/shared.py.

An acceptance check that spawns a hook must fail only when the hook hangs, not
when a cold interpreter start is slow because other gates share the box. A fake
hook that sleeps 16 s before answering stands in for that slow start:
`shared.run` passes under `HOOK_SPAWN_TIMEOUT_S` and raises `TimeoutExpired`
under the former 15 s bound.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/guard_checks/test_hook_spawn_timeout.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from acceptance import shared  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

_FORMER_BOUND_S = 15
_SLOW_START_S = _FORMER_BOUND_S + 1


def _run_slow_hook(timeout_s: int) -> str:
    """Return "ok" or "timeout" for the slow fake hook under `timeout_s`."""
    shared.HOOK_SPAWN_TIMEOUT_S = timeout_s
    try:
        code, _out, _err = shared.run("slow_hook", {})
    except subprocess.TimeoutExpired:
        return "timeout"
    return "ok" if code == 0 else f"exit {code}"


tmp = Path(tempfile.mkdtemp(prefix="hook_spawn_timeout_"))
real_hooks, real_timeout = shared.HOOKS, shared.HOOK_SPAWN_TIMEOUT_S
try:
    check(real_timeout > _SLOW_START_S, f"the shared bound outlasts a slow start, got {real_timeout}s")
    (tmp / "slow_hook.py").write_text(f"import time\ntime.sleep({_SLOW_START_S})\n", encoding="utf-8")
    shared.HOOKS = tmp
    # shared.run reads its module globals at call time and both bounds share them,
    # so the two runs go one after the other.
    new = _run_slow_hook(real_timeout)
    old = _run_slow_hook(_FORMER_BOUND_S)
    check(new == "ok", f"a {_SLOW_START_S}s slow start passes under the shared bound, got {new}")
    check(old == "timeout", f"the same slow start fails under the former {_FORMER_BOUND_S}s bound, got {old}")
finally:
    shared.HOOKS, shared.HOOK_SPAWN_TIMEOUT_S = real_hooks, real_timeout
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_hook_spawn_timeout")
