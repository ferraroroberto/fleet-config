"""Run the measurement script once over a page and print its `inventory` section (fleet-config#1497).

Executed by a sibling checkout's Playwright interpreter, never this repo's stdlib venv:

    <python> inventory_driver.py <url> <params.json>

Before measuring it prepends one hand-made button with a class no vendored component owns
(`#bespokeButton.my-own-btn`), so the page carries a known bespoke control beside its vendored ones.
Prints the section as sorted-key JSON on stdout, so two runs over one page compare byte for byte.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib" / "design_review"))
import measure  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ADD_BESPOKE_JS = """() => { const b = document.createElement('button'); b.type = 'button'; b.id = 'bespokeButton';
  b.className = 'my-own-btn'; b.textContent = 'Bespoke 12'; document.body.prepend(b); }"""


def main() -> int:
    url, params_path = sys.argv[1], sys.argv[2]
    params = json.loads(Path(params_path).read_text(encoding="utf-8"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle")
            page.evaluate(ADD_BESPOKE_JS)
            metrics = page.evaluate(measure.build_script(None), params)
        finally:
            browser.close()
    sys.stdout.write(json.dumps(metrics.get("inventory"), sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
