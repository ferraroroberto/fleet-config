"""Owned scheduled process scope and nonblocking pipe reads (stdlib only).

Windows creates a suspended launcher, assigns it to a private kill-on-close job,
then resumes it. Even a venv redirector cannot create children before ownership.
The stdin release is prepared while suspended; a failed release cannot run code.
"""
from __future__ import annotations

import codecs
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import logging
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
logger = logging.getLogger(__name__)


@contextmanager
def _defer_launch_interrupt():
    # A Ctrl+C inside Popen can otherwise raise after CreateProcess succeeds
    # but before its object reaches the owner. Delay only this short Windows
    # creation window, then deliver the original handler inside launch's try.
    if sys.platform != "win32" or threading.current_thread() is not threading.main_thread():
        yield
        return
    handler = signal.getsignal(signal.SIGINT)
    if not callable(handler):
        yield
        return
    pending = []
    def remember(signum, frame):
        pending[:] = [(signum, frame)]
    signal.signal(signal.SIGINT, remember)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, handler)
        if pending:
            handler(*pending[0])


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("tid", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD)]


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
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetProcessIdOfThread": ([wintypes.HANDLE], wintypes.DWORD),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
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

    def resume(self, process: subprocess.Popen) -> None:
        # Popen closes CreateProcess's initial thread handle. Its suspended
        # process is retained and has exactly one thread; never resume a thread
        # selected by name or by the PID of an exited process.
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        thread_ids = []
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == process.pid:
                    thread_ids.append(entry.tid)
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(snapshot)
        if process.poll() is not None or len(thread_ids) != 1:
            raise OSError("suspended launcher does not have one live primary thread")
        thread = self.api.OpenThread(0x0002 | 0x0040, False, thread_ids[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.api.GetProcessIdOfThread(thread) != process.pid:
                raise OSError("suspended thread ownership could not be confirmed")
            previous = self.api.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if previous != 1:
                raise OSError(f"unexpected launcher suspend count: {previous}")
        finally:
            self.api.CloseHandle(thread)

    def terminate(self) -> bool:
        return bool(self.api.TerminateJobObject(self.handle, 1))

    def wait_process(self, process: subprocess.Popen) -> bool:
        # GetExitCodeProcess/Popen.poll can expose an exit code before the
        # process handle is signaled. Observe the actual terminal handle too.
        return self.api.WaitForSingleObject(int(process._handle), int(TERMINATE_TIMEOUT_SECONDS * 1000)) == 0

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

    def launch(self, command: list[str], *, env: Optional[dict[str, str]] = None) -> subprocess.Popen:
        """Create and release one owned process; callers cannot launch it early."""
        if self.process is not None:
            raise RuntimeError("process scope already used")
        argv = command if self.job is None else [
            sys.executable, "-I", str(Path(__file__).resolve()), "--bootstrap", *command]
        try:
            with _defer_launch_interrupt():
                self.process = subprocess.Popen(
                    argv, stdin=subprocess.PIPE if self.job is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, env=env,
                    creationflags=NO_WINDOW | (0x00000004 if self.job is not None else 0),
                    start_new_session=self.job is None,
                )
            self.process._scheduled_scope = self
            if self.job is not None:
                self.job.assign(self.process)
                self._release()
                self.job.resume(self.process)
            return self.process
        except BaseException as error:
            confirmed = self._abort_launch()
            logger.info("scoped launch aborted: cleanup=%s", "confirmed" if confirmed else "unknown")
            if not confirmed:
                raise RuntimeError("scoped launch failed; owned cleanup unknown") from error
            raise

    def _release(self) -> None:
        assert self.process is not None and self.process.stdin is not None
        if self.process.stdin.write(b"1") != 1:
            raise OSError("bootstrap release was not written")
        self.process.stdin.close()

    def _abort_launch(self) -> bool:
        # An unassigned launcher is still suspended and has no descendants.
        # A resumed launcher and all its ordinary children are owned by the job.
        confirmed = True
        try:
            if self.process is not None:
                confirmed = self.terminate()
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
                if self.job is not None:
                    confirmed = self.job.wait_process(self.process) and confirmed
        except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
            confirmed = False
        finally:
            self.close()
            if self.process is not None:
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        stream.close()
        return confirmed

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
