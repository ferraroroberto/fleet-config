"""Unit tests for design_lint's icon contracts (fleet-config#284): emoji vs Lucide, comment and char-class immunity, vendored-bundle scope, the app-icon family and chevron placement.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_icon_contracts.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _suite import _h, check  # noqa: E402
import design_lint as dl  # noqa: E402

from design_lint_support import (  # noqa: E402
    APP_ICON_FILES, APP_ICON_INDEX, APP_ICON_MANIFEST, APP_ICON_SPEC, GOOD_CSS, HIT_SPEC_940, legacy_files, run_contracts,
)
from throwaway_dir import throwaway_dir  # noqa: E402


# ---- icon-set: emoji vs Lucide (design.md Icons, fleet-config#284) ----

_ic = Path(throwaway_dir("dl-icon-"))
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
_icjs = Path(throwaway_dir("dl-iconjs-"))
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

_ic394 = Path(throwaway_dir("dl-iconjs394-"))
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
_htree394 = Path(throwaway_dir("dl-html394-"))
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

_rb = Path(throwaway_dir("dl-regex416-"))
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
_vend = Path(throwaway_dir("dl-vendor416-"))
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


# ---- #940: third-party vendor/ CSS is out of scope for the app-authored
#      contracts, and `_vendored/` is emphatically still in scope ----

# Leaflet's real popup close button (home-automation#738): a hardcoded #757575
# on a `button` selector is a button-tiers FAIL, and 24x24 is under the 44px
# hit-target floor. Neither is home-automation's to fix.
LEAFLET_CSS = """
.leaflet-container a.leaflet-popup-close-button {
  position: absolute; top: 0; right: 0; border: none;
  width: 24px; height: 24px; color: #757575; background: transparent;
}
.leaflet-container a.leaflet-popup-close-button:focus { color: #585858; }
"""
# The same two violations, but in a project-scaffolding component we author.
FLEET_COMPONENT_CSS = """
.btn-scaffold { background: #ff0000; color: #ffffff; }
.scaffold-close-btn { width: 24px; height: 24px; }
"""
# Nav-contract signals living only inside `_vendored/` — the load-bearing
# proof that the exclusion did not turn a passing contract into a no-op.
VENDORED_NAV_CSS = """
body:has(dialog[open]) .tabs { visibility: hidden; }
.tabs { height: 100dvh; padding-bottom: env(safe-area-inset-bottom); }
@media (display-mode: standalone) {
  .app { position: fixed; top: 0; height: 100lvh; overflow-y: auto; }
}
"""



def _contracts_940(files: dict[str, str]) -> dict:
    t = Path(throwaway_dir("dl-940-"))
    try:
        for name, body in files.items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        css = sorted(t.rglob("*.css"))
        return {c["id"]: c for c in dl.contracts(t, css, [], [], HIT_SPEC_940)}
    finally:
        shutil.rmtree(t, ignore_errors=True)


third = _contracts_940({"app/webapp/static/vendor/leaflet/leaflet.css": LEAFLET_CSS})
check(third["button-tiers"]["status"] == "NA",
      "button-tiers: a third-party vendor/ stylesheet is not a button rule (#940)")
check(third["hit-target"]["status"] == "NA",
      "hit-target: a third-party vendor/ control is not an app touch target (#940)")

own = _contracts_940({"app/webapp/static/_vendored/btn/btn.css": FLEET_COMPONENT_CSS})
check(own["button-tiers"]["status"] == "FAIL" and "#ff0000" in own["button-tiers"]["detail"],
      "button-tiers: `_vendored/` is ours and stays fully in scope (#940) — "
      "`vendor` must not be read as a prefix of `_vendored`")
check(own["hit-target"]["status"] == "WARN",
      "hit-target: a compact control under `_vendored/` still WARNs (#940)")

nav940 = _contracts_940({"app/webapp/static/_vendored/nav/nav-tabs.css": VENDORED_NAV_CSS})
check(nav940["nav-contract"]["status"] == "PASS",
      "nav-contract still fires on CSS that lives only under `_vendored/` — "
      "the regression the #940 exclusion must not cause")

# the escapee accounting follows the same rule, so the two cannot diverge
_ad940 = Path(throwaway_dir("dl-ad940-"))
try:
    for name, body in (("app/webapp/static/vendor/leaflet/leaflet.css", LEAFLET_CSS),
                       ("app/webapp/static/_vendored/btn/btn.css", FLEET_COMPONENT_CSS)):
        path = _ad940 / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    fam940 = dl.adoption(_ad940, sorted(_ad940.rglob("*.css")))
    files940 = {e["file"] for e in fam940["color"]["escapees"]}
    check(all("/vendor/" not in f for f in files940),
          "adoption: third-party vendor/ literals are not escapees (#940) — "
          "the same exclusion the contracts apply")
    check(any("_vendored/btn" in f for f in files940),
          "adoption: `_vendored/` literals are still counted as escapees (#940)")
finally:
    shutil.rmtree(_ad940, ignore_errors=True)


# ---- app-icon-family: one generated Lucide master across install surfaces (#369) ----

app_icons_ok = run_contracts(
    GOOD_CSS, APP_ICON_INDEX, spec_light=APP_ICON_SPEC,
    html_name="index.html", files=APP_ICON_FILES,
)
check(app_icons_ok["app-icon-family"]["status"] == "PASS",
      "canonical generated PWA icon family -> PASS")

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

_h.report_and_exit("test_icon_contracts")
