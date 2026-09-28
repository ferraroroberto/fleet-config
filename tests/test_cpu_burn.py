"""cpu_burn.py: below-normal burners and command, scoped cleanup (fleet-config#1076)."""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402
import cpu_burn  # noqa: E402

_h = CheckHarness()
check = _h.check
WINDOWS = sys.platform == "win32"

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    SYNCHRONIZE = 0x00100000


def priority_class(proc: subprocess.Popen) -> int:
    return _k32.GetPriorityClass(int(proc._handle))


def exited_within(handle: int, seconds: float) -> bool:
    return _k32.WaitForSingleObject(handle, int(seconds * 1000)) == 0


# ---- the burners start below normal priority and stop when asked ----
with cpu_burn.burning(2, max_minutes=5) as procs:
    check(len(procs) == 2, "burning(2) starts two burners")
    time.sleep(0.5)
    check(all(p.poll() is None for p in procs), "burners are running inside the block")
    if WINDOWS:
        classes = [priority_class(p) for p in procs]
        check(all(c == cpu_burn.BELOW_NORMAL for c in classes),
              f"burners run at BELOW_NORMAL_PRIORITY_CLASS (got {classes})")
check(all(p.poll() is not None for p in procs), "burners are stopped after the block")

# ---- cleanup also happens when the body raises ----
held = []
try:
    with cpu_burn.burning(1, max_minutes=5) as procs:
        held.extend(procs)
        raise RuntimeError("proof blew up")
except RuntimeError:
    pass
check(held and all(p.poll() is not None for p in held),
      "burners are stopped when the body raises")

# ---- a burner ends itself when the helper's pipe closes (helper hard-killed) ----
[orphan] = cpu_burn.start_burners(1, max_minutes=5)
orphan.stdin.close()
try:
    orphan.wait(timeout=10)
except subprocess.TimeoutExpired:
    orphan.kill()
    orphan.wait()
    check(False, "a burner exits within 10s of its stdin pipe closing")

# ---- ... and after its deadline, even with the pipe still open ----
[capped] = cpu_burn.start_burners(1, max_minutes=0.01)
try:
    capped.wait(timeout=10)
except subprocess.TimeoutExpired:
    check(False, "a burner exits by itself at --max-minutes")
finally:
    cpu_burn.stop_burners([capped])

# ---- the command and its descendants inherit below-normal priority ----
if WINDOWS:
    probe = textwrap.dedent("""
        import ctypes, subprocess, sys
        k = ctypes.windll.kernel32
        k.GetPriorityClass.restype = ctypes.c_uint32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.GetPriorityClass.argtypes = (ctypes.c_void_p,)
        own = k.GetPriorityClass(k.GetCurrentProcess())
        if len(sys.argv) > 1:
            sys.exit(0 if own == 0x4000 else 5)
        child = subprocess.run([sys.executable, __file__, "child"],
                               creationflags=subprocess.CREATE_NO_WINDOW)
        sys.exit(0 if own == 0x4000 and child.returncode == 0 else 4)
    """)
    with tempfile.TemporaryDirectory() as tmp:
        probe_file = Path(tmp) / "priority_probe.py"
        probe_file.write_text(probe, encoding="utf-8")
        code = cpu_burn.run_below_normal([sys.executable, str(probe_file)])
        check(code == 0, f"command and its child both run below normal (exit {code})")
else:
    _h.skip("priority-class inheritance is only checked on Windows")

# ---- main(): red is reported, burners cleaned up, others left alone ----
started = []
real_start = cpu_burn.start_burners


def recording_start(count, max_minutes=cpu_burn.DEFAULT_MAX_MINUTES):
    procs = real_start(count, max_minutes)
    started.extend(procs)
    return procs


bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             creationflags=NO_WINDOW)
cpu_burn.start_burners = recording_start
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        code = cpu_burn.main(["--burners", "2", "--runs", "2", "--",
                              sys.executable, "-c", "import sys; sys.exit(3)"])
finally:
    cpu_burn.start_burners = real_start
check(code == 1, f"main exits 1 when a run is red (got {code})")
check("BURN-RESULT: red 2 of 2" in out.getvalue(), "main prints the red count")
check(len(started) == 2 and all(p.poll() is not None for p in started),
      "main stops every burner it started after red runs")
check(bystander.poll() is None, "a process the helper didn't start is left alone")
bystander.kill()
bystander.wait()

with contextlib.redirect_stdout(io.StringIO()) as out:
    code = cpu_burn.main(["--burners", "1", "--", sys.executable, "-c", "pass"])
check(code == 0 and "BURN-RESULT: red 0 of 1" in out.getvalue(),
      "main exits 0 when every run is green")

# ---- a relative program path resolves; an unknown one is a usage error ----
venv_python = Path(sys.executable)
if venv_python.is_relative_to(Path.cwd()):
    rel = venv_python.relative_to(Path.cwd()).as_posix()
    with contextlib.redirect_stdout(io.StringIO()):
        code = cpu_burn.main(["--burners", "1", "--", rel, "-c", "pass"])
    check(code == 0, f"a relative forward-slash program path runs ({rel})")
else:
    _h.skip("relative program path: the interpreter is not under the cwd")
try:
    with contextlib.redirect_stderr(io.StringIO()):
        cpu_burn.main(["--burners", "1", "--", "no-such-program-1076"])
    check(False, "an unknown program is refused")
except SystemExit as exc:
    check(exc.code == 2, "an unknown program is a usage error (exit 2)")

# ---- the command's output reaches the helper's own stdout and stderr ----
shown = subprocess.run(
    [sys.executable, cpu_burn.__file__, "--burners", "1", "--", sys.executable, "-c",
     "import sys; print('proof-out-1076'); print('proof-err-1076', file=sys.stderr)"],
    capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW)
check("proof-out-1076" in shown.stdout and "proof-err-1076" in shown.stderr,
      "the command under proof writes to the helper's stdout and stderr "
      f"(stdout={shown.stdout!r}, stderr={shown.stderr!r})")

# ---- more burners than cores needs --over-cores ----
too_many = str(cpu_burn.core_count() + 1)
try:
    with contextlib.redirect_stderr(io.StringIO()):
        cpu_burn.main(["--burners", too_many, "--", sys.executable, "-c", "pass"])
    check(False, "more burners than cores is refused without --over-cores")
except SystemExit as exc:
    check(exc.code == 2, "the over-cores refusal is a usage error (exit 2)")

# ---- a hard-killed helper takes its burners with it ----
if WINDOWS:
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]); import cpu_burn; "
         "procs = cpu_burn.start_burners(2, 5); "
         "print(' '.join(str(p.pid) for p in procs), flush=True); time.sleep(60)",
         str(Path(cpu_burn.__file__).parent)],
        stdout=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
    pids = [int(p) for p in holder.stdout.readline().split()]
    handles = [_k32.OpenProcess(SYNCHRONIZE, False, pid) for pid in pids]
    holder.kill()
    holder.wait()
    check(len(pids) == 2 and all(handles), "the holder reported two open burners")
    check(all(h and exited_within(h, 10) for h in handles),
          "burners exit within 10s of their helper being hard-killed")
    for h in handles:
        if h:
            _k32.CloseHandle(h)
else:
    _h.skip("hard-kill cleanup is only checked on Windows")

_h.report_and_exit("test_cpu_burn")
