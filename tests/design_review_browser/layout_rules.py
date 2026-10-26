import json
import subprocess

from _suite import REPO, STATE, check, interp, scaffold
from measure_fixture import by_id
from design_review import evaluate as ev, report as report_mod
from design_review_support import FIX, rubric, specs as _specs


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
import functools, http.server, threading  # noqa: E402
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
