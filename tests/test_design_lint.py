"""Unit tests for the pure logic in skills/_lib/design_lint/ (fleet-config#277).

No live repos or gh — synthetic spec/CSS/JS strings and temp trees exercise the
frontmatter parser (inline maps, nesting, {token} refs), the custom-prop
extractor (theme split, comment/P3 immunity — the real home-automation bug),
the alias mapper (match/drift/missing/unmapped), the adoption-ratio counter
(exemptions), the contract checks (the green-switch decision, focus ring,
checkbox detection, the nav standalone-shell architecture — fleet-config#282),
the vendored byte-compare, and the sibling duplicate detector.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_lint.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import design_lint as dl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


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
                  html_name: str = "i.html", spec_dark: dict | None = None,
                  js: str = "", files: dict[str, str] | None = None) -> dict:
    t = Path(tempfile.mkdtemp(prefix="dl-con-"))
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


# ---- #394: `//` line comments must not count as emoji-glyph sites ----

check("⎇" not in dl.strip_comments("// header ⎇ status button\ncode();", "js"),
      "strip_comments(js): // line comment emoji is blanked")
check("\U0001F389" in dl.strip_comments(
    "// see https://example.com\nconst s = 'hi \U0001F389';", "js"),
    "strip_comments(js): a // comment's URL doesn't over-strip a later string literal")

_ic394 = Path(tempfile.mkdtemp(prefix="dl-iconjs394-"))
try:
    (_ic394 / "s.css").write_text("", encoding="utf-8")
    (_ic394 / "app.js").write_text(
        "// The header ⎇ status button: re-fetch fresh data (#139)\n"
        "export function fetchGitStatus() {}\n", encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(_ic394, [_ic394 / "s.css"], [], [_ic394 / "app.js"], {})}
    check(out["icon-set"]["status"] == "NA",
          "emoji glyph inside a // comment is not a rendered-text site (#394, the real apps.js:242 case)")
finally:
    shutil.rmtree(_ic394, ignore_errors=True)

# a regex literal's character class can contain a quote/backtick (e.g. the
# real life-os.js `.replace(/`([^`]+)`/g, ...)` markdown-code-span rule) —
# the odd backtick count must NOT be read as opening a template literal that
# then swallows every real // comment for the rest of the file.
check(dl.strip_comments(
    "s.replace(/`([^`]+)`/g, '<code>$1</code>');\n"
    "// header ⎇ status button\n"
    "code();", "js") == (
    "s.replace(/`([^`]+)`/g, '<code>$1</code>');\n"
    "\n"
    "code();"),
    "strip_comments(js): a backtick inside a regex char class doesn't fake-open a "
    "template literal that swallows the following // comment (life-os.js real case)")

# adjacent HTML `<!--...-->` blocks, each carrying an emoji — locks in that
# the non-greedy <!--.*?--> regex pairs each opener with its own closer
# rather than bleeding across blocks (investigated as part of #394; not
# reproducible against the real app-launcher fixture at HEAD, but worth a
# permanent regression guard).
_html394 = ('<!-- attach flow, same as the outer-bar \U0001F5BC button --><button>x</button>'
            '<!-- dismiss (or ✕) --><span>ok</span>')
_htree394 = Path(tempfile.mkdtemp(prefix="dl-html394-"))
try:
    (_htree394 / "i.html").write_text(_html394, encoding="utf-8")
    sites394 = dl.find_emoji_sites(_htree394, [_htree394 / "i.html"], [])
    check(sites394 == [], "adjacent HTML comment blocks each fully stripped, no bleed-through (#394)")
finally:
    shutil.rmtree(_htree394, ignore_errors=True)


# ---- #416: a regex char class is input matching, never rendered UI copy ----

# The real app-launcher terminal-readback.js constructs: character classes that
# match the glyphs Claude Code's TUI emits *into* the PTY (turn bullets, spinner
# marks, tool-result gutter arrows). The parser must byte-match them, so the
# "replace with a Lucide icon" remedy the icon-set finding prescribes would
# break read-aloud outright — they are not icon choices at all.
_RB_JS = (
    "const BULLET_RE = /^[●⏺•◉○]$/;\n"
    "const SPINNER_LINE_RE = /^\\s*[*✶✻✽✢✱·•∗⁘]?\\s*[A-Z][a-z]+(?:…|\\.\\.\\.)\\s*\\(/;\n"
    "const TIP_RESULT_RE = /^\\s*[⎿└╰⤷↳]\\s*Tip\\b/i;\n"
)
check("●" not in dl.strip_comments(_RB_JS, "js", blank_regex_literals=True),
      "strip_comments(js, blank_regex_literals): regex char-class glyphs dropped (#416)")
check("●" in dl.strip_comments(_RB_JS, "js"),
      "strip_comments(js): regex literals still preserved by default — every "
      "other contract reads pattern text as source (#416)")

_rb = Path(tempfile.mkdtemp(prefix="dl-regex416-"))
try:
    (_rb / "s.css").write_text("", encoding="utf-8")
    (_rb / "readback.js").write_text(_RB_JS, encoding="utf-8")
    check(dl.find_emoji_sites(_rb, [], [_rb / "readback.js"]) == [],
          "parser regex char classes are not emoji sites (#416, real "
          "app-launcher terminal-readback.js case)")
    out416 = {c["id"]: c for c in dl.contracts(_rb, [_rb / "s.css"], [], [_rb / "readback.js"], {})}
    check(out416["icon-set"]["status"] == "NA",
          "a file of nothing but parser regexes -> icon-set NA, not FAIL (#416)")

    # ...but an emoji in real UI copy *beside* those regexes is still caught:
    # blanking regex literals must not become a blanket amnesty for the file.
    (_rb / "readback.js").write_text(
        _RB_JS + "el.textContent = 'Nothing here \U0001F389';\n", encoding="utf-8")
    sites_mixed = dl.find_emoji_sites(_rb, [], [_rb / "readback.js"])
    check(len(sites_mixed) == 1 and sites_mixed[0].endswith(":4"),
          "an emoji in UI copy alongside parser regexes is still flagged, at "
          "its true line (#416 must not over-suppress; line numbers preserved)")
finally:
    shutil.rmtree(_rb, ignore_errors=True)


# ---- #416: third-party vendor/ bundles are not the app's icon choice ----

# xterm.js ships the VT100 DEC Special Graphics scan-line table (U+23BA-U+23BD)
# inside its minified bundle. Not authored UI copy, and unfixable in the repo
# that vendored it. The fleet's own `_vendored/` family stays in scope.
_vend = Path(tempfile.mkdtemp(prefix="dl-vendor416-"))
try:
    (_vend / "s.css").write_text("", encoding="utf-8")
    third = _vend / "app/webapp/static/vendor"
    third.mkdir(parents=True)
    (third / "xterm.js").write_text(
        'var t={j:"┘",o:"⎺",p:"⎻",r:"⎼",s:"⎽"};', encoding="utf-8")
    fleet = _vend / "app/webapp/static/_vendored/nav"
    fleet.mkdir(parents=True)
    (fleet / "nav-tabs.js").write_text(
        "b.textContent = 'Home \U0001F3E0';", encoding="utf-8")
    js416 = [third / "xterm.js", fleet / "nav-tabs.js"]
    sites_v = dl.find_emoji_sites(_vend, [], js416)
    check(all("vendor/xterm.js" not in s for s in sites_v),
          "third-party vendor/ bundle glyphs are not emoji sites (#416, "
          "xterm.js DEC Special Graphics table)")
    check(len(sites_v) == 1 and "_vendored/nav" in sites_v[0],
          "the fleet's own _vendored/ family stays in scope — `vendor` must "
          "not be read as a prefix of `_vendored` (#416)")
finally:
    shutil.rmtree(_vend, ignore_errors=True)


# ---- app-icon-family: one generated Lucide master across install surfaces (#369) ----

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
app_icons_ok = run_contracts(
    GOOD_CSS, APP_ICON_INDEX, spec_light=APP_ICON_SPEC,
    html_name="index.html", files=APP_ICON_FILES,
)
check(app_icons_ok["app-icon-family"]["status"] == "PASS",
      "canonical generated PWA icon family -> PASS")

legacy_files = dict(APP_ICON_FILES)
legacy_files["scripts/gen_icons.py"] = "def draw_mic(): pass\n"
app_icons_legacy = run_contracts(
    GOOD_CSS, APP_ICON_INDEX, spec_light=APP_ICON_SPEC,
    html_name="index.html", files=legacy_files,
)
check(app_icons_legacy["app-icon-family"]["status"] == "FAIL"
      and "brand_gen.render_set" in app_icons_legacy["app-icon-family"]["detail"],
      "bespoke legacy generator -> FAIL with shared-generator evidence")

combined_files = dict(APP_ICON_FILES)
combined_files["static/manifest.webmanifest"] = """
{"icons": [{"src": "/static/icon-512.png", "purpose": "any maskable"}]}
"""
app_icons_combined = run_contracts(
    GOOD_CSS, APP_ICON_INDEX, spec_light=APP_ICON_SPEC,
    html_name="index.html", files=combined_files,
)
check(app_icons_combined["app-icon-family"]["status"] == "FAIL"
      and "combines purpose 'any maskable'" in app_icons_combined["app-icon-family"]["detail"],
      "one source declared any+maskable -> FAIL")

missing_files = {
    "static/manifest.webmanifest": APP_ICON_MANIFEST,
    "scripts/gen_icons.py": APP_ICON_FILES["scripts/gen_icons.py"],
}
app_icons_missing = run_contracts(
    GOOD_CSS, '<head><link rel="manifest" href="/static/manifest.webmanifest"></head>',
    spec_light=APP_ICON_SPEC, html_name="index.html", files=missing_files,
)
check(app_icons_missing["app-icon-family"]["status"] == "FAIL"
      and "missing canonical asset(s)" in app_icons_missing["app-icon-family"]["detail"]
      and "apple-touch-icon" in app_icons_missing["app-icon-family"]["detail"]
      and "favicon" in app_icons_missing["app-icon-family"]["detail"],
      "missing assets and index links -> one actionable FAIL")


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

# a dialog with no editable FIELDS (an alert/confirm) is not an "editor
# modal" -> NA across the board. The boundary is fields, not <form> —
# fleet-config#342 dropped the <form> requirement (see the form-less
# editor fixture below), so this fixture stays field-less on purpose.
NON_FORM_DIALOG = '<dialog class="scan-dialog"><p>Are you sure?</p><button type="button">OK</button></dialog>'
non_form = run_contracts(GOOD_CSS, NON_FORM_DIALOG)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(non_form[_cid]["status"] == "NA", f"{_cid}: field-less dialog -> NA, not a finding")

# no <dialog> at all -> NA across the board
no_dialog = run_contracts(GOOD_CSS)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(no_dialog[_cid]["status"] == "NA", f"{_cid}: no dialog in the app -> NA")

# ---- #342: form-less JS-managed editor dialogs are real editors ----
# The exact home-automation#409 shape: a native <dialog> with bare
# select/input fields, a role="switch" toggle, a body-level danger Delete,
# and a plain type="button" Save — no <form>. Before #342 this returned NA
# for every modal-* check; it must now be held to the full contract.

FORMLESS_MODAL_HTML = """
<dialog id="overrideDialog" class="detail-dialog">
  <div class="detail-card">
    <div class="detail-header">
      <h2 id="overrideTitle">Override</h2>
      <button id="overrideClose" type="button" class="detail-close hit-target" aria-label="Close">
        <svg class="icon"><use href="#i-x"></use></svg>
      </button>
    </div>
    <div class="row"><span>Enabled</span>
      <button id="overrideEnabled" type="button" class="toggle on" role="switch" aria-checked="true"></button>
    </div>
    <label class="row"><span>Detector</span><select id="overrideZone" class="select-native"></select></label>
    <label class="row"><span>Bypass after</span><select id="overrideRetries" class="select-native"></select></label>
    <button id="overrideDelete" type="button" class="schedule-editor-delete" hidden>Delete override</button>
    <div class="detail-actions"><button id="overrideSave" type="button" class="detail-save-btn">Save</button></div>
  </div>
