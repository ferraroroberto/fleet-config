"""Unit tests for design_lint's core contract checks (fleet-config#277): the green-switch decision, focus ring, nav shell, viewport zoom lock, button tiers, theme and text-size settings, typography and the rendered leg.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_contracts_core.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _suite import _h, check  # noqa: E402
import design_lint as dl  # noqa: E402

from design_lint_support import (  # noqa: E402
    GOOD_CSS, OLD_SHELL_CSS, run_contracts,
)
from throwaway_dir import throwaway_dir  # noqa: E402


# ---- contract checks ----

good = run_contracts(GOOD_CSS, "<dialog class=\"d\"></dialog>")
check(good["focus-visible-ring"]["status"] == "PASS", "tokenized focus ring PASS")
check(good["reduced-motion"]["status"] == "PASS", "reduced motion PASS")
check(good["desktop-measure"]["status"] == "PASS", "772px measure PASS")
check(good["switch-on-accent"]["status"] == "PASS", "accent on-track PASS")
check(good["no-native-checkbox"]["status"] == "PASS", "no checkboxes PASS")
check(good["native-dialog"]["status"] == "PASS", "native dialog PASS")
check(good["nav-contract"]["status"] == "PASS", "nav signals + bar anchor PASS")
check("hand-carried" in good["nav-contract"]["detail"],
      "hand-carried nav with the full architecture passes on merit")

# signal-passing hand-carried nav WITHOUT the installed-app bar anchor: caps at
# WARN, never PASS.
NO_ANCHOR_CSS = """
body:has(dialog[open]) .tabs { visibility: hidden; }
.tabs { height: 100dvh; padding-bottom: env(safe-area-inset-bottom); }
"""
noanchor = run_contracts(NO_ANCHOR_CSS)
check(noanchor["nav-contract"]["status"] == "WARN",
      "all grep signals but no 100lvh bar anchor -> WARN, never PASS")
check("100lvh bar anchor" in noanchor["nav-contract"]["detail"],
      "no-anchor WARN names the missing anchor")

# vendored nav present but the standalone anchor missing -> still WARN (the
# vendored file's presence alone can't PASS; the CSS decides).
t = Path(throwaway_dir("dl-nav-"))
try:
    (t / "s.css").write_text(NO_ANCHOR_CSS, encoding="utf-8")
    vend = t / "app/webapp/static/_vendored/nav"
    vend.mkdir(parents=True)
    (vend / "nav-tabs.css").write_text("/* vendored */", encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(t, [t / "s.css"], [], [], {})}
    check(out["nav-contract"]["status"] == "WARN",
          "vendored nav without the standalone bar anchor -> WARN")
finally:
    shutil.rmtree(t, ignore_errors=True)

# The superseded fixed-inset .app scroller reports drift even when every other
# signal passes; it is a WARN so an adopter held on the old nav does not FAIL.
old = run_contracts(GOOD_CSS + OLD_SHELL_CSS)
check(old["nav-contract"]["status"] == "WARN" and "superseded standalone shell" in old["nav-contract"]["detail"],
      "old standalone-shell block -> WARN naming the superseded shell")
old_only = run_contracts(NO_ANCHOR_CSS + OLD_SHELL_CSS)
check(old_only["nav-contract"]["status"] == "WARN",
      "old shell without the new anchor -> WARN, not FAIL")

# standalone_bar_anchor_present: a `.tabs` rule with 100lvh and `bottom: auto`
# inside a display-mode:standalone block, nested or not.
NEW_ANCHOR = ("@media (pointer: coarse) and (max-width: 520px) {"
              " @media (display-mode: standalone) {"
              " .tabs { top: calc(100lvh - 82px); bottom: auto; } } }")
check(dl.standalone_bar_anchor_present(NEW_ANCHOR), "bar anchor detected in nested @media")
check(not dl.standalone_bar_anchor_present(
    "@media (display-mode: standalone) { .tabs { top: calc(100lvh - 82px); } }"),
    "no bottom: auto -> bar still bottom-anchored")
check(not dl.standalone_bar_anchor_present(
    "@media (display-mode: standalone) { .tabs { top: calc(100vh - 82px); bottom: auto; } }"),
    "no 100lvh -> not the large-viewport anchor")
check(not dl.standalone_bar_anchor_present(
    ".tabs { top: calc(100lvh - 82px); bottom: auto; }"),
    "anchor outside a standalone media block doesn't count")

# standalone_shell_present: the superseded markers must all sit inside a
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
    "no 100lvh sizing -> not the superseded architecture")
check(not dl.standalone_shell_present(
    ".app { position: fixed; height: 100lvh; overflow-y: auto; }"),
    "markers outside a standalone media block don't count")

BAD_CSS = """
.toggle.on { background: var(--success); }
.app { max-width: 1160px; }
"""
# fleet-config#994: a vendored component's scoped `outline: none` (action-row
# draws its ring on the wrapper via :focus-within) sorting ahead of the app's
# global tokenized ring must not mask it.
SCOPED_FIRST_CSS = """
.action-row-filter input:focus-visible { outline: none; }
.action-row-filter:focus-within { outline: 2px solid var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""
scoped_first = run_contracts(SCOPED_FIRST_CSS)
check(scoped_first["focus-visible-ring"]["status"] == "PASS",
      "scoped outline:none ahead of the global tokenized ring -> PASS (#994)")
scoped_only = run_contracts(".action-row-filter input:focus-visible { outline: none; color: var(--fg); }\n")
check(scoped_only["focus-visible-ring"]["status"] == "WARN",
      "only a scoped outline:none (a var() elsewhere in the body) -> WARN, not PASS (#994)")

bad = run_contracts(BAD_CSS, "<input type=\"checkbox\"><div class=\"modal\"></div>")
check(bad["focus-visible-ring"]["status"] == "FAIL", "missing focus ring FAIL")
check(bad["reduced-motion"]["status"] == "FAIL", "missing reduced motion FAIL")
check(bad["desktop-measure"]["status"] == "FAIL", "1160px cap is not near-772 -> FAIL")
check(bad["switch-on-accent"]["status"] == "FAIL",
      "green on-track FAILs (the accent decision is enforced, #1200)")
check(bad["no-native-checkbox"]["status"] == "FAIL", "native checkbox FAIL")
check(bad["native-dialog"]["status"] == "WARN", "hand-rolled modal WARN")
check(bad["nav-contract"]["status"] == "FAIL", "no nav signals FAIL")

near = run_contracts(".app { max-width: 780px; margin: 0 auto; }")
check(near["desktop-measure"]["status"] == "WARN", "near-772 cap WARNs with the value")

# a breakpoint is not the content measure, and the measure may come through a token (#1087)
_bp = run_contracts("@media (max-width: 720px) { .row { grid-template-columns: 1fr; } }\n")["desktop-measure"]
check(_bp["status"] == "FAIL", f"desktop-measure: a (max-width: 720px) media query is a breakpoint, not a cap -- {_bp}")
_tok = run_contracts(":root { --layout-measure: 772px; }\n.review-host { max-width: 820px; }\n"
                     "@media (min-width: 720px) {\n  .app { max-width: var(--layout-measure); margin: 0 auto; }\n}\n")["desktop-measure"]
check(_tok["status"] == "PASS" and (_tok.get("evidence") or "").endswith(":4"),
      f"desktop-measure: max-width through a 772px custom property is the fleet measure (#1087) -- {_tok}")
check(near["switch-on-accent"]["status"] == "NA", "no switch -> NA")

# toast-neutral: a toast is the neutral frosted surface; only an error tints (#1200)
_t_ok = run_contracts(".toast { background: var(--tabbar-bg); border: 1px solid var(--tabbar-border); }\n"
                      ".toast.error { background: var(--danger); border-color: var(--danger); }\n")["toast-neutral"]
check(_t_ok["status"] == "PASS", f"toast-neutral: a neutral toast with a red error variant PASSes -- {_t_ok}")
for _label, _css in (
        ("a success-variant border on a token", ".toast { border: 1px solid var(--tabbar-border); }\n.toast.success { border-color: var(--success); }\n"),
        ("a green hex border", ".toast { border: 1px solid #1a7f37; }\n"),
        ("a green rgba fill (snackbar)", ".snackbar-ok { background: rgba(26, 127, 55, 0.2); }\n"),
        ("the --on alias in a color-mix", ".toast-saved { background: color-mix(in srgb, var(--on) 20%, transparent); }\n")):
    _t_bad = run_contracts(_css)["toast-neutral"]
    check(_t_bad["status"] == "FAIL" and _t_bad.get("evidence"), f"toast-neutral: {_label} FAILs with evidence -- {_t_bad}")
check(run_contracts(".toast { background: #0969da; }\n")["toast-neutral"]["status"] == "PASS",
      "toast-neutral: a blue (non-green) colour is not this check's business")
check(run_contracts(".row.success { border-color: var(--success); }\n")["toast-neutral"]["status"] == "NA",
      "toast-neutral: green on a non-toast rule is not a toast; no toast rule -> NA")

# icon-button-unpainted: the vendored header toggles and modal close draw no fill at rest (#1259)
_HEAD = ".home-head .home-toggle { background: var(--close-bg, transparent); }\n"
for _label, _css in (
        ("--close-bg: transparent in both themes",
         ":root { --close-bg: transparent; }\n[data-theme=dark] { --close-bg: transparent; }\n" + _HEAD),
        ("a zero-alpha rgba", ":root { --close-bg: rgba(0, 0, 0, 0); }\n" + _HEAD),
        ("--close-bg left unset (the scaffold default)", _HEAD)):
    _ib = run_contracts(_css)["icon-button-unpainted"]
    check(_ib["status"] == "PASS", f"icon-button-unpainted: {_label} PASSes -- {_ib}")
for _label, _css in (
        ("the canvas-subtle fill in light", ":root { --close-bg: var(--card-off); }\n" + _HEAD),
        ("a dark-only literal fill", ":root { --close-bg: transparent; }\n[data-theme=dark] { --close-bg: #30363d; }\n" + _HEAD)):
    _ib = run_contracts(_css)["icon-button-unpainted"]
    check(_ib["status"] == "FAIL" and (_ib.get("evidence") or "").startswith("s.css:"),
          f"icon-button-unpainted: {_label} FAILs with evidence -- {_ib}")
check(run_contracts(".card { background: var(--card); }\n")["icon-button-unpainted"]["status"] == "NA",
      "icon-button-unpainted: no --close-bg declared or read -> NA")

# vendored-tokens: a declared vendored component must not read a token the app never
# defines (#1290). The fixture is the real failure: icon-button.css reads
# --icon-inline with no fallback (the glyph collapses to 0) and --hit-min with one.
_VT_DIR = "app/webapp/static/_vendored/icon-button"
_VT_CSS = """.icon-button {
  width: var(--icon-btn-box, 28px);
  border-radius: var(--radius-sm, 8px);
  color: var(--muted);
}
.icon-button::before { inset: min(0px, calc((var(--icon-btn-box, 28px) - var(--hit-min, 44px)) / 2)); }
.icon-button .icon {
  width: var(--icon-inline);
}
"""
_VT_README = """# Icon button

## Required design tokens

| Token | Light value | Used for |
| --- | --- | --- |
| `--muted` | `#656d76` | glyph at rest |
| `--icon-inline` | `16px` | glyph size |
| `--radius-sm` (fallback `8px`) | `8px` | box corners |
| `--hit-min` (fallback `44px`) | `44px` | target floor |

`--icon-btn-box` (default `28px`) is a per-context knob, not a design token.
"""
_VT_TOML = (f'[vendored]\nicon-button = {{ src = "{_VT_DIR}", sha = "abc", dest = "{_VT_DIR}" }}\n'
            'classify_e2e = { src = "scripts/classify_e2e.py", sha = "abc", dest = "scripts/classify_e2e.py" }\n')
_VT_ALL = ":root { --muted: #656d76; --icon-inline: 16px; --radius-sm: 8px; --hit-min: 44px; }\n"


def run_vendored_tokens(app_css: str, *, toml: str | None = _VT_TOML, comp_css: str = _VT_CSS,
                        readme: str | None = _VT_README, markup: str = "", js: str = "") -> dict:
    t = Path(throwaway_dir("dl-vt-"))
    try:
        files = {"app/webapp/static/styles.css": app_css, f"{_VT_DIR}/icon-button.css": comp_css,
                 "scripts/classify_e2e.py": "pass\n"}
        if toml is not None:
            files[".fleet.toml"] = toml
        if readme is not None:
            files[f"{_VT_DIR}/README.md"] = readme
        if markup:
            files["app/webapp/static/index.html"] = markup
        if js:
            files["app/webapp/static/app.js"] = js
        for name, body in files.items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        out = dl.contracts(t, sorted(t.rglob("*.css")), sorted(t.rglob("*.html")),
                           sorted(t.rglob("*.js")), {"icons.size.inline": "16px"})
        rows = [c for c in out if c["id"] == "vendored-tokens"]
        check(len(rows) == 1, f"vendored-tokens: exactly one row -- {rows}")
        return rows[0] if rows else {"status": None, "detail": "", "evidence": None}
    finally:
        shutil.rmtree(t, ignore_errors=True)


_vt = run_vendored_tokens(_VT_ALL)
check(_vt["status"] == "PASS", f"vendored-tokens: every token the component reads is defined -> PASS -- {_vt}")
check("--icon-btn-box" not in _vt["detail"],
      f"vendored-tokens: a README-documented per-context knob is never a gap -- {_vt}")

# home-automation before #828: no --icon-inline anywhere -> FAIL naming it, at the read site
_vt = run_vendored_tokens(_VT_ALL.replace(" --icon-inline: 16px;", ""))
check(_vt["status"] == "FAIL" and "--icon-inline" in _vt["detail"]
      and _vt["evidence"] == f"{_VT_DIR}/icon-button.css:8",
      f"vendored-tokens: an undefined token with no fallback FAILs by name at its var() -- {_vt}")

# app-launcher before #1417: no --hit-min, but the component falls back to 44px -> WARN
_vt = run_vendored_tokens(_VT_ALL.replace(" --hit-min: 44px;", ""))
check(_vt["status"] == "WARN" and "--hit-min" in _vt["detail"] and "44px" in _vt["detail"],
      f"vendored-tokens: an undefined token the component falls back on only WARNs -- {_vt}")

_vt = run_vendored_tokens(_VT_ALL.replace(" --icon-inline: 16px;", "").replace(" --hit-min: 44px;", ""))
check(_vt["status"] == "FAIL" and "--icon-inline" in _vt["detail"] and "--hit-min" in _vt["detail"],
      f"vendored-tokens: a FAIL still lists the fallback-only gaps beside it -- {_vt}")

# a definition anywhere the app ships counts: another stylesheet, the component's own
# CSS, an inline style attribute, or a JS setProperty
_no_inline = _VT_ALL.replace(" --icon-inline: 16px;", "")
for _label, _kwargs in (
        ("the component's own CSS", {"comp_css": _VT_CSS + ".icon-button { --icon-inline: 16px; }\n"}),
        ("an inline style attribute", {"markup": '<div style="--icon-inline: 16px"></div>\n'}),
        ("a JS setProperty", {"js": "document.body.style.setProperty('--icon-inline', '16px');\n"})):
    _vt = run_vendored_tokens(_no_inline, **_kwargs)
    check(_vt["status"] == "PASS", f"vendored-tokens: a definition in {_label} counts -- {_vt}")

# a nested fallback is only needed when the outer token is undefined
_nested = ".x { width: var(--icon-inline, var(--icon-size)); }\n"
_vt = run_vendored_tokens(_VT_ALL, comp_css=_nested, readme=None)
check(_vt["status"] == "PASS", f"vendored-tokens: outer defined -> the nested fallback is never read -- {_vt}")
_vt = run_vendored_tokens(":root { --x: 1; }\n", comp_css=_nested, readme=None)
check(_vt["status"] == "FAIL" and "--icon-size" in _vt["detail"],
      f"vendored-tokens: outer undefined -> the nested var() is read, and its own gap FAILs -- {_vt}")

# the README table is a cross-check, never the source: drift is reported, and the
# status still comes from the CSS
_vt = run_vendored_tokens(_VT_ALL + ":root { --ink: #000; }\n",
                          comp_css=_VT_CSS + ".icon-button:hover { color: var(--ink); }\n")
check(_vt["status"] == "PASS" and "README" in _vt["detail"] and "--ink" in _vt["detail"],
      f"vendored-tokens: a token the CSS reads but the README table omits is noted -- {_vt}")
_vt = run_vendored_tokens(_VT_ALL, readme=_VT_README.replace("| `--muted` |", "| `--line` | x | y |\n| `--muted` |"))
check(_vt["status"] == "PASS" and "README" not in _vt["detail"] and "---" not in _vt["detail"],
      f"vendored-tokens: a README row the CSS never reads is not drift (a container's table lists "
      f"its controls' tokens too), and the |---| separator row is no token -- {_vt}")

# without the knob sentence, --icon-btn-box is an ordinary fallback-only gap
_vt = run_vendored_tokens(_VT_ALL, readme=_VT_README.replace("per-context knob", "size"))
check(_vt["status"] == "WARN" and "--icon-btn-box" in _vt["detail"],
      f"vendored-tokens: only a README-documented knob is exempt -- {_vt}")

# the prose rule takes the plural too: the nav README's paragraph says "knob settings" (#1301)
_vt = run_vendored_tokens(_VT_ALL, readme=_VT_README.replace("per-context knob,", "per-context knobs,"))
check(_vt["status"] == "PASS" and "--icon-btn-box" not in _vt["detail"],
      f"vendored-tokens: 'knobs' exempts a token the same as 'knob' -- {_vt}")

# a token-table row marked optional is exempt, no prose paragraph needed (#1301); the row's
# own text is the signal, so only that row's tokens are exempt
_no_hit = _VT_ALL.replace(" --hit-min: 44px;", "")
_opt_row = "| `--hit-min` | `44px` | target floor — optional, falls back to `44px` |"
_vt = run_vendored_tokens(_no_hit, readme=_VT_README.replace("| `--hit-min` (fallback `44px`) | `44px` | target floor |", _opt_row))
check(_vt["status"] == "PASS",
      f"vendored-tokens: a README table row marked optional exempts its token -- {_vt}")
_vt = run_vendored_tokens(_no_hit.replace(" --radius-sm: 8px;", ""),
                          readme=_VT_README.replace("| `--hit-min` (fallback `44px`) | `44px` | target floor |", _opt_row))
check(_vt["status"] == "WARN" and "--radius-sm" in _vt["detail"] and "--hit-min" not in _vt["detail"],
      f"vendored-tokens: an optional row never exempts the unmarked row beside it -- {_vt}")

for _label, _toml in (("no .fleet.toml", None),
                      ("no [vendored] table", 'layer = "working-web"\n'),
                      ("only non-component entries",
                       '[vendored]\nclassify_e2e = { src = "scripts/classify_e2e.py", sha = "a", dest = "scripts/classify_e2e.py" }\n')):
    _vt = run_vendored_tokens(_no_inline, toml=_toml)
    check(_vt["status"] == "NA", f"vendored-tokens: {_label} -> NA -- {_vt}")
_vt = run_vendored_tokens(_VT_ALL, toml="[vendored\n")
check(_vt["status"] == "FAIL" and ".fleet.toml" in _vt["detail"],
      f"vendored-tokens: an unreadable .fleet.toml is never a pass -- {_vt}")

# --on is an app's own alias: judge the colour it resolves to, never the name (#1200)
_on_acc = run_contracts(":root { --on: var(--accent); }\n.toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_on_acc["status"] == "PASS", f"switch-on-accent: --on aliasing the accent PASSes -- {_on_acc}")
_on_grn = run_contracts(":root { --on: var(--success); }\n.toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_on_grn["status"] == "FAIL", f"switch-on-accent: --on aliasing success FAILs -- {_on_grn}")
# the fleet's real shape: --on is a literal per theme (`--on: #1a7f37; /* success */`), judged by the colour it is
_lit_green = run_contracts(":root { --accent: #0969da; --on: #1a7f37; }\n[data-theme=dark] { --accent: #2f81f7; --on: #3fb950; }\n"
                           ".toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_lit_green["status"] == "FAIL", f"switch-on-accent: --on as a green literal in both themes FAILs -- {_lit_green}")
_lit_acc = run_contracts(":root { --accent: #0969da; --accent-fill: #0969da; --on: #0969da; }\n[data-theme=dark] { --accent: #2f81f7; --accent-fill: #1f6feb; --on: #1f6feb; }\n"
                         ".toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_lit_acc["status"] == "PASS", f"switch-on-accent: --on equal to the app's own accent literals PASSes -- {_lit_acc}")
_lit_mixed = run_contracts(":root { --accent: #0969da; --on: #0969da; }\n[data-theme=dark] { --accent: #2f81f7; --on: #3fb950; }\n"
                           ".toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_lit_mixed["status"] == "FAIL", f"switch-on-accent: green in either theme is a FAIL, not a PASS -- {_lit_mixed}")
_lit_other = run_contracts(":root { --on: #8250df; }\n.toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_lit_other["status"] == "WARN", f"switch-on-accent: a literal that is neither green nor the accent WARNs -- {_lit_other}")
_on_lit = run_contracts(".toggle.on { background: #1a7f37; }\n")["switch-on-accent"]
check(_on_lit["status"] == "WARN", f"switch-on-accent: an untokenized colour WARNs, never PASSes -- {_on_lit}")
_on_unk = run_contracts(".toggle.on { background: var(--on); }\n")["switch-on-accent"]
check(_on_unk["status"] == "WARN", f"switch-on-accent: an unresolvable --on WARNs, never PASSes -- {_on_unk}")

# width follows the shape of the view: declared wide views turn the no-cap FAIL into a WARN (#1113)
_WIDE = '[design]\nwide_views = ["board", "table"]\n'
_nocap = "@media (max-width: 720px) { .row { grid-template-columns: 1fr; } }\n"
_w = run_contracts(_nocap, files={".fleet.toml": _WIDE})["desktop-measure"]
check(_w["status"] == "WARN" and "board, table" in _w["detail"] and "LAYOUT-06" in _w["detail"],
      f"desktop-measure: no cap plus declared wide views WARNs and names them -- {_w}")
check(run_contracts(_nocap, files={".fleet.toml": 'layer = "x"\n'})["desktop-measure"]["status"] == "FAIL",
      "desktop-measure: a .fleet.toml with no wide_views leaves the FAIL")
_wb = run_contracts(_nocap, files={".fleet.toml": "[design]\nwide_views = [\n"})["desktop-measure"]
check(_wb["status"] == "FAIL" and "unparseable" in _wb["detail"],
      f"desktop-measure: an unreadable declaration stays a FAIL, unknown is never a pass -- {_wb}")
check(run_contracts(".app { max-width: 772px; margin: 0 auto; }", files={".fleet.toml": _WIDE})["desktop-measure"]["status"] == "PASS",
      "desktop-measure: a real 772px cap still PASSes when wide views are declared")

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

# ---- status-bar style: `default` so the installed app starts with its final viewport ----

SB_DEFAULT = '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
SB_TRANSLUCENT = '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
sb = run_contracts(GOOD_CSS, SB_DEFAULT, html_name="index.html")
check(sb["status-bar-style"]["status"] == "PASS", "status-bar style default PASS")
sb2 = run_contracts(GOOD_CSS, SB_TRANSLUCENT, html_name="index.html")
check(sb2["status-bar-style"]["status"] == "WARN" and "black-translucent" in sb2["status-bar-style"]["detail"],
      "black-translucent WARNs (drift, not FAIL) and names the value")
sb3 = run_contracts(GOOD_CSS, "<dialog></dialog>", html_name="index.html")
check(sb3["status-bar-style"]["status"] == "WARN", "missing status-bar meta WARN")
sb4 = run_contracts(GOOD_CSS, "<!-- " + SB_TRANSLUCENT + " -->" + SB_DEFAULT, html_name="index.html")
check(sb4["status-bar-style"]["status"] == "PASS", "a commented-out black-translucent is ignored")
check(good["status-bar-style"]["status"] == "NA", "no index.html -> status-bar-style NA")

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
bt_tint_text = run_contracts(
    ".big-btn { background: var(--accent-soft); color: var(--accent-text); "
    "border: 1px solid var(--accent-border-soft); }")
check(bt_tint_text["button-tiers"]["status"] == "PASS",
      "tint recipe with the contrast-safe accent-text PASSes (#963)")
bt_fill_stray = run_contracts(".run-btn { background: var(--accent-fill); color: var(--accent-fg); }")
check(bt_fill_stray["button-tiers"]["status"] == "WARN"
      and ".run-btn" in bt_fill_stray["button-tiers"]["detail"],
      "solid accent-fill outside the primary WARNs like solid accent (#963)")
bt_fill_primary = run_contracts(".button-primary { background: var(--accent-fill); color: var(--accent-fg); }")
check(bt_fill_primary["button-tiers"]["status"] == "PASS",
      "the primary may fill with accent-fill (#963)")

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

# ---- user text-size setting (fleet-config#967): WARN-only while apps adopt ----

TS_BOOT = ("<head><script>(function(){"
           "var s = localStorage.getItem('app.textsize');"
           "if (s) document.documentElement.dataset.textsize = s;"
           "})();</script>")
TS_CONTROL = "</head><body><script>localStorage.setItem('app.textsize', 'large');</script></body>"
ts_ok = run_contracts(GOOD_CSS, TS_BOOT + TS_CONTROL, html_name="index.html")
check(ts_ok["text-size"]["status"] == "PASS", "pre-paint data-textsize stamp + persisted control PASS")
TS_CONST = ("<head><script>var TEXTSIZE_KEY = 'app.textsize';"
            "document.documentElement.setAttribute('data-textsize', localStorage.getItem(TEXTSIZE_KEY) || 'default');"
            "</script></head><body><script>localStorage.setItem(TEXTSIZE_KEY, 'small');</script></body>")
ts_const = run_contracts(GOOD_CSS, TS_CONST, html_name="index.html")
check(ts_const["text-size"]["status"] == "PASS", "setAttribute stamp + named-constant key PASS")
ts_none = run_contracts(GOOD_CSS, "<head></head><body></body>", html_name="index.html")
check(ts_none["text-size"]["status"] == "WARN" and "not adopted" in ts_none["text-size"]["detail"],
      "no text-size mechanism WARNs (never FAIL) as not adopted")
ts_noboot = run_contracts(GOOD_CSS, "<head>" + TS_CONTROL, html_name="index.html")
check(ts_noboot["text-size"]["status"] == "WARN" and "no pre-paint" in ts_noboot["text-size"]["detail"],
      "control without the pre-paint stamp WARNs (reflow)")
ts_nocontrol = run_contracts(GOOD_CSS, TS_BOOT + "</head><body></body>", html_name="index.html")
check(ts_nocontrol["text-size"]["status"] == "WARN" and "no persisted control" in ts_nocontrol["text-size"]["detail"],
      "stamp without a persisted control WARNs")
check(good["text-size"]["status"] == "NA", "no index.html -> text-size NA")

# built storage keys (#1087): the scaffold's own boot script reads `app + '.theme'` /
# `app + '.textsize'`, and its vendored text-size.js writes through a `storageKey(app)` helper
SCAFFOLD_BOOT = ("<head><script>(function () { var app = 'my-app'; var root = document.documentElement;"
                 "var theme = null; var size = null;"
                 "try { theme = localStorage.getItem(app + '.theme'); size = localStorage.getItem(app + '.textsize'); } catch (e) {}"
                 "var dark = theme ? theme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;"
                 "root.dataset.theme = dark ? 'dark' : 'light';"
                 "root.dataset.textsize = size === 'small' || size === 'large' ? size : 'default'; })();</script>")
BUILT_CONTROLS = ("</head><body><script>"
                  "function storageKey(app) { return app + '.textsize'; }"
                  "localStorage.setItem(storageKey(APP), size);"
                  "localStorage.setItem(APP + '.theme', next);"
                  "</script></body>")
built = run_contracts(GOOD_CSS, SCAFFOLD_BOOT + TT_METAS + BUILT_CONTROLS, spec_light=TT_SPEC,
                      spec_dark=TT_DARK, html_name="index.html")
check(built["theme-toggle"]["status"] == "PASS",
      f"theme-toggle: `app + '.theme'` boot read and toggle write PASS (#1087) -- {built['theme-toggle']}")
check(built["text-size"]["status"] == "PASS",
      f"text-size: `app + '.textsize'` boot read and a `storageKey(app)` helper write PASS (#1087) -- {built['text-size']}")
TMPL = ("<head><script>var t = localStorage.getItem(`${app}.theme`);"
        "document.documentElement.dataset.theme = t || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');"
        "</script>" + TT_METAS + "</head><body><script>localStorage.setItem(`${app}.theme`, t);</script></body>")
tmpl = run_contracts(GOOD_CSS, TMPL, spec_light=TT_SPEC, spec_dark=TT_DARK, html_name="index.html")
check(tmpl["theme-toggle"]["status"] == "PASS", f"theme-toggle: a template-literal key PASSes (#1087) -- {tmpl['theme-toggle']}")
UNRELATED = ("</head><body><script>function storageKey(app) { return app + '.session'; }"
             "localStorage.setItem(storageKey(APP), id); localStorage.setItem(APP + '.session', id);</script></body>")
unrel = run_contracts(GOOD_CSS, TS_BOOT + UNRELATED, html_name="index.html")
check(unrel["text-size"]["status"] == "WARN" and "no persisted control" in unrel["text-size"]["detail"],
      f"text-size: a helper or concatenation building another key is not the control -- {unrel['text-size']}")

# ---- typography, glyph icons, spec contrast, rendered leg (fleet-config#969) ----
# every new check lands as WARN (never FAIL): the apps it flags fix it in their own lanes

ff_ok = run_contracts(GOOD_CSS + "\nbutton, input,\nselect, textarea { font: inherit; }")
check(ff_ok["form-font-inherit"]["status"] == "PASS", "global font: inherit on all four form tags PASS")
ff_family = run_contracts(GOOD_CSS + "\nbutton, input, select, textarea { font-family: inherit; }")
check(ff_family["form-font-inherit"]["status"] == "PASS", "font-family: inherit also counts")
ff_scoped = run_contracts(GOOD_CSS + "\n.card button, input { font: inherit; }")
check(ff_scoped["form-font-inherit"]["status"] == "WARN"
      and "button" in ff_scoped["form-font-inherit"]["detail"]
      and "input" not in ff_scoped["form-font-inherit"]["detail"].split("for:")[1].split("(")[0],
      "a scoped `.card button` is not the global reset; only the missing tags are named")
check(good["form-font-inherit"]["status"] == "WARN", "no reset at all WARNs")

UP_SPEC = {"icons.size.inline": "16px", "typography.overline.textTransform": "uppercase"}
up_bad = run_contracts(GOOD_CSS + "\n.chip { text-transform: uppercase; letter-spacing: .09em; }", spec_light=UP_SPEC)
check(up_bad["uppercase-role"]["status"] == "WARN" and ".chip" in up_bad["uppercase-role"]["detail"],
      "uppercase chip outside the overline role WARNs with the selector")
up_ok = run_contracts(GOOD_CSS + "\n.list-overline { text-transform: uppercase; }", spec_light=UP_SPEC)
check(up_ok["uppercase-role"]["status"] == "PASS", "uppercase on the legal overline role PASS")
up_nolegal = run_contracts(GOOD_CSS + "\n.list-overline { text-transform: uppercase; }")
check(up_nolegal["uppercase-role"]["status"] == "WARN", "with no legal caps role in the spec, any uppercase WARNs")

ba_bad = run_contracts(GOOD_CSS + "\n.coding-name { word-break: break-all; }")
check(ba_bad["break-all"]["status"] == "WARN" and ".coding-name" in ba_bad["break-all"]["detail"],
      "break-all on a name WARNs with the selector")
ba_ok = run_contracts(GOOD_CSS + "\n.file-path, .commit-sha { word-break: break-all; }")
check(ba_ok["break-all"]["status"] == "PASS", "break-all on path/sha selectors PASS")

gl_bad = run_contracts(GOOD_CSS, '<button class="refresh">↻</button>', js="el.textContent = '▾ ' + label;")
check(gl_bad["glyph-icons"]["status"] == "WARN" and "i.html:1" in gl_bad["glyph-icons"]["detail"]
      and "s.js:1" in gl_bad["glyph-icons"]["detail"],
      "an arrow in markup and a caret in a JS UI string both WARN")
gl_ok = run_contracts(GOOD_CSS, '<button class="refresh"><svg><use href="#i-refresh"/></svg></button>',
                      js="const RE = /^[●○]$/; // → a comment arrow")
check(gl_ok["glyph-icons"]["status"] == "PASS", "Lucide markup, regex literals and comments are not glyph icons")

SC_BAD = {"colors.card": "#ffffff", "colors.accent": "#0969da",
          "colors.accent-soft": "color-mix(in srgb, var(--accent) 16%, transparent)",
          "components.button-tint.backgroundColor": "color-mix(in srgb, var(--accent) 16%, transparent)",
          "components.button-tint.textColor": "#0969da"}
sc_bad = run_contracts(GOOD_CSS, spec_light=SC_BAD)
check(sc_bad["spec-contrast"]["status"] == "WARN" and "button-tint 4.13" in sc_bad["spec-contrast"]["detail"],
      "the pre-#963 accent-on-accent-soft pair WARNs at 4.13")
sc_ok = run_contracts(GOOD_CSS, spec_light=dict(SC_BAD, **{"components.button-tint.textColor": "#0550ae"}))
check(sc_ok["spec-contrast"]["status"] == "PASS", "accent-text on the tint PASS")
check(good["spec-contrast"]["status"] == "NA", "a spec with no component pairs -> NA")

rl_none = run_contracts(GOOD_CSS, "<html></html>", html_name="index.html")
check(rl_none["rendered-leg"]["status"] == "WARN" and "unmeasured" in rl_none["rendered-leg"]["detail"],
      "no rendered-geometry helper -> rendered leg unmeasured WARN")
rl_ok = run_contracts(GOOD_CSS, "<html></html>", html_name="index.html",
                      files={"tests/e2e/_geometry.py": "# helper\n"})
check(rl_ok["rendered-leg"]["status"] == "PASS", "tests/e2e/_geometry.py present -> PASS")
check(good["rendered-leg"]["status"] == "NA", "no index.html -> rendered-leg NA")

_h.report_and_exit("test_contracts_core")
