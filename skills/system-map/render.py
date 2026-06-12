"""Render the system-map HTML to a PNG via headless Chrome — deterministically.

Two-pass: probe once to read the page's own ``DIMS w h`` (logged to the console),
then screenshot at exactly that size so there is no empty canvas and nothing is
clipped. Always renders with ``?placeholders=1`` so the committed PNG never bakes
in the real hardware specs from a local ``system-map.local.js``.

Used by the ``/system-map`` skill; also runnable by hand::

    py skills/system-map/render.py            # defaults to architecture/system-map.{html,png}
    py skills/system-map/render.py --scale 3  # crisper

Chrome and a working tmp dir are the only requirements (no extra Python deps).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HTML = REPO_ROOT / "architecture" / "system-map.html"
DEFAULT_OUT = REPO_ROOT / "architecture" / "system-map.png"
DIMS_RE = re.compile(rb"DIMS (\d+) (\d+)")


def _file_url(html: Path) -> str:
    # forward-slashed absolute path + force placeholders (never real specs)
    return "file:///" + str(html.resolve()).replace("\\", "/") + "?placeholders=1"


def _run_chrome(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", *args],
        capture_output=True, timeout=120,
    )


def render(html: Path, out: Path, scale: float = 2.0) -> tuple[int, int]:
    """Render ``html`` to ``out`` at ``scale`` DPR. Return the (w, h) used."""
    if not html.is_file():
        raise FileNotFoundError(html)
    url = _file_url(html)
    tmp = Path(tempfile.gettempdir())

    # 1. probe for the page's measured dimensions
    probe = _run_chrome([
        "--enable-logging=stderr", "--v=0", "--virtual-time-budget=8000",
        "--window-size=400,300", f"--screenshot={tmp / ('sm_probe_' + uuid.uuid4().hex + '.png')}",
        url,
    ])
    m = DIMS_RE.search(probe.stderr)
    if not m:
        raise RuntimeError(
            "could not read DIMS from the page — render failed.\n"
            + probe.stderr.decode("utf-8", "replace")[-800:]
        )
    w, h = int(m.group(1)), int(m.group(2))

    # 2. screenshot at the measured size; render to tmp then copy (the target dir
    #    sometimes holds a write lock on the existing PNG on Windows).
    staged = tmp / f"sm_{uuid.uuid4().hex}.png"
    shot = _run_chrome([
        f"--force-device-scale-factor={scale}", f"--window-size={w},{h}",
        "--virtual-time-budget=8000", f"--screenshot={staged}", url,
    ])
    if not staged.is_file():
        raise RuntimeError(
            "screenshot was not written.\n" + shot.stderr.decode("utf-8", "replace")[-800:]
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(staged, out)
    staged.unlink(missing_ok=True)
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render system-map.html → PNG (placeholders only).")
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args(argv)
    try:
        w, h = render(args.html, args.out, args.scale)
    except Exception as exc:  # noqa: BLE001 - surface a clean one-line error
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {args.out} at {w}x{h} (scale {args.scale}, placeholders)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
