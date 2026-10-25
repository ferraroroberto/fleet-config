"""claude_progress process control: the stall watchdog kills a silent run and reaps its grandchild, and the CLI background-wait ceiling is lifted for every child.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_stall_and_ceiling.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from _suite import INIT_LINE, RESULT_LINE, ReadinessClock, TOOL_LINE, _h, check, cp  # noqa: E402


# ---- stall watchdog: a silent run is killed, not left wedged (fleet-config#411) ----

# The child leaves a grandchild holding the inherited stdout pipe and then goes
# quiet — the exact shape that wedged a scheduled run for eight hours. The exit
# code and elapsed time cannot pin the tree-kill on their own: run_process polls
# its pipes without blocking and leaves the read loop at a bounded drain deadline
# whether or not the pipes reached EOF (fleet-config#926). So the grandchild
# records its own PID, and each stall case asserts that process is gone once
# run_process returns — killed by the watchdog's job-object kill, with the job
# close in run_process's teardown as the backstop.
HEARTBEAT_LINE = (
    "print(json.dumps({'type':'system','subtype':'thinking_tokens',"
    "'estimated_tokens':1}), flush=True)"
)
GRANDCHILD_EXIT_WAIT_SECONDS = 5.0


def stall_script(pid_file: Path) -> str:
    """Child that spawns a pipe-holding grandchild, then goes silent.

    The child keeps the stream alive until the grandchild has written its PID,
    so a slow grandchild start on a loaded box cannot race the 2 s stall limit
    and kill the tree before there is a PID to check. The child's own start,
    before its first line, is covered by the caller's ``ReadinessClock``.
    """
    # Written aside then renamed, so the child's exists() poll never sees an empty file.
    grandchild = (f"import os,time; from pathlib import Path; "
                  f"Path({str(pid_file) + '.tmp'!r}).write_text(str(os.getpid())); "
                  f"os.replace({str(pid_file) + '.tmp'!r}, {str(pid_file)!r}); time.sleep(300)")
    return (
        "import json,subprocess,sys,time\n"
        "from pathlib import Path\n"
        + INIT_LINE + "\n"
        + f"subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
        + "deadline = time.monotonic() + 60\n"
        + f"while not Path({str(pid_file)!r}).exists() and time.monotonic() < deadline:\n"
        + f"    {HEARTBEAT_LINE}; time.sleep(0.2)\n"
        + "time.sleep(300)"
    )


def wait_for_exit(pid: int, timeout: float) -> Optional[bool]:
    """True once ``pid`` has exited, False if still alive at ``timeout``, None if unknowable."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # ERROR_INVALID_PARAMETER: no process has this PID any more.
            return True if ctypes.get_last_error() == 87 else None
        try:
            result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        finally:
            kernel32.CloseHandle(handle)
        return {0x0: True, 0x102: False}.get(result)  # WAIT_OBJECT_0 / WAIT_TIMEOUT
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return None
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def check_grandchild_reaped(pid_file: Path, case: str) -> None:
    try:
        pid = int(pid_file.read_text())
    except (OSError, ValueError):
        check(False, f"{case}: the grandchild reported its PID before the stall kill")
        return
    exited = wait_for_exit(pid, GRANDCHILD_EXIT_WAIT_SECONDS)
    state = {True: "exited", False: "still running", None: "liveness unknown"}[exited]
    check(exited is True, f"{case}: the stall kill reaped the pipe-holding grandchild (pid {pid} {state})")


stall_dir = Path(tempfile.mkdtemp(prefix="claude_progress_stall_"))
stall_lines: list[str] = []
started = time.monotonic()
stall_exit = cp.run_process(
    [sys.executable, "-c", stall_script(stall_dir / "stall.pid")],
    formatter=cp.ProgressFormatter(emit=stall_lines.append,
                                   clock=ReadinessClock((stall_dir / "stall.pid").exists)),
    stall_timeout=2.0,
)
stall_elapsed = time.monotonic() - started
stall_output = "\n".join(stall_lines)
check(stall_exit == cp.STALL_EXIT_CODE,
      f"a stalled run exits {cp.STALL_EXIT_CODE}, not the killed child's code")
check(stall_elapsed < 60, f"the watchdog returns promptly (took {stall_elapsed:.1f}s)")
check("no stream activity" in stall_output, "the stall is reported with its idle time")
check("⏱ stalled" in stall_output, "the terminal milestone names the stall distinctly")
check_grandchild_reaped(stall_dir / "stall.pid", "plain stall")

# A jammed stdout must not cost the kill (fleet-config#514). The diagnostic emit
# used to run *before* _kill_process_tree, so a blocked write parked the watchdog
# thread and the child was never killed. This emit blocks forever on the stall
# line; the run must still tear the tree down and exit STALL_EXIT_CODE. The exit
# code and elapsed time don't prove the kill: the watchdog kills the job object
# (every descendant is a member from creation) *before* the emit, and the read
# loop leaves at a bounded drain deadline either way, so the grandchild's own
# PID is what shows the tree really died.
blocked_lines: list[str] = []
blocked_release = threading.Event()


