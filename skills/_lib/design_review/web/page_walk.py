"""The web-page walk: load each page at 390, 800 and 1440 px against the static preview, record raw facts.

Run as a script under the **target repo's** interpreter (where Playwright
lives), by `measure_web.py`:

    <target python> page_walk.py --url http://127.0.0.1:<port> --pages /,/about
        --devices phone,tablet,desktop --params <params.json> --out <run_dir>

Per page and viewport it loads the page, notes the images that load eagerly
below the first screen, scrolls through once so lazy images load, runs
`MEASURE_JS`, and takes a full-page screenshot. Then the interactive checks:
the phone menu hit test (390), one click on each carousel's next arrow, the
sticky-media scan and the keyboard focus walk (1440). Finally one cold load
per page on the phone profile under Lighthouse's mobile throttling, for the
Web Vitals and the transferred bytes.

Writes `<run_dir>/raw.json`; verdicts are `checks.py`'s. Read-only: it never
fills a field or submits anything, and clicks only the menu button and the
carousel arrows. Chromium for every profile (the CDP session is Chromium's).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import page_js  # noqa: E402
from playwright.sync_api import Page, sync_playwright  # noqa: E402

DEVICES: Dict[str, Dict[str, Any]] = {
    "phone": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
    "tablet": {"viewport": {"width": 800, "height": 1000}, "device_scale_factor": 1, "is_mobile": False, "has_touch": True},
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
}
# Lighthouse's mobile profile with DevTools throttling ("slow 4G" at 1.6 Mbps / 150 ms RTT, 4x CPU),
# with Lighthouse's request-level adjustments: latency x3.75, throughput x0.9.
THROTTLE = {"offline": False, "latency": 562.5, "downloadThroughput": 1474.56 * 1024 / 8, "uploadThroughput": 675 * 1024 / 8}
CPU_SLOWDOWN = 4
NAV_TIMEOUT_MS = 60000
FOCUS_STEPS = 60
STICKY_SAMPLES = 150


def slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"


def scroll_through(page: Page) -> None:
    """Scroll to the bottom in steps so lazy images load, then back to the top."""
    y, height = 0, page.evaluate("document.documentElement.scrollHeight")
    while y < height:
        page.evaluate(f"window.scrollTo(0, {y})")
        time.sleep(0.15)
        y += 600
        height = page.evaluate("document.documentElement.scrollHeight")
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.6)


def guarded(fn, *args: Any) -> Any:
    """Run one check; its failure is recorded as `{"error": ...}` for that check alone."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - one broken check must not lose the page
        return {"error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"}


def menu_check(page: Page) -> Dict[str, Any]:
    """Open the phone menu and hit-test each link at its centre; with no menu button, hit-test the visible nav."""
    toggle = page.locator("header button[aria-controls][aria-expanded], .menu-toggle")
    visible = [toggle.nth(i) for i in range(toggle.count()) if toggle.nth(i).is_visible()]
    if not visible:
        return {"toggle": False, **page.evaluate(page_js.MENU_HIT_JS, ".site-nav, header nav")}
    button = visible[0]
    controls = button.get_attribute("aria-controls")
    button.click()
    time.sleep(0.6)
    result = page.evaluate(page_js.MENU_HIT_JS, f"#{controls}" if controls else ".site-nav, header nav")
    expanded = button.get_attribute("aria-expanded")
    page.keyboard.press("Escape")
    time.sleep(0.3)
    if page.locator("html.menu-open").count():
        button.click()
    return {"toggle": True, "expanded": expanded, **result}


