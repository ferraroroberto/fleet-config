"""Unit tests for design_lint's layout contracts: nav nesting, the row-height scale and its false positives, the evidence file:line, and the native-checkbox ban.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_layout_contracts.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _suite import _h, check  # noqa: E402
import design_lint as dl  # noqa: E402

from design_lint_support import (  # noqa: E402
    GOOD_CSS, run_contracts,
)
from throwaway_dir import throwaway_dir  # noqa: E402


# ---- nav-nesting: nav.tabs must be a <body> sibling of main.app, never
#      nested inside it (_vendored/nav/README.md; app-launcher#369, #284) ----

_nn = Path(throwaway_dir("dl-navnest-"))
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


# ---- row-height-scale false positives (fleet-config#843) ----
# Verbatim from task-os `1e5723d`, the CSS that produced 9 hits of which 2
# were real: `.trow` is the row, `.trow-*` are the parts inside it.
TROW_CSS = """
.trow-status { height: 30px; }
.trow-prio::before { height: 6px; }
.trow-meta { min-height: 24px; }
.trow-check { height: 20px; }
.trow-meta .trow-folder,
.trow-meta .trow-ai { height: 18px; }
.c-status .trow-status { height: 30px; }
.link-row { min-height: 34px; }
.blocker-row { min-height: 34px; }
"""
rh_parts = run_contracts(TROW_CSS)
check(rh_parts["row-height-scale"]["status"] == "WARN",
      "task-os fixture still WARNs on the two genuine row strays")
check(rh_parts["row-height-scale"]["detail"].endswith("scale: 34pxx2"),
      "only the 2 real hits survive — .link-row + .blocker-row at 34px (#843)")
for part in ("30px", "6px", "24px", "20px", "18px"):
    check(part not in rh_parts["row-height-scale"]["detail"],
          f"a part inside a row ({part}) is not a row-height finding (#843)")

check(run_contracts(".trow-status { height: 30px; }")["row-height-scale"]["status"] == "NA",
      "`.trow-status` alone is no row rule at all, not a PASS-by-luck")
check(run_contracts(".trow { height: 30px; }")["row-height-scale"]["status"] == "WARN",
      "`.trow` IS the row container — a prefixed block name still counts")
check(run_contracts(".trows > .trow:first-child { height: 30px; }"
                    )["row-height-scale"]["status"] == "WARN",
      "pseudo-classes and a descendant scope don't hide the row target")
check(run_contracts(".row--stack { height: 30px; }")["row-height-scale"]["status"] == "WARN",
      "a BEM modifier of a row is still a row")
check(run_contracts(".dropdown-arrow { height: 30px; }")["row-height-scale"]["status"] == "NA",
      "`.arrow` merely ends in those three letters")
check(run_contracts(".task-row .check { height: 30px; }")["row-height-scale"]["status"] == "NA",
      "a row-scoped part is the part's size, not the row's")

# the WARN says so when it truncates (#843)
rh_many = run_contracts("".join(f".r{i}-row {{ height: {30 + i}px; }}" for i in range(9)))
check("showing 8 of 9 distinct values" in rh_many["row-height-scale"]["detail"],
      "a truncated stray list says how many it left out (#843)")
rh_few = run_contracts(".list-row { height: 47px; }")
check("showing" not in rh_few["row-height-scale"]["detail"],
      "an untruncated list says nothing about truncation")


# ---- the /*FILE ...*/ delimiter is not part of the selector (#843) ----
# `_BLOCK_RE` group 1 runs from the previous `}`, so it also carries the
# marker `contracts()` injects — a *path* with "row" or "icon" in it must not
# make that file's first rule a row or an icon rule.
_fm = Path(throwaway_dir("dl-marker-"))
try:
    for name in ("_vendored/row/row.css", "_vendored/icons/icons.css"):
        path = _fm / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(".panel { height: 30px; width: 30px; }", encoding="utf-8")
    marker = {c["id"]: c for c in dl.contracts(
        _fm, sorted(_fm.rglob("*.css")), [], [],
        {"icons.size.inline": "16px"})}
    check(marker["row-height-scale"]["status"] == "NA",
          "a stylesheet under _vendored/row/ is no row rule (#843)")
    check(marker["icon-sizes"]["status"] == "PASS",
          "a stylesheet under _vendored/icons/ is no icon rule (#843)")
finally:
    shutil.rmtree(_fm, ignore_errors=True)


# ---- evidence file:line is the true line within the marked file (#858) ----
# `contracts()` prefixes each file with `/*FILE <rel>*/\n`; the newline ending
# that marker line is not part of the file, so it must not shift the count.
from design_lint.contracts._ctx import _evidence, _loc_at  # noqa: E402

_ev_blob = "/*FILE a.css*/\nFIRST needle\n/*FILE b.css*/\nL1\nL2\nL3 needle"
check(_evidence(_ev_blob, r"FIRST needle") == "a.css:1",
      "_evidence: a match on a file's first content line reports line 1 (#858)")
check(_evidence(_ev_blob, r"L3 needle") == "b.css:3",
      "_evidence: a match on a later line reports that line (#858)")
check(_loc_at(_ev_blob, _ev_blob.index("FIRST")) == "a.css:1",
      "_loc_at: a first-content-line position reports line 1 (#858)")
check(_loc_at(_ev_blob, _ev_blob.index("L3")) == "b.css:3",
      "_loc_at: a later-line position reports that line (#858)")
check(_evidence("x\ny needle", r"needle") == "?:2"
      and _loc_at("needle", 0) == "?:1",
      "bare blob with no marker keeps counting from line 1 (#858)")

_fl = Path(throwaway_dir("dl-evline-"))
try:
    (_fl / "a.css").write_text(".x { color: red; }\n.y { margin: 0; }\n"
                               "*:focus-visible { outline: 2px solid; }\n",
                               encoding="utf-8")
    evline = {c["id"]: c for c in dl.contracts(_fl, [_fl / "a.css"], [], [], {})}
    check(evline["focus-visible-ring"]["evidence"] == "a.css:3",
          "end to end: contracts() evidence names the real line (#858)")
finally:
    shutil.rmtree(_fl, ignore_errors=True)


# ---- no-native-checkbox (design.md Base UI mapping, fleet-config#843) ----
# design.md maps Checkbox -> shadcn `checkbox`, which IS a real
# <input type=checkbox> skinned with `appearance: none`. Fixtures verbatim
# from task-os `1e5723d`: app.js:1267 is a selector *string* (the old false
# positive), and the three real controls are built in JS (the old misses).
CB_SELECTOR_STRING_JS = """
document.addEventListener('keydown', (ev) => {
  if (ev.target.closest('textarea, select, input:not([type="checkbox"])')) return;
});
"""
CB_BUILT_JS = """
const cb = document.createElement('input');
cb.type = 'checkbox';
cb.className = 'check';
const box = document.createElement('input');
box.type = 'checkbox';
const third = { type: 'checkbox' };
"""
CB_SKIN_CSS = ".check { appearance: none; -webkit-appearance: none; width: 16px; }"

cb_str = run_contracts(GOOD_CSS, js=CB_SELECTOR_STRING_JS)
check(cb_str["no-native-checkbox"]["status"] == "PASS"
      and cb_str["no-native-checkbox"]["detail"] == "no checkbox controls",
      "an attribute selector inside a JS string is not a control (#843)")

cb_ok = run_contracts(GOOD_CSS + CB_SKIN_CSS, js=CB_BUILT_JS)
check(cb_ok["no-native-checkbox"]["status"] == "PASS", "a skinned checkbox PASSes (#843)")
check("3 checkbox control(s)" in cb_ok["no-native-checkbox"]["detail"],
      "JS-built controls are counted — the three the old rule never saw (#843)")
check("`.check`" in cb_ok["no-native-checkbox"]["detail"],
      "the PASS names the skin rule it found")

cb_raw = run_contracts(GOOD_CSS, js=CB_BUILT_JS)
check(cb_raw["no-native-checkbox"]["status"] == "FAIL",
      "no `appearance: none` anywhere -> the browser tick ships -> FAIL")

# voice-transcriber's real shape: the control's class is resolvable and
# nothing targeting it is skinned, so the FAIL is provable and names it.
CB_UNSKINNED_JS = """
const checkbox = document.createElement('input');
checkbox.type = 'checkbox';
checkbox.className = 'select-checkbox';
"""
cb_named = run_contracts(GOOD_CSS + ".select-native { appearance: none; }",
                         js=CB_UNSKINNED_JS)
check(cb_named["no-native-checkbox"]["status"] == "FAIL",
      "an `appearance: none` on some *other* control is not the checkbox's skin")
check(".select-checkbox" in cb_named["no-native-checkbox"]["detail"],
      "the FAIL names the class that should have carried the skin")

# only the control's own receiver counts — a sibling's class must not make a
# stray `appearance: none` look like the checkbox's skin
CB_SIBLING_JS = """
const label = document.createElement('label');
label.className = 'filter-select';
const cb = document.createElement('input');
cb.type = 'checkbox';
cb.className = 'select-checkbox';
"""
cb_sibling = run_contracts(GOOD_CSS + ".filter-select { appearance: none; }",
                           js=CB_SIBLING_JS)
check(cb_sibling["no-native-checkbox"]["status"] == "FAIL",
      "a neighbouring element's skin is not the checkbox's skin")

# class unresolvable + a skin somewhere -> the honest "couldn't establish"
cb_opaque = run_contracts(GOOD_CSS + ".filter-select { appearance: none; }",
                          js="const o = { type: 'checkbox' };")
check(cb_opaque["no-native-checkbox"]["status"] == "WARN",
      "an unattributable skin is WARN, never a silent PASS")
check("cannot establish" in cb_opaque["no-native-checkbox"]["detail"],
      "the WARN says what it failed to establish")

# an HTML control reads its class off its own tag
cb_html = run_contracts(GOOD_CSS + ".tick { appearance: none; }",
                        '<input class="tick" type="checkbox"><dialog></dialog>')
check(cb_html["no-native-checkbox"]["status"] == "PASS",
      "an HTML checkbox skinned under its own class PASSes")

# a bundled third-party library's own control is not the app's design choice
# (the same `vendor/` exclusion find_emoji_sites applies, #416/#843)
_cbv = Path(throwaway_dir("dl-cbv-"))
try:
    (_cbv / "s.css").write_text(GOOD_CSS, encoding="utf-8")
    lib = _cbv / "static/vendor/leaflet"
    lib.mkdir(parents=True)
    (lib / "leaflet.js").write_text(CB_BUILT_JS, encoding="utf-8")
    out = {c["id"]: c for c in dl.contracts(
        _cbv, [_cbv / "s.css"], [], [lib / "leaflet.js"], {"icons.size.inline": "16px"})}
    check(out["no-native-checkbox"]["status"] == "PASS"
          and out["no-native-checkbox"]["detail"] == "no checkbox controls",
          "a checkbox inside a vendor/ bundle is not the app's checkbox (#843)")
finally:
    shutil.rmtree(_cbv, ignore_errors=True)

_h.report_and_exit("test_layout_contracts")
