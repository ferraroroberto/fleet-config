"""Owned scheduled process scope and nonblocking pipe reads (stdlib only).

Windows starts a waiting Python bootstrap, assigns it to a private kill-on-close
job, then releases the native command. Descendants remain in that job after the
bootstrap/native parent exits. Assignment failure never releases the command.
"""
from __future__ import annotations

import codecs
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW

TERMINATE_TIMEOUT_SECONDS = 2.0
DRAIN_TIMEOUT_SECONDS = 1.0


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
        ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
        ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
    ]


class _WindowsJob:
    def __init__(self) -> None:
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen) -> None:
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def active(self) -> Optional[int]:
        info = _Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return int(info.active)

    def terminate(self) -> bool:
        return bool(self.api.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProcessScope:
    """Own only this launch; Windows job identity survives its root PID."""

    def __init__(self) -> None:
        self.job = _WindowsJob() if sys.platform == "win32" else None
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def command(self, command: list[str]) -> list[str]:
        if self.job is None:
            return command
        return [sys.executable, "-I", str(Path(__file__).resolve()), "--bootstrap", *command]

    def start(self, process: subprocess.Popen) -> None:
        self.process = process
        if self.job is not None:
            try:
                self.job.assign(process)
                assert process.stdin is not None
                process.stdin.write(b"1")
                process.stdin.close()
            except BaseException:
                # Assignment failure leaves the bootstrap waiting. If release
                # itself failed, the job still owns any command already started.
                try:
                    process.kill()
                    process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
                finally:
                    self.close()
                raise

    def active(self) -> Optional[int]:
        if self.job is not None:
            return self.job.active()
        if self.process is None:
            return 0
        try:
            os.killpg(self.process.pid, 0)
            return 1
        except ProcessLookupError:
            return 0
        except OSError:
            return None

    def terminate(self) -> bool:
        with self._lock:
            if self.process is None:
                return True
            if self.job is not None:
                requested = self.job.terminate()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    requested = True
                except ProcessLookupError:
                    requested = True
                except OSError:
                    requested = False
            if not requested:
                return False
            deadline = time.monotonic() + TERMINATE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                # Reap the direct child before checking group liveness on POSIX.
                self.process.poll()
                count = self.active()
                if count == 0:
                    return True
                if count is None:
                    return False
                time.sleep(0.01)
            return False

    def close(self) -> None:
        with self._lock:
            if self.job is not None:
                self.job.close()


class PipeReader:
    """Poll one pipe without a blocked readline/read thread or blocking close."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.eof = False
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        if sys.platform != "win32":
            os.set_blocking(stream.fileno(), False)

    def poll(self) -> list[str]:
        if self.eof:
            return []
        if sys.platform == "win32":
            import _winapi
            import msvcrt
            try:
                available = _winapi.PeekNamedPipe(msvcrt.get_osfhandle(self.stream.fileno()), 0)[0]
            except BrokenPipeError:
                chunk = b""
            else:
                if not available:
                    return []
                chunk = os.read(self.stream.fileno(), min(available, 65536))
        else:
            try:
                chunk = os.read(self.stream.fileno(), 65536)
            except BlockingIOError:
                return []
        self.eof = chunk == b""
        self.pending += self.decoder.decode(chunk, final=self.eof)
        parts = self.pending.split("\n")
        self.pending = parts.pop()
        if self.eof and self.pending:
            parts.append(self.pending)
            self.pending = ""
        return parts


def _bootstrap() -> int:
    if sys.argv[1:2] != ["--bootstrap"] or len(sys.argv) < 3:
        return 2
    if sys.stdin.buffer.read(1) != b"1":
        return 2
    try:
        return subprocess.call(sys.argv[2:], stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError:
        return 127


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
