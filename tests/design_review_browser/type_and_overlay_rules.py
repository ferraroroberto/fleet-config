import json
import subprocess

from _suite import REPO, STATE, _walk_failure, _walk_run, check, interp, scaffold
from design_review import evaluate as ev, plan
from design_review_support import FIX, doc as _doc, rubric, specs as _specs


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
