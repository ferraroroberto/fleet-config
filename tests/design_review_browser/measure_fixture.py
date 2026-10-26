import json
import os
import subprocess
import sys
from pathlib import Path

from _suite import REPO, STATE, _h, _walk_failure, _walk_run, check, interp, scaffold
from design_review import capture, evaluate as ev, measure, rubric as rb
from design_review_support import FIX, RUBRIC, rubric


run_dir = STATE / "fixture-run"
proc = subprocess.run(
    [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(REPO),
     "--url", (FIX / "fixture.html").as_uri(), "--devices", "desktop", "--python", str(interp),
     "--scaffold", str(scaffold), "--run-dir", str(run_dir), "--rubric", str(RUBRIC),
     "--spec", str(FIX / "spec_compliant.md")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)},
)
lines = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)
check(proc.returncode == 0 and lines.get("UNMEASURED") == "none" and lines.get("SCREENS") == "6/8" and lines.get("ABSENT") == "2",
      f"measure CLI walks the fixture: 2 tabs + 1 dialog x light/dark, plus the absent Settings gear (#1217) ({proc.stdout[-300:]}{proc.stderr[-300:]})")
check(lines.get("RUN_DIR") == str(run_dir) and Path(lines.get("METRICS", "")).is_file(), "RUN_DIR/METRICS lines point at the run dir")
doc = json.loads(Path(lines["METRICS"]).read_text(encoding="utf-8"))
check(doc["interpreter"] == str(interp) and doc["schema_version"] == 1 and doc["rubric_version"] == "1.42.0", "metrics.json records the interpreter + versions")
if not (scaffold / "tests" / "e2e" / "_geometry.py").is_file():
    _h.skip("browser leg: project-scaffolding/tests/e2e/_geometry.py absent -- hit-target assertions NOT verified")
check(doc["walk"]["info"]["geometry"] == ("loaded" if (scaffold / "tests" / "e2e" / "_geometry.py").is_file() else "GEOMETRY_MISSING"),
      "walk reports whether the scaffold geometry JS loaded")
by_id = {s["id"]: s for s in doc["screens"]}
check(set(by_id) == {f"desktop-{t}-{v}" for t in ("light", "dark") for v in ("home", "list", "dialog-editdialog", "gear-settings")}
  and by_id["desktop-light-gear-settings"]["reason"] == "SETTINGS_GEAR_ABSENT", "screen ids from tabs + dialog + the Settings gear step (absent here)")
home = by_id["desktop-light-home"]["metrics"]
_cat_ok = capture.vendored_components(str(scaffold))["components"] is not None
check(isinstance(home.get("inventory"), dict) and "error" not in home["inventory"]
      and (home["inventory"]["unmeasured"] is None) == _cat_ok == (doc["params"]["script"]["components"] is not None),
      f"measure passes the scaffold's component catalog in, and every screen carries an inventory (#1497) -- {str(home.get('inventory'))[:300]}")
tx, ct, tg, ic, nv, ly, ay = (home[k] for k in ("text", "controls", "targets", "icons", "nav", "layout", "a11y"))
check(tx["min_px"] == 10 and tx["under11_count"] == 1 and {"10", "12", "14", "16", "24"} <= set(tx["sizes"]), "text size histogram + 10px floor")
check(tx["low_contrast_count"] == 3 and {r["sel"] for r in tx["low_contrast"]} == {"p.faint", "pre.log.faint", "button.faint-btn"}
      and all(r["ratio"] < 3 for r in tx["low_contrast"]),
      f"low-contrast runs found, log text in <pre> still measured for contrast (#1119) -- {tx['low_contrast']}")
check({r["text"] for r in tx["low_contrast"] if r["sel"] == "button.faint-btn"} == {"On label"},
      f"COLOR-02 exempts a :disabled / [aria-disabled=true] label (WCAG 2.2 SC 1.4.3); an enabled one in the same colours still fails (#1385) -- {tx['low_contrast']}")
check(tx["glyph_icon_count"] == 3
      and {g["sel"] for g in tx["glyph_icons"]} == {"button.big.glyph-btn", "button.big.glyph-lone", "button.big.glyph-run"},
      f"glyph icons: a control label ending or starting with a glyph, or a lone one, counts; an arrow inside a sentence "
      f"or a title (p.glyph) and <pre>/<code>/<samp>/<kbd> text do not (#1119, #1261) -- {tx['glyph_icons']}")
check(ct["font_family_mismatch_count"] == 1 and ct["font_family_mismatch"][0]["sel"] == "select.ua-font", "UA-font select found")
if "error" not in tg:
    small = {s["sel"] for s in tg["small"]}
    check("button.small-btn" in small and "button.hit-target" not in small, "24px button is small; 34px + ::before inset -5px measures 44 effective")
    check(tg["overlap_count"] == 0, "home: no expanded rects overlap")
    check(by_id["desktop-light-list"]["metrics"]["targets"]["overlap_count"] == 1, "list: two touching expanded targets overlap")
