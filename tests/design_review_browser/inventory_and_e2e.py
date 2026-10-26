import json
import os
import shutil
import sys
from pathlib import Path

from _suite import REPO, STATE, _h, _walk_run, check, interp, scaffold
from design_review import capture, evaluate as ev, measure
from design_review_support import FIX, RUBRIC, rubric, specs as _specs


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