def carousel_steps(page: Page, carousels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Click each scrolling carousel's next arrow once: how far did it move, against one card plus the gap?"""
    out: List[Dict[str, Any]] = []
    for c in carousels:
        if not c.get("scrolls") or c.get("nav_centre") is None:
            continue
        i = int(c["index"])
        button = page.evaluate_handle(
            """(i) => { const t = [...document.querySelectorAll('[data-carousel], .carousel')].filter(e => e.getBoundingClientRect().width > 0)[i];
                        const nav = t && t.nextElementSibling; return nav ? nav.querySelectorAll('button')[1] || null : null; }""", i).as_element()
        if button is None:
            out.append({"index": i, "error": "no next button"})
            continue
        before = page.evaluate(page_js.CAROUSEL_JS, i)
        button.click()
        time.sleep(0.8)
        after = page.evaluate(page_js.CAROUSEL_JS, i)
        out.append({"index": i, "step": after["scroll_left"] - before["scroll_left"],
                    "expected": before["card_w"] + before["gap"]})
        page.evaluate("""(i) => { const t = [...document.querySelectorAll('[data-carousel], .carousel')]
                            .filter(e => e.getBoundingClientRect().width > 0)[i]; if (t) t.scrollLeft = 0; }""", i)
    return out


def sticky_scan(page: Page) -> Dict[str, Any]:
    """Scroll the page through the viewport; the largest blank stretch a sticky picture leaves beside its text."""
    first = page.evaluate(page_js.STICKY_JS)
    if not first["sticky"]:
        return {"sticky": 0, "blank_max": 0}
    height = page.evaluate("document.documentElement.scrollHeight - innerHeight")
    step = max(100, height // STICKY_SAMPLES)
    worst = first["blank"]
    for y in range(0, max(0, height) + 1, step):
        page.evaluate(f"window.scrollTo(0, {y})")
        time.sleep(0.05)
        worst = max(worst, page.evaluate(page_js.STICKY_JS)["blank"])
    page.evaluate("window.scrollTo(0, 0)")
    return {"sticky": first["sticky"], "blank_max": worst}


def focus_walk(page: Page) -> Dict[str, Any]:
    """Tab through the page: which focused elements draw no ring, and which hide under the sticky header."""
    page.evaluate("window.scrollTo(0, 0); document.activeElement && document.activeElement.blur()")
    seen: List[str] = []
    missing: List[str] = []
    obscured: List[str] = []
    for _ in range(FOCUS_STEPS):
        page.keyboard.press("Tab")
        time.sleep(0.05)
        f = page.evaluate(page_js.FOCUS_JS)
        if not f:
            continue
        if seen and f["id"] == seen[0]:
            break
        seen.append(f["id"])
        if not f["ring"]:
            missing.append(f["id"])
        if f["obscured"]:
            obscured.append(f["id"])
    return {"focused": len(seen), "ring_missing": missing, "obscured": obscured}


def measure_page(page: Page, url: str, device: str, params: Dict[str, Any], shot: Path) -> Dict[str, Any]:
    response = page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
    time.sleep(0.5)
    rec: Dict[str, Any] = {"status": response.status if response else None, "final_url": page.url}
    rec["fold_eager"] = guarded(page.evaluate, page_js.FOLD_JS)
    scroll_through(page)
    rec["measure"] = guarded(page.evaluate, page_js.MEASURE_JS, params)
    shot.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shot), full_page=True, scale="css")
    rec["screenshot"] = str(shot)
    carousels = rec["measure"].get("page", {}).get("carousels", []) if "error" not in rec["measure"] else []
    if device == "phone":
        rec["menu"] = guarded(menu_check, page)
    rec["carousel_steps"] = guarded(carousel_steps, page, carousels)
    if device == "desktop":
        rec["sticky"] = guarded(sticky_scan, page)
    rec["focus"] = guarded(focus_walk, page)
    return rec


def cold_load(browser: Any, url: str) -> Dict[str, Any]:
    """One cold phone-profile load under throttling: Web Vitals, bytes and requests, fonts, render-blocking hosts."""
    ctx = browser.new_context(**DEVICES["phone"], reduced_motion="reduce")
    try:
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        responses: Dict[str, Dict[str, Any]] = {}
        cdp.on("Network.responseReceived", lambda e: responses.__setitem__(e["requestId"], {
            "url": e["response"]["url"], "type": e.get("type"), "mime": e["response"].get("mimeType"), "bytes": 0}))
        cdp.on("Network.loadingFinished", lambda e: responses.get(e["requestId"], {}).__setitem__("bytes", e.get("encodedDataLength", 0)))
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.send("Network.emulateNetworkConditions", THROTTLE)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
        page.add_init_script(page_js.PERF_INIT_JS)
        page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS * 2)
        try:
            page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - a page that never idles is still measured as it stands
            pass
        time.sleep(1.5)
        vitals = page.evaluate(page_js.PERF_READ_JS)
        reqs = list(responses.values())
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        fonts = [r for r in reqs if r["type"] == "Font"]
        return {**vitals,
                "bytes": int(sum(r["bytes"] for r in reqs)), "requests": len(reqs),
                "html_bytes": int(sum(r["bytes"] for r in reqs if r["type"] == "Document")),
                "font_files": [{"url": r["url"].split("/")[-1], "bytes": int(r["bytes"])} for r in fonts],
                "first_party_js_bytes": int(sum(r["bytes"] for r in reqs if r["type"] == "Script" and r["url"].startswith(origin + "/")))}
    finally:
        ctx.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="web-page walk against a static preview")
    ap.add_argument("--url", required=True)
    ap.add_argument("--pages", required=True, help="comma-separated paths")
    ap.add_argument("--devices", default="phone,tablet,desktop")
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-perf", action="store_true")
    args = ap.parse_args(argv)
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    out = Path(args.out)
    base = args.url.rstrip("/")
    pages = [p for p in args.pages.split(",") if p]
    devices = [d for d in args.devices.split(",") if d in DEVICES]
    raw: Dict[str, Any] = {"base_url": base, "devices": devices, "pages": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for path in pages:
                entry: Dict[str, Any] = {"devices": {}}
                for device in devices:
                    ctx = browser.new_context(**DEVICES[device], reduced_motion="reduce")
                    try:
                        page = ctx.new_page()
                        entry["devices"][device] = guarded(measure_page, page, base + path, device, params,
                                                           out / "screens" / f"{device}-{slug(path)}.png")
                    finally:
                        ctx.close()
                    print(f"walked {device} {path}", file=sys.stderr, flush=True)
                if not args.no_perf:
                    entry["perf"] = guarded(cold_load, browser, base + path)
                    print(f"cold load {path}", file=sys.stderr, flush=True)
                raw["pages"][path] = entry
        finally:
            browser.close()
    (out / "raw.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
