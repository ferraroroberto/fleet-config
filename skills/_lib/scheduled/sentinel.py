"""Detached launch and result-file publishing for a sweep too long to wait on inline.

The launching call returns at once; the detached child publishes one JSON
sentinel that a caller polls with `wait_for_sentinel.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import runtime_files  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402


def write_result(out_path: Path, payload: dict) -> None:
    """Atomically publish `payload` as JSON to `out_path`.

    Temp-then-rename, so a poller can never observe a partially written file.
    `Path.replace` is an atomic overwrite on Windows too (unlike `Path.rename`,
    which raises if the destination exists), so a stale sentinel left by a
    crashed run at the same path is safe to overwrite.
    """
    runtime_files.make_dirs(out_path.parent, parents=True, exist_ok=True)
    tmp = out_path.with_name(f"{out_path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    runtime_files.save_text(tmp, json.dumps(payload), encoding="utf-8")
    tmp.replace(out_path)


def launch_detached(entry: Path, child_argv: list[str], log_path: Path) -> int:
    """Spawn `entry` (a script or a package directory) with this interpreter as an
    independent, console-less process that outlives this call, its output in
    `log_path`. Does not wait for the child; returns its pid.
    """
    runtime_files.make_dirs(log_path.parent, parents=True, exist_ok=True)
    with runtime_files.open_for_write(log_path, "wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, str(entry), *child_argv],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | NO_WINDOW,
            close_fds=True,
        )
    return proc.pid
