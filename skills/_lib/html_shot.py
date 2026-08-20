"""Shared headless-Chrome HTML -> PNG screenshot helper (fleet-config#96).

`.claude/skills/system-map/render.py` (fleet-config#94) and `/docs-shots`
(fleet-config#93) both need to turn an HTML page into a deterministic PNG.
Before there were two divergent copies of that dance to maintain, this module
extracts the shared technique: a two-pass **measure-then-shoot** render — probe
once to read the page's own `DIMS w h` (logged to the console via `--enable-
logging=stderr`), then screenshot at exactly that size so there is no empty
canvas and nothing is clipped — plus the Windows write-lock workaround
(render to a temp file, then copy into place, since the target PNG sometimes
holds an open handle).

`shoot()` accepts either a local HTML file (`Path`, or a plain path string —
built into a `file:///` URL) or an already-live URL (`http://`/`https://`,
e.g. a running webapp's page for `/docs-shots`) — used as-is, since a running
server is the only way to screenshot a page whose assets aren't same-origin-
fetchable from `file://`. An optional `query` string is appended either way
(`?query=` or `&query=` depending on whether the target already has one) —
`render_cli()` (below) uses this to force `?placeholders=1` so the committed
PNG never bakes in real hardware specs from a local `system-map.local.js`; it
is also the shared `--html/--out/--scale` entry point both map renderers call,
so neither has to re-implement the wrapper (fleet-config#677).

stdlib + Chrome only (matches the `_lib` module contract) — no extra Python
deps, per the repo's "system Python, no venv" hook convention.
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
from typing import List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

_DEFAULT_CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
DIMS_RE = re.compile(rb"DIMS (\d+) (\d+)")
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


# ---- pure helpers (unit-tested without Chrome) ----------------------------

def is_url(target: str) -> bool:
    """True if `target` already carries a URL scheme (`http://`, `file://`, ...)."""
    return bool(_URL_SCHEME_RE.match(target))


def to_file_url(path: Path) -> str:
    """Absolute, forward-slashed `file:///` URL for a local HTML file."""
    return "file:///" + str(path.resolve()).replace("\\", "/")


def append_query(url: str, query: Optional[str]) -> str:
    """Append `query` to `url`, using `&` if `url` already has a `?`, else `?`.

    A no-op when `query` is falsy, so callers can pass `query=None` freely.
    """
    if not query:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{query}"


def build_target_url(html_or_url: Union[str, Path], query: Optional[str]) -> str:
    """Resolve `html_or_url` (a live URL string, or a local HTML path) + `query`.

    A string already carrying a URL scheme is used as-is (the CORS-safe path
    for a page whose assets aren't `file://`-fetchable, e.g. a running
    webapp); anything else — a `Path`, or a plain path string — is built into
    a `file:///` URL.
    """
    if isinstance(html_or_url, str) and is_url(html_or_url):
        return append_query(html_or_url, query)
    path = html_or_url if isinstance(html_or_url, Path) else Path(html_or_url)
    return append_query(to_file_url(path), query)


def parse_dims(stderr: bytes) -> Optional[Tuple[int, int]]:
    """Extract the page's self-reported `DIMS w h` from Chrome's stderr log."""
    m = DIMS_RE.search(stderr)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# ---- IO layer: Chrome discovery + the two-pass render ---------------------

def find_chrome() -> str:
    """The known Windows install path, or a `PATH` lookup as fallback."""
    if Path(_DEFAULT_CHROME).exists():
        return _DEFAULT_CHROME
    return shutil.which("chrome") or _DEFAULT_CHROME


def _run_chrome(chrome_exe: str, args: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [chrome_exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", *args],
        capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
    )


def shoot(
    html_or_url: Union[str, Path],
    out: Path,
    *,
    scale: float = 2.0,
    query: Optional[str] = None,
    virtual_time_budget: int = 8000,
    timeout: int = 120,
) -> Tuple[int, int]:
    """Render `html_or_url` to `out` at `scale` DPR. Returns the (w, h) used.

    Two-pass: probe once for the page's measured `DIMS w h`, then screenshot
    at exactly that size. Screenshots to a temp file first, then copies into
    place — the target dir sometimes holds a write lock on an existing PNG
    on Windows.
    """
    if isinstance(html_or_url, Path) and not html_or_url.is_file():
        raise FileNotFoundError(html_or_url)
    url = build_target_url(html_or_url, query)
    chrome_exe = find_chrome()
    tmp = Path(tempfile.gettempdir())

    # 1. probe for the page's measured dimensions
    probe = _run_chrome(chrome_exe, [
        "--enable-logging=stderr", "--v=0", f"--virtual-time-budget={virtual_time_budget}",
        "--window-size=400,300", f"--screenshot={tmp / ('html_shot_probe_' + uuid.uuid4().hex + '.png')}",
        url,
    ], timeout)
    dims = parse_dims(probe.stderr)
    if dims is None:
        raise RuntimeError(
            "could not read DIMS from the page — render failed.\n"
            + probe.stderr.decode("utf-8", "replace")[-800:]
        )
    w, h = dims

    # 2. screenshot at the measured size; render to tmp then copy.
    staged = tmp / f"html_shot_{uuid.uuid4().hex}.png"
    shot = _run_chrome(chrome_exe, [
        f"--force-device-scale-factor={scale}", f"--window-size={w},{h}",
        f"--virtual-time-budget={virtual_time_budget}", f"--screenshot={staged}", url,
    ], timeout)
    if not staged.is_file():
        raise RuntimeError(
            "screenshot was not written.\n" + shot.stderr.decode("utf-8", "replace")[-800:]
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(staged, out)
    staged.unlink(missing_ok=True)
    return w, h


def render_cli(
    argv: Optional[List[str]],
    *,
    default_html: Path,
    default_out: Path,
    description: str,
    query: Optional[str] = "placeholders=1",
    success_note: str = "",
) -> int:
    """The `--html/--out/--scale` CLI wrapper both map renderers need.

    `.claude/skills/system-map/render.py` and `.claude/skills/config-map/render.py`
    were near-byte-for-byte copies of the same argparse + try/except + one-line
    report (fleet-config#677) — differing only in their two default paths, the
    argparse description, and whether the success line mentions placeholders.
    Those four differences are now the parameters; each script keeps its
    docstring and nothing else. Returns the process exit code (0 / 1) so the
    caller's `main` is a one-liner.

    `query` defaults to `placeholders=1`, which is what forces the committed
    PNGs never to bake in real hardware specs from a local
    `system-map.local.js`; pass `None` for a renderer that must not set it.
    """
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--html", type=Path, default=default_html)
    ap.add_argument("--out", type=Path, default=default_out)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args(argv)
    try:
        w, h = shoot(args.html, args.out, scale=args.scale, query=query)
    except Exception as exc:  # noqa: BLE001 - surface a clean one-line error
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {args.out} at {w}x{h} (scale {args.scale}{success_note})")
    return 0
