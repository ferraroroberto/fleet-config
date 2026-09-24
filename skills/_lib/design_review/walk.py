"""The read-only browser walk — a child process of the *target's* interpreter.

`capture.py` spawns this file by path with `<target>/.venv/Scripts/python.exe`
(POSIX `bin/python`), the same interpreter `browser_verify.py` resolves for
the visual gate: Playwright lives in the fleet apps' venvs, and this repo's
venv stays stdlib-only. Everything Playwright-shaped is confined here; the
measurement script comes from the sibling `measure.py` (stdlib, imported by
path), and the effective-rect JS from project-scaffolding's
`tests/e2e/_geometry.py`, loaded from the scaffold checkout at run time.

What it does, per device x theme, and nothing else:

  1. `goto` the base URL (loopback, certificate errors ignored);
  2. click each primary tab (`[role=tablist] [role=tab]`, first tablist),
     screenshot, set every `details.open` in the visible pane, full-page
     screenshot, run the measurement script;
  3. `showModal()` each `dialog[id]`, screenshot, set every `details.open`
     in the dialog, measure, `close()`;
  4. any `[design.review].extra_steps` (one `click` on a declared selector
     after a fresh `goto`, never on a `no_go` selector), screenshot, set
     every `details.open` in the open dialog or else the page, measure.

Disclosures are opened on every screen kind so folded content is measured
open (fleet-config#995). One that stays closed -- an exclusive accordion
closes its siblings -- stays excluded by `measure.py`'s `checkVisibility()`
(#998). A screen that opened any also gets a full screenshot of that state.

No submit, no fill, no session attach, no navigation away from the base URL.

Output: `<out>/screens.json` — a list of screen records
`{id, device, theme, view, kind, status, reason, error, screenshot,
screenshot_full, metrics}` — plus `<out>/walk.json` with engine versions and
timings. A screen that fails to open is recorded with `status: "error"` and a
distinct `reason` (`TIMEOUT`, `NOT_LISTENING`, `TAB_FAILED`, `DIALOG_FAILED`,
`BROWSER_FAILED`); its rules evaluate to `unmeasured`, never pass.

Usage (normally via capture.py):

    <target-venv-python> walk.py --url URL --out DIR --devices iphone,desktop
        [--scaffold E:/automation/project-scaffolding] [--params params.json]
        [--review review.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure  # noqa: E402
import plan  # noqa: E402

log = logging.getLogger("design_review.walk")

SETTLE_MS = 2500       # after goto — the prototype's numbers were taken with these waits
TAB_SETTLE_MS = 2200
DETAILS_SETTLE_MS = 1200
DIALOG_SETTLE_MS = 500
STEP_SETTLE_MS = 1200
DEFAULT_TIMEOUT_MS = 15000

_TABS_JS = """
sel => { const list = document.querySelector('[role=tablist]'); const root = list || document;
  return [...root.querySelectorAll(sel)].map((t, i) => ({
    index: i,
    id: t.dataset.tab || (t.getAttribute('aria-controls') || '').replace(/^pane[-_]?/i, '') || t.id || t.textContent.trim().toLowerCase()
  })); }
"""
_OPEN_DETAILS_JS = """
() => { const pane = document.querySelector('[role=tabpanel]:not([hidden])')
  || document.querySelector('section.pane:not([hidden])') || document.querySelector('main') || document.body;
  let n = 0; pane.querySelectorAll('details').forEach(d => { if (!d.open) { d.open = true; n++; } }); return n; }
"""
# Dialogs and extra steps: the open dialog when there is one, else the page (#995).
_OPEN_SCOPE_DETAILS_JS = """
() => { const root = document.querySelector('dialog[open]') || document.body;
  let n = 0; root.querySelectorAll('details').forEach(d => { if (!d.open) { d.open = true; n++; } }); return n; }
