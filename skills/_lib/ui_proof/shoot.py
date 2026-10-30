"""The Playwright child of `ui_proof capture`: one state of one URL at named viewports x themes.

`capture_run.py` spawns this file by path with the target repo's own `.venv` interpreter, because
Playwright lives in the fleet apps' venvs and this repo's venv stays stdlib-only. It loads the URL
(a loopback instance of the app), optionally clicks the given selectors to reach the state, and
writes `<out>/<viewport>-<theme>.png` for each leg plus `<out>/shots.json`.

    <target-venv-python> shoot.py --url URL --out DIR --viewport phone=390x844 --viewport desktop=1280x900
        --theme light --theme dark [--click SELECTOR]... [--wait-ms N] [--theme-storage-key KEY]

Exit 0 when every leg was written, 1 when a leg failed (recorded in `shots.json`), 3 when the
browser could not start.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

SETTLE_MS = 2000
CLICK_SETTLE_MS = 800
TIMEOUT_MS = 15000


def _size(text: str) -> Dict[str, int]:
    width, _, height = text.partition("x")
    return {"width": int(width), "height": int(height)}


def _leg(browser, args: argparse.Namespace, label: str, size: Dict[str, int], theme: str) -> Dict[str, object]:
    """Load the URL in a fresh context for one viewport x theme, reach the state, screenshot it."""
    png = Path(args.out) / f"{label}-{theme}.png"
    ctx = browser.new_context(viewport=size, color_scheme=theme, ignore_https_errors=True)
    try:
        if args.theme_storage_key:
            ctx.add_init_script(f"try{{localStorage.setItem({json.dumps(args.theme_storage_key)},{json.dumps(theme)})}}catch(e){{}}")
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        page.goto(args.url, wait_until="domcontentloaded")
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        page.wait_for_timeout(args.wait_ms)
        for selector in args.click:
            page.click(selector)
            page.wait_for_timeout(CLICK_SETTLE_MS)
        page.screenshot(path=str(png))
    finally:
        ctx.close()
    return {"viewport": label, "theme": theme, "screenshot": str(png)}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--viewport", action="append", required=True, help="'<label>=<W>x<H>'")
    ap.add_argument("--theme", action="append", required=True)
    ap.add_argument("--click", action="append", default=[])
    ap.add_argument("--wait-ms", type=int, default=SETTLE_MS)
    ap.add_argument("--theme-storage-key", default="")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    shots: List[Dict[str, object]] = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — a missing engine is its own exit code, not a crash
            print(f"BROWSER_FAILED: {str(exc)[:300]}", file=sys.stderr)
            return 3
        try:
            for spec in args.viewport:
                label, _, dims = spec.partition("=")
                for theme in args.theme:
                    try:
                        shots.append(_leg(browser, args, label, _size(dims), theme))
                    except Exception as exc:  # noqa: BLE001 — one failed leg is reported, the rest still run
                        shots.append({"viewport": label, "theme": theme, "error": str(exc)[:300]})
        finally:
            browser.close()
    (Path(args.out) / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
    return 1 if any("error" in s for s in shots) else 0


if __name__ == "__main__":
    sys.exit(main())