</dialog>
"""
FORMLESS_MODAL_CSS = """
.detail-dialog { margin-top: 16px; max-height: calc(100dvh - 32px); overflow-y: auto; }
.detail-card .row { display: flex; padding: 12px 0; border-top: 1px solid var(--line-muted); }
.detail-actions { display: flex; flex-direction: column; align-items: stretch; }
.detail-save-btn { background: var(--accent); color: var(--accent-fg); width: 100%; min-height: 48px; }
"""
formless = run_contracts(FORMLESS_MODAL_CSS, FORMLESS_MODAL_HTML)
for _cid, _why in (
        ("modal-unstyled-rows", ".detail-card .row is dialog-scoped via the wrapper class"),
        ("modal-raw-fieldset", "no fieldset"),
        ("modal-header", "h2 + aria-label=Close close button"),
        ("modal-footer", "one full-width solid-accent Save"),
        ("modal-top-anchor", "max-height + overflow-y on .detail-dialog")):
    check(formless[_cid]["status"] == "PASS",
          f"{_cid}: form-less #409-style editor is evaluated ({_why}) -> PASS, not NA (#342)")

# a live-control dialog (fields, action rails, NO Save) is not a *staged*
# editor: the footer contract is NA for it — its 5-button control rail must
# not be misread as a persistence footer (the home-automation camera live
# view; #342).
CONTROL_DIALOG_HTML = """
<dialog id="liveDialog" class="detail-dialog">
  <div class="detail-card">
    <div class="detail-header"><h2>Camera</h2>
      <button type="button" class="detail-close" aria-label="Close">x</button></div>
    <div class="live-actions">
      <button type="button" class="range-tab">Step</button>
      <button type="button" class="range-tab">-</button>
      <button type="button" class="range-tab">+</button>
      <button type="button" class="range-tab">Snapshot</button>
      <button type="button" class="range-tab">Record</button>
    </div>
    <label class="row"><span>Pan</span><input type="number" class="input-native"></label>
  </div>
