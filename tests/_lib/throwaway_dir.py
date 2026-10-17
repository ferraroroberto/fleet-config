"""A temp directory that is removed when the process exits (fleet-config#1484).

`tempfile.mkdtemp` hands back a directory nothing ever removes; the design-review suites each made
one per run and left 782 of them under the system temp dir. `atexit` runs on a pass, a failed check
(`CheckHarness.report_and_exit` raises SystemExit), and an uncaught exception alike. A hard kill
(a gate timeout) skips it, and `ignore_errors` leaves a directory a still-running child holds open.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path


def throwaway_dir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path
