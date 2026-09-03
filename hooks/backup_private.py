"""Thin CLI entry point for the daily fleet-private backup engine.

Not a hook — a plain scheduled program (`run-backup-daily.bat` → an
app-launcher Job → Task Scheduler). The engine itself lives in the
`hooks/backup/` package (fleet-config#731; formerly this file's own
1768 lines) — `hooks/backup/__init__.py` carries the full design-rationale
docstring (selection layers, storage model, honesty rules). This file stays
in place, unchanged in behaviour, so the daily job's invocation
(`python hooks/backup_private.py [--dry-run|--check-freshness|...]`) and the
docs that reference it need no edits.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backup.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
