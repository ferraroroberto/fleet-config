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

def run_contracts(css: str, markup: str = "", spec_light: dict | None = None) -> dict:
    t = Path(tempfile.mkdtemp(prefix="dl-con-"))
    try:
        (t / "s.css").write_text(css, encoding="utf-8")
        html: list[Path] = []
        if markup:
            (t / "i.html").write_text(markup, encoding="utf-8")
            html = [t / "i.html"]
        out = dl.contracts(t, [t / "s.css"], html, [],
                           spec_light if spec_light is not None else {"icons.size.inline": "16px"})
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


if _fails:
    print(f"FAILED {len(_fails)} check(s):")
    for f in _fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("design_lint: all pure-logic checks passed")
raise SystemExit(0)
