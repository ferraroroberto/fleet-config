"""Unit tests for the pure logic in skills/_lib/design_lint.py (fleet-config#277).

No live repos or gh — synthetic spec/CSS/JS strings and temp trees exercise the
frontmatter parser (inline maps, nesting, {token} refs), the custom-prop
extractor (theme split, comment/P3 immunity — the real home-automation bug),
the alias mapper (match/drift/missing/unmapped), the adoption-ratio counter
(exemptions), the contract checks (the green-switch decision, focus ring,
checkbox detection, the nav standalone-shell architecture — fleet-config#282),
the vendored byte-compare, and the sibling duplicate detector.

Run: `C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_design_lint.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import design_lint as dl  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- spec frontmatter parsing ----

SPEC = """---
name: Fleet
colors:
  canvas:        "#ffffff"   # page background
  accent:        "#0969da"
  success:       "#1a7f37"
typography:
  body:       { fontFamily: "system-ui, sans-serif", fontSize: 1rem,    fontWeight: 400, lineHeight: 1.5 }
rounded:
  lg:   16px
components:
  switch:         { trackOn: "{colors.success}", width: 44px }
icons:
  size:
    inline:  16px
    title:   18px
---

## Prose

Ignored.
"""

spec = dl.parse_spec(SPEC)
check(spec.get("colors.canvas") == "#ffffff", "flat leaf parsed + comment stripped")
check(spec.get("typography.body.fontSize") == "1rem",
      "inline map flattened despite quoted comma in fontFamily")
check(spec.get("typography.body.fontFamily") == "system-ui, sans-serif",
      "quoted comma value survives the inline-map split")
check(spec.get("rounded.lg") == "16px", "scalar group leaf parsed")
check(spec.get("components.switch.trackOn") == "#1a7f37",
      "{colors.success} token ref resolved to the literal")
check(spec.get("icons.size.inline") == "16px", "two-level nesting (icons.size)")
check("name" in spec and spec["name"] == "Fleet", "top-level scalar kept")


# ---- CSS custom-prop extraction ----

CSS = """/* tokens — see @media (color-gamut: p3) below for the P3 twins. */
:root {
  --bg: #ffffff;   /* canvas */
  --accent: #0969da;
}
[data-theme="dark"] {
  --bg: #0d1117;
  --accent: #2f81f7;
}
@media (color-gamut: p3) {
  :root { --accent: oklch(0.52 0.18 256); }
}
@media (prefers-color-scheme: dark) {
  :root { --scheme-var: #111111; }
}
"""

props = dl.parse_custom_props(CSS, "styles.css")
check(props["light"].get("--bg", ("", 0))[0] == "#ffffff",
      "light :root parsed despite @media mention inside a comment (the real bug)")
check(props["dark"].get("--bg", ("", 0))[0] == "#0d1117", "dark theme block parsed")
check(props["light"].get("--accent", ("", 0))[0] == "#0969da",
      "P3 media twin did NOT clobber the sRGB light value")
check(props["dark"].get("--scheme-var", ("", 0))[0] == "#111111",
      "prefers-color-scheme dark media lands in dark")


# ---- value normalization ----

check(dl.normalize_value("#FFF") == "#ffffff", "#fff shorthand expands")
check(dl.normalize_value("  1REM ") == "1rem", "case + whitespace normalize")


# ---- alias mapping: match / drift / missing / unmapped ----

app = {
    "light": {"--bg": ("#ffffff", 2), "--accent": ("#123456", 3), "--house": ("#000", 4)},
    "dark": {"--bg": ("#0d1117", 7)},
}
spec_l = {"colors.canvas": "#ffffff", "colors.accent": "#0969da", "colors.fg": "#1f2328"}
spec_d = {"colors.canvas": "#0d1117", "colors.accent": "#2f81f7", "colors.fg": "#e6edf3"}
res = dl.map_tokens(spec_l, spec_d, app, "styles.css")
check(any(m["role"] == "colors.canvas" and m["theme"] == "light" for m in res["matched"]),
      "canvas light matches via the --bg alias")
check(any(m["role"] == "colors.canvas" and m["theme"] == "dark" for m in res["matched"]),
      "canvas dark matches")
drifts = {(d["role"], d["theme"], d["kind"]) for d in res["drift"]}
check(("colors.accent", "light", "value-drift") in drifts,
      "accent light value drift detected")
check(("colors.accent", "dark", "missing-theme-value") in drifts,
      "accent has no dark value -> missing-theme-value")
check(any(m["role"] == "colors.fg" for m in res["missing"]),
      "fg has no candidate var -> missing")
check("--house" in res["unmapped"], "unclaimed app var surfaces as unmapped")
# structural tokens defined only in :root are NOT dark-drift (inheritance)
app2 = {"light": {"--radius": ("16px", 1)}, "dark": {}}
res2 = dl.map_tokens({"rounded.lg": "16px"}, {"rounded.lg": "16px"}, app2, "s.css")
check(not res2["drift"], "root-only structural token inherits into dark, no drift")


# ---- adoption ratios ----

ADOPT_CSS = """
.a { color: var(--ink); background: #fff; border-radius: var(--radius); }
.b { font-size: 1rem; padding: 8px; margin: 0; }
.c { font-size: var(--font-body); border-radius: 50%; gap: var(--gap); }
.d { background: transparent; color: currentColor; }
"""
tree = Path(tempfile.mkdtemp(prefix="dl-adopt-"))
try:
    (tree / "s.css").write_text(ADOPT_CSS, encoding="utf-8")
    fam = dl.adoption(tree, [tree / "s.css"])
    check(fam["color"]["total"] == 2 and fam["color"]["tokenized"] == 1,
          "color: var counts tokenized, #fff escapee, transparent/currentColor exempt")
    check(fam["font-size"]["total"] == 2 and fam["font-size"]["tokenized"] == 1,
          "font-size: one tokenized one literal")
    check(fam["radius"]["total"] == 1 and fam["radius"]["tokenized"] == 1,
          "radius: 50% exempt, var counted")
    check(fam["spacing"]["total"] == 2 and fam["spacing"]["tokenized"] == 1,
          "spacing: margin 0 exempt; 8px escapee + var(--gap) tokenized")
    esc = fam["font-size"]["escapees"]
    check(esc and esc[0]["value"] == "1rem" and esc[0]["line"] == 3,
          "escapee carries value + line")
finally:
    shutil.rmtree(tree, ignore_errors=True)


# ---- contract checks ----

def run_contracts(css: str, markup: str = "", spec_light: dict | None = None,
                  html_name: str = "i.html", spec_dark: dict | None = None) -> dict:
    t = Path(tempfile.mkdtemp(prefix="dl-con-"))
    try:
        (t / "s.css").write_text(css, encoding="utf-8")
        html: list[Path] = []
        if markup:
            (t / html_name).write_text(markup, encoding="utf-8")
            html = [t / html_name]
        out = dl.contracts(t, [t / "s.css"], html, [],
                           spec_light if spec_light is not None else {"icons.size.inline": "16px"},
                           spec_dark)
        return {c["id"]: c for c in out}
    finally:
        shutil.rmtree(t, ignore_errors=True)


GOOD_CSS = """
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { transition-duration: 0.01ms !important; } }
.app { max-width: 772px; margin: 0 auto; }
.toggle.on { background: var(--on); }
body:has(dialog[open]) .tabs { visibility: hidden; }
.tabs { height: 100dvh; padding-bottom: env(safe-area-inset-bottom); }
@media (display-mode: standalone) {
  .app { position: fixed; top: 0; height: 100vh; height: 100lvh; overflow-y: auto; }
}
"""
good = run_contracts(GOOD_CSS, "<dialog class=\"d\"></dialog>")
check(good["focus-visible-ring"]["status"] == "PASS", "tokenized focus ring PASS")
check(good["reduced-motion"]["status"] == "PASS", "reduced motion PASS")
check(good["desktop-measure"]["status"] == "PASS", "772px measure PASS")
check(good["switch-on-green"]["status"] == "PASS", "green on-track PASS")
check(good["no-native-checkbox"]["status"] == "PASS", "no checkboxes PASS")
check(good["native-dialog"]["status"] == "PASS", "native dialog PASS")
check(good["nav-contract"]["status"] == "PASS", "nav signals + shell PASS")
check("hand-carried" in good["nav-contract"]["detail"],
      "hand-carried nav with the full architecture passes on merit (#282)")

# signal-passing hand-carried nav WITHOUT the standalone shell — the exact
# app-launcher false-PASS that motivated fleet-config#282: must cap at WARN.
NO_SHELL_CSS = """
body:has(dialog[open]) .tabs { visibility: hidden; }
.tabs { height: 100dvh; padding-bottom: env(safe-area-inset-bottom); }
"""
noshell = run_contracts(NO_SHELL_CSS)
check(noshell["nav-contract"]["status"] == "WARN",
      "all grep signals but no standalone shell -> WARN, never PASS (#282)")
check("standalone fixed-inset scroller" in noshell["nav-contract"]["detail"],
      "no-shell WARN names the missing scroller + the vendored fix")

# vendored nav present but the app-side shell missing -> still WARN (the shell
# lives in the consuming app's CSS, so vendored presence alone can't PASS).
t = Path(tempfile.mkdtemp(prefix="dl-nav-"))
try:
    (t / "s.css").write_text(NO_SHELL_CSS, encoding="utf-8")
    vend = t / "app/webapp/static/_vendored/nav"
    vend.mkdir(parents=True)
    (vend / "nav-tabs.css").write_text("/* vendored */", encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(t, [t / "s.css"], [], [], {})}
    check(out["nav-contract"]["status"] == "WARN",
          "vendored nav without the app-side shell -> WARN (#282)")
finally:
    shutil.rmtree(t, ignore_errors=True)

# standalone_shell_present: the #303 markers must all sit inside a
# display-mode:standalone block; nesting inside another @media is fine.
NESTED = ("@media (pointer: coarse) and (max-width: 520px) {"
          " @media (display-mode: standalone) {"
          " .app { position: fixed; height: 100lvh; overflow-y: auto; } } }")
check(dl.standalone_shell_present(NESTED), "shell detected in nested @media")
check(not dl.standalone_shell_present(
    "@media (display-mode: standalone) { .app { position: fixed; height: 100lvh; } }"),
    "no overflow-y: auto -> not the scroller architecture")
check(not dl.standalone_shell_present(
    "@media (display-mode: standalone) { .app { position: fixed; overflow-y: auto; height: 100vh; } }"),
    "no 100lvh sizing -> not the #303 architecture")
check(not dl.standalone_shell_present(
    ".app { position: fixed; height: 100lvh; overflow-y: auto; }"),
    "markers outside a standalone media block don't count")

BAD_CSS = """
.toggle.on { background: var(--accent); }
.app { max-width: 1160px; }
"""
bad = run_contracts(BAD_CSS, "<input type=\"checkbox\"><div class=\"modal\"></div>")
check(bad["focus-visible-ring"]["status"] == "FAIL", "missing focus ring FAIL")
check(bad["reduced-motion"]["status"] == "FAIL", "missing reduced motion FAIL")
check(bad["desktop-measure"]["status"] == "FAIL", "1160px cap is not near-772 -> FAIL")
check(bad["switch-on-green"]["status"] == "FAIL",
      "accent on-track FAILs (the green decision is enforced)")
check(bad["no-native-checkbox"]["status"] == "FAIL", "native checkbox FAIL")
check(bad["native-dialog"]["status"] == "WARN", "hand-rolled modal WARN")
check(bad["nav-contract"]["status"] == "FAIL", "no nav signals FAIL")

near = run_contracts(".app { max-width: 780px; margin: 0 auto; }")
check(near["desktop-measure"]["status"] == "WARN", "near-772 cap WARNs with the value")
check(near["switch-on-green"]["status"] == "NA", "no switch -> NA")

# icon sizes come FROM the spec (spec-driven, not hardcoded)
icon_css = ".icon { width: 16px; } .big-icon { width: 40px; height: 40px; }"
strays = run_contracts(icon_css, spec_light={"icons.size.inline": "16px"})
check(strays["icon-sizes"]["status"] == "WARN" and "40px" in strays["icon-sizes"]["detail"],
      "off-step icon size surfaces as a stray")
ok_icons = run_contracts(".icon { width: 16px; }", spec_light={"icons.size.inline": "16px"})
check(ok_icons["icon-sizes"]["status"] == "PASS", "on-step icon sizes PASS")

# an icon-NAMED button box is not a glyph — no stray (the home-automation
# .header-icon-btn 40px false positive)
btn_css = ".header-icon-btn { width: 40px; height: 40px; } .icon { width: 16px; }"
btn = run_contracts(btn_css, spec_light={"icons.size.inline": "16px"})
check(btn["icon-sizes"]["status"] == "PASS",
      "icon-named button box excluded from icon-size strays")

# comment immunity: a checkbox mentioned in an HTML comment is not a finding
commented = run_contracts(GOOD_CSS, "<!-- <input type=\"checkbox\"> --><dialog></dialog>")
check(commented["no-native-checkbox"]["status"] == "PASS",
      "checkbox inside an HTML comment is ignored")

# ---- viewport zoom lock (fleet-config#296) ----

VP_LOCKED = ('<meta name="viewport" content="width=device-width, initial-scale=1, '
             'maximum-scale=1, user-scalable=no, viewport-fit=cover">')
VP_UNLOCKED = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
VP_NO_COVER = ('<meta name="viewport" content="width=device-width, initial-scale=1, '
               'maximum-scale=1, user-scalable=no">')
vp = run_contracts(GOOD_CSS, VP_LOCKED, html_name="index.html")
check(vp["viewport-lock"]["status"] == "PASS", "locked viewport meta PASS")
vp2 = run_contracts(GOOD_CSS, VP_UNLOCKED, html_name="index.html")
check(vp2["viewport-lock"]["status"] == "FAIL",
      "zoomable viewport (no user-scalable=no / maximum-scale=1) FAIL")
vp3 = run_contracts(GOOD_CSS, VP_NO_COVER, html_name="index.html")
check(vp3["viewport-lock"]["status"] == "WARN",
      "locked but missing viewport-fit=cover WARN")
vp4 = run_contracts(GOOD_CSS, "<dialog></dialog>", html_name="index.html")
check(vp4["viewport-lock"]["status"] == "FAIL", "index.html without any viewport meta FAIL")
check(good["viewport-lock"]["status"] == "NA", "no index.html -> viewport-lock NA")

# ---- button tiers (fleet-config#296) ----

check(good["button-tiers"]["status"] == "NA", "no button rules -> button-tiers NA")
bt_hard = run_contracts(".photo-warning .retake-btn { background: #d29922; color: #000; }")
check(bt_hard["button-tiers"]["status"] == "FAIL"
      and "#d29922" in bt_hard["button-tiers"]["detail"],
      "hardcoded button fill FAILs with the literal in the detail")
bt_ghost = run_contracts(".ghost-btn { background: var(--accent-soft); color: var(--accent); }")
check(bt_ghost["button-tiers"]["status"] == "FAIL",
      "a filled ghost class FAILs (ghost = transparent — the inversion detector)")
bt_state = run_contracts(
    ".ghost-btn { background: transparent; border: 1px solid var(--line); color: var(--muted); }\n"
    ".ghost-btn.copied { background: var(--on); color: var(--accent-fg); }")
check(bt_state["button-tiers"]["status"] == "PASS",
      "a state flash on a ghost (.copied) is not an inversion")
bt_solid = run_contracts(".run-btn { background: var(--accent); color: var(--accent-fg); }")
check(bt_solid["button-tiers"]["status"] == "WARN"
      and ".run-btn" in bt_solid["button-tiers"]["detail"],
      "solid accent outside the primary WARNs with the selector")
bt_primary = run_contracts(".detail-save-btn { background: var(--accent); color: var(--accent-fg); }")
check(bt_primary["button-tiers"]["status"] == "PASS",
      "the primary class may carry the solid accent")
bt_tint_off = run_contracts(".big-btn { background: var(--accent-soft); color: var(--muted); }")
check(bt_tint_off["button-tiers"]["status"] == "WARN",
      "tint fill without accent text WARNs")
bt_tint_ok = run_contracts(
    ".big-btn { background: var(--accent-soft); color: var(--accent); "
    "border: 1px solid var(--accent-border-soft); }")
check(bt_tint_ok["button-tiers"]["status"] == "PASS", "canonical tint recipe PASSes")

# ---- user-selectable theme (fleet-config#290) ----

TT_SPEC = {"icons.size.inline": "16px", "colors.canvas": "#ffffff"}
TT_DARK = {"colors.canvas": "#0d1117"}
TT_BOOT = ("<head><script>(function(){"
           "var t = localStorage.getItem('app.theme');"
           "var dark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;"
           "document.documentElement.dataset.theme = dark ? 'dark' : 'light';"
           "})();</script>")
TT_METAS = ('<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">'
            '<meta name="theme-color" content="#0d1117" media="(prefers-color-scheme: dark)">')
TT_TOGGLE = ('</head><body><button id="themeToggle"></button>'
             "<script>localStorage.setItem('app.theme', 'dark');</script></body>")
tt = run_contracts(GOOD_CSS, TT_BOOT + TT_METAS + TT_TOGGLE, spec_light=TT_SPEC,
                   spec_dark=TT_DARK, html_name="index.html")
check(tt["theme-toggle"]["status"] == "PASS", "canonical theme mechanism PASS")

# grocery shape: setAttribute stamp, media-before-content self-closing metas,
# kebab-case button id — structurally divergent but contract-conformant.
TT_G = ('<head><script>(function(){'
        'var t = localStorage.getItem("grocery.theme");'
        'document.documentElement.setAttribute("data-theme", t ? t : '
        '(window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));'
        '})();</script>'
        '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />'
        '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0d1117" />'
        '</head><body><button id="theme-toggle"></button>'
        '<script>localStorage.setItem("grocery.theme", "light");</script></body>')
tt_g = run_contracts(GOOD_CSS, TT_G, spec_light=TT_SPEC, spec_dark=TT_DARK,
                     html_name="index.html")
check(tt_g["theme-toggle"]["status"] == "PASS",
      "setAttribute stamp + attr-order-divergent metas still PASS (grocery shape)")

# home-automation shape: the toggle writes through a theme-named constant,
# not a `.theme` string literal.
TT_K = (TT_BOOT + TT_METAS
        + "</head><body><script>"
        + "localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');"
        + "</script></body>")
tt_k = run_contracts(GOOD_CSS, TT_K, spec_light=TT_SPEC, spec_dark=TT_DARK,
                     html_name="index.html")
check(tt_k["theme-toggle"]["status"] == "PASS",
      "setItem via a theme-named constant PASSes (home-automation shape)")

tt_noboot = run_contracts(GOOD_CSS, "<head>" + TT_METAS + TT_TOGGLE,
                          spec_light=TT_SPEC, spec_dark=TT_DARK, html_name="index.html")
check(tt_noboot["theme-toggle"]["status"] == "FAIL",
      "missing pre-paint boot script FAIL")

tt_notoggle = run_contracts(GOOD_CSS, TT_BOOT + TT_METAS + "</head><body></body>",
                            spec_light=TT_SPEC, spec_dark=TT_DARK, html_name="index.html")
check(tt_notoggle["theme-toggle"]["status"] == "FAIL",
      "boot without a persisted toggle (no .theme setItem) FAIL")

tt_nometa = run_contracts(GOOD_CSS, TT_BOOT + TT_TOGGLE, spec_light=TT_SPEC,
                          spec_dark=TT_DARK, html_name="index.html")
check(tt_nometa["theme-toggle"]["status"] == "WARN",
      "mechanism present but theme-color meta pair missing WARN")

TT_OFFSPEC = TT_METAS.replace("#ffffff", "#fafafa")
tt_drift = run_contracts(GOOD_CSS, TT_BOOT + TT_OFFSPEC + TT_TOGGLE,
                         spec_light=TT_SPEC, spec_dark=TT_DARK, html_name="index.html")
check(tt_drift["theme-toggle"]["status"] == "WARN"
      and "#fafafa" in tt_drift["theme-toggle"]["detail"],
      "theme-color meta off the spec canvas WARNs with the literal")

check(good["theme-toggle"]["status"] == "NA", "no index.html -> theme-toggle NA")


# ---- icon-set: emoji vs Lucide (design.md Icons, fleet-config#284) ----

_ic = Path(tempfile.mkdtemp(prefix="dl-icon-"))
try:
    (_ic / "s.css").write_text("", encoding="utf-8")
    (_ic / "i.html").write_text(
        '<button class="tab-emoji">\U0001F3E0</button><span>Home</span>', encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(_ic, [_ic / "s.css"], [_ic / "i.html"], [], {})}
    check(out["icon-set"]["status"] == "FAIL",
          "emoji glyph + no vendored lucide sprite -> FAIL")
    check("emoji-glyphs (1 site" in out["icon-set"]["detail"],
          "icon-set FAIL reports the emoji site count")

    vend = _ic / "app/webapp/static/_vendored/icons"
    vend.mkdir(parents=True)
    (vend / "icons-sprite.html").write_text('<symbol id="i-home"></symbol>', encoding="utf-8")
    out2 = {c["id"]: c for c in dl.contracts(_ic, [_ic / "s.css"], [_ic / "i.html"], [], {})}
    check(out2["icon-set"]["status"] == "WARN",
          "emoji glyph alongside an adopted lucide sprite -> WARN (mixed set)")

    (_ic / "i.html").write_text('<span>Home</span>', encoding="utf-8")
    out3 = {c["id"]: c for c in dl.contracts(_ic, [_ic / "s.css"], [_ic / "i.html"], [], {})}
    check(out3["icon-set"]["status"] == "PASS",
          "lucide sprite adopted, no emoji -> PASS")
finally:
    shutil.rmtree(_ic, ignore_errors=True)

# emoji baked into a JS string literal (app-launcher#368), not markup — the
# scan target is rendered text content generally, not just tag positions.
_icjs = Path(tempfile.mkdtemp(prefix="dl-iconjs-"))
try:
    (_icjs / "s.css").write_text("", encoding="utf-8")
    (_icjs / "app.js").write_text(
        "el.textContent = 'Nothing here \U0001F389';", encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(_icjs, [_icjs / "s.css"], [], [_icjs / "app.js"], {})}
    check(out["icon-set"]["status"] == "FAIL",
          "emoji baked into a JS string literal is caught (app-launcher#368)")
finally:
    shutil.rmtree(_icjs, ignore_errors=True)

check(run_contracts(GOOD_CSS)["icon-set"]["status"] == "NA",
      "no emoji and no vendored icons/ component -> NA")


# ---- chevron placement (design.md disclosure.chevron: right, fleet-config#284) ----

_lead = run_contracts(
    "", '<details><summary><span class="chevron">›</span><span>Title</span></summary></details>')
check(_lead["chevron-placement"]["status"] == "FAIL",
      "leading chevron before the title text -> FAIL (app-launcher#362)")
check("1 disclosure" in _lead["chevron-placement"]["detail"],
      "chevron-placement FAIL reports the count")

_trail = run_contracts(
    "", '<details><summary><span>Title</span><span class="chevron">›</span></summary></details>')
check(_trail["chevron-placement"]["status"] == "PASS",
      "trailing chevron after the title text -> PASS")

_none = run_contracts("", '<details><summary>Plain title, no chevron</summary></details>')
check(_none["chevron-placement"]["status"] == "NA",
      "no chevron-bearing disclosure -> NA")


# ---- nav-nesting: nav.tabs must be a <body> sibling of main.app, never
#      nested inside it (_vendored/nav/README.md; app-launcher#369, #284) ----

_nn = Path(tempfile.mkdtemp(prefix="dl-navnest-"))
try:
    (_nn / "s.css").write_text(GOOD_CSS, encoding="utf-8")
    (_nn / "index.html").write_text(
        '<body><main class="app">stuff<nav class="tabs">tabs</nav></main></body>',
        encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(_nn, [_nn / "s.css"], [_nn / "index.html"], [], {})}
    check(out["nav-contract"]["status"] == "FAIL",
          "nav.tabs nested inside main.app -> FAIL regardless of other signals (#369)")
    check("nested-inside-app" in out["nav-contract"]["detail"],
          "nav-nesting FAIL names the structural violation")

    (_nn / "index.html").write_text(
        '<body><nav class="tabs">tabs</nav><main class="app">stuff</main></body>',
        encoding="utf-8")
    out2 = {c["id"]: c for c in dl.contracts(_nn, [_nn / "s.css"], [_nn / "index.html"], [], {})}
    check(out2["nav-contract"]["status"] == "PASS",
          "nav.tabs as a body sibling of main.app -> PASS")
    check("nav-nesting: sibling" in out2["nav-contract"]["detail"],
          "nav-nesting PASS notes the sibling relationship")
finally:
    shutil.rmtree(_nn, ignore_errors=True)


# ---- row-height-scale (design.md rows scale, fleet-config#284/app-launcher#365) ----

rh_bad = run_contracts(".list-row { height: 47px; }")
check(rh_bad["row-height-scale"]["status"] == "WARN",
      "row height outside the 44/52/60 scale -> WARN")
check("47px" in rh_bad["row-height-scale"]["detail"], "WARN names the stray literal")

rh_ok = run_contracts(".list-row { height: 52px; }")
check(rh_ok["row-height-scale"]["status"] == "PASS", "row height on the scale -> PASS")

rh_var = run_contracts(".list-row { height: var(--row-md); }")
check(rh_var["row-height-scale"]["status"] == "PASS",
      "var(--row-*) reference never flagged as a stray")

rh_na = run_contracts(".card { padding: 12px; }")
check(rh_na["row-height-scale"]["status"] == "NA",
      "no row/action-rail selectors -> NA")

rh_spec = run_contracts(".list-row { height: 46px; }",
                        spec_light={"icons.size.inline": "16px", "rows.sm": "46px"})
check(rh_spec["row-height-scale"]["status"] == "PASS",
      "spec-driven rows.* override the hardcoded default scale")


# ---- editor-modal contract (design.md `modal` component, fleet-config#307) ----
# Fixtures mirror the real app-launcher#70 before/after (job-editor dialog):
# pre-fix `.stacked` styled only under `.settings-card`, a raw unstyled
# `.job-chain-fieldset`, a footer Cancel button instead of a header close,
# two always-visible footer actions, and no max-height/scroll; post-fix adopts
# the dialog-scoped stacked rows, drops the fieldset for a `.dialog-section`,
# moves Cancel into a header `.dialog-close`, collapses the footer to one
# full-width solid-accent primary, and top-anchors with an internal scroll.

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
pre_modal = run_contracts(PRE_MODAL_CSS, PRE_MODAL_HTML)
check(pre_modal["modal-unstyled-rows"]["status"] == "FAIL",
      "label.stacked styled only under .settings-card -> FAIL (the #70 root cause)")
check(pre_modal["modal-raw-fieldset"]["status"] == "FAIL",
      "raw .job-chain-fieldset with zero authored CSS -> FAIL")
check(pre_modal["modal-header"]["status"] == "FAIL",
      "no header close button + footer Cancel -> FAIL")
check(pre_modal["modal-footer"]["status"] == "FAIL",
      "two always-visible footer actions (Cancel + Add and verify) -> FAIL")
check(pre_modal["modal-top-anchor"]["status"] == "FAIL",
      "no max-height/overflow on .rename-dialog -> FAIL")

POST_MODAL_HTML = """
<dialog id="jobDialog" class="rename-dialog">
  <form id="jobForm">
    <div class="dialog-head">
      <h2 id="jobDialogTitle">Add job</h2>
      <button type="button" id="jobCancel" class="dialog-close" aria-label="Close">
        <svg class="icon"><use href="#i-x"></use></svg>
      </button>
    </div>
    <label class="stacked"><span>Name</span><input type="text" required></label>
    <section class="dialog-section"><h3 class="dialog-section-title">Run on success</h3></section>
    <div class="dialog-actions dialog-actions--stacked">
      <button type="button" id="jobSaveAnyway" class="big-btn warn" hidden>Save anyway</button>
      <button type="submit" id="jobSaveBtn" class="big-btn">Add and verify</button>
    </div>
  </form>
</dialog>
"""
POST_MODAL_CSS = """
.rename-dialog { margin-top: 16px; max-height: calc(100dvh - 32px); overflow-y: auto; }
.rename-dialog .stacked {
  display: flex; flex-direction: column; padding: 12px 0;
  border-top: 1px solid var(--line-muted);
}
.dialog-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.dialog-actions--stacked { flex-direction: column; align-items: stretch; padding-top: 12px; }
.rename-dialog .dialog-actions .big-btn:not(.warn) {
  background: var(--accent); color: var(--accent-fg); min-height: 48px;
}
"""
post_modal = run_contracts(POST_MODAL_CSS, POST_MODAL_HTML)
check(post_modal["modal-unstyled-rows"]["status"] == "PASS",
      "dialog-scoped .rename-dialog .stacked rule -> PASS")
check(post_modal["modal-raw-fieldset"]["status"] == "PASS",
      "no <fieldset> left (replaced by .dialog-section) -> PASS")
check(post_modal["modal-header"]["status"] == "PASS",
      "header .dialog-close (aria-label=Close), no footer Cancel -> PASS")
check(post_modal["modal-footer"]["status"] == "PASS",
      "one always-visible full-width solid-accent primary -> PASS")
check(post_modal["modal-top-anchor"]["status"] == "PASS",
      "max-height + overflow-y: auto on .rename-dialog -> PASS")

# a dialog with no <form>/inputs is not an "editor modal" -> NA across the board
NON_FORM_DIALOG = '<dialog class="scan-dialog"><p>Are you sure?</p><button type="button">OK</button></dialog>'
non_form = run_contracts(GOOD_CSS, NON_FORM_DIALOG)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(non_form[_cid]["status"] == "NA", f"{_cid}: non-form dialog -> NA, not a finding")

# no <dialog> at all -> NA across the board
no_dialog = run_contracts(GOOD_CSS)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(no_dialog[_cid]["status"] == "NA", f"{_cid}: no dialog in the app -> NA")


# ---- vendored byte-compare ----

scaffold = Path(tempfile.mkdtemp(prefix="dl-scaf-"))
approot = Path(tempfile.mkdtemp(prefix="dl-app-"))
try:
    ref = scaffold / "app/webapp/static/_vendored/switch"
    ref.mkdir(parents=True)
    (ref / "switch.css").write_text(".toggle{}", encoding="utf-8")
    (ref / "switch.js").write_text("export function switchEl(){}", encoding="utf-8")
    ref2 = scaffold / "app/webapp/static/_vendored/card"
    ref2.mkdir(parents=True)
    (ref2 / "card.css").write_text(".card{}", encoding="utf-8")

    mine = approot / "app/webapp/static/_vendored/switch"
    mine.mkdir(parents=True)
    (mine / "switch.css").write_text(".toggle{}", encoding="utf-8")       # identical
    (mine / "switch.js").write_text("export function forked(){}", encoding="utf-8")  # forked

    res = dl.vendored(approot, scaffold)
    comps = res["components"]
    check(comps["switch"]["status"] == "FORKED", "edited vendored file -> FORKED")
    check(comps["switch"]["files"]["switch.css"] == "IDENTICAL", "byte-identical file detected")
    check(comps["switch"]["files"]["switch.js"] == "FORKED", "diverged file flagged FORKED")
    check(comps["card"]["status"] == "NOT_ADOPTED", "component absent from app -> NOT_ADOPTED")
finally:
    shutil.rmtree(scaffold, ignore_errors=True)
    shutil.rmtree(approot, ignore_errors=True)


# ---- vendored icons-sprite: per-symbol compare, not whole-file (#284 finding 4) ----

_spr_scaf = Path(tempfile.mkdtemp(prefix="dl-sprscaf-"))
_spr_app = Path(tempfile.mkdtemp(prefix="dl-sprapp-"))
try:
    _sref = _spr_scaf / "app/webapp/static/_vendored/icons"
    _sref.mkdir(parents=True)
    (_sref / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-star"><path d="M1 1"/></symbol>',
        encoding="utf-8")

    _sapp = _spr_app / "app/webapp/static/_vendored/icons"
    _sapp.mkdir(parents=True)
    # trimmed subset, byte-identical to the scaffold's matching symbol
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>', encoding="utf-8")

    _sres = dl.vendored(_spr_app, _spr_scaf)
    check(_sres["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL (trimmed)",
          "trimmed-but-identical sprite subset -> IDENTICAL (trimmed), not FORKED")
    check(_sres["components"]["icons"]["status"] == "IDENTICAL",
          "a trimmed-identical sprite doesn't fork the whole icons component")

    # a hand-edited symbol IS a genuine fork
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M9 9"/></symbol>', encoding="utf-8")
    _sres2 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres2["components"]["icons"]["files"]["icons-sprite.html"] == "FORKED",
          "a hand-edited symbol is still genuinely FORKED")

    # untrimmed, fully identical set — no "(trimmed)" suffix
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-star"><path d="M1 1"/></symbol>',
        encoding="utf-8")
    _sres3 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres3["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL",
          "a full, untrimmed identical set reports plain IDENTICAL")
finally:
    shutil.rmtree(_spr_scaf, ignore_errors=True)
    shutil.rmtree(_spr_app, ignore_errors=True)


# ---- vendored-root discovery: non-scaffold static layouts (fleet-config#291, #292) ----

for _layout_name, _static_prefix in (("app_web/static", "app_web/static"),  # local-llm-hub (#291)
                                       ("app/static", "app/static")):        # grocery (#292)
    _scaf = Path(tempfile.mkdtemp(prefix="dl-scaf2-"))
    _app = Path(tempfile.mkdtemp(prefix="dl-app2-"))
    try:
        _ref = _scaf / "app/webapp/static/_vendored/switch"
        _ref.mkdir(parents=True)
        (_ref / "switch.css").write_text(".toggle{}", encoding="utf-8")

        _mine = _app / _static_prefix / "_vendored/switch"
        _mine.mkdir(parents=True)
        (_mine / "switch.css").write_text(".toggle{}", encoding="utf-8")

        _res = dl.vendored(_app, _scaf)
        check(_res["app_has_vendored_dir"] is True,
              f"{_layout_name}: discovered without the hardcoded app/webapp/static/ prefix")
        check(_res["components"]["switch"]["status"] == "IDENTICAL",
              f"{_layout_name}: byte-identical vendored copy reports IDENTICAL, not NOT_ADOPTED")
    finally:
        shutil.rmtree(_scaf, ignore_errors=True)
        shutil.rmtree(_app, ignore_errors=True)

# nav-contract provenance follows the same discovery, not just the vendored lens (#291)
_t = Path(tempfile.mkdtemp(prefix="dl-nav2-"))
try:
    (_t / "s.css").write_text(GOOD_CSS, encoding="utf-8")
    _vend = _t / "app_web/static/_vendored/nav"
    _vend.mkdir(parents=True)
    (_vend / "nav-tabs.css").write_text("/* vendored */", encoding="utf-8")
    _out = {c["id"]: c for c in dl.contracts(_t, [_t / "s.css"], [], [], {})}
    check(_out["nav-contract"]["status"] == "PASS", "app_web/static layout: nav signals + shell PASS")
    check("vendored" in _out["nav-contract"]["detail"],
          "app_web/static layout: nav-contract provenance reads vendored, not hand-carried (#291)")
finally:
    shutil.rmtree(_t, ignore_errors=True)


# ---- sibling duplicate detection ----

sib = Path(tempfile.mkdtemp(prefix="dl-sib-"))
try:
    (sib / "a.js").write_text(
        "function schedule(ms) {}\nexport function unique1() {}\n", encoding="utf-8")
    (sib / "b.js").write_text(
        "function schedule(ms) {}\nconst fmtW = (v) => v;\n"
        "/* function schedule(fake) {} */\n", encoding="utf-8")
    (sib / "c.js").write_text("const fmtW = () => 0;\nfunction init() {}\n", encoding="utf-8")
    dupes = dl.siblings(sib, [sib / "a.js", sib / "b.js", sib / "c.js"])
    names = {d["name"] for d in dupes}
    check("schedule" in names, "same-name function across 2 files flagged (the #369 case)")
    check("fmtW" in names, "const-arrow duplicates flagged")
    check("unique1" not in names, "single-file definition not flagged")
    check("init" not in names, "stoplist name ignored")
    sched = next(d for d in dupes if d["name"] == "schedule")
    check(len(sched["sites"]) == 2, "commented-out definition not counted")
finally:
    shutil.rmtree(sib, ignore_errors=True)


# ---- real-spec smoke: the shipped design.md parses and carries v2 groups ----

real_spec = Path.home() / ".claude" / "design.md"
if real_spec.is_file():
    parsed = dl.parse_spec(real_spec.read_text(encoding="utf-8", errors="replace"))
    check(parsed.get("colors.canvas") == "#ffffff", "real design.md: canvas parses")
    check(parsed.get("components.switch.trackOn") == parsed.get("colors.success"),
          "real design.md: switch trackOn resolves to success (the green decision)")
    check(parsed.get("icons.size.nav-tab") == "20px",
          "real design.md: nav-tab icon step is the phone-validated 20px")
    check((parsed.get("colors.accent-soft") or "").startswith("color-mix("),
          "real design.md: accent-soft is the color-mix derivation (#296)")
    check(parsed.get("components.button-tint.backgroundColor")
          == parsed.get("colors.accent-soft"),
          "real design.md: button-tint fill resolves to accent-soft")
    check(parsed.get("components.button-ghost.backgroundColor") == "transparent",
          "real design.md: ghost = transparent (the settled vocabulary, #296)")

real_dark = Path.home() / ".claude" / "design.dark.md"
if real_dark.is_file():
    parsed_dark = dl.parse_spec(real_dark.read_text(encoding="utf-8", errors="replace"))
    check(parsed_dark.get("colors.canvas") == "#0d1117",
          "real design.dark.md: dark canvas parses (the dark theme-color meta, #290)")


if _fails:
    print(f"FAILED {len(_fails)} check(s):")
    for f in _fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("design_lint: all pure-logic checks passed")
raise SystemExit(0)