"""
_DIALOG_IDS_JS = "() => [...document.querySelectorAll('dialog[id]')].map(d => d.id)"


def load_geometry_js(scaffold_root: Optional[str]) -> Optional[str]:
    """`_EFFECTIVE_RECT_JS` from project-scaffolding's `tests/e2e/_geometry.py`.

    Imported from the scaffold checkout rather than copied: the issue is
    explicit that a second implementation must not exist. The module imports
    `playwright.sync_api` at top level, which is why this runs here (target
    venv) and not in `measure.py`. Returns `None` when the file is absent or
    the constant is gone (a rename upstream surfaces as `GEOMETRY_MISSING`).
    The public `EFFECTIVE_RECT_JS` name is preferred; the underscored one is
    what the scaffold ships today (project-scaffolding#269 asks for the alias).
    """
    if not scaffold_root:
        return None
    path = Path(scaffold_root) / "tests" / "e2e" / "_geometry.py"
    if not path.is_file():
        log.warning("GEOMETRY_MISSING: %s not found", path)
        return None
    spec = importlib.util.spec_from_file_location("_fleet_geometry", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: its `@dataclass`es resolve `sys.modules[__module__]`
    # at class-creation time, which is None for an unregistered module.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — a broken scaffold file is a missing geometry, reported
        log.warning("GEOMETRY_MISSING: %s failed to import: %s", path, exc)
        return None
    js = getattr(module, "EFFECTIVE_RECT_JS", None) or getattr(module, "_EFFECTIVE_RECT_JS", None)
    return str(js) if js else None


def classify_error(exc: BaseException) -> str:
    """Map a Playwright failure to one of the distinct walk reasons."""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "timeout" in lowered:
        return "TIMEOUT"
    if any(k in lowered for k in ("err_connection_refused", "could not connect", "connection refused", "econnrefused")):
        return "NOT_LISTENING"
    return "RENDER_FAILED"


def _record(**fields: object) -> Dict[str, object]:
    base: Dict[str, object] = {
        "id": None, "device": None, "theme": None, "view": None, "kind": None,
        "status": "ok", "reason": None, "error": None,
        "screenshot": None, "screenshot_full": None, "metrics": None,
    }
    base.update(fields)
    return base


def _shot(page, shots: Path, name: str, full: bool = False) -> str:
    path = shots / f"{name}{'-full' if full else ''}.png"
    page.screenshot(path=str(path), full_page=full)
    return path.name


def _open_scope_details(page, shots: Path, sid: str) -> Optional[str]:
    """Open every closed `<details>` in the dialog or page; the full screenshot of that state, if any opened."""
    opened = int(page.evaluate(_OPEN_SCOPE_DETAILS_JS) or 0)
    if not opened:
        return None
    page.wait_for_timeout(DETAILS_SETTLE_MS)
    log.info("opened %d details on %s", opened, sid)
    return _shot(page, shots, sid, full=True)


def walk_context(pw, device: str, theme: str, args: argparse.Namespace, script: str,
                 params: dict, review: dict, shots: Path) -> List[Dict[str, object]]:
    """Every screen for one device x theme leg."""
    profile = plan.DEVICES[device]
    screens: List[Dict[str, object]] = []
    try:
        browser = getattr(pw, profile["engine"]).launch()
    except Exception as exc:  # noqa: BLE001 — a missing engine is a per-device fact, not a crash
        screens.append(_record(id=plan.screen_id(device, theme, "root"), device=device, theme=theme,
                               view="root", kind="tab", status="error", reason="BROWSER_FAILED",
                               error=str(exc)[:300]))
        return screens
    ctx_args: dict = dict(pw.devices[profile["descriptor"]]) if "descriptor" in profile else {"viewport": dict(profile["viewport"])}
    ctx = browser.new_context(ignore_https_errors=True, color_scheme=theme, **ctx_args)
    key = review.get("theme_storage_key")
    if key:
        ctx.add_init_script(f"try{{localStorage.setItem({json.dumps(str(key))},{json.dumps(theme)})}}catch(e){{}}")
    page = ctx.new_page()
    page.set_default_timeout(args.timeout_ms)
    tab_selector = str(review.get("tab_selector") or "[role=tab]")
    no_go = [str(s) for s in review.get("no_go", [])]

    def stamp_theme() -> None:
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)

    def open_base() -> None:
        page.goto(args.url, wait_until="domcontentloaded")
        stamp_theme()
        page.wait_for_timeout(SETTLE_MS)

    try:
        open_base()
    except Exception as exc:  # noqa: BLE001
        screens.append(_record(id=plan.screen_id(device, theme, "root"), device=device, theme=theme,
                               view="root", kind="tab", status="error", reason=classify_error(exc),
                               error=str(exc)[:300]))
        ctx.close()
        browser.close()
        return screens

    tabs = page.evaluate(_TABS_JS, tab_selector) or [{"index": None, "id": "root"}]
    tablist = page.locator("[role=tablist]").first if page.locator("[role=tablist]").count() else page
    for tab in tabs:
        view = str(tab["id"] or f"tab{tab['index']}")
        sid = plan.screen_id(device, theme, view)
        try:
            if tab["index"] is not None:
                tablist.locator(tab_selector).nth(int(tab["index"])).click()
                page.wait_for_timeout(TAB_SETTLE_MS)
            shot = _shot(page, shots, sid)
            page.evaluate(_OPEN_DETAILS_JS)
            page.wait_for_timeout(DETAILS_SETTLE_MS)
            full = _shot(page, shots, sid, full=True)
            metrics = page.evaluate(script, params)
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="tab",
                                   screenshot=shot, screenshot_full=full, metrics=metrics))
            log.info("ok %s", sid)
        except Exception as exc:  # noqa: BLE001 — the walk must continue past one broken tab
            reason = classify_error(exc)
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="tab",
                                   status="error", reason="TAB_FAILED" if reason == "RENDER_FAILED" else reason,
                                   error=str(exc)[:300]))
            log.warning("FAIL %s: %s", sid, str(exc)[:200])

    for did in page.evaluate(_DIALOG_IDS_JS) or []:
        view = f"dialog-{did}"
        sid = plan.screen_id(device, theme, view)
        try:
            page.evaluate("id => document.getElementById(id).showModal()", did)
            page.wait_for_timeout(DIALOG_SETTLE_MS)
            shot = _shot(page, shots, sid)
            full = _open_scope_details(page, shots, sid)
            metrics = page.evaluate(script, params)
            page.evaluate("id => document.getElementById(id).close()", did)
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="dialog",
                                   screenshot=shot, screenshot_full=full, metrics=metrics))
            log.info("ok %s", sid)
        except Exception as exc:  # noqa: BLE001
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="dialog",
                                   status="error", reason="DIALOG_FAILED", error=str(exc)[:300]))
            log.warning("FAIL %s: %s", sid, str(exc)[:200])

    for step in review.get("extra_steps", []) or []:
        if not isinstance(step, dict) or not step.get("click") or not step.get("id"):
            continue
        view = f"{step.get('tab', 'root')}-{step['id']}"
        sid = plan.screen_id(device, theme, view)
        if step["click"] in no_go:
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="step",
                                   status="error", reason="NO_GO", error=f"{step['click']} is declared no_go"))
            continue
        try:
            open_base()
            tab_id = step.get("tab")
            if tab_id:
                idx = next((t["index"] for t in tabs if t["id"] == tab_id), None)
                if idx is not None:
                    tablist.locator(tab_selector).nth(int(idx)).click()
                    page.wait_for_timeout(TAB_SETTLE_MS)
            page.locator(str(step["click"])).first.click()
            page.wait_for_timeout(STEP_SETTLE_MS)
            shot = _shot(page, shots, sid)
            full = _open_scope_details(page, shots, sid)
            metrics = page.evaluate(script, params)
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="step",
                                   screenshot=shot, screenshot_full=full, metrics=metrics))
            log.info("ok %s", sid)
        except Exception as exc:  # noqa: BLE001
            screens.append(_record(id=sid, device=device, theme=theme, view=view, kind="step",
                                   status="error", reason=classify_error(exc), error=str(exc)[:300]))
            log.warning("FAIL %s: %s", sid, str(exc)[:200])

    ctx.close()
    browser.close()
    return screens


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="design-review read-only browser walk (target-venv child)")
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True, help="run directory (screens.json, walk.json, shots/)")
    ap.add_argument("--devices", default=",".join(plan.DEFAULT_DEVICES))
    ap.add_argument("--scaffold", default=None, help="project-scaffolding root for tests/e2e/_geometry.py")
    ap.add_argument("--params", default=None, help="JSON file with the measurement params")
    ap.add_argument("--review", default=None, help="JSON file with the target's [design.review] block")
    ap.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    out = Path(args.out)
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    params = json.loads(Path(args.params).read_text(encoding="utf-8")) if args.params else measure.default_params()
    review = json.loads(Path(args.review).read_text(encoding="utf-8")) if args.review else {}
    geometry = load_geometry_js(args.scaffold)
    script = measure.build_script(geometry)
    devices = plan.device_list([d for d in args.devices.split(",") if d])

    from playwright.sync_api import sync_playwright  # noqa: E402 — the one Playwright import, target venv only

    screens: List[Dict[str, object]] = []
    info: Dict[str, object] = {"engines": {}, "geometry": "loaded" if geometry else "GEOMETRY_MISSING",
                               "started": time.time()}
    try:
        with sync_playwright() as pw:
            for device in devices:
                for theme in plan.THEMES:
                    t0 = time.time()
                    screens.extend(walk_context(pw, device, theme, args, script, params, review, shots))
                    info.setdefault("legs", {})[f"{device}-{theme}"] = round(time.time() - t0, 1)  # type: ignore[union-attr]
    finally:
        info["finished"] = time.time()
        (out / "screens.json").write_text(json.dumps(screens, indent=1), encoding="utf-8")
        (out / "walk.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
