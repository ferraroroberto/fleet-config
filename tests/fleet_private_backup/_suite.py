"""Unit tests for the daily fleet-private backup engine (fleet-config#590).

Everything here runs against real temp trees, a real `git init`ed repo, a real
Windows junction, and real hardlinks — the three things this engine gets wrong
if it only ever meets mocks are exactly junction traversal, `st_nlink`, and what
`git ls-files --others --ignored` actually emits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import backup as bp  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
skip = _h.skip

NOW = datetime(2026, 8, 11, 3, 0, 0, tzinfo=timezone.utc)
#: The wedged-run test waits out the real lock deadline plus the rest of the run.
DB_LOCK_WAIT_LIMIT = bp.DB_LOCK_TIMEOUT_SECONDS + 60


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=bp._lib.NO_WINDOW,
    )


def _make_junction(link: Path, target: Path) -> bool:
    """Create a real directory junction; False when the platform cannot."""
    if sys.platform != "win32":
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except OSError:
            return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, creationflags=bp._lib.NO_WINDOW,
    )
    return result.returncode == 0 and link.exists()


def _cfg(tmp: Path, **overrides) -> bp.BackupConfig:
    base = dict(
        source_root=tmp / "fleet",
        dest=tmp / "dest",
        transcripts_src=tmp / "transcripts",
        transcripts_dest=tmp / "tdest",
        codex_sessions_src=tmp / "codex-sessions",
        codex_sessions_dest=tmp / "codex-dest",
        # Absent by default, so every pre-existing case here keeps its two-leg
        # shape and the runtime-data cases opt in explicitly.
        runtime_data_src=tmp / "runtime-data-src",
        runtime_data_dest=tmp / "runtime-data-dest",
        keep_daily=14,
        keep_weekly=8,
        max_file_bytes=1024,
        bulk_dir_bytes=2048,
        freshness_max_hours=48,
    )
    base.update(overrides)
    return bp.BackupConfig(**base)


tmp = Path(tempfile.mkdtemp(prefix="backup_private_"))

cfg = _cfg(tmp)
