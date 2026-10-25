"""Fixtures for the scheduled runner's orphan-descendant tests: a held provider and kernel32 handles."""
from __future__ import annotations

import ctypes
from ctypes import wintypes


def kernel32_handles():
    """kernel32 with the process-handle calls the orphan test needs, typed for 64-bit handles."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def held_provider_script(pipe, ready, marker, release, good, flags):
    """A provider that starts a slow descendant on `pipe`, prints one record, then holds its exit."""
    descendant = f"import os,time;from pathlib import Path;Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(8);Path({str(marker)!r}).touch()"
    out = "sys.stdout" if pipe in ("stdout", "both") else "subprocess.DEVNULL"
    err = "sys.stderr" if pipe in ("stderr", "both") else "subprocess.DEVNULL"
    # The provider holds its exit until the test releases it: an
    # exit the runner sees before the cancel starts a 1s drain the
    # cancel then has to win, and under load it lost (#1039).
    return (f"import subprocess,sys,time;from pathlib import Path;"
            f"subprocess.Popen([sys.executable,'-c',{descendant!r}],stdout={out},stderr={err},creationflags={flags});"
            f"print({good!r},flush=True);t=time.monotonic()+30\n"
            f"while not Path({str(release)!r}).exists() and time.monotonic()<t: time.sleep(.01)")
