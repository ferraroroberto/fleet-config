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

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from html_shot import shoot  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HTML = REPO_ROOT / "architecture" / "config-map.html"
DEFAULT_OUT = REPO_ROOT / "architecture" / "config-map.png"


def render(html: Path, out: Path, scale: float = 2.0) -> tuple[int, int]:
    """Render ``html`` to ``out`` at ``scale`` DPR. Return the (w, h) used.

    Forces ``?placeholders=1`` for parity with ``/system-map`` — the config
    dataset carries only wiring/structure (never a secret), so this is a
    reserved no-op safety net here rather than a spec-hider.
    """
    return shoot(html, out, scale=scale, query="placeholders=1")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render config-map.html → PNG.")
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args(argv)
    try:
        w, h = render(args.html, args.out, args.scale)
    except Exception as exc:  # noqa: BLE001 - surface a clean one-line error
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {args.out} at {w}x{h} (scale {args.scale})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