lay_list, lay_home = by_id["desktop-light-list"]["metrics"]["layout"], ly
check(lay_list["rows_over_limit"] == [{"sel": "li.arow.arow-over", "controls": 4}],
      f"LAYOUT-03: star + Run + kebab beside the row's main control is within budget; a fourth extra is over (#996) -- {lay_list['rows_over_limit']}")
check(lay_list["content_w"] <= 500 and lay_list.get("content_span", 0) >= 1400,
      f"LAYOUT-06: content_span runs from the list pane across the detail pane (#996) -- {lay_list['content_w']} / {lay_list.get("content_span")}")
check(lay_home.get("content_span") == lay_home["content_w"], "LAYOUT-06: with no detail pane the span is the pane itself")
check(lay_home.get("pane_split") is False and lay_list.get("pane_split") is False,
      f"LAYOUT-06: a detail pane docked outside the tab's pane is not a split inside it (#1361) -- {lay_home.get('pane_split')} / {lay_list.get('pane_split')}")
check({b["sel"] for b in ct["boundary_low"]} == {"input.faint-border", "button.bare-switch", "input.inset-faint"},
      f"COLOR-03: a faint input, a faint inset-shadow field and a bare switch track fail; text-labelled buttons and switch pills are exempt; a wrapper-drawn boundary counts (#996) and so does a 3:1 inset-shadow boundary (#1185) -- {[b['sel'] for b in ct['boundary_low']]}")
via = {b["sel"]: b["via"] for b in ct["boundary_low"]}
check(via.get("input.inset-faint") == "inset-shadow" and via.get("input.faint-border") == "border" and "input.inset-ok" not in via,
      f"COLOR-03: the boundary read from an inset shadow says so, and a 4.8:1 inset boundary passes -- {via}")
for theme in ("light", "dark"):
    seg = by_id[f"desktop-{theme}-list"]["metrics"]["controls"]["segmented_bad"]
    check(seg == [{"sel": "div.segmented.seg-wrap", "options": 2, "wrapped": True}],
          f"COMP-03 {theme}: a 44px single-line segment is not wrapped; a label broken onto two lines is (#997) -- {seg}")
check(ic["boxes"] == {"16x16": 1, "22x22": 1}, "icon boxes")
check(ic["elements"] == {"16x16": [{"glyph": "i16", "host": "section#paneHome", "label": ""}],
                         "22x22": [{"glyph": "play", "host": "section#paneHome", "label": ""}]},
      f"COMP-02: each icon box names its glyph (sprite <use> first, else a class) and its host (#1020) -- {ic.get('elements')}")
check(nv["primary_count"] == 2 and nv["pane_header_visible"] is True, "nav: 2 primary tabs, header visible")
check(ly["overflow_x"] is False and ly["inner_w"] == 1440, "no overflow at 1440")
check(ay["unnamed"] == ["button.big"] and ay["zoom_locked"] is True, "unnamed button + locked zoom")
check(ay["text_size_control"] is True and ay["text_size_stamped"] is True,
      f"a11y probe: a hidden group named by aria-labelledby is the text-size control; html[data-textsize] is the stamp (#1185) -- {ay}")
dlg = by_id["desktop-light-dialog-editdialog"]["metrics"]
check(dlg["controls"]["total"] == 2 and dlg["a11y"]["unnamed_count"] == 0,
      f"dialog scope measures only the dialog, its folded Options field opened and measured (#995) -- {dlg['controls']['total']}")
if "error" not in dlg["targets"]:
    small_d = {s["sel"] for s in dlg["targets"]["small"]}
    check(dlg["targets"]["total"] == 4 and dlg["targets"]["overlap_count"] == 0
          and "input#openedField" in small_d and "input#hiddenField" not in small_d,
          f"dialog targets: two summaries, the opened field and Close; the field the accordion re-closed is excluded (#995, #998) -- {dlg['targets']}")
check(dlg["text"]["low_contrast_count"] == 0, f"text inside a details that stays closed is not measured (#998) -- {dlg['text']['low_contrast']}")
check((run_dir / "shots" / "desktop-light-home.png").is_file() and (run_dir / "shots" / "desktop-light-home-full.png").is_file(),
      "screenshots in the run dir")
dark = by_id["desktop-dark-home"]["metrics"]["text"]
check(dark["low_contrast_count"] == 0 or dark["low_contrast"][0]["bg"] != "#ffffff", "dark leg renders the dark canvas")
# evaluate CLI on the real run
proc2 = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "evaluate", lines["METRICS"],
                        "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md"), "--spec-dark", str(FIX / "spec_compliant.md")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
