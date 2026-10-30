"""Fixtures shared by the design_lint contract suites in `tests/design_lint/`.

`run_contracts` runs `design_lint.contracts` over synthetic CSS/markup written to a throwaway tree;
`GOOD_CSS` is the passing baseline most contract checks mutate. The app-icon, pre-modal and
hit-target fixtures are read by more than one contract suite, so they live here once.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

import design_lint as dl  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402


def run_contracts(css: str, markup: str = "", spec_light: dict | None = None,
                  html_name: str = "i.html", spec_dark: dict | None = None,
                  js: str = "", files: dict[str, str] | None = None) -> dict:
    t = Path(throwaway_dir("dl-con-"))
    try:
        (t / "s.css").write_text(css, encoding="utf-8")
        html: list[Path] = []
        if markup:
            (t / html_name).write_text(markup, encoding="utf-8")
            html = [t / html_name]
        js_files: list[Path] = []
        if js:
            (t / "s.js").write_text(js, encoding="utf-8")
            js_files = [t / "s.js"]
        for name, body in (files or {}).items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        out = dl.contracts(t, [t / "s.css"], html, js_files,
                           spec_light if spec_light is not None else {"icons.size.inline": "16px"},
                           spec_dark)
        return {c["id"]: c for c in out}
    finally:
        shutil.rmtree(t, ignore_errors=True)


GOOD_CSS = """
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { transition-duration: 0.01ms !important; } }
.app { max-width: 772px; margin: 0 auto; }
.toggle.on { background: var(--accent-fill); }
body:has(dialog[open]) .tabs { visibility: hidden; }
.tabs { height: 100dvh; padding-bottom: env(safe-area-inset-bottom); }
@media (display-mode: standalone) {
  .tabs { top: calc(100vh - 82px); top: calc(100lvh - 82px); bottom: auto; }
}
"""

# The superseded nav architecture: the app shell is the scroller in the installed app.
OLD_SHELL_CSS = """
@media (display-mode: standalone) {
  .app { position: fixed; top: 0; height: 100vh; height: 100lvh; overflow-y: auto; }
}
"""

HIT_SPEC_940 = {"icons.size.inline": "16px", "components.hit-target.min": "44px"}

APP_ICON_SPEC = {
    "icons.size.inline": "16px",
    "app-icon.generator": "brand_gen",
    "app-icon.apple": "icon-180.png",
    "app-icon.regular-small": "icon-192.png",
    "app-icon.regular-large": "icon-512.png",
    "app-icon.maskable": "icon-512-maskable.png",
    "app-icon.favicon": "favicon.ico",
}
APP_ICON_INDEX = """
<head>
  <link rel="manifest" href="/static/manifest.webmanifest">
  <link rel="apple-touch-icon" href="/static/icon-180.png">
  <link rel="icon" href="/static/favicon.ico" sizes="any">
</head><body></body>
"""
APP_ICON_MANIFEST = """
{"icons": [
  {"src": "/static/icon-192.png", "sizes": "192x192", "purpose": "any"},
  {"src": "/static/icon-512.png", "sizes": "512x512", "purpose": "any"},
  {"src": "/static/icon-512-maskable.png", "sizes": "512x512", "purpose": "maskable"}
]}
"""
APP_ICON_FILES = {
    "static/manifest.webmanifest": APP_ICON_MANIFEST,
    "static/icon-180.png": "asset",
    "static/icon-192.png": "asset",
    "static/icon-512.png": "asset",
    "static/icon-512-maskable.png": "asset",
    "static/favicon.ico": "asset",
    "scripts/gen_icons.py": "from brand_gen import render_set\nrender_set(master='house.svg')\n",
}

legacy_files = dict(APP_ICON_FILES)
legacy_files["scripts/gen_icons.py"] = "def draw_mic(): pass\n"

PRE_MODAL_HTML = """
<dialog id="jobDialog" class="rename-dialog">
  <form id="jobForm">
    <h2 id="jobDialogTitle">Add job</h2>
    <label class="stacked"><span>Name</span><input type="text" required></label>
    <fieldset class="job-chain-fieldset"><legend>Run on success</legend></fieldset>
    <div class="row dialog-actions">
      <button type="button" id="jobCancel" class="ghost-btn">Cancel</button>
      <button type="button" id="jobSaveAnyway" class="big-btn warn" hidden>Save anyway</button>
      <button type="submit" id="jobSaveBtn" class="big-btn">Add and verify</button>
    </div>
  </form>
</dialog>
"""
PRE_MODAL_CSS = """
.settings-card .stacked { display: flex; flex-direction: column; }
.job-params-fieldset { border: 1px solid var(--line); padding: 10px 12px; }
.rename-dialog { padding: 18px; max-width: 560px; }
.dialog-actions { justify-content: flex-end; gap: 8px; }
"""
