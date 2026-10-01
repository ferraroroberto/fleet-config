"""Make the package directory executable, like `design_review/__main__.py`.

`python <...>/skills/_lib/perf_review measure <repo>` (directory execution)
and `python -m perf_review measure <repo>` both land here. Directory
execution puts *this* directory on `sys.path[0]` rather than `skills/_lib`,
so the parent is added explicitly before importing the package by name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perf_review.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