res = json.loads(proc2.stdout)
check(proc2.returncode == 0 and {r["id"] for r in res["rules"]} == {r.id for r in rubric.rules}, "evaluate CLI prints every rule")
rs = {r["id"]: r["status"] for r in res["rules"]}
check(rs["TYPE-03"] == "fail" and rs["COMP-01"] == "fail" and rs["A11Y-01"] == "fail" and rs["LAYOUT-01"] == "pass",
      "evaluate on the fixture walk: planted defects fail, overflow passes")
proc3 = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "evaluate", lines["METRICS"],
                        "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md"), "--spec-dark", str(FIX / "spec_compliant.md")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
res2 = json.loads(proc3.stdout)
check(res2["rules"] == res["rules"] and res2["categories"] == res["categories"], "two evaluate runs -> identical rules and grades")
check(all(s.get("theme_applied") is True for s in doc["screens"] if s["status"] == "ok"),
      f"a page that honours the stamped theme reads theme_applied true on every measured screen -- {[(s['id'], s.get('theme_applied')) for s in doc['screens']]}")
# a theme the app picks for itself (fleet-config#1216)
_tf = STATE / "theme-fight-run"
_p = subprocess.run(
    [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(REPO),
     "--url", (FIX / "theme_fight.html").as_uri(), "--devices", "desktop", "--python", str(interp),
     "--scaffold", str(scaffold), "--run-dir", str(_tf), "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)})
_kv = dict(l.split("=", 1) for l in _p.stdout.splitlines() if "=" in l)
if "METRICS" not in _kv:
    check(False, f"theme-fight fixture walk produced metrics -- {_p.stdout[-300:]}{_p.stderr[-300:]}")
else:
    _tdoc = json.loads(Path(_kv["METRICS"]).read_text(encoding="utf-8"))
    _tby = {s["id"]: s for s in _tdoc["screens"]}
    _tl, _td = _tby["desktop-light-root"], _tby["desktop-dark-root"]
    check(_tl["theme_applied"] is False and _tl["theme_observed"]["attr"] == "dark" and _td["theme_applied"] is True,
          f"an app that re-applies dark is not applied on its light leg, and is on its dark leg -- {_tl.get('theme_observed')}")
    _tev = ev.evaluate(_tdoc, rubric, rb.load_specs(FIX / "spec_compliant.md", FIX / "spec_compliant.md"))
    _trules = {r["id"]: r for r in _tev["rules"]}
    _c4 = _trules["COLOR-04"]
    check("desktop-light-root" not in _c4["measured"] and "desktop-dark-root" in _c4["measured"]
          and "desktop-light-root" not in [e["screen"] for e in _c4["evidence"]]
          and "1 unmeasured" in _c4["reason"] and _tev["theme_mismatch_screens"] == [{"id": "desktop-light-root", "requested": "light", "rendered": "dark"}],
          f"COLOR-04 reads the light leg that rendered dark as unmeasured, never as a failure against the light spec -- {_c4['status']} {_c4['reason']}")
    check("desktop-light-root" in _trules["TOUCH-01"]["measured"] and "desktop-light-root" in _trules["LAYOUT-01"]["measured"],
          "its touch and layout rules still score that screen")
# embedded content (fleet-config#1185): a declared [design.review].exclude_selectors keeps a session-themed preview out of scoring
def _embedded_walk(label: str, fleet_toml: str) -> dict:
    tgt = STATE / f"embedded-{label}"
    tgt.mkdir(parents=True, exist_ok=True)
    (tgt / ".fleet.toml").write_text(fleet_toml, encoding="utf-8")
    p = _walk_run(
        [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(tgt),
         "--url", (FIX / "embedded.html").as_uri(), "--devices", "desktop", "--python", str(interp),
         "--scaffold", str(scaffold), "--run-dir", str(STATE / f"embedded-run-{label}"), "--rubric", str(RUBRIC),
         "--spec", str(FIX / "spec_compliant.md")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)})
    kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    return json.loads(Path(kv["METRICS"]).read_text(encoding="utf-8")) if "METRICS" in kv else {"error": p.stdout[-300:] + p.stderr[-300:]}

emb_all = _embedded_walk("all", '[design]\nwide_views = []\n')
emb_ex = _embedded_walk("excluded", '[design.review]\nexclude_selectors = [".preview-frame"]\n')
_emb_bad = {label: ("measure run: " + str(d["error"])) if "error" in d else _walk_failure((d.get("screens") or [None])[0])
            for label, d in (("all", emb_all), ("excluded", emb_ex))}
if any(_emb_bad.values()):
    check(False, f"embedded.html: both walks produced metrics -- {_emb_bad}")
else:
    m_all, m_ex = emb_all["screens"][0]["metrics"], emb_ex["screens"][0]["metrics"]
    check(m_all["text"]["under11_count"] == 2 and m_all["text"]["uppercase_count"] == 1 and m_all["targets"]["small_count"] >= 1 and "excluded" not in m_all,
          f"without a declaration the embedded preview is measured as app UI -- {m_all['text']['under11']} {m_all['text']['uppercase']} {m_all['targets']['small_count']}")
    check(m_ex["text"]["under11_count"] == 0 and m_ex["text"]["uppercase_count"] == 0 and m_ex["targets"]["small_count"] == 0
          and m_ex["controls"]["total"] == 0 and m_ex["a11y"]["unnamed_count"] == 0,
          f"a declared exclude_selectors leaves the preview out of text, targets and controls -- {m_ex['text']['under11']} {m_ex['targets']['small_count']}")
    check(m_ex["excluded"] == {"selectors": [".preview-frame"], "elements": 1} and m_ex["text"]["runs"] == 1,
          f"the run records what it excluded and still measures the app's own text -- {m_ex.get('excluded')} runs={m_ex['text']['runs']}")
    check(emb_ex["params"]["script"]["excludeSelectors"] == [".preview-frame"] and emb_all["params"]["script"]["excludeSelectors"] == [],
          "metrics.json records the exclusion in the params the run measured with")
# extra steps, driven through walk.py with a declared review block (the fixture repo has no [design.review])
step_dir = STATE / "fixture-steps"
(STATE / "steps-review.json").write_text(json.dumps({"no_go": [".danger-zone", "#revealForbidden"], "extra_steps": [
    {"tab": "home", "id": "reveal", "click": "#revealBtn"},
    {"tab": "list", "id": "row-menu", "open": "details.card", "clicks": [".card .kebab"]},
    {"tab": "list", "id": "row-delete", "open": "details.card", "clicks": [".card .kebab", ".card .danger-item"]},
    {"tab": "home", "id": "forbidden", "click": "#revealForbidden"},
    {"tab": "home", "id": "absent", "click": "#noSuchTarget"},
    {"tab": "list", "id": "absent-second", "open": "details.card", "clicks": [".card .kebab", "#noSuchMenuItem"]},
    {"tab": "home", "id": "synthetic-only", "click": "#revealBtn", "synthetic": True},
]}), encoding="utf-8")
(STATE / "steps-params.json").write_text(json.dumps(measure.default_params()), encoding="utf-8")
proc4 = subprocess.run(
    [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "fixture.html").as_uri(),
     "--out", str(step_dir), "--devices", "desktop", "--scaffold", str(scaffold),
     "--review", str(STATE / "steps-review.json"), "--params", str(STATE / "steps-params.json")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
steps = {s["id"]: s for s in json.loads((step_dir / "screens.json").read_text(encoding="utf-8"))} if proc4.returncode == 0 else {}
reveal = steps.get("desktop-light-home-reveal", {})
check(reveal.get("status") == "ok", f"the reveal step is measured ({proc4.stderr[-300:]})")
if reveal.get("status") == "ok" and "error" not in reveal["metrics"]["targets"]:
    check("input#foldedField" in {s["sel"] for s in reveal["metrics"]["targets"]["small"]},
          "an extra step opens the closed details it reveals before measuring, so the folded field is measured (#995)")
menu = steps.get("desktop-light-list-row-menu", {})
check(menu.get("status") == "ok" and "button.menu-item" in {s["sel"] for s in (menu.get("metrics") or {}).get("targets", {}).get("small", [])},
      f"a multi-click step opens a closed card and taps its row kebab; the menu is measured (#995) -- {menu.get('status')} {menu.get('error')}")
delete = steps.get("desktop-light-list-row-delete", {})
check(delete.get("status") == "error" and delete.get("reason") == "NO_GO" and ".card .danger-item" in str(delete.get("error")),
      f"a click inside a no_go zone is vetoed at click time, not only by selector equality (#995) -- {delete.get('reason')} {delete.get('error')}")
check("desktop-light-home-synthetic-only" not in steps and proc4.returncode == 0,
      "a live walk never runs a step marked synthetic (#995)")
forbidden = steps.get("desktop-light-home-forbidden", {})
check(forbidden.get("reason") == "NO_GO", "a step whose click is literally a no_go selector is refused")
for _sid, _sel in (("desktop-light-home-absent", "#noSuchTarget"), ("desktop-light-list-absent-second", "#noSuchMenuItem")):
    _s = steps.get(_sid, {})
    check(_s.get("status") == "absent" and _s.get("reason") == "STEP_TARGET_ABSENT" and _sel in str(_s.get("error")),
          f"a step target that never appears is recorded STEP_TARGET_ABSENT, naming the selector (#995) -- {_sid}: {_s.get('status')} {_s.get('reason')}")
