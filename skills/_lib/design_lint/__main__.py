"""Make the package directory executable, so the pre-split CLI path still works.

`python <...>/skills/_lib/design_lint all <root>` (directory execution) and
`python -m design_lint all <root>` both land here. Directory execution puts
*this* directory on `sys.path[0]` rather than `skills/_lib`, so the parent is
added explicitly before importing the package by name — a module run as
`__main__` has no package context, so relative imports are unavailable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from design_lint.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
