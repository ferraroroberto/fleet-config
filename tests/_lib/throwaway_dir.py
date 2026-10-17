"""A temp directory that is removed when the process exits (fleet-config#1484).

`tempfile.mkdtemp` hands back a directory nothing ever removes; the design-review suites each made
one per run and left 782 of them under the system temp dir. `atexit` runs on a pass, a failed check
(`CheckHarness.report_and_exit` raises SystemExit), and an uncaught exception alike. A hard kill
(a gate timeout) skips it, and a directory a still-running child holds open is left behind.

A dir holding a committed git repo needs its read-only bits cleared first: git writes object files
read-only, and `rmtree(ignore_errors=True)` silently skips them on Windows (fleet-config#1487).
"""

from __future__ import annotations

import atexit
import os
import shutil
import stat
import tempfile
from pathlib import Path


def _clear_readonly_and_retry(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_dir(path: Path) -> None:
    shutil.rmtree(path, onexc=_clear_readonly_and_retry)


def throwaway_dir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(remove_dir, path)
    return path
