"""Render the system-map HTML to a PNG via headless Chrome — deterministically.

Two-pass: probe once to read the page's own ``DIMS w h`` (logged to the console),
then screenshot at exactly that size so there is no empty canvas and nothing is
clipped. Always renders with ``?placeholders=1`` so the committed PNG never bakes
in the real hardware specs from a local ``system-map.local.js``.

Used by the ``/system-map`` skill; also runnable by hand (invoke the resolved
Python path directly — a bare ``py``/``python`` is not reliably on ``PATH`` on
this machine; see ``_lib.find_python_executable``)::

    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render.py            # defaults to architecture/system-map.{html,png}
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render.py --scale 3  # crisper

Chrome and a working tmp dir are the only requirements (no extra Python deps).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from html_shot import render_cli  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HTML = REPO_ROOT / "architecture" / "system-map.html"
DEFAULT_OUT = REPO_ROOT / "architecture" / "system-map.png"


def main(argv: list[str] | None = None) -> int:
    return render_cli(
        argv,
        default_html=DEFAULT_HTML,
        default_out=DEFAULT_OUT,
        description="Render system-map.html → PNG (placeholders only).",
        success_note=", placeholders",
    )


if __name__ == "__main__":
    sys.exit(main())
