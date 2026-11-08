"""Tests for the shared hook-spawn timeout in tests/acceptance/shared.py.

An acceptance check that spawns a hook must fail only when the hook hangs, not
when a cold interpreter start is slow because other gates share the box. A fake
hook that sleeps 16 s before answering stands in for that slow start:
`shared.run` passes under `HOOK_SPAWN_TIMEOUT_S` and raises `TimeoutExpired`
under the former 15 s bound.

It also fails only on the hook's verdict, not on the caller's code page: with
`PYTHONUTF8` unset, `text=True` decodes with cp1252, a hook printing `❌`
(`e2 9d 8c`, `0x9d` undefined there) makes the reader thread raise, and `run()`
hands back `None` for stdout and stderr, so the next `res.stdout + res.stderr`
dies with a `NoneType` error. A child interpreter with `PYTHONUTF8` and
`PYTHONIOENCODING` removed drives `shared.run` against such a hook, because
this process may already be in UTF-8 mode.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/guard_checks/test_hook_spawn_timeout.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from _lib import NO_WINDOW  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

from acceptance import shared  # noqa: E402

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


_UTF8_HOOK = (
    "import sys\n"
    "sys.stdout.buffer.write('\\u274c out\\n'.encode('utf-8'))\n"
    "sys.stderr.buffer.write('\\u274c err\\n'.encode('utf-8'))\n"
)
_DRIVER = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, {tests!r})\n"
    "from acceptance import shared\n"
    "shared.HOOKS = Path({hooks!r})\n"
    "print(json.dumps(shared.run('probe', {{}})))\n"
)


def _run_utf8_hook(hooks_dir: Path) -> object:
    """`shared.run`'s (code, stdout, stderr) for the UTF-8 hook, in a child with no UTF-8 mode."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    res = subprocess.run(
        [sys.executable, "-c", _DRIVER.format(tests=str(REPO / "tests"), hooks=str(hooks_dir))],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, creationflags=NO_WINDOW, cwd=hooks_dir,
    )
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return f"driver printed no verdict: rc={res.returncode} stderr={res.stderr[-300:]!r}"


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
    (tmp / "probe.py").write_text(_UTF8_HOOK, encoding="utf-8")
    verdict = _run_utf8_hook(tmp)
    check(isinstance(verdict, list) and verdict[1:] == ["❌ out\n", "❌ err\n"],
          f"shared.run decodes a hook's UTF-8 output without PYTHONUTF8, got {verdict!r}")
finally:
    shared.HOOKS, shared.HOOK_SPAWN_TIMEOUT_S = real_hooks, real_timeout
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_hook_spawn_timeout")