</dialog>
"""
control_dlg = run_contracts(FORMLESS_MODAL_CSS, CONTROL_DIALOG_HTML)
check(control_dlg["modal-footer"]["status"] == "NA",
      "modal-footer: live-control dialog (fields, no Save) -> NA, rail not a footer (#342)")
check(control_dlg["modal-header"]["status"] == "PASS",
      "modal-header: live-control dialog still held to the header contract")


# ---- #342: hit-target contract (static leg) ----

HIT_SPEC = {"icons.size.inline": "16px", "components.hit-target.min": "44px"}

# no components.hit-target token in the spec -> NA (spec-driven, no hardcoded floor)
check(no_dialog["hit-target"]["status"] == "NA",
      "hit-target: token absent from spec -> NA")

# compact 34px controls mitigated by (a) the co-applied .hit-target utility
# and (b) a per-control ::before expansion; a real-geometry 44px control needs nothing
HIT_OK_CSS = """
.weather-icon-btn { width: 34px; height: 34px; }
.hit-target { position: relative; }
.hit-target::before { content: ""; position: absolute; inset: -5px; }
.detail-close { width: 34px; height: 34px; position: relative; }
.detail-close::before { content: ""; position: absolute; inset: -5px; }
.day-btn { width: 44px; height: 44px; }
"""
HIT_OK_HTML = '<button class="weather-icon-btn hit-target"></button><button class="detail-close"></button><button class="day-btn"></button>'
hit_ok = run_contracts(HIT_OK_CSS, HIT_OK_HTML, spec_light=HIT_SPEC)
check(hit_ok["hit-target"]["status"] == "PASS",
      "hit-target: 34px controls with utility/pseudo expansion + 44px real geometry -> PASS")

# a compact control with no expansion anywhere -> WARN naming the selector
HIT_BAD_CSS = ".tiny-btn { width: 30px; height: 30px; }"
hit_bad = run_contracts(HIT_BAD_CSS, '<button class="tiny-btn"></button>', spec_light=HIT_SPEC)
check(hit_bad["hit-target"]["status"] == "WARN",
      "hit-target: 30x30 control, no ::before expansion, no utility -> WARN")
check(".tiny-btn" in hit_bad["hit-target"]["detail"],
      "hit-target WARN names the offending selector")

# no fixed-size compact pointer targets authored at all -> NA even with the token
hit_na = run_contracts(GOOD_CSS, spec_light=HIT_SPEC)
check(hit_na["hit-target"]["status"] == "NA",
      "hit-target: no fixed-size compact control rules -> NA")


# ---- #342: chart contracts (static leg) ----

# no Chart.js at all -> both chart checks NA
check(no_dialog["chart-tick-budget"]["status"] == "NA", "chart-tick-budget: no Chart.js -> NA")
check(no_dialog["chart-noncolor-cue"]["status"] == "NA", "chart-noncolor-cue: no Chart.js -> NA")

CHART_GOOD_JS = """
const chart = new Chart(canvas, {
  data: { datasets: [
    { label: 'Generation', borderColor: pal.gen, borderDash: [], pointStyle: 'circle' },
    { label: 'Grid', borderColor: pal.grid, borderDash: [8, 4], pointStyle: 'rectRot' },
  ]},
  options: { scales: { x: { ticks: {
    maxRotation: 0, autoSkip: true, autoSkipPadding: 12, maxTicksLimit: budget(w),
  }}}},
});
"""
chart_good = run_contracts(GOOD_CSS, js=CHART_GOOD_JS)
check(chart_good["chart-tick-budget"]["status"] == "PASS",
      "chart-tick-budget: maxTicksLimit + autoSkip + maxRotation -> PASS")
check(chart_good["chart-noncolor-cue"]["status"] == "PASS",
      "chart-noncolor-cue: borderDash + pointStyle on coloured datasets -> PASS")

CHART_BAD_JS = """
const chart = new Chart(canvas, {
  data: { datasets: [
    { label: 'A', borderColor: '#0969da' },
    { label: 'B', borderColor: '#1a7f37' },
  ]},
  options: { scales: { x: { ticks: { color: '#888' } } } },
});
"""
chart_bad = run_contracts(GOOD_CSS, js=CHART_BAD_JS)
check(chart_bad["chart-tick-budget"]["status"] == "WARN",
      "chart-tick-budget: Chart.js with no maxTicksLimit/autoSkip -> WARN")
check(chart_bad["chart-noncolor-cue"]["status"] == "WARN",
      "chart-noncolor-cue: two coloured datasets, colour-only -> WARN")

CHART_SINGLE_JS = "new Chart(canvas, { data: { datasets: [{ borderColor: '#0969da' }] } });"
chart_single = run_contracts(GOOD_CSS, js=CHART_SINGLE_JS)
check(chart_single["chart-noncolor-cue"]["status"] == "NA",
      "chart-noncolor-cue: single coloured dataset -> NA (no second series to distinguish)")


# ---- #342: async-lifecycle contract (static leg) ----

# no data-state anywhere -> NA
check(no_dialog["async-lifecycle"]["status"] == "NA", "async-lifecycle: no data-state -> NA")

# shadcn-style interaction states are a different channel -> NA, not a finding
lc_interaction = run_contracts(GOOD_CSS, '<div data-state="open"></div><div data-state="closed"></div>')
check(lc_interaction["async-lifecycle"]["status"] == "NA",
      "async-lifecycle: interaction-only data-state (open/closed) -> NA")

# canonical vocabulary + role=status live region -> PASS (values may come from
# markup, JS literals, or CSS attribute selectors)
LC_GOOD_HTML = ('<main data-state="loading"></main>'
                '<div id="toast" role="status" aria-live="polite" hidden></div>')
LC_GOOD_CSS = GOOD_CSS + '\n[data-state="stale"] .note { color: var(--fg-muted); }\n'
LC_GOOD_JS = "pane.dataset.state = 'error';"
lc_good = run_contracts(LC_GOOD_CSS, LC_GOOD_HTML, js=LC_GOOD_JS)
check(lc_good["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: loading/stale/error within vocabulary + role=status -> PASS")
check("error" in lc_good["async-lifecycle"]["detail"],
      "async-lifecycle PASS lists the states it found")

# lifecycle vocabulary mixed with a non-canonical synonym -> WARN naming it
lc_mixed = run_contracts(GOOD_CSS, '<main data-state="loading"></main><section data-state="busy"></section>'
                                   '<div role="status"></div>')
check(lc_mixed["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: 'busy' outside the five-state vocabulary -> WARN")
check("busy" in lc_mixed["async-lifecycle"]["detail"],
      "async-lifecycle WARN names the stray value")

# lifecycle states with no role=status live region -> WARN
lc_silent = run_contracts(GOOD_CSS, '<main data-state="loading"></main>')
check(lc_silent["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: lifecycle states but no role=status live region -> WARN")

# ---- #416: the live region may be set from JS, not just declared in markup ----

# The real app-launcher board.js drawer: both halves of the contract are set
# through the DOM API. The check already read `dataset.state = '...'` for the
# lifecycle values, so demanding a markup-literal role="status" made every
# JS-rendered surface — exactly what this contract is for — unpassable.
LC_JS_DOM = ("exchange.dataset.state = 'loading';\n"
             "exchange.setAttribute('role', 'status');\n"
             "exchange.setAttribute('aria-live', 'polite');\n")
lc_jsdom = run_contracts(GOOD_CSS, js=LC_JS_DOM)
check(lc_jsdom["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: setAttribute('role', 'status') satisfies the live "
      "region (#416, real app-launcher board.js:222-224)")

# the reflected IDL property spelling, and irregular whitespace
check(run_contracts(GOOD_CSS, js="p.dataset.state = 'error';\np.role = 'status';"
                    )["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: the `el.role = 'status'` IDL spelling also counts (#416)")
check(run_contracts(GOOD_CSS, js='x.dataset.state="ready";\n'
                    'x.setAttribute( "role" , "status" );'
                    )["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: setAttribute spacing/quote style is not load-bearing (#416)")

# a JS surface that sets lifecycle state but no live region at all still WARNs —
# widening the spellings must not turn the check into a rubber stamp.
check(run_contracts(GOOD_CSS, js="pane.dataset.state = 'loading';"
                    )["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: JS lifecycle with no live region in any spelling still WARNs (#416)")
check(run_contracts(GOOD_CSS, js="pane.dataset.state = 'loading';\n"
                    "pane.setAttribute('role', 'alert');"
                    )["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: a different ARIA role is not a status live region (#416)")


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

    # app-only symbol absent from the reference (local-llm-hub scenario, #389):
    # every shared symbol is byte-identical, but the app also vendors a
    # glyph the reference sprite never had -> still IDENTICAL (trimmed),
    # not FORKED, since the extra symbol isn't drift by itself.
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-only-in-app"><path d="M2 2"/></symbol>',
        encoding="utf-8")
    _sres4 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres4["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL (trimmed)",
          "app-only symbol absent from reference -> IDENTICAL (trimmed), not FORKED (#389)")
    check(_sres4["components"]["icons"]["status"] == "IDENTICAL",
          "an app-only extra symbol doesn't fork the whole icons component (#389)")

    # a genuinely different shared symbol must still fork, even with an
    # unrelated app-only extra symbol present alongside it.
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M9 9"/></symbol>'
        '<symbol id="i-only-in-app"><path d="M2 2"/></symbol>',
        encoding="utf-8")
    _sres5 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres5["components"]["icons"]["files"]["icons-sprite.html"] == "FORKED",
          "a genuinely-diverged shared symbol still forks, even alongside an app-only extra (#389)")
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

source_spec = Path(__file__).resolve().parent.parent / "design.md"
parsed_source = dl.parse_spec(source_spec.read_text(encoding="utf-8", errors="replace"))
check(parsed_source.get("app-icon.generator") == "brand_gen"
      and parsed_source.get("app-icon.maskable") == "icon-512-maskable.png",
      "source design.md: app-icon family contract parses")


_h.report_and_exit("design_lint")