def blocking_emit(line: str) -> None:
    blocked_lines.append(line)
    if "no stream activity" in line:
        blocked_release.wait()  # never set while the run is in flight


blocked_started = time.monotonic()
blocked_exit = cp.run_process(
    [sys.executable, "-c", stall_script(stall_dir / "blocked.pid")],
    formatter=cp.ProgressFormatter(emit=blocking_emit,
                                   clock=ReadinessClock((stall_dir / "blocked.pid").exists)),
    stall_timeout=2.0,
)
blocked_elapsed = time.monotonic() - blocked_started
blocked_release.set()  # release the parked watchdog thread
check(blocked_exit == cp.STALL_EXIT_CODE,
      "a stall kill still exits 124 when the diagnostic emit blocks forever")
check(blocked_elapsed < 60,
      f"the blocked-emit stall still returns promptly (took {blocked_elapsed:.1f}s)")
check(any("⏱ stalled" in line for line in blocked_lines),
      "finish() still reports the stall after the watchdog's emit blocked")
check_grandchild_reaped(stall_dir / "blocked.pid", "blocked-emit stall")
shutil.rmtree(stall_dir, ignore_errors=True)

# A child that keeps talking must never be killed, however long it runs.
chatty_script = (
    "import json,sys,time; "
    + INIT_LINE
    + TOOL_LINE
    + RESULT_LINE
    + "[ (print(json.dumps({'type':'system','subtype':'thinking_tokens',"
    "'estimated_tokens':1}), flush=True), time.sleep(0.2)) for _ in range(15) ]"
)
chatty_lines: list[str] = []
chatty_exit = cp.run_process(
    [sys.executable, "-c", chatty_script],
    # Ready at the child's first milestone (init's "session started"): from
    # there it talks every 0.2 s, and that is the cadence under test.
    formatter=cp.ProgressFormatter(emit=chatty_lines.append,
                                   clock=ReadinessClock(lambda: bool(chatty_lines))),
    stall_timeout=2.0,
)
chatty_output = "\n".join(chatty_lines)
check(chatty_exit == 0, "a continuously-producing run is left alone by the watchdog")
check("no stream activity" not in chatty_output and "⏱ stalled" not in chatty_output,
      "a busy run is never reported as stalled")

# Explicitly disabled: the watchdog must not fire at all.
disabled_lines: list[str] = []
disabled_exit = cp.run_process(
    [sys.executable, "-c", "import json; " + INIT_LINE + TOOL_LINE + RESULT_LINE],
    formatter=cp.ProgressFormatter(emit=disabled_lines.append),
    stall_timeout=0,
)
check(disabled_exit == 0 and "⏱ stalled" not in "\n".join(disabled_lines),
      "stall_timeout=0 disables the watchdog")


# ---- the CLI background-wait ceiling is lifted for every child (fleet-config#519) ----

# #506 only made the sub-agent kill *visible*; the work was still lost. These
# probes exit with a code encoding what the child actually saw, so a missing or
# wrong value can never pass as a clean run.
CEILING = cp.BG_WAIT_CEILING_ENV
ceiling_probe = (
    "import json,os,sys; "
    + INIT_LINE
    + TOOL_LINE
    + RESULT_LINE
    + f"sys.exit(0 if os.environ.get({CEILING!r}) == '0' else 3)"
)
ceiling_lines: list[str] = []
ceiling_exit = cp.run_process(
    [sys.executable, "-c", ceiling_probe],
    formatter=cp.ProgressFormatter(emit=ceiling_lines.append),
)
check(ceiling_exit == 0, f"run_process sets {CEILING}=0 in the spawned child's environment")

# A stale value inherited from the parent must not silently reinstate the kill.
os.environ[CEILING] = "600000"
inherited_lines: list[str] = []
try:
    inherited_exit = cp.run_process(
        [sys.executable, "-c", ceiling_probe],
        formatter=cp.ProgressFormatter(emit=inherited_lines.append),
    )
finally:
    del os.environ[CEILING]
check(inherited_exit == 0,
      "an inherited ceiling from the parent environment is overridden, never honoured")

# An explicit caller override is still the escape hatch and still wins.
override_probe = (
    "import json,os,sys; "
    + INIT_LINE
    + TOOL_LINE
    + RESULT_LINE
    + f"sys.exit(0 if os.environ.get({CEILING!r}) == '900' else 3)"
)
override_lines: list[str] = []
override_exit = cp.run_process(
    [sys.executable, "-c", override_probe],
    formatter=cp.ProgressFormatter(emit=override_lines.append),
    env={CEILING: "900"},
)
check(override_exit == 0, "an explicit env= override still beats the adapter's default ceiling")

_h.report_and_exit("test_stall_and_ceiling")
