"""Render the config-map HTML to a PNG via headless Chrome — deterministically.

Two-pass: probe once to read the page's own ``DIMS w h`` (logged to the console),
then screenshot at exactly that size so there is no empty canvas and nothing is
clipped. The ``?placeholders=1`` flag is forced for parity with ``/system-map`` —
the config dataset carries only wiring/structure (never a secret), so it is a
reserved no-op safety net here rather than a spec-hider.

Used by the ``/config-map`` skill; also runnable by hand (invoke the resolved
Python path directly — a bare ``py``/``python`` is not reliably on ``PATH`` on
this machine; see ``_lib.find_python_executable``)::

    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/render.py            # architecture/config-map.{html,png}
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/render.py --scale 3  # crisper

Chrome and a working tmp dir are the only requirements (no extra Python deps).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from html_shot import render_cli  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HTML = REPO_ROOT / "architecture" / "config-map.html"
DEFAULT_OUT = REPO_ROOT / "architecture" / "config-map.png"


def main(argv: list[str] | None = None) -> int:
    return render_cli(
        argv,
        default_html=DEFAULT_HTML,
        default_out=DEFAULT_OUT,
        description="Render config-map.html → PNG.",
    )


if __name__ == "__main__":
    sys.exit(main())
