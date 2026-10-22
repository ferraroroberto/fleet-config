"""Browser leg of the design-review suite (fleet-config#971).

Drives `measure` and the walk against the static fixture pages in
`tests/fixtures/design_review/` through whichever sibling fleet checkout has Playwright in its
`.venv` (project-scaffolding, then app-launcher). About 78 walks, each two real browser launches
(light + dark): ~34 of the gate's ~40 minutes. The gate runs it only when the design-review
surface changed (`tests/acceptance/gate_routing.py`); `GATE_FULL=1` forces it. No sibling
Playwright is reported as a skip — never a pass.

The pure-logic leg is the suites in `tests/design_review/`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_review_browser.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402
from acceptance.shared import SKIP_EXIT  # noqa: E402

from design_review import capture, evaluate as ev, measure, plan, rubric as rb  # noqa: E402
from design_review import report as report_mod  # noqa: E402
from design_review_support import (  # noqa: E402
    FIX, RUBRIC, L10_MISSING as _L10_MISSING, rubric,
    c18_of as _c18_of, doc as _doc, port_open as _port_open, specs as _specs,
)

sys.path.insert(0, str(REPO / "skills" / "_lib" / "design_review"))
import walk  # noqa: E402

_h = CheckHarness()
check = _h.check

# Every hooks-state write in this file lands in a throwaway dir, never in the live one.
STATE = throwaway_dir("design-review-test-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)

# ---- browser leg: the static fixture page through a sibling venv --------------

FLEET = REPO.parent


def _walk_failure(screen: object) -> str | None:
    """Why a fixture walk's screen cannot be read, or None when it carries metrics (fleet-config#1447).

    A walk that failed or timed out hands back no screen, an `{"error": ...}` stub, or a screen whose
    `metrics` is None. A leg that indexed `screen["metrics"]` raised TypeError there; it names the walk's
    error in one FAIL line instead, and never reads a missing walk as a pass."""
    if not isinstance(screen, dict) or not screen:
        return "no screen came back from the walk"
    if "error" in screen and "metrics" not in screen:
        return f"walk error: {screen['error']}"
    if not isinstance(screen.get("metrics"), dict):
        return f"no metrics (status={screen.get('status')} reason={screen.get('reason')} error={screen.get('error')})"
    return None


def _walk_run(argv: list, **kw) -> "subprocess.CompletedProcess":
    """`subprocess.run` for a fixture walk, where a timeout is a failed walk (rc 124 + stderr), not a raised TimeoutExpired (fleet-config#1447)."""
    try:
        return subprocess.run(argv, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, stdout="", stderr=f"walk timed out after {kw.get('timeout')}s")


interp = None
for sibling in ("project-scaffolding", "app-launcher"):
    cand = capture.resolve_interpreter(plan.Target(name=sibling, root=FLEET / sibling, base_url="file:///"))
    if cand["status"] == "ok":
        interp = cand["python"]
        break
scaffold = FLEET / "project-scaffolding"
if interp is None:
    _h.skip("browser leg: no sibling .venv with Playwright (project-scaffolding, app-launcher) -- fixture walk NOT verified")
else:
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

    # layers: a control covered by a fixed bar is not an overlap; a list must scroll clear of the bar (#1019, #1094)
    layers_dir = STATE / "fixture-layers"
    proc_l = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "layers.html").as_uri(),
         "--out", str(layers_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    lay = {s["id"]: s for s in json.loads((layers_dir / "screens.json").read_text(encoding="utf-8"))} if proc_l.returncode == 0 else {}
    cov, clr = (lay.get(f"desktop-light-{v}", {}).get("metrics") or {} for v in ("covered", "clear"))
    check(bool(cov) and bool(clr), f"the layers fixture walks both tabs ({proc_l.stderr[-300:]})")
    if cov and clr and "error" not in cov["targets"] and "error" not in clr["targets"]:
        check(cov["targets"]["covered_count"] == 1 and "button#underPill" in cov["targets"]["covered"][0].values(),
              f"TOUCH-02: a control under the fixed pill is covered, not an overlap -- {cov['targets']['covered']}")
        check(cov["targets"]["overlap_count"] == 1 and {cov["targets"]["overlaps"][0]["a"], cov["targets"]["overlaps"][0]["b"]} == {"button.hit-target"},
              f"TOUCH-02: two touching controls in the flow still overlap -- {cov['targets']['overlaps']}")
        check(clr["targets"]["covered_count"] == 1 and "button#abovePill" in clr["targets"]["covered"][0].values(),
              f"TOUCH-02: a control beside the pill whose expansion reaches into a tab the pill wins is covered (#1094) -- {clr['targets']}")
        check(clr["targets"]["overlap_count"] == 1 and "button#overPill" in clr["targets"]["overlaps"][0].values(),
              f"TOUCH-02: a control painted over a tab wins taps inside it, so the pair still overlaps (#1094) -- {clr['targets']}")
    check(cov.get("clearance", {}).get("hidden_row_count") == 1 and cov["clearance"]["hidden_rows"][0]["bar"].startswith("nav.pill"),
          f"LAYOUT-07: a list ending under the pill at full scroll is hidden -- {cov.get('clearance')}")
    check(clr.get("clearance", {}).get("hidden_row_count") == 0 and clr["clearance"]["bars"] == ["nav.pill"],
          f"LAYOUT-07: a list padded clear of the pill passes, and an open row-menu over its last row is no bar (#1464) -- {clr.get('clearance')}")
    check(by_id["desktop-light-home"]["metrics"]["clearance"] == {"bars": [], "hidden_rows": [], "hidden_row_count": 0},
          "LAYOUT-07: a page with no fixed bottom bar has nothing to clear")

    # lanes: a board's lanes are each a <section>; the filter above them, in the pane, serves every lane (#1155)
    lanes_dir = STATE / "fixture-lanes"
    proc_ln = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "lanes.html").as_uri(),
         "--out", str(lanes_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    lns = {s["id"]: s for s in json.loads((lanes_dir / "screens.json").read_text(encoding="utf-8"))} if proc_ln.returncode == 0 else {}
    _filtered, _bare = ((lns.get(f"desktop-light-{v}", {}).get("metrics") or {}).get("layout", {}).get("lists") for v in ("filtered", "bare"))
    check(_filtered == [{"sel": "ul.lane-list", "rows": 20, "has_filter": True}] * 2,
          f"LAYOUT-02: a lane list in its own <section> finds the pane's filter above the lanes (#1155) -- {_filtered} ({proc_ln.stderr[-300:]})")
    check(_bare == [{"sel": "ul.lane-list", "rows": 20, "has_filter": False}],
          f"LAYOUT-02: lanes in a pane with no filter still have none; the lookup stops at the pane (#1155) -- {_bare}")

    # clipped: options scrolled out of a scroller take no tap over the control beneath it; a popup
    # whose containing block is outside an overflow-hidden box escapes that box (#1155)
    clip_dir = STATE / "fixture-clipped"
    proc_cl = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "clipped.html").as_uri(),
         "--out", str(clip_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    cls = {s["id"]: s for s in json.loads((clip_dir / "screens.json").read_text(encoding="utf-8"))} if proc_cl.returncode == 0 else {}
    _scroll, _escape = ((cls.get(f"desktop-light-{v}", {}).get("metrics") or {}).get("targets", {}) for v in ("scroll", "escape"))
    check(_scroll.get("overlap_count") == 0,
          f"TOUCH-02: options clipped by their scroller are not overlaps with the control beneath it (#1155) -- {_scroll.get('overlaps')} ({proc_cl.stderr[-300:]})")
    check(_escape.get("overlap_count") == 1 and {_escape["overlaps"][0]["a"], _escape["overlaps"][0]["b"]} == {"button.hit-target"},
          f"TOUCH-02: a popup escaping a static overflow-hidden box is not clipped by it; its touching items still overlap (#1155) -- {_escape.get('overlaps')}")

    # settings tab: a tab named Settings and a text-less gear tab count; the header gear and a plain tab do not (#1200)
    st_dir = STATE / "fixture-settings-tab"
    proc_st = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "settings_tab.html").as_uri(),
         "--out", str(st_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    sts = json.loads((st_dir / "screens.json").read_text(encoding="utf-8")) if proc_st.returncode == 0 else []
    _st_nav = ((sts[0].get("metrics") or {}).get("nav") or {}) if sts else {}
    check(_st_nav.get("primary_count") == 4 and _st_nav.get("settings_tab_count") == 2
          and [t["label"] for t in _st_nav.get("settings_tabs", [])] == ["Settings", ""] ,
          f"NAV-03: the tab named Settings and the text-less gear tab are counted, the header gear and Logs are not (#1200) -- {_st_nav} ({proc_st.stderr[-300:]})")

    # home title: the first tab's header against the manifest name, a pass and a fail pair, and a header shared above the panes (#1360)
    ht = {}
    for _kind in ("pass", "fail", "shared"):
        ht_dir = STATE / f"fixture-home-title-{_kind}"
        proc_ht = subprocess.run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / f"home_title_{_kind}.html").as_uri(),
             "--out", str(ht_dir), "--devices", "desktop", "--scaffold", str(scaffold),
             "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        _hts = json.loads((ht_dir / "screens.json").read_text(encoding="utf-8")) if proc_ht.returncode == 0 else []
        ht[_kind] = {s["id"]: s for s in _hts}
        ht[_kind + "_err"] = proc_ht.stderr[-300:]
        _ht_doc = {"screens": _hts, "wide_views": []}
        ht[_kind + "_rule"] = next(r for r in ev.evaluate(_ht_doc, rubric, _specs("compliant"))["rules"] if r["id"] == "NAV-04")
    _ht_p, _ht_f = (ht[k].get("desktop-light-home", {}).get("metrics", {}).get("nav", {}).get("home_title") for k in ("pass", "fail"))
    check(_ht_p == {"is_home": True, "manifest": "ok", "app_names": ["Spot Keeper", "Spots"], "tab": "Today", "title": "Today"},
          f"NAV-04 pass fixture: the home tab reads its own name against the data: manifest -- {_ht_p} ({ht['pass_err']})")
    check(_ht_f == {"is_home": True, "manifest": "ok", "app_names": ["Spot Keeper", "Spots"], "tab": "Today", "title": "Spot Keeper"},
          f"NAV-04 fail fixture: the home tab reads the manifest name as its title -- {_ht_f} ({ht['fail_err']})")
    _ht_second = ht["fail"].get("desktop-light-history", {}).get("metrics", {}).get("nav", {}).get("home_title") or {}
    check(_ht_second.get("is_home") is False and _ht_second.get("title") is None,
          f"NAV-04: the second tab is not the home tab and reads no title, whatever its header says -- {_ht_second}")
    check(ht["pass_rule"]["status"] == "pass" and ht["pass_rule"]["measured"] == {"desktop-light-home": 0.0, "desktop-dark-home": 0.0},
          f"NAV-04: the pass fixture passes on its home tab only -- {ht['pass_rule']['status']} {ht['pass_rule']['measured']}")
    check(ht["fail_rule"]["status"] == "fail" and ht["fail_rule"]["measured"] == {"desktop-light-home": 1.0, "desktop-dark-home": 1.0}
          and "Spot Keeper" in report_mod.finding_sentence(ht["fail_rule"], None),
          f"NAV-04: the fail fixture fails on its home tab and the finding names the title -- {ht['fail_rule']['status']} {ht['fail_rule']['measured']}")
    check(ht["shared_rule"]["status"] == "pass" and ht["shared_rule"]["measured"] == {"desktop-light-home": 0.0, "desktop-dark-home": 0.0}
          and (ht["shared"].get("desktop-light-home", {}).get("metrics", {}).get("nav", {}).get("home_title") or {}).get("title") == "Today",
          f"NAV-04: one header shared above the panes is the home tab's title, found from outside the pane -- {ht['shared_rule']['status']} {ht['shared_err']}")

    # header context line: a count that changes and a build line pass, one fixed sentence fails (#1395)
    # Served over http: the measure reads the page source to tell served markup from script, which a file: page cannot be asked for.
    import functools, http.server, threading
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 -- keep the test output clean
            pass
    _hc_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(FIX)))
    threading.Thread(target=_hc_srv.serve_forever, daemon=True).start()
    hc = {}
    for _kind in ("pass", "fail", "script"):
        hc_dir = STATE / f"fixture-header-context-{_kind}"
        proc_hc = subprocess.run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", f"http://127.0.0.1:{_hc_srv.server_address[1]}/header_context_{_kind}.html",
             "--out", str(hc_dir), "--devices", "desktop", "--scaffold", str(scaffold),
             "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        _hcs = json.loads((hc_dir / "screens.json").read_text(encoding="utf-8")) if proc_hc.returncode == 0 else []
        hc[_kind + "_err"] = proc_hc.stderr[-300:]
        hc[_kind] = next((r for r in ev.evaluate({"screens": _hcs, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "NAV-05"), {}) if _hcs else {}
        hc[_kind + "_lines"] = {s["id"]: ((s.get("metrics") or {}).get("nav") or {}).get("header_context", {}).get("text") for s in _hcs if s.get("kind") == "tab"}
    check(hc["pass_lines"] == {"desktop-light-home": "3 running", "desktop-dark-home": "2 running",
                               "desktop-light-history": "Build deadbee", "desktop-dark-history": "Build deadbee"},
          f"NAV-05 pass fixture: each tab's header context line is read from its pane -- {hc['pass_lines']} ({hc['pass_err']})")
    check(hc["pass"].get("status") == "pass", f"NAV-05 pass fixture: a count that changes and a build line pass -- {hc['pass'].get('status')} {hc['pass'].get('reason')}")
    check(hc["fail"].get("status") == "fail" and sorted(hc["fail"].get("measured", {})) == ["desktop-dark-history", "desktop-dark-home", "desktop-light-history", "desktop-light-home"]
          and "Manage your jobs." in report_mod.finding_sentence(hc["fail"], None),
          f"NAV-05 fail fixture: the same sentence in every state fails on both tabs and the finding names it -- {hc['fail'].get('status')} {hc['fail'].get('measured')} ({hc['fail_err']})")
    check(hc["script"].get("status") == "unmeasured" and "written by script" in hc["script"].get("reason", ""),
          f"NAV-05 script fixture: the same sentence written by script is not measured, never a pass or a fail -- {hc['script'].get('status')} {hc['script'].get('reason')} ({hc['script_err']})")
    _hc_srv.shutdown()

    # icon buttons and reference pills: what is painted at rest, and how many pill shapes a screen draws (#1259)
    ip_dir = STATE / "fixture-icon-pill"
    proc_ip = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "icon_pill.html").as_uri(),
         "--out", str(ip_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    ips = json.loads((ip_dir / "screens.json").read_text(encoding="utf-8")) if proc_ip.returncode == 0 else []
    _ip = ((ips[0].get("metrics") or {}).get("controls") or {}) if ips else {}
    check(_ip.get("icon_button_count") == 5 and _ip.get("icon_buttons_painted_count") == 4
          and sorted(b["via"] for b in _ip.get("icon_buttons_painted", [])) == ["border", "fill", "fill", "shadow"],
          f"COMP-05: the filled, bordered, shadowed and screen-reader-only icon buttons are painted; the clean one is not, "
          f"the pressed and the labelled ones are not counted (#1259) -- {_ip.get('icon_buttons_painted')} ({proc_ip.stderr[-300:]})")
    check(_ip.get("reference_pill_count") == 4 and _ip.get("reference_pill_variants") == 2
          and [(v["radius"], v["count"]) for v in _ip.get("reference_pill_styles", [])] == [("pill", 3), ("12px", 1)],
          f"COMP-06: three link chips share one shape whatever their colour, the boxed one is a second shape; a plain link "
          f"and a status pill are not reference pills (#1259) -- {_ip.get('reference_pill_styles')}")

    # icon-only controls: which have a label beside them, and which names are conventional (#1366)
    io_dir = STATE / "fixture-icon-only"
    proc_io = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "icon_only.html").as_uri(),
         "--out", str(io_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    ios = json.loads((io_dir / "screens.json").read_text(encoding="utf-8")) if proc_io.returncode == 0 else []
    _io = ((ios[0].get("metrics") or {}).get("controls") or {}) if ios else {}
    check(_io.get("icon_only_count") == 13
          and sorted(i["name"] for i in _io.get("icon_only_unlabelled", [])) == sorted(
              ["Edit take", "Close", "Search", "Switch to dark", "Incognito", "Gallery", "Reset", "Start", "Wake", ""])
          and {i["name"]: i["glyph"] for i in _io["icon_only_unlabelled"] if i["name"] in ("Start", "Wake")} == {"Start": "i-play", "Wake": "i-ghost"},
          f"COMP-09: a label sibling, a wrapping <label> text and an aria-labelledby label take a control out; a neighbouring pill does not "
          f"(#1366) -- {_io.get('icon_only_count')} {_io.get('icon_only_unlabelled')} ({proc_io.stderr[-300:]})")
    _io_rule = next((r for r in ev.evaluate({"screens": ios, "wide_views": []},
                                            rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-09"), {}) if ios else {}
    check(_io_rule.get("status") == "fail" and sorted(i["name"] for i in _io_rule["evidence"][0]["items"]) == ["Gallery", "Incognito", "Reset", "Wake"],
          f"COMP-09: the unlabelled unconventional Incognito, Gallery and Reset and a Wake with the wrong glyph fail; the conventional names, "
          f"a Start with its play glyph and the nameless control do not -- "
          f"{_io_rule.get('status')} {_io_rule.get('evidence')}")

    # empty-state blocks: which hold a way forward, and which are not empty states at all (#1367)
    es_dir = STATE / "fixture-empty-state"
    proc_es = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "empty_state.html").as_uri(),
         "--out", str(es_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    ess = json.loads((es_dir / "screens.json").read_text(encoding="utf-8")) if proc_es.returncode == 0 else []
    _es = ((ess[0].get("metrics") or {}).get("feedback") or {}) if ess else {}
    check(_es.get("empty_state_count") == 5
          and sorted(i["text"] for i in _es.get("empty_states_no_action", [])) == ["No devices", "No history yet", "Nothing scheduled"],
          f"COMP-10: five empty-state blocks are found (a button, a link, a reason only, a disabled button, an action outside the block); "
          f"a loading line, a trailing ellipsis, an error block and a hidden block are not -- {_es} ({proc_es.stderr[-300:]})")
    _es_rule = next((r for r in ev.evaluate({"screens": ess, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-10"), {}) if ess else {}
    check(_es_rule.get("status") == "fail" and len(_es_rule["evidence"][0]["items"]) == 3,
          f"COMP-10: the bare blocks fail, the ones holding a button or a link do not -- {_es_rule.get('status')} {_es_rule.get('reason')}")

    # expanded sections that show nothing, and the ones that only look empty (#1337)
    eo_dir = STATE / "fixture-empty-open-section"
    proc_eo = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "empty_open_section.html").as_uri(),
         "--out", str(eo_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    eos = json.loads((eo_dir / "screens.json").read_text(encoding="utf-8")) if proc_eo.returncode == 0 else []
    _eo = ((eos[0].get("metrics") or {}).get("layout") or {}) if eos else {}
    check(_eo.get("open_section_count") == 14
          and sorted(i["sel"].split(".")[0] for i in _eo.get("empty_open_sections", [])) == ["details#empty-list", "details#hidden-text", "details#whitespace", "div#panel-empty"],
          f"COMP-12: whitespace, an empty list, hidden text and an empty aria-controls panel are empty; a sentence, a control, a graphic, a glyph, a spinner, "
          f"a busy region, a loading data-state, an empty-state block, a details closed in the markup (the walk opens it), a collapsed trigger and a trigger naming a menu or no panel are not "
          f"-- {_eo} ({proc_eo.stderr[-300:]})")
    _eo_rule = next((r for r in ev.evaluate({"screens": eos, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-12"), {}) if eos else {}
    check(_eo_rule.get("status") == "fail" and len(_eo_rule["evidence"][0]["items"]) == 4,
          f"COMP-12: the four empty sections fail, the rest do not -- {_eo_rule.get('status')} {_eo_rule.get('reason')}")

    # list rows with more than one secondary line, and the rows that only look like it (#1338): at 1440px and at 390px,
    # where the wrapping meta lines take two to four rendered lines and must still count as one
    ms_dir = STATE / "fixture-multi-secondary"
    proc_ms = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "multi_secondary.html").as_uri(),
         "--out", str(ms_dir), "--devices", "desktop,iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    mss = json.loads((ms_dir / "screens.json").read_text(encoding="utf-8")) if proc_ms.returncode == 0 else []
    for _dev in ("desktop", "iphone"):
        _ms_scr = next((s for s in mss if s.get("device") == _dev), None)
        _ms = ((_ms_scr or {}).get("metrics") or {}).get("layout") or {}
        check(sorted((i["sel"].split(".")[0], i["lines"]) for i in _ms.get("multi_secondary_rows", []))
              == [("li#f-br", 2), ("li#f-slots", 2), ("li#f-three", 3), ("li#f-two", 2)],
              f"COMP-13 ({_dev}): a title with two meta blocks, three, a <br> inside one and an avatar and kebab around two fail; one meta line, "
              f"one that wraps (text, inline spans, a wrapping flex row), spans on one flex line, a chip, a date tile, a leading time, a trailing "
              f"value with a caption, a title alone, a lone row, a nav item, an open disclosure's body and a nested list do not -- {_ms} ({proc_ms.stderr[-300:]})")
    _ms_rule = next((r for r in ev.evaluate({"screens": mss, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-13"), {}) if mss else {}
    check(_ms_rule.get("status") == "fail" and len(_ms_rule["evidence"][0]["items"]) == 4,
          f"COMP-13: the four rows fail, the rest do not -- {_ms_rule.get('status')} {_ms_rule.get('reason')}")

    # row verbs that draw paint at rest, and the plain ones beside them (#1420)
    rv_dir = STATE / "fixture-row-verb"
    proc_rv = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "row_verb.html").as_uri(),
         "--out", str(rv_dir), "--devices", "desktop,iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    rvs = json.loads((rv_dir / "screens.json").read_text(encoding="utf-8")) if proc_rv.returncode == 0 else []
    for _dev in ("desktop", "iphone"):
        _rv_scr = next((s for s in rvs if s.get("device") == _dev), None)
        _rv = ((_rv_scr or {}).get("metrics") or {}).get("layout") or {}
        check(sorted((i["row"].split(".")[0], i["via"]) for i in _rv.get("painted_row_verbs", []))
              == [("li#f-lone", "fill+border"), ("li#f-more-verb", "fill+border"), ("li#f-outl-glyph", "border"), ("li#f-outl-text", "border"),
                  ("li#f-plus-verb", "fill+border"), ("li#f-shadow", "shadow"),
                  ("li#f-tint-glyph", "fill"), ("li#f-tint-text", "fill+border")],
              f"COMP-14 ({_dev}): a tint text verb, an outlined text verb, a tint glyph verb, an outlined glyph verb, a shadowed verb, a lone row's tint verb, a painted verb chip beside a +3 more chip and a painted + Add chip fail; "
              f"a painted +3 more or +12 count chip (a disclosure, #1429), a plain glyph verb, a plain cluster, a kebab, a pressed favourite, a switch, the row's own tap target, a page button and buttons in a menu or nav list do not "
              f"-- {_rv} ({proc_rv.stderr[-300:]})")
    for _dev in ("desktop", "iphone"):
        _rv_page = next((s for s in rvs if s.get("device") == _dev and s.get("id", "").endswith("-root")), None)
        _rv_sheet = next((s for s in rvs if s.get("device") == _dev and s.get("id", "").endswith("-dialog-sheet")), None)
        _rvp = ((_rv_page or {}).get("metrics") or {}).get("layout") or {}
        _rvs = ((_rv_sheet or {}).get("metrics") or {}).get("layout") or {}
        check(sorted(i["row"].split(".")[0] for i in _rvp.get("delete_row_verbs", [])) == ["li#d-glyph", "li#d-icon", "li#d-text"]
              and sorted(i["row"].split(".")[0] for i in _rvs.get("delete_row_verbs", [])) == ["li#d-sheet-a", "li#d-sheet-b"],
              f"COMP-15 ({_dev}): a Remove icon button, a trash glyph and a text Delete on the page and both rows of a dialog's editing list fail; a kebab, an Edit verb, "
              f"a page-level Delete all and a Delete menu item do not -- {_rvp.get('delete_row_verbs')} {_rvs.get('delete_row_verbs')} ({proc_rv.stderr[-300:]})")
    _rv15 = next((r for r in ev.evaluate({"screens": rvs, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-15"), {}) if rvs else {}
    _rv15_rows = sorted({i["row"].split(".")[0] for e in _rv15.get("evidence", []) for i in e["items"]})
    check(_rv15.get("status") == "fail" and _rv15_rows == ["li#d-glyph", "li#d-icon", "li#d-sheet-a", "li#d-sheet-b", "li#d-text"],
          f"COMP-15: the page's three and the dialog's two delete or remove controls fail across the screens, the rest do not -- {_rv15.get('status')} {_rv15_rows}")
    _rv_rule = next((r for r in ev.evaluate({"screens": rvs, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-14"), {}) if rvs else {}
    check(_rv_rule.get("status") == "fail" and len(_rv_rule["evidence"][0]["items"]) == 8,
          f"COMP-14: the eight painted verbs fail, a disclosure chip does not, the rest do not -- {_rv_rule.get('status')} {_rv_rule.get('reason')}")

    # raw identifiers: which text counts, and which only looks like one (#1336)
    ri_dir = STATE / "fixture-raw-identifier"
    proc_ri = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "raw_identifier.html").as_uri(),
         "--out", str(ri_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    ris = json.loads((ri_dir / "screens.json").read_text(encoding="utf-8")) if proc_ri.returncode == 0 else []
    _ri = ((ris[0].get("metrics") or {}).get("text") or {}) if ris else {}
    check(_ri.get("raw_identifier_count") == 6
          and sorted((i["kind"], i["text"]) for i in _ri.get("raw_identifiers", [])) == sorted([
              ("ip", "192.0.2.14"), ("ip", "192.0.2.7"), ("mac", "02:00:5E:10:00:2A"), ("mac", "02-00-5e-10-00-2b"),
              ("snake", "quiet_hours_start"), ("snake", "ECO_BOOST")]),
          f"COMP-11: an address, a MAC and a snake_case or UPPER_SNAKE token in ordinary text count; the same text in code, pre, kbd, samp, "
          f"a field or a hidden row, and a version, date, time, file name, path, URL, handle, hyphenated word or camelCase name do not -- "
          f"{_ri.get('raw_identifiers')} ({proc_ri.stderr[-300:]})")
    _ri_rule = next((r for r in ev.evaluate({"screens": ris, "wide_views": []}, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-11"), {}) if ris else {}
    check(_ri_rule.get("status") == "fail" and len(_ri_rule["evidence"][0]["items"]) == 6,
          f"COMP-11: the page with raw identifiers in text fails -- {_ri_rule.get('status')} {_ri_rule.get('reason')}")

    # label fit and container overflow at the 390px phone leg: the camera dialog that scored clean while broken (#1325)
    lf = {}
    for _kind in ("broken", "fixed"):
        lf_dir = STATE / f"fixture-label-fit-{_kind}"
        proc_lf = subprocess.run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / f"label_fit_{_kind}.html").as_uri(),
             "--out", str(lf_dir), "--devices", "iphone", "--scaffold", str(scaffold),
             "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        lf_screens = json.loads((lf_dir / "screens.json").read_text(encoding="utf-8")) if proc_lf.returncode == 0 else []
        lf[_kind] = next((s for s in lf_screens if s["id"] == "iphone-light-root"), {})
        lf[_kind + "_err"] = proc_lf.stderr[-300:]
    _lf_b, _lf_f = (lf[k].get("metrics") or {} for k in ("broken", "fixed"))
    check(_lf_b.get("layout", {}).get("inner_w") == 390 and _lf_f.get("layout", {}).get("inner_w") == 390,
          f"the iphone leg is 390px wide (#1325) -- {_lf_b.get('layout', {}).get('inner_w')} ({lf['broken_err']})")
    _lf_bad = _lf_b.get("controls", {}).get("label_fit_bad", [])
    check([(b["label"], b["lines"] > 1, b["spill_px"] > 1.5) for b in _lf_bad] == [("Zoom in", True, False), ("Screenshot", False, True)],
          f"COMP-07 before: the wrapped Zoom in and the overrun Screenshot are found -- {_lf_bad}")
    _lf_over = _lf_b.get("layout", {}).get("control_overflow", [])
    check([(o["label"], o["container"], o["side"]) for o in _lf_over] == [("Record clip", "div.card", "right")]
          and _lf_b["layout"]["overflow_x"] is False,
          f"LAYOUT-08 before: Record clip leaves the card on the right while the page does not scroll sideways (LAYOUT-01 green) -- {_lf_over}")
    check(_lf_f.get("controls", {}).get("label_fit_bad_count") == 0 and _lf_f.get("layout", {}).get("control_overflow_count") == 0,
          f"COMP-07 + LAYOUT-08 after: the fixed dialog passes, and a prose button, an ellipsis label and a self-scrolling chip strip are not findings "
          f"-- {_lf_f.get('controls', {}).get('label_fit_bad')} {_lf_f.get('layout', {}).get('control_overflow')}")

    # LAYOUT-09: a step line's ellipsis must not cut its counts at the 390px leg (#1343)
    def _walk_clipped(case: str) -> dict:
        out_dir = STATE / f"fixture-clipped-trailing-{case}"
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "clipped_trailing.html").as_uri() + f"?case={case}",
             "--out", str(out_dir), "--devices", "iphone", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr[-300:]}
        return next((s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s["id"] == "iphone-light-root"), {})

    def _l09_status(screen: dict) -> str:
        d = _doc("compliant")
        d["screens"] = [dict(d["screens"][1], id=screen["id"], view=screen.get("view"), metrics=screen["metrics"])]
        return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "LAYOUT-09")["status"]

    ct = {case: _walk_clipped(case) for case in ("fail", "pass", "edge", "prose", "status")}
    _ct_bad = {case: _walk_failure(s) for case, s in ct.items() if _walk_failure(s)}
    check(not _ct_bad, f"clipped_trailing.html: every case walks the 390px leg with metrics -- {_ct_bad}")
    if not _ct_bad:
        _ct_hits = lambda s: [(h["text"][:12], h["why"]) for h in (s["metrics"]["layout"].get("clipped_trailing") or [])]
        check(_ct_hits(ct["fail"]) == [("Ran 2 comman", "cuts failed 1m30s"), ("Ran 2 comman", "cuts 1m30s")] and _l09_status(ct["fail"]) == "fail",
              f"LAYOUT-09: 'Ran 2 commands, read 3 files +128 -46 2 failed 1m30s' in one ellipsis element, and a label ending in a duration, fail -- {_ct_hits(ct['fail'])}")
        check(_ct_hits(ct["pass"]) == [] and _l09_status(ct["pass"]) == "pass",
              f"LAYOUT-09: the same counts as non-shrinking trailing items pass, a label clipped on words only passes, a fitting label passes -- {_ct_hits(ct['pass'])}")
        check(_ct_hits(ct["edge"]) == [] and _l09_status(ct["edge"]) == "pass",
              f"LAYOUT-09: a cut file name and date, a cut 'failed' with no number before it, a cut tail of words, a wrapping line, a line clamp and a hidden row are not cut figures -- {_ct_hits(ct['edge'])}")
        # narrowed to status and meta text (#1438): a figure inside a run of words a person typed is not a finding, and what is let
        # pass is kept as a capped sample so the exclusion can be reviewed
        _ct_lay = lambda s: (s.get("metrics") or {}).get("layout") or {}
        _ct_excl = lambda s: _ct_lay(s).get("clipped_trailing_excluded") or []
        check(_ct_hits(ct["prose"]) == [] and _l09_status(ct["prose"]) == "pass",
              f"LAYOUT-09: a house number cut from an address line, a version string and a clock time cut from a free-text title pass -- {_ct_hits(ct['prose'])}")
        check(_ct_lay(ct["prose"]).get("clipped_trailing_excluded_count") == 3 and len(_ct_excl(ct["prose"])) == 3
              and [fig in e["why"] for fig, e in zip(("14", "1.38.0", "14:30"), _ct_excl(ct["prose"]))] == [True] * 3
              and all(e["why"].endswith("in prose") and e["sel"] and e["text"] for e in _ct_excl(ct["prose"])),
              f"LAYOUT-09: the three prose rows are kept in the excluded sample with the figure each cut -- {_ct_excl(ct['prose'])}")
        check(len(_ct_hits(ct["status"])) == 3 and _l09_status(ct["status"]) == "fail"
              and "16:00" in _ct_hits(ct["status"])[0][1] and all("2m10s" in h[1] for h in _ct_hits(ct["status"])[1:]),
              f"LAYOUT-09: a .meta slot, a status-row dd and an unmarked status line still fail on the figure they cut -- {_ct_hits(ct['status'])}")
        check(all(_ct_excl(ct[c]) == [] and _ct_lay(ct[c]).get("clipped_trailing_excluded_count") == 0 for c in ("fail", "status")),
              "LAYOUT-09: a row that counts is never also listed as excluded")

    # COMP-18: a turn is one block under one header (#1344)
    def _walk_turn(case: str) -> dict:
        out_dir = STATE / f"fixture-repeated-header-{case}"
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "repeated_header.html").as_uri() + f"?case={case}",
             "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr[-300:]}
        return next((s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s["id"].endswith("-light-root")), {})

    def _c18_status(screen: dict) -> str:
        d = _doc("compliant")
        d["screens"] = [dict(d["screens"][0], id=screen["id"], view=screen.get("view"), metrics=screen["metrics"])]
        return _c18_of(d)["status"]

    tn = {case: _walk_turn(case) for case in ("fail", "pass", "list", "edge", "none")}
    _tn_bad = {case: _walk_failure(s) for case, s in tn.items() if _walk_failure(s)}
    check(not _tn_bad, f"repeated_header.html: every case walks the desktop leg with metrics -- {_tn_bad}")
    if not _tn_bad:
        _tn_runs = lambda s: [(h["who"], h["blocks"], h["text"]) for h in (s["metrics"]["layout"].get("repeated_headers") or [])]
        _tn_whos = lambda s: sorted((h["who"], h["count"]) for h in (s["metrics"]["layout"].get("turn_headers") or []))
        check(_tn_runs(tn["fail"]) == [("Agent", 3, "Agent · <time>"), ("Claude", 2, "Claude · <time>")] and _c18_status(tn["fail"]) == "fail",
              f"COMP-18: three consecutive 'Agent 10:42/10:42/10:43' disclosures and two plain 'Claude 9:05 AM' cards each fail as one run -- {_tn_runs(tn['fail'])}")
        check(_tn_runs(tn["pass"]) == [] and _tn_whos(tn["pass"]) == [("Agent", 1)] and _c18_status(tn["pass"]) == "pass",
              f"COMP-18: one 'Agent 10:42' header over three parts passes and counts as a transcript reached -- {_tn_runs(tn['pass'])} {_tn_whos(tn['pass'])}")
        check(_tn_runs(tn["list"]) == [] and _tn_whos(tn["list"]) == [],
              f"COMP-18: a list of rows that each start 'Today' and event cards that each read 'Standup 09:00' are not turns -- {_tn_runs(tn['list'])} {_tn_whos(tn['list'])}")
        check(_tn_runs(tn["edge"]) == [] and _c18_status(tn["edge"]) == "pass" and ("You", 2) in _tn_whos(tn["edge"]),
              f"COMP-18: alternating speakers, a differing hint, a header with no control, a header-only card, a step line between two cards and a hidden twin are not runs -- {_tn_runs(tn['edge'])} {_tn_whos(tn['edge'])}")
        check(_tn_runs(tn["none"]) == [] and _tn_whos(tn["none"]) == [] and _c18_status(tn["none"]) == "unmeasured",
              f"COMP-18: a page with no transcript is unmeasured, not a pass -- {_tn_whos(tn['none'])}")

    # LAYOUT-10: a glance card that grows as its late reads land (#1354). Served over loopback: `/late?ms=N` answers after N ms
    # (capped at 120 s, past every bound the walk has), so the page's reads are real requests the walk's idle wait can see.
    import functools as _gl_functools, http.server as _gl_http, threading as _gl_threading, time as _gl_time, urllib.parse as _gl_parse

    class _LateReads(_gl_http.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 -- keep the test output clean
            pass

        def do_GET(self):  # noqa: N802 -- http.server's name
            url = _gl_parse.urlparse(self.path)
            if url.path != "/late":
                return super().do_GET()
            _gl_time.sleep(min(int((_gl_parse.parse_qs(url.query).get("ms") or ["0"])[0]), 120000) / 1000)
            try:
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:  # the walk closed the page mid-read (the hang case)
                pass

    _gl_srv = _gl_http.ThreadingHTTPServer(("127.0.0.1", 0), _gl_functools.partial(_LateReads, directory=str(FIX)))
    _gl_threading.Thread(target=_gl_srv.serve_forever, daemon=True).start()

    def _walk_glance(case: str, device: str = "desktop") -> dict:
        out_dir = STATE / f"fixture-glance-{case}-{device}"
        t0 = _gl_time.time()
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"),
             "--url", f"http://127.0.0.1:{_gl_srv.server_address[1]}/glance_shift.html?case={case}",
             "--out", str(out_dir), "--devices", device, "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr[-300:]}
        tabs = [s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s.get("kind") == "tab"]
        return {"tabs": tabs, "seconds": round(_gl_time.time() - t0, 1)}

    def _l10_on(walked: dict) -> dict:
        return next((r for r in ev.evaluate({"screens": walked["tabs"], "wide_views": []}, rubric, _specs("compliant"))["rules"]
                     if r["id"] == "LAYOUT-10"), _L10_MISSING)

    gl = {case: _walk_glance(case) for case in ("fail", "pass", "shrink", "animate", "cols", "last", "none", "hang")}
    gl["fail-iphone"] = _walk_glance("fail", "iphone")
    _gl_srv.shutdown()
    _gl_bad = {case: (w["error"] if "error" in w else _walk_failure((w["tabs"] or [None])[0]) if len(w["tabs"]) != 4
                      else next((f for f in map(_walk_failure, w["tabs"]) if f), None))
               for case, w in gl.items()}
    _gl_bad = {case: why for case, why in _gl_bad.items() if why}
    check(not _gl_bad, f"glance_shift.html: every case walks both tabs in light and dark with metrics -- {_gl_bad}")
    if not _gl_bad:
        _gl = lambda case: {s["id"]: s["metrics"].get("glance") or {} for s in gl[case]["tabs"]}
        _gl_grew = lambda case: {sid: g.get("grew_px") for sid, g in _gl(case).items()}
        _gl_fail = _gl("fail")
        check(all(g.get("state") == "measured" and g.get("below") is True and g.get("grew_px", 0) >= 132 for g in _gl_fail.values())
              and _l10_on(gl["fail"])["status"] == "fail" and len(_l10_on(gl["fail"])["measured"]) == 4,
              f"LAYOUT-10: three rows appended after the read fail on both tabs, light and dark -- {_gl_grew('fail')}")
        _gl_today = _gl_fail.get("desktop-light-today") or {}
        check(_gl_today.get("first_px", 999) < 100 and _gl_today.get("change_count", 0) >= 1
              and all(c.get("ms", 0) >= 4500 for c in _gl_today.get("changes") or [{}]),
              f"LAYOUT-10: Today's 5 s read lands after the walk's fixed settle; only the idle wait sees the card grow at ~5 s -- {_gl_today}")
        check(all(g.get("grew_px", 0) >= 132 for g in _gl("fail-iphone").values()) and _l10_on(gl["fail-iphone"])["status"] == "fail",
              f"LAYOUT-10: the same growth is read in WebKit at the phone width -- {_gl_grew('fail-iphone')}")
        check(all(g.get("state") == "measured" and g.get("grew_px") == 0 for g in _gl("pass").values()) and _l10_on(gl["pass"])["status"] == "pass",
              f"LAYOUT-10: every row drawn at first paint with a dash and a spinning glyph, filled in place, passes -- {_gl_grew('pass')}")
        check(all(g.get("grew_px", 0) < 0 for g in _gl("shrink").values()) and _l10_on(gl["shrink"])["status"] == "pass",
              f"LAYOUT-10: a 'Reading...' block replaced by one shorter row only gets shorter and passes -- {_gl_grew('shrink')}")
        check(all(g.get("state") == "measured" and g.get("grew_px") == 0 and g.get("first_px", 0) > 100 for g in _gl("animate").values())
              and _l10_on(gl["animate"])["status"] == "pass",
              f"LAYOUT-10: a max-height entrance and a scale-in entrance are not growth: the baseline waits for them -- {_gl('animate')}")
        check(all(g.get("state") == "measured" and g.get("grew_px", 0) >= 132 for g in _gl("cols").values()) and _l10_on(gl["cols"])["status"] == "fail",
              f"LAYOUT-10: a card in the left column of a two-column pane is still the first card, and its growth fails -- {_gl('cols')}")
        _gl_last = _l10_on(gl["last"])
        check(all(g.get("below") is False and g.get("grew_px", 0) >= 132 for g in _gl("last").values())
              and _gl_last["status"] == "pass" and "nothing under the card" in _gl_last["reason"],
              f"LAYOUT-10: the same growth with nothing under the card moves nothing: n/a -- {_gl_last['reason']}")
        _gl_none = _l10_on(gl["none"])
        check(all(g.get("state") == "no_card" for g in _gl("none").values()) and _gl_none["status"] == "pass"
              and "no card under the page header" in _gl_none["reason"],
              f"LAYOUT-10: a tab with only a paragraph under its header is n/a -- {_gl_none['reason']}")
        _gl_hang = _l10_on(gl["hang"])
        check(all(g.get("state") == "not_idle" and g.get("idle") is False and g.get("pending") == ["/late"] for g in _gl("hang").values())
              and _gl_hang["status"] == "unmeasured" and "never went quiet" in _gl_hang["reason"],
              f"LAYOUT-10: a read that never lands is unmeasured, naming the request, never a pass -- {_gl_hang['reason']}")
        check(gl["hang"]["seconds"] < gl["pass"]["seconds"] + 4 * getattr(walk, "IDLE_WAIT_MS", 10000) / 1000 + 30,
              f"LAYOUT-10: the idle wait is bounded, so a page that never goes quiet does not hang the walk -- "
              f"{gl['hang']['seconds']}s against {gl['pass']['seconds']}s")

    # stretched graphic: a chart SVG with preserveAspectRatio="none" is not an icon; a real off-step icon still is (#1211)
    sg_dir = STATE / "fixture-stretched-graphic"
    proc_sg = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "stretched_graphic.html").as_uri(),
         "--out", str(sg_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    sgs = json.loads((sg_dir / "screens.json").read_text(encoding="utf-8")) if proc_sg.returncode == 0 else []
    _sg_icons = ((sgs[0].get("metrics") or {}).get("icons") or {}) if sgs else {}
    check(_sg_icons.get("boxes") == {"22x22": 1, "20x20": 1},
          f"COMP-02: the two stretched 162x64 chart SVGs are not icons; the 22px and 20px icons still are (#1211) -- {_sg_icons} ({proc_sg.stderr[-300:]})")

    # pane_split: two tall regions side by side inside one pane count; a lone column and a row of short cards do not (#1361)
    ps_dir = STATE / "fixture-pane-split"
    proc_ps = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "pane_split.html").as_uri(),
         "--out", str(ps_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    pss = {s["id"]: s for s in json.loads((ps_dir / "screens.json").read_text(encoding="utf-8"))} if proc_ps.returncode == 0 else {}
    _ps = {k: ((pss.get(f"desktop-light-{k}") or {}).get("metrics") or {}).get("layout", {}).get("pane_split") for k in ("split", "column", "cards")}
    check(_ps == {"split": True, "column": False, "cards": False},
          f"LAYOUT-06: pane_split is true for a month + day pane inside one pane, false for a lone column and for a row of short cards (#1361) "
          f"-- {_ps} ({proc_ps.stderr[-300:]})")

    # COMP-08: a destructive control in a dialog or menu is unfilled and not the largest labelled control there (#1364)
    do_dir = STATE / "fixture-destructive-overlay"
    proc_do = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "destructive_overlay.html").as_uri(),
         "--out", str(do_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    dos = {s["id"]: s for s in json.loads((do_dir / "screens.json").read_text(encoding="utf-8"))} if proc_do.returncode == 0 else {}

    def _overlay(view: str) -> dict:
        return ((dos.get(f"desktop-light-{view}") or {}).get("metrics") or {}).get("overlay") or {}

    _do_bad = {v: (_overlay(v).get("destructive_bad_count"), [d.get("why") for d in _overlay(v).get("destructive_bad", [])])
               for v in ("menu", "menubad", "dialog-okdialog", "dialog-filldialog", "dialog-tintdialog", "dialog-bigdialog", "dialog-confirmdialog")}
    check(_do_bad == {"menu": (0, []), "menubad": (1, ["filled"]), "dialog-okdialog": (0, []),
                      "dialog-filldialog": (1, ["filled"]), "dialog-tintdialog": (1, ["filled"]), "dialog-bigdialog": (1, ["largest"]),
                      "dialog-confirmdialog": (1, ["filled"])},
          f"COMP-08: a filled Delete (solid or tinted) or one larger than Redo fails; an unfilled last menu Delete and an outlined one "
          f"as wide as Copy pass, and the tinted Delete of a confirm dialog that holds only Cancel beside it fails like any other (#1403) -- {_do_bad} ({proc_do.stderr[-300:]})")
    check(all(_overlay(v).get("destructive") for v in ("menu", "dialog-okdialog")) and _overlay("menu").get("surfaces", 0) >= 1,
          f"COMP-08: the passing menu and dialog still report the destructive control they measured -- {_overlay('menu')}")

    # COMP-16: a dialog's commit (its submit or primary button) is not quieter than another control on its row or below (#1339)
    qc_dir = STATE / "fixture-quiet-commit"
    proc_qc = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "quiet_commit.html").as_uri(),
         "--out", str(qc_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    qcs = {s["id"]: s for s in json.loads((qc_dir / "screens.json").read_text(encoding="utf-8"))} if proc_qc.returncode == 0 else {}

    def _dialogs(view: str) -> dict:
        return ((qcs.get(f"desktop-light-{view}") or {}).get("metrics") or {}).get("dialogs") or {}

    _qc_views = ("okdialog", "ghostdialog", "filledpair", "equaldialog", "smalldialog", "bodydialog", "nocommitdialog")
    _qc = {v: (_dialogs(f"dialog-{v}").get("quiet_commit_count"), [q.get("why") for q in _dialogs(f"dialog-{v}").get("quiet_commit", [])])
           for v in _qc_views}
    check(_qc == {"okdialog": (0, []), "ghostdialog": (1, ["filled"]), "filledpair": (0, []), "equaldialog": (0, []),
                  "smalldialog": (1, ["larger"]), "bodydialog": (0, []), "nocommitdialog": (0, [])},
          f"COMP-16: a ghost Save beside a filled Delete, and a narrow ghost Save beside a wider Cancel, fail; a filled full-width Save alone, "
          f"a filled Save beside an outlined Delete, equal ghosts, a Delete in the body above Save and a dialog with no commit pass -- {_qc} ({proc_qc.stderr[-300:]})")
    check(_dialogs("dialog-okdialog").get("with_commit") == 1 and _dialogs("dialog-nocommitdialog").get("with_commit") == 0
          and _dialogs("dialog-ghostdialog").get("count") == 1,
          f"COMP-16: a dialog with a commit is counted as measured, one without is not -- {_dialogs('dialog-okdialog')} {_dialogs('dialog-nocommitdialog')}")

    # TYPE-05 / TYPE-06: capitals typed in the markup, and one class of label cased two ways across the tabs (#1365)
    def _walk_casing(case: str) -> list:
        out_dir = STATE / f"fixture-casing-{case}"
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "casing.html").as_uri() + f"?case={case}",
             "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return [{"error": proc.stderr[-300:]}]
        walked = json.loads((out_dir / "screens.json").read_text(encoding="utf-8"))
        return [s for s in walked if s["id"].startswith("desktop-light-") and s.get("status") == "ok"] or [{"error": f"no screen walked ok -- {[(s.get('id'), s.get('status'), s.get('error')) for s in walked]}"}]

    def _casing_rules(screens: list) -> dict:
        d = _doc("compliant")
        d["screens"] = [dict(base, id=s["id"], view=s.get("view"), metrics=s["metrics"]) for base, s in zip(d["screens"], screens)]
        return {r["id"]: r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] in ("TYPE-05", "TYPE-06")}

    cs_ok, cs_caps, cs_mixed = _walk_casing("ok"), _walk_casing("caps"), _walk_casing("mixed")
    _cs_all = cs_ok + cs_caps + cs_mixed
    _cs_good = all(_walk_failure(s) is None for s in _cs_all) and len(cs_ok) == len(cs_caps) == len(cs_mixed) == 2
    check(_cs_good, f"casing: each case walks its tabs with metrics -- {[_walk_failure(s) or s['id'] for s in _cs_all]}")
    if _cs_good:
        _lit = [lab["text"] for s in cs_caps for lab in s["metrics"]["casing"]["labels"] if lab["literal"]]
        check(_lit == ["RECORD", "PDF", "ES", "EXPORT PDF", "SETTINGS"],
              f"casing: every label typed in capitals is recorded as literal, the identifier-shaped ACER-PC1 heading is not -- {_lit}")
        _r_caps, _r_ok, _r_mixed = _casing_rules(cs_caps), _casing_rules(cs_ok), _casing_rules(cs_mixed)
        _ok_texts = {lab["text"] for s in cs_ok for lab in s["metrics"]["casing"]["labels"]}
        check("Translate to English" in _ok_texts and not _ok_texts & {"Instructions View", "(no transcript) 0 words"},
              f"casing: a proper noun is recorded, a name-and-action row and a reading with a digit are not -- {sorted(_ok_texts)}")
        check(_r_caps["TYPE-05"]["status"] == "fail"
              and [i["text"] for e in _r_caps["TYPE-05"]["evidence"] for i in e["items"]] == ["RECORD", "EXPORT PDF", "SETTINGS"]
              and _r_caps["TYPE-06"]["status"] == "pass",
              f"TYPE-05: RECORD, a SETTINGS heading and EXPORT PDF fail; PDF and the ES chip pass -- {_r_caps['TYPE-05']['evidence']}")
        check(_r_ok["TYPE-05"]["status"] == "pass" and _r_ok["TYPE-06"]["status"] == "pass",
              f"TYPE-05 / TYPE-06: sentence case, an acronym button, an ES chip and list-row data cased either way pass -- "
              f"{ [(r['id'], r['status'], r['measured']) for r in _r_ok.values()] }")
        _clash = sorted(i["text"] for e in _r_mixed["TYPE-06"]["evidence"] for i in e["items"])
        check(_r_mixed["TYPE-06"]["status"] == "fail" and _clash == ["Open Settings", "Running"] and _r_mixed["TYPE-05"]["status"] == "pass",
              f"TYPE-06: the capitalised stat label and the Title Case button are the minority of their classes across the two tabs -- {_clash}")

    # TYPE-07: a heading rendered smaller than the body text under it (#1341)
    def _walk_heading_body(case: str) -> list:
        out_dir = STATE / f"fixture-heading-body-{case}"
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "heading_body.html").as_uri() + f"?case={case}",
             "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return [{"error": proc.stderr[-300:]}]
        walked = json.loads((out_dir / "screens.json").read_text(encoding="utf-8"))
        return [s for s in walked if s["id"].startswith("desktop-light-") and s.get("status") == "ok"] or [{"error": f"no screen walked ok -- {[(s.get('id'), s.get('status'), s.get('error')) for s in walked]}"}]

    def _t07_hits(screens: list) -> list:
        return [(h.get("text"), h.get("px"), h.get("body_px")) for s in screens for h in (s["metrics"]["text"].get("heading_smaller_than_body") or [])]

    def _t07_status(screens: list) -> str:
        d = _doc("compliant")
        d["screens"] = [dict(base, id=s["id"], view=s.get("view"), metrics=s["metrics"]) for base, s in zip(d["screens"], screens)]
        return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "TYPE-07")["status"]

    hb_fail, hb_pass, hb_edge = _walk_heading_body("fail"), _walk_heading_body("pass"), _walk_heading_body("edge")
    _hb_all = hb_fail + hb_pass + hb_edge
    _hb_good = all(_walk_failure(s) is None for s in _hb_all) and len(hb_fail) == len(hb_pass) == len(hb_edge) == 2
    check(_hb_good, f"heading-body: each case walks its tabs with metrics -- {[_walk_failure(s) or s['id'] for s in _hb_all]}")
    if _hb_good:
        check(_t07_hits(hb_fail) == [("Recent takes", 14, 16), ("Text size", 14, 16)] and _t07_status(hb_fail) == "fail",
              f"TYPE-07: a 14px h2 and a 14px role=heading above 16px paragraphs fail -- {_t07_hits(hb_fail)}")
        check(_t07_hits(hb_pass) == [] and _t07_status(hb_pass) == "pass",
              f"TYPE-07: a 20px h2 above 16px body, a 14px h3 above equal 14px body and a 14px h2 above a 12px caption pass -- {_t07_hits(hb_pass)}")
        check(_t07_hits(hb_edge) == [] and _t07_status(hb_edge) == "pass",
              f"TYPE-07: a stat value, a header row, a heading before a heading, a hidden paragraph, a paragraph four siblings away and a short badge are not body text -- {_t07_hits(hb_edge)}")

    # COMP-17: code at its size, off the dark hole (#1342)
    def _walk_code(case: str) -> list:
        out_dir = STATE / f"fixture-code-{case}"
        proc = _walk_run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "code_spec.html").as_uri() + f"?case={case}",
             "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if proc.returncode != 0:
            return [{"error": proc.stderr[-300:]}]
        walked = json.loads((out_dir / "screens.json").read_text(encoding="utf-8"))
        return [s for s in walked if s.get("status") == "ok"] or [{"error": f"no screen walked ok -- {[(s.get('id'), s.get('status'), s.get('error')) for s in walked]}"}]

    def _c17_hits(screens: list, theme: str) -> list:
        return [(h.get("text"), h.get("px"), h.get("why")) for s in screens if s["id"].startswith(f"desktop-{theme}-")
                for h in (s["metrics"]["text"].get("code_off_spec") or [])]

    def _c17_status(screens: list, theme: str) -> str:
        d = _doc("compliant")
        sc = [s for s in screens if s["id"].startswith(f"desktop-{theme}-")]
        d["screens"] = [dict(base, id=s["id"], view=s.get("view"), metrics=s["metrics"]) for base, s in zip(d["screens"], sc)]
        return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-17")["status"]

    cd_fail, cd_pass, cd_edge = _walk_code("fail"), _walk_code("pass"), _walk_code("edge")
    _cd_all = cd_fail + cd_pass + cd_edge
    _cd_good = all(_walk_failure(s) is None for s in _cd_all) and len(cd_fail) == len(cd_pass) == len(cd_edge) == 6
    check(_cd_good, f"code: each case walks its tabs with metrics -- {[_walk_failure(s) or s['id'] for s in _cd_all]}")
    if _cd_good:
        _c17_light = [("git status", 12, "inline at caption size"), ("git diff", 10.5, "inline under 0.875em"), ("$ git log --oneline", 12, "listing under 13px")]
        check(_c17_hits(cd_fail, "light") == _c17_light and _c17_status(cd_fail, "light") == "fail",
              f"COMP-17: a 12px code in 16px text, a 10.5px code in 14px text and a 12px pre fail in light; the inset-filled code does not -- {_c17_hits(cd_fail, 'light')}")
        check(_c17_hits(cd_fail, "dark") == _c17_light + [("server.py", 14, "darker than its card")] and _c17_status(cd_fail, "dark") == "fail",
              f"COMP-17: in dark the same three fail and a code filled true black on a dark card fails too -- {_c17_hits(cd_fail, 'dark')}")
        check(all(_c17_hits(cd_pass, t) == [] and _c17_status(cd_pass, t) == "pass" for t in ("light", "dark")),
              f"COMP-17: 14px code in 16px text, 12.25px in 14px, a 14px kbd, a 13px pre, the neutral-soft chip and a lighter fill pass in both themes -- "
              f"{_c17_hits(cd_pass, 'light')} {_c17_hits(cd_pass, 'dark')}")
        check(all(_c17_hits(cd_edge, t) == [] and _c17_status(cd_edge, t) == "pass" for t in ("light", "dark")),
              f"COMP-17: hidden code, a terminal, a pre filled with its card's own colour and a code inside a 13px pre are not off-spec -- "
              f"{_c17_hits(cd_edge, 'light')} {_c17_hits(cd_edge, 'dark')}")

    # UA-font nav tab: voice-transcriber's <button class="tab"> tabs had no `font: inherit` and read Arial under a system-ui body;
    # TYPE-02 reads them (a tab is a <button>), an inheriting button beside them is not reported (#1211)
    uf_dir = STATE / "fixture-ua-font-tab"
    proc_uf = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "ua_font_tab.html").as_uri(),
         "--out", str(uf_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    ufs = json.loads((uf_dir / "screens.json").read_text(encoding="utf-8")) if proc_uf.returncode == 0 else []
    _uf_ctl = ((ufs[0].get("metrics") or {}).get("controls") or {}) if ufs else {}
    check(_uf_ctl.get("font_family_mismatch_count") == 2
          and [m["sel"] for m in _uf_ctl.get("font_family_mismatch", [])] == ["button#tabRecord.tab", "button#tabHistory.tab"],
          f"TYPE-02: the two UA-font nav tabs are reported, the inheriting button is not (#1211) -- {_uf_ctl} ({proc_uf.stderr[-300:]})")

    # mono code field: a text-entry control in the spec's mono stack is a code field, not a missed `font: inherit`;
    # a UA-font button, a mono button, a mono select and a non-mono textarea still count (#1462)
    mf_dir = STATE / "fixture-mono-code-field"
    proc_mf = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "mono_code_field.html").as_uri(),
         "--out", str(mf_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    mfs = json.loads((mf_dir / "screens.json").read_text(encoding="utf-8")) if proc_mf.returncode == 0 else []
    _mf_ctl = ((mfs[0].get("metrics") or {}).get("controls") or {}) if mfs else {}
    _mf_sels = [m["sel"] for m in _mf_ctl.get("font_family_mismatch", [])]
    check(bool(mfs) and not any(x in _mf_sels for x in ("textarea#jsonEditor.mono", "input#codeInput.mono")),
          f"TYPE-02: a mono textarea and a mono text input are code fields and not reported (#1462) -- {_mf_ctl} ({proc_mf.stderr[-300:]})")
    check(bool(mfs) and _mf_sels == ["textarea#proseSerif.serif", "button#uaButton.btn-ua", "button#monoButton.btn-mono", "select#monoSelect.mono"],
          f"TYPE-02: a non-mono textarea, a UA-font button, a mono button and a mono select still count (#1462) -- {_mf_ctl}")
    check(_mf_ctl.get("mono_code_field_count") == 2,
          f"TYPE-02: the two exempted code fields are recorded under their own key (#1462) -- {_mf_ctl}")

    # switch on-state: the track colour of each switch that is on is read, an off switch is counted only (#1200)
    sw_dir = STATE / "fixture-switch"
    proc_sw = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "switch.html").as_uri(),
         "--out", str(sw_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    sws = json.loads((sw_dir / "screens.json").read_text(encoding="utf-8")) if proc_sw.returncode == 0 else []
    _sw_ct = ((sws[0].get("metrics") or {}).get("controls") or {}) if sws else {}
    check(_sw_ct.get("switch_count") == 3 and _sw_ct.get("switch_on_count") == 2
          and [t["track"] for t in _sw_ct.get("switches_on", [])] == ["#1a7f37", "#0969da"],
          f"COLOR-04: the track colour of each on switch is read, the off one is only counted (#1200) -- {_sw_ct.get('switches_on')} ({proc_sw.stderr[-300:]})")

    # toast tint: a visible fixed toast with a green border counts, the neutral and the red error do not (#1200)
    ts_dir = STATE / "fixture-toast"
    proc_ts = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "toast.html").as_uri(),
         "--out", str(ts_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    tss = json.loads((ts_dir / "screens.json").read_text(encoding="utf-8")) if proc_ts.returncode == 0 else []
    _ts_fb = ((tss[0].get("metrics") or {}).get("feedback") or {}) if tss else {}
    check(_ts_fb.get("toast_count") == 3 and _ts_fb.get("toasts_tinted_count") == 1
          and _ts_fb["toasts_tinted"][0]["sel"].startswith("div.toast.saved") and _ts_fb["toasts_tinted"][0]["via"] == "border",
          f"COLOR-05: of three visible toasts only the green-bordered one is tinted; the hidden one is not counted (#1200) -- {_ts_fb} ({proc_ts.stderr[-300:]})")

    # settings gear: the walk opens the header gear on every app, with no extra_steps, and measures the Settings pane (#1217)
    def _gear_walk(variant: str, review: dict) -> tuple:
        out = STATE / f"fixture-gear-{variant or 'default'}"
        rv = STATE / f"gear-review-{variant or 'default'}.json"
        rv.write_text(json.dumps(review), encoding="utf-8")
        pr = subprocess.run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"),
             "--url", (FIX / "settings_gear.html").as_uri() + (f"?{variant}" if variant else ""),
             "--out", str(out), "--devices", "desktop", "--scaffold", str(scaffold),
             "--review", str(rv), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        scr = json.loads((out / "screens.json").read_text(encoding="utf-8")) if pr.returncode == 0 else []
        res = ev.evaluate({"screens": scr, "target": "gear-fixture", "wide_views": []}, rubric, _specs("compliant"))
        return {s["id"]: s for s in scr}, next(r for r in res["rules"] if r["id"] == "A11Y-02"), pr.stderr[-300:]

    g_ok, g_ok_a11y, g_err = _gear_walk("", {})
    g_set = g_ok.get("desktop-light-gear-settings", {})
    check(g_set.get("status") == "ok" and g_set.get("kind") == "step"
          and (g_set.get("metrics") or {}).get("a11y", {}).get("text_size_control") is True,
          f"a default walk (no extra_steps) clicks the header gear and measures the Settings pane it opens (#1217) -- {g_set.get('status')} {g_set.get('reason')} {g_err}")
    check(g_ok_a11y["status"] == "pass", f"A11Y-02 passes on a fixture whose text-size control sits behind the gear (#1217) -- {g_ok_a11y['status']}: {g_ok_a11y['reason']}")
    g_no, g_no_a11y, _ = _gear_walk("nocontrol", {})
    check(g_no.get("desktop-light-gear-settings", {}).get("status") == "ok" and g_no_a11y["status"] == "fail",
          f"A11Y-02 fails when the Settings pane the gear opens holds no text-size control, even though the boot stamp is present (#1217) -- {g_no_a11y['status']}: {g_no_a11y['reason']}")
    g_zone, g_zone_a11y, _ = _gear_walk("zone", {"no_go": [".danger-zone"]})
    g_zs = g_zone.get("desktop-light-gear-settings", {})
    check(g_zs.get("status") == "error" and g_zs.get("reason") == "NO_GO" and not g_zs.get("metrics") and g_zone_a11y["status"] == "unmeasured",
          f"a gear inside a declared no_go selector is never clicked; A11Y-02 is unmeasured, never a pass (#1217) -- {g_zs.get('status')} {g_zs.get('reason')} {g_zone_a11y['status']}")
    g_none, g_none_a11y, _ = _gear_walk("nogear", {})
    g_ns = g_none.get("desktop-light-gear-settings", {})
    check(g_ns.get("status") == "absent" and g_ns.get("reason") == "SETTINGS_GEAR_ABSENT" and g_none_a11y["status"] == "unmeasured"
          and "no Settings gear" in g_none_a11y["reason"],
          f"an app with no header gear reports SETTINGS_GEAR_ABSENT as its own state and A11Y-02 unmeasured (#1217) -- {g_ns.get('status')} {g_ns.get('reason')} {g_none_a11y['reason']}")

    # overlay coverage: a surface present in the DOM that no walked screen opened is listed, never graded (#1353)
    def _overlay_walk(variant: str, review: dict) -> tuple:
        out = STATE / f"fixture-overlay-{variant or 'default'}-{len(review)}"
        rv = STATE / f"overlay-review-{variant or 'default'}-{len(review)}.json"
        rv.write_text(json.dumps(review), encoding="utf-8")
        pr = subprocess.run(
            [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"),
             "--url", (FIX / "overlay_coverage.html").as_uri() + (f"?{variant}" if variant else ""),
             "--out", str(out), "--devices", "desktop", "--scaffold", str(scaffold),
             "--review", str(rv), "--params", str(STATE / "steps-params.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        scr = json.loads((out / "screens.json").read_text(encoding="utf-8")) if pr.returncode == 0 else []
        return scr, ev.evaluate({"screens": scr, "target": "overlay-fixture", "wide_views": []}, rubric, _specs("compliant")), pr.stderr[-300:]

    _drawer_step = {"extra_steps": [{"tab": "home", "id": "task-drawer", "click": "#openDrawer"}]}
    ov_scr, ov_res, ov_err = _overlay_walk("", _drawer_step)
    ov_cov = ov_res.get("overlay_coverage") or {}
    check(ov_cov.get("not_walked") == ["aside#itemSheet"] and ov_cov.get("not_walked_count") == 1 and ov_cov.get("present") == 3,
          f"of a declared drawer, an auto-walked dialog[id] and an undeclared sheet, only the sheet is not walked (#1353) -- "
          f"{ov_cov} {ov_err}")
    _no_cov = [{**s, "metrics": {k: v for k, v in (s.get("metrics") or {}).items() if k != "coverage"} if s.get("metrics") else s.get("metrics")}
               for s in ov_scr]
    _no_cov_res = ev.evaluate({"screens": _no_cov, "target": "overlay-fixture", "wide_views": []}, rubric, _specs("compliant"))
    check(bool(ov_scr) and [(r["id"], r["status"]) for r in _no_cov_res["rules"]] == [(r["id"], r["status"]) for r in ov_res["rules"]]
          and _no_cov_res["overall"] == ov_res["overall"] and (_no_cov_res.get("overlay_coverage") or {}).get("status") == "unknown",
          f"the same walk scores the same grade with and without the surface listing (#1353) -- {ov_res['overall']} vs {_no_cov_res['overall']}")
    _, ov_bare, _ = _overlay_walk("", {})
    check((ov_bare.get("overlay_coverage") or {}).get("not_walked") == ["aside#itemSheet", "aside#taskDrawer"],
          f"with no extra_steps entry the drawer is not walked either: the walk opens nothing it was not told to (#1353) -- {ov_bare.get('overlay_coverage')}")
    _, ov_more, _ = _overlay_walk("more", _drawer_step)
    check((ov_more.get("overlay_coverage") or {}).get("not_walked") == ["aside#itemSheet", "dialog.item-editor", "div#tipPop"],
          f"an id-less <dialog> and a [popover] nothing opens are listed too (#1353) -- {ov_more.get('overlay_coverage')}")

    # fold: a popup over a chip far below the fold is covered there too; the walk puts the scroll back (#1155)
    fold_dir = STATE / "fixture-fold"
    proc_fo = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "fold.html").as_uri(),
         "--out", str(fold_dir), "--devices", "desktop,iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    fos = {s["id"]: s for s in json.loads((fold_dir / "screens.json").read_text(encoding="utf-8")) if s["view"] != plan.SETTINGS_GEAR_VIEW} if proc_fo.returncode == 0 else {}
    check(len(fos) == 4, f"the fold fixture walks desktop + iphone x 2 themes ({proc_fo.stderr[-300:]})")
    for _dev in ("desktop", "iphone"):
        _m = fos.get(f"{_dev}-light-root", {}).get("metrics") or {}
        _t = _m.get("targets", {})
        check(_t.get("covered") == [{"a": "button.opt", "b": "a.chip"}],
              f"TOUCH-02 {_dev}: a popup option over a chip below the fold is covered, decided in view (#1155) -- {_t.get('covered')}")
        check(_t.get("overlaps") == [{"a": "button.hit-target", "b": "button.hit-target"}],
              f"TOUCH-02 {_dev}: touching flow controls below the fold still overlap (#1155) -- {_t.get('overlaps')}")
        check(_m.get("nav", {}).get("pane_header_visible") is True,
              f"TOUCH-02 {_dev}: the scroll that brought the pair into view is put back before the next section (#1155)")

    # popovers: the walk opens the first popover of each family, every inline disclosure (#1155).
    # 11 targets = 4 menu summaries + Export + row one's 2 items + 2 accordion summaries + their 2 fields;
    # every row menu open is 15, a second family suppressed 10, an accordion left closed 9 or 10.
    pop_dir = STATE / "fixture-popovers"
    proc_po = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "popovers.html").as_uri(),
         "--out", str(pop_dir), "--devices", "desktop,iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    pos = {s["id"]: s for s in json.loads((pop_dir / "screens.json").read_text(encoding="utf-8")) if s["view"] != plan.SETTINGS_GEAR_VIEW} if proc_po.returncode == 0 else {}
    check(len(pos) == 4, f"the popovers fixture walks desktop + iphone x 2 themes ({proc_po.stderr[-300:]})")
    for _sid, _s in sorted(pos.items()):
        _total = ((_s.get("metrics") or {}).get("targets") or {}).get("total")
        check(_total == 11, f"{_sid}: one popover per family opens, the row menus never all at once; accordions all open (#1155) -- {_total} targets")

    # android: touch emulation survives each full-page screenshot; ARIA grid rows are not action rows (#1017)
    pg_dir = STATE / "fixture-pointer-grid"
    proc_pg = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "pointer_grid.html").as_uri(),
         "--out", str(pg_dir), "--devices", "android", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    pgs = {s["id"]: s for s in json.loads((pg_dir / "screens.json").read_text(encoding="utf-8")) if s["view"] != plan.SETTINGS_GEAR_VIEW} if proc_pg.returncode == 0 else {}
    check(len(pgs) == 4 and all(s["status"] == "ok" for s in pgs.values()), f"the pointer/grid fixture walks 2 tabs x 2 themes on android ({proc_pg.stderr[-300:]})")
    for _sid, _s in sorted(pgs.items()):
        _boxes = (_s.get("metrics") or {}).get("icons", {}).get("boxes")
        check(_boxes == {"20x20": 2}, f"{_sid}: the coarse-pointer tab icons are measured after a full-page screenshot, not the 13px fine-pointer ones (#1017) -- {_boxes}")
    cal = (pgs.get("android-light-calendar", {}).get("metrics") or {}).get("layout", {})
    check(cal.get("rows_over_limit") == [{"sel": "li.over", "controls": 5}],
          f"LAYOUT-03: the month grid's week rows are skipped; a list row with too many controls still fails (#1017) -- {cal.get('rows_over_limit')}")

    # open disclosures in a row: not the row's controls (LAYOUT-03), and a popup over its row takes the tap (TOUCH-02) (#1029)
    disc_dir = STATE / "fixture-disclosure"
    proc_d = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "disclosure.html").as_uri(),
         "--out", str(disc_dir), "--devices", "desktop,iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    discs = {s["id"]: s for s in json.loads((disc_dir / "screens.json").read_text(encoding="utf-8")) if s["view"] != plan.SETTINGS_GEAR_VIEW} if proc_d.returncode == 0 else {}
    check(len(discs) == 4 and all(s["status"] == "ok" for s in discs.values()), f"the disclosure fixture walks desktop + iphone x 2 themes ({proc_d.stderr[-300:]})")
    for _dev in ("desktop", "iphone"):
        _m = (discs.get(f"{_dev}-light-root", {}).get("metrics") or {})
        _rows = _m.get("layout", {}).get("rows_over_limit")
        check(_rows == [{"sel": "li.collapsed-row", "controls": 3}, {"sel": "li.crowded-row", "controls": 4}],
              f"LAYOUT-03 {_dev}: an open menu, an expanded aria-controls drawer and an open details body are not the row's controls; "
              f"a collapsed trigger's neighbours and a crowded row still count (#1029) -- {_rows}")
        _t = _m.get("targets", {})
        if "error" not in _t:
            check(_t.get("covered") == [{"a": "button.main", "b": "button"}],
                  f"TOUCH-02 {_dev}: a menu item drawn over its own row's main control is covered, not an overlap (#1029) -- {_t.get('covered')}")
            check(_t.get("overlap_count") == 2 and all("button.hit-target" in (o["a"], o["b"]) for o in _t.get("overlaps", [])),
                  f"TOUCH-02 {_dev}: touching items inside the one menu still overlap (#1029) -- {_t.get('overlaps')}")

    # measure --synthetic: boot the target's launcher, walk it with synthetic steps + no_go, stop it (#995)
    syn_root = STATE / "synthetic-app"
    syn_root.mkdir(parents=True, exist_ok=True)
    for f in ("synthetic_launcher.py", "fixture.html"):
        shutil.copy(FIX / f, syn_root / f)
    (syn_root / ".fleet.toml").write_text(
        '[design.review]\nno_go = ["#revealBtn"]\n'
        '[design.review.synthetic]\ncommand = ["synthetic_launcher.py"]\nno_go = []\n'
        '[[design.review.extra_steps]]\ntab = "home"\nid = "reveal-synthetic"\nclick = "#revealBtn"\nsynthetic = true\n',
        encoding="utf-8")
    syn_run = STATE / "synthetic-run"
    proc5 = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(syn_root), "--synthetic",
         "--devices", "desktop", "--python", str(interp), "--scaffold", str(scaffold), "--run-dir", str(syn_run),
         "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)},
    )
    syn_lines = dict(l.split("=", 1) for l in proc5.stdout.splitlines() if "=" in l)
    check(proc5.returncode == 0 and syn_lines.get("MODE") == "synthetic" and syn_lines.get("UNMEASURED") == "none"
          and syn_lines.get("SCREENS") == "8/10",
          f"measure --synthetic walks the launcher's instance: 6 screens + the synthetic step in both themes, plus the absent gear ({proc5.stdout[-400:]}{proc5.stderr[-300:]})")
    syn_doc = json.loads((syn_run / "metrics.json").read_text(encoding="utf-8")) if (syn_run / "metrics.json").is_file() else {}
    syn_ids = {s["id"]: s for s in syn_doc.get("screens", [])}
    check(syn_ids.get("desktop-light-home-reveal-synthetic", {}).get("status") == "ok",
          "the synthetic step ran under the synthetic no_go, which lifts the live veto on #revealBtn")
    check(syn_doc.get("mode") == "synthetic" and str(syn_doc.get("base_url", "")).startswith("http://127.0.0.1:")
          and (syn_doc.get("synthetic") or {}).get("stop") == "stopped" and not _port_open(str(syn_doc.get("base_url"))),
          f"metrics.json records the synthetic URL, and the launcher was stopped by closing its stdin -- {syn_doc.get('synthetic')}")
    (syn_root / ".fleet.toml").write_text('[design.review.synthetic]\ncommand = ["synthetic_launcher.py", "--fail"]\n', encoding="utf-8")
    proc6 = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(syn_root), "--synthetic",
         "--devices", "desktop", "--python", str(interp), "--run-dir", str(STATE / "synthetic-run-fail")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)},
    )
    check(proc6.returncode == 0 and "UNMEASURED=SYNTHETIC_FAILED" in proc6.stdout and "MODE=synthetic" in proc6.stdout,
          f"a launcher that never prints a URL makes the run SYNTHETIC_FAILED, not a crash -- {proc6.stdout[-300:]}")

    # inventory over the scaffold's own component gallery, served read-only from its checkout (#1497)
    _demo_dir = scaffold / "app" / "webapp" / "static" / "_vendored"
    _inv_cat = capture.vendored_components(str(scaffold))
    if not (_demo_dir / "demo.html").is_file() or _inv_cat["components"] is None:
        _h.skip(f"browser leg: no project-scaffolding demo.html or component catalog ({_inv_cat['error']}) -- inventory NOT verified")
    else:
        import functools, http.server, threading
        class _InvQuiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 -- keep the test output clean
                pass
        _inv_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_InvQuiet, directory=str(_demo_dir)))
        threading.Thread(target=_inv_srv.serve_forever, daemon=True).start()
        _inv_url = f"http://127.0.0.1:{_inv_srv.server_address[1]}/demo.html"
        (STATE / "inv-params.json").write_text(json.dumps(measure.default_params(components=_inv_cat["components"])), encoding="utf-8")
        (STATE / "inv-params-none.json").write_text(json.dumps(measure.default_params(components_error="no scaffold checkout")), encoding="utf-8")
        _inv_out = {}
        for _run, _p in (("a", "inv-params.json"), ("b", "inv-params.json"), ("none", "inv-params-none.json")):
            _proc = _walk_run([str(interp), str(FIX / "inventory_driver.py"), _inv_url, str(STATE / _p)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            _inv_out[_run] = _proc.stdout if _proc.returncode == 0 else ""
            _inv_out[_run + "_err"] = _proc.stderr[-300:]
        _inv_srv.shutdown()
        _inv = json.loads(_inv_out["a"]) if _inv_out["a"] else {}
        _inv_by_key = {c["key"]: c for c in _inv.get("controls", [])}
        _inv_owner = {cls: name for name, c in _inv_cat["components"].items() for cls in c["roots"]}
        _inv_expect = {"button#demoSwitchOn": ("switch", "vendored:switch"), "button#demoButtonPrimary": ("button", "vendored:button"),
                       "button#demoIconButton": ("icon-button", "vendored:icon-button"), "button#demoRangeDay": ("range-tab", "vendored:range-tab"),
                       "button#demoSegMine": ("segmented", "vendored:segmented"), "select#demoSelectNative": ("select", "vendored:select-native"),
                       "input#demoActionFilterInput": ("input", "vendored:action-row"), "button#bespokeButton": ("button", "bespoke")}
        check(_inv.get("unmeasured") is None and all((_inv_by_key.get(k, {}).get("kind"), _inv_by_key.get(k, {}).get("component")) == v
                                                     for k, v in _inv_expect.items())
              and _inv_by_key["button#bespokeButton"]["label"] == "Bespoke #",
              f"inventory on demo.html: each vendored control reads vendored:<its component> with its kind, the filter field through its "
              f"wrapping label; the hand-added button reads bespoke, its digits masked -- "
              f"{ {k: (_inv_by_key.get(k, {}).get('kind'), _inv_by_key.get(k, {}).get('component')) for k in _inv_expect} } ({_inv_out['a_err']})")
        _inv_bespoke = sorted(c["key"] for c in _inv.get("controls", []) if c["component"] == "bespoke")
        check(_inv_bespoke == ["button#bespokeButton", "button#demoBaseButton", "input#demoBaseInput", "select#demoBaseSelect", "textarea#demoBaseTextarea"]
              and not any(c["component"] == "bespoke" and _inv_owner.get(c["key"].split(".", 1)[-1]) for c in _inv.get("controls", [])),
              f"inventory on demo.html: bespoke is only the added button and base's four bare elements (base styles elements, never a class); "
              f"no control carrying a component's root class reads bespoke -- {_inv_bespoke}")
        check(_inv.get("controls_count") == sum(c["n"] for c in _inv.get("controls", [])) == sum(_inv.get("kinds", {}).values())
              == sum(_inv.get("components", {}).values()) and _inv.get("text_count") == len(_inv.get("text", [])) > 0
              and all(t["runs"] == sum(v["runs"] for v in t["variants"]) for t in _inv.get("text", [])),
              f"inventory: the counts agree with the entries, and every text role carries its variants' runs -- {_inv.get('kinds')} {_inv.get('components')}")
        check(bool(_inv_out["a"]) and _inv_out["a"] == _inv_out["b"],
              f"inventory: two runs over the same page are byte-identical ({len(_inv_out['a'])} vs {len(_inv_out['b'])} bytes) ({_inv_out['b_err']})")
        _inv_none = json.loads(_inv_out["none"]) if _inv_out["none"] else {}
        check(_inv_none.get("unmeasured") == "no scaffold checkout" and _inv_none.get("controls_count") == _inv.get("controls_count")
              and set(_inv_none.get("components", {})) == {"unknown"} and not any(c["component"] == "bespoke" for c in _inv_none.get("controls", [])),
              f"inventory with no catalog: unmeasured says why and every control reads unknown, never bespoke -- "
              f"{_inv_none.get('unmeasured')} {_inv_none.get('components')} ({_inv_out['none_err']})")

    # option counts and group keys (#1505), over a fixture and a hand-made catalog (no scaffold needed)
    _ch_params = STATE / "inv-choices-params.json"
    _ch_params.write_text(json.dumps(measure.default_params(components={"segmented": {"roots": ["segmented"], "parts": ["segmented-item"]}})), encoding="utf-8")
    _ch_out = []
    for _i in range(2):
        _ch_proc = _walk_run([str(interp), str(FIX / "inventory_driver.py"), (FIX / "inventory_choices.html").as_uri(), str(_ch_params)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        _ch_out.append(_ch_proc.stdout if _ch_proc.returncode == 0 else "")
    _ch = json.loads(_ch_out[0]) if _ch_out[0] else {}
    _ch_ctl = _ch.get("controls", [])
    _ch_opts = {c["key"]: c.get("options") for c in _ch_ctl if c["kind"] in ("select", "combobox")}
    _ch_groups = {}
    for _c in _ch_ctl:
        if _c["kind"] == "segmented":
            _ch_groups[_c["group"]] = _ch_groups.get(_c["group"], 0) + _c["n"]
    check(_ch_opts == {"select#model": 6, "input#city": 2, "input#tag": 3, "input#gone": None},
          f"inventory: a select, a datalist input and an aria-controls listbox carry their option count; a listbox not in the DOM carries none -- {_ch_opts}")
    check(_ch_groups == {"div.segmented": 3, "div.segmented~2": 2, "div#scope": 2},
          f"inventory: segmented controls carry their group key, numbered in document order when several share one -- {_ch_groups}")
    check(bool(_ch_out[0]) and _ch_out[0] == _ch_out[1],
          f"inventory: two runs over the choices page are byte-identical ({len(_ch_out[0])} vs {len(_ch_out[1])} bytes)")

    # measure --e2e (#1498): a throwaway copy of the fixture target, project-scaffolding's vendored surface() hook
    # copied in as an adopter vendors it; its own pytest suite captures two surfaces (one on a Pixel 7 context).
    _e2e_hook = scaffold / "tests" / "e2e" / "_surface.py"
    _e2e_pytest = _walk_run([str(interp), "-c", "import pytest"], capture_output=True, timeout=60).returncode == 0
    if not _e2e_hook.is_file() or not _e2e_pytest:
        _h.skip("browser leg: no tests/e2e/_surface.py in project-scaffolding, or no pytest in the sibling venv -- measure --e2e NOT verified")
    else:
        _e2e_root = STATE / "e2e-target"
        shutil.copytree(FIX / "e2e_target", _e2e_root)
        shutil.copy(_e2e_hook, _e2e_root / "tests" / "e2e" / "_surface.py")

        def _measure_e2e(run_name: str, *extra: str, root: Path = _e2e_root):
            p = _walk_run([sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(root),
                           "--url", (root / "page.html").as_uri(), "--devices", "desktop", "--python", str(interp),
                           "--scaffold", str(scaffold), "--run-dir", str(STATE / run_name), "--rubric", str(RUBRIC),
                           "--spec", str(FIX / "spec_compliant.md"), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                          env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)})
            lines = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
            mpath = Path(lines.get("METRICS", ""))
            return p, lines, (json.loads(mpath.read_text(encoding="utf-8")) if lines.get("METRICS") and mpath.is_file() else {})

        _pe, _le, _de = _measure_e2e("e2e-run-a", "--e2e")
        _e2e_by = {s["id"]: s for s in _de.get("screens", []) if s.get("source") == "e2e"}
        check(_pe.returncode == 0 and _le.get("UNMEASURED") == "none" and _le.get("E2E") == "ok"
              and _le.get("E2E_SURFACES") == "2" and _le.get("E2E_SCREENS") == "4",
              f"measure --e2e runs the target's own suite after the walk and reports its outcome (#1498) -- {_pe.stdout[-500:]}{_pe.stderr[-300:]}")
        check(set(_e2e_by) == {f"{d}-{t}-e2e-{v}" for d, v in (("desktop", "items-sheet"), ("android", "home-root")) for t in ("light", "dark")}
              and all(s["kind"] == "step" and s["engine"] == "chromium" for s in _e2e_by.values())
              and (_e2e_by.get("android-light-e2e-home-root") or {}).get("viewport", {}).get("width") == 412,
              f"two surface() calls -> four e2e screens, each on the device its test's context was (Pixel 7 -> android) -- {sorted(_e2e_by)}")
        check(len(_e2e_by) == 4 and all(s["status"] == "ok" and not measure.section_errors(s["metrics"])
                                        and isinstance(s["metrics"].get("inventory"), dict) and "error" not in s["metrics"]["inventory"]
                                        and s["metrics"]["inventory"]["controls_count"] > 0 and s["theme_applied"] is True
                                        and (STATE / "e2e-run-a" / "shots" / str(s["screenshot"])).is_file()
                                        for s in _e2e_by.values()),
              "every e2e screen carries full metrics, its inventory included, the theme it asked for, and its screenshot in shots/")
        check("1 deselected" in (STATE / "e2e-run-a" / "e2e.log").read_text(encoding="utf-8"),
              "in capture mode the hook deselects the suite's test that never calls surface(")
        _ev_e = ev.evaluate(_de, rubric, _specs("compliant"))
        _pp, _lp, _dp = _measure_e2e("e2e-run-plain")
        _ev_p = ev.evaluate(_dp, rubric, _specs("compliant"))
        check(_ev_p["overlay_coverage"].get("not_walked") == ["aside#itemSheet"] and _ev_e["overlay_coverage"].get("not_walked_count") == 0,
              f"overlay coverage counts the sheet only the e2e suite opened as walked -- plain {_ev_p['overlay_coverage']} e2e {_ev_e['overlay_coverage']}")
        _walk_ids = [s["id"] for s in _de.get("screens", []) if s.get("source") != "e2e"]
        check(_pp.returncode == 0 and "e2e" not in _dp and "E2E" not in _lp and not (STATE / "e2e-run-plain" / "e2e").exists()
              and _walk_ids == [s["id"] for s in _dp.get("screens", [])]
              and all((a.get("metrics") or {}).get("inventory") == (b.get("metrics") or {}).get("inventory")
                      for a, b in zip([s for s in _de["screens"] if s.get("source") != "e2e"], _dp["screens"])),
              "without --e2e, measure runs no suite and writes no e2e key or line; the walk's own screens are the same either way")
        _pe2, _le2, _de2 = _measure_e2e("e2e-run-b", "--e2e")
        _e2e_by2 = {s["id"]: s for s in _de2.get("screens", []) if s.get("source") == "e2e"}
        check(_le2.get("E2E") == "ok" and set(_e2e_by2) == set(_e2e_by)
              and all(_e2e_by2[k]["metrics"]["inventory"] == _e2e_by[k]["metrics"]["inventory"] for k in _e2e_by),
              "a second --e2e run over the same tree gives an identical inventory for every e2e screen")

        # #1507: a page that stamps data-theme="light" at load keeps it through the hook's dark flip unless the app
        # declares [design.review] theme_storage_key, so every dark capture is light: its own state, not "ok".
        _stamp_root = STATE / "e2e-target-stamped"
        shutil.copytree(_e2e_root, _stamp_root)
        _stamp_page = _stamp_root / "page.html"
        _stamp_page.write_text(_stamp_page.read_text(encoding="utf-8").replace(
            "</body>", '<script>document.documentElement.setAttribute("data-theme", "light");</script>\n</body>'), encoding="utf-8")
        _ps, _ls, _ds = _measure_e2e("e2e-run-stamped", "--e2e", root=_stamp_root)
        _stamp_dark = sorted(s["id"] for s in _ds.get("screens", []) if s.get("source") == "e2e" and s["theme"] == "dark")
        check(_ps.returncode == 0 and _ls.get("E2E") == "E2E_THEME_MISMATCH" and _ls.get("E2E_SCREENS") == "4"
              and sorted(m["id"] for m in _ds["e2e"]["theme_mismatch"]) == _stamp_dark and len(_stamp_dark) == 2
              and _ls.get("E2E_TREE") == "unchecked",
              f"a load-time data-theme and no theme_storage_key: the dark captures are reported E2E_THEME_MISMATCH, not ok (#1507) -- {_ps.stdout[-600:]}")
        with open(_stamp_root / ".fleet.toml", "a", encoding="utf-8") as _f:
            _f.write('\n[design.review]\ntheme_storage_key = "fixture.theme"\n')
        _pk, _lk, _dk = _measure_e2e("e2e-run-keyed", "--e2e", root=_stamp_root)
        check(_pk.returncode == 0 and _lk.get("E2E") == "ok" and _dk["e2e"]["theme_mismatch"] == [] and _dk["e2e"]["warning"] is None,
              f"with theme_storage_key declared the same page raises no mismatch (#1507) -- {_pk.stdout[-600:]}")

_h.report_and_exit("test_design_review_browser", skip_code=SKIP_EXIT)
