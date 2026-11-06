"""Replay a full-page capture, then measure, on a touch leg and print what the measure saw (fleet-config#1895).

Executed by a sibling checkout's Playwright interpreter, never this repo's stdlib venv:

    <python> touch_drop_driver.py <url>

A Chromium full-page screenshot drops the Pixel 7 descriptor's touch emulation. The driver prints, as sorted-key
JSON, the pointer after that capture (`dropped`), what `walk.read_metrics` measured when the restorer is the walk's
own (`restored`), and what it did when the restorer cannot bring touch back (`unrestorable`).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib" / "design_review"))
import walk  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

POINTER_JS = "() => matchMedia('(pointer: coarse)').matches"
LABEL_COLOR_JS = "() => getComputedStyle(document.getElementById('label')).color"


def main() -> int:
    url = sys.argv[1]
    out: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx_args = dict(pw.devices["Pixel 7"])
            ctx = browser.new_context(**ctx_args)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            retouch = walk.touch_restorer(ctx, page, "chromium", ctx_args)
            with tempfile.TemporaryDirectory() as tmp:
                page.screenshot(path=str(Path(tmp) / "full.png"), full_page=True)
            out["dropped"] = page.evaluate(POINTER_JS)
            out["label"] = walk.read_metrics(page, LABEL_COLOR_JS, {}, retouch)
            out["restored"] = page.evaluate(POINTER_JS)
            with tempfile.TemporaryDirectory() as tmp:
                page.screenshot(path=str(Path(tmp) / "full.png"), full_page=True)
            try:
                walk.read_metrics(page, POINTER_JS, {}, lambda: None)
                out["unrestorable"] = "measured"
            except walk.TouchLost as exc:
                out["unrestorable"] = walk.classify_error(exc)
        finally:
            browser.close()
    sys.stdout.write(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
