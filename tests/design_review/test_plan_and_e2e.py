"""Unit tests for design_review's plan resolution, the missing-target and theme-mismatch states, the synthetic instance and `measure --e2e` (fleet-config#995, #1498, #1507).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_plan_and_e2e.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from _suite import STATE, _h, check  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

from design_review import capture, evaluate as ev, measure, plan  # noqa: E402
from design_review import report as report_mod  # noqa: E402
from design_review_support import (  # noqa: E402
    FIX, rubric,
    doc as _doc, out_c, port_open as _port_open,
    specs as _specs, statuses_c,
)


# ---- plan: target resolution from projects.toml + .fleet.toml ----------------

tmp = Path(throwaway_dir("design-review-plan-"))
repo_dir = tmp / "demo-app"
repo_dir.mkdir()
(repo_dir / ".fleet.toml").write_text(
    'layer = "working-web"\n[design.review]\ntheme_storage_key = "demo.theme"\nno_go = ["#danger"]\n'
    '[[design.review.extra_steps]]\ntab = "home"\nid = "kebab"\nclick = ".row .kebab"\n', encoding="utf-8")
projects = tmp / "projects.toml"
projects.write_text(
    f'[demo-app]\ncwd_prefix = "{repo_dir.as_posix()}"\nwebapp_port = 8555\nbrowser_scheme = "https"\n'
    f'[no-port]\ncwd_prefix = "{(tmp / "no-port").as_posix()}"\n[global]\nnever_kill_ports = []\n', encoding="utf-8")
t = plan.resolve_target("demo-app", projects)
check(t.name == "demo-app" and t.base_url == "https://127.0.0.1:8555" and t.root == repo_dir, "name -> loopback base url + root")
check(t.review["theme_storage_key"] == "demo.theme" and t.review["extra_steps"][0]["click"] == ".row .kebab", "[design.review] block loaded")
wt = tmp / "demo-app-wt-42"
wt.mkdir()
check(plan.resolve_target(str(wt), projects).name == "demo-app", "a -wt-<N> worktree path resolves to its repo")
check(plan.resolve_target("demo-app", projects, "file:///x/y.html/").base_url == "file:///x/y.html", "--url override wins")
for bad, label in (("nope", "unknown target"), ("no-port", "declared repo without a port")):
    try:
        plan.resolve_target(bad, projects)
        check(False, f"PlanError on {label}")
    except plan.PlanError:
        check(True, label)
# A repo with no `webapp_port` in projects.toml but a `port` in its own `.fleet.toml` resolves (fleet-config#1180,
# facilitation-suite#164: `measure facilitation-suite` stopped with BAD_TARGET and needed `--url`). The scheme is probed.
fs_dir = tmp / "fs-app"
fs_dir.mkdir()
(fs_dir / ".fleet.toml").write_text('layer = "working-web"\nport = ":8449"\n', encoding="utf-8")
fs_projects = tmp / "fs-projects.toml"
fs_projects.write_text(f'[fs-app]\ncwd_prefix = "{fs_dir.as_posix()}"\n[demo-app]\ncwd_prefix = "{repo_dir.as_posix()}"\n'
                       f'webapp_port = 8555\nbrowser_scheme = "https"\n', encoding="utf-8")
probed: list = []
fs_t = plan.resolve_target("fs-app", fs_projects, scheme_probe=lambda port: probed.append(port) or "https")
check(fs_t.base_url == "https://127.0.0.1:8449" and probed == [8449] and fs_t.root == fs_dir,
      f"no webapp_port: the repo's own .fleet.toml `port` and a probed scheme make the base url -- {fs_t.base_url}")
check(plan.resolve_target("fs-app", fs_projects, scheme_probe=lambda port: "http").base_url == "http://127.0.0.1:8449",
      "a plain-HTTP app resolves to http")
probed.clear()
check(plan.resolve_target("demo-app", fs_projects, scheme_probe=lambda port: probed.append(port) or "http").base_url == "https://127.0.0.1:8555"
      and probed == [], "projects.toml wins: its port and scheme are used and nothing is probed")
for raw, want in (('":8449"', 8449), ("8449", 8449), ('"127.0.0.1:8449"', 8449), ('"abc"', None), ("0", None), ("70000", None), ('""', None)):
    (fs_dir / ".fleet.toml").write_text(f"port = {raw}\n", encoding="utf-8")
    check(plan.declared_port(fs_dir) == want, f"`.fleet.toml` port {raw} -> {want}")
(fs_dir / ".fleet.toml").write_text("layer = [broken", encoding="utf-8")
check(plan.declared_port(fs_dir) is None and plan.declared_port(None) is None and plan.declared_port(tmp / "absent") is None,
      "an unparseable, absent or rootless .fleet.toml declares no port")
try:
    plan.resolve_target("fs-app", fs_projects, scheme_probe=lambda port: "https")
    check(False, "PlanError when neither projects.toml nor .fleet.toml declares a port")
except plan.PlanError as exc:
    check("no `port` in its .fleet.toml" in str(exc), f"the refusal names both places it looked -- {exc}")
import socket as _socket  # noqa: E402
import threading as _threading  # noqa: E402
_srv = _socket.socket()
_srv.bind(("127.0.0.1", 0))
_srv.listen(1)
_threading.Thread(target=lambda: _srv.accept()[0].close(), daemon=True).start()
check(plan.probe_scheme(_srv.getsockname()[1]) == "http", "a port that does not complete a TLS handshake is http")
_srv.close()
check(plan.probe_scheme(1) == "http", "a port nothing listens on is http (the listening probe then reports it, never a guess)")
(repo_dir / ".fleet.toml").write_text("layer = [broken", encoding="utf-8")
check("error" in plan.load_review_block(repo_dir), "an unparseable .fleet.toml is an error key, not an empty block")
check(plan.load_review_block(tmp / "absent") == {} and plan.load_review_block(None) == {}, "no file / no root -> {}")
check(plan.screen_id("iphone", "light", "board") == "iphone-light-board"
      and plan.screen_id("desktop", "dark", "dialog-Settings Dialog") == "desktop-dark-dialog-settings-dialog", "screen ids")
check(plan.device_list(None) == ["iphone", "desktop", "android"] and plan.device_list(["desktop"]) == ["desktop"], "device list")
try:
    plan.device_list(["tablet"])
    check(False, "unknown device refused")
except plan.PlanError:
    check(True, "unknown device refused")

# ---- #995: a missing step target is its own state -----------------------------

_absent = {"id": "desktop-light-home-row-menu", "device": "desktop", "theme": "light", "view": "home-row-menu",
           "kind": "step", "status": "absent", "reason": "STEP_TARGET_ABSENT", "error": "#rowKebab never appeared",
           "metrics": None}
_doc_a = _doc("compliant")
_doc_a["screens"].append(dict(_absent))
_out_a = ev.evaluate(_doc_a, rubric, _specs("compliant"))
_st_a = {r["id"]: r["status"] for r in _out_a["rules"]}
check(_st_a == statuses_c, f"an absent step target leaves every rule as it was without that screen (#995) -- "
      f"{ {k: v for k, v in _st_a.items() if v != statuses_c[k]} }")
check(_out_a["overall"] == out_c["overall"] and not any(v["unmeasured"] for v in _out_a["categories"].values()),
      "an absent step target does not flag any category or the verdict unmeasured")
check(_out_a.get("absent_screens") == ["desktop-light-home-row-menu"], "evaluate lists the absent step screens")
check(any("step target absent on 1" in r["reason"] for r in _out_a["rules"]), "a rule that would have read the screen says so in its reason")
_doc_only = _doc("compliant")
_doc_only["screens"] = [dict(_absent)]
_out_only = ev.evaluate(_doc_only, rubric, _specs("compliant"))
check(all(r["status"] == "unmeasured" for r in _out_only["rules"] if not r["metric"].startswith("spec.")),
      "a screen-read rule whose only applicable screen is absent is unmeasured, never a vacuous pass")
_doc_e = _doc("compliant")
_doc_e["screens"].append({**_absent, "status": "error", "reason": "TIMEOUT"})
check(any(r["status"] == "unmeasured" for r in ev.evaluate(_doc_e, rubric, _specs("compliant"))["rules"]),
      "a step that errored for another reason still makes its rules unmeasured")

# ---- #1216: a leg whose app ignores the stamped theme is not measured as the requested one ----

_dark_obs = {"attr": "dark", "luminance": 0.005}
_light_obs = {"attr": "light", "luminance": 0.93}
check(measure.theme_agreement("light", _light_obs)[0] is True and measure.theme_agreement("dark", _dark_obs)[0] is True,
      "a page that renders the requested theme is applied")
check(measure.theme_agreement("light", _dark_obs)[0] is False, "light requested, dark rendered (attribute and canvas) is not applied")
check(measure.theme_agreement("light", {"attr": "light", "luminance": 0.005})[0] is False,
      "an app that ignores the stamp but paints a dark canvas is caught by the luminance alone")
check(measure.theme_agreement("light", {"attr": "dark", "luminance": None})[0] is False,
      "an app that re-applies its own attribute is caught by the attribute alone")
check(measure.theme_agreement("light", {"attr": "light", "luminance": None})[0] is True
      and measure.theme_agreement("light", {"attr": "light", "luminance": 0.35})[0] is True,
      "an unreadable or mid-grey canvas is not a disagreement")
check(measure.theme_agreement("light", None)[0] is None, "no observation is unknown, never a verdict")
_applied, _seen = measure.theme_agreement("light", _dark_obs)
check(_seen["attr"] == "dark" and _seen["rendered"] == "dark", f"the observation names what rendered -- {_seen}")
check([r.id for r in rubric.rules if r.theme_dependent] == ["COLOR-02", "COLOR-03", "COLOR-04", "COLOR-05"],
      "the rules that judge a palette are declared theme_dependent in the rubric")

_doc_t = _doc("compliant")
_light_sid = "desktop-light-home"
for _s in _doc_t["screens"]:
    _s["theme_applied"] = True
_base_t = {r["id"]: r for r in ev.evaluate(_doc_t, rubric, _specs("compliant"))["rules"]}
for _s in _doc_t["screens"]:
    if _s["id"] == _light_sid:
        _s["theme_applied"] = False
        _s["theme_observed"] = {"attr": "dark", "luminance": 0.005, "rendered": "dark"}
_out_t = ev.evaluate(_doc_t, rubric, _specs("compliant"))
_rt = {r["id"]: r for r in _out_t["rules"]}
for _rid in ("COLOR-02", "COLOR-03", "COLOR-04", "COLOR-05"):
    check(_rt[_rid]["status"] == "unmeasured" and "requested light" in _rt[_rid]["reason"] and "rendered dark" in _rt[_rid]["reason"]
          and _light_sid not in _rt[_rid]["measured"],
          f"{_rid} on a screen that did not render the requested theme is unmeasured, naming both themes -- {_rt[_rid]['status']} {_rt[_rid]['reason']}")
check(all(_rt[r]["status"] == _base_t[r]["status"] and _rt[r]["measured"] == _base_t[r]["measured"]
          for r in _rt if not _rt[r]["metric"].startswith("spec.") and r not in ("COLOR-02", "COLOR-03", "COLOR-04", "COLOR-05")),
      "touch, layout, nav and type rules still score that screen, unchanged")
check(_out_t["theme_mismatch_screens"] == [{"id": _light_sid, "requested": "light", "rendered": "dark"}],
      f"evaluate lists the screens that did not render the requested theme -- {_out_t.get('theme_mismatch_screens')}")
_unk = _doc("compliant")
for _s in _unk["screens"]:
    _s["theme_applied"] = None
_unk_out = ev.evaluate(_unk, rubric, _specs("compliant"))
check({r["id"]: r["status"] for r in _unk_out["rules"]} == {k: v["status"] for k, v in _base_t.items()} and _unk_out["theme_mismatch_screens"] == [],
      "a screen whose theme could not be observed is left as it was, never flagged")
_html_t = report_mod._method(_out_t)
check("did not render the requested theme" in _html_t and _light_sid in _html_t,
      "the report's method section says which leg was not the theme it is labelled")
check("did not render the requested theme" not in report_mod._method(ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))),
      "no caveat when every screen rendered what was asked")

# ---- #1353: overlay surfaces no walked screen opened are listed as coverage, never graded ----

def _with_overlays(per_screen: dict) -> dict:
    doc = _doc("compliant")
    for s in doc["screens"]:
        listed = per_screen.get(s["id"], [])
        s["metrics"]["coverage"] = {"overlays": [{"key": k, "kind": kind, "open": op} for k, kind, op in listed],
                                    "overlay_count": len(listed)}
    return doc

_ov_doc = _with_overlays({
    "desktop-light-home": [("aside#taskDrawer", "role-dialog", False), ("aside#itemSheet", "role-dialog", False),
                           ("dialog#edit", "dialog", False)],
    "iphone-light-home": [("aside#taskDrawer", "role-dialog", True), ("aside#itemSheet", "role-dialog", False),
                          ("div#tipPop", "popover", False), ("dialog#edit", "dialog", False)],
    "desktop-light-dialog-edit": [("dialog#edit", "dialog", True)],
})
_ov_out = ev.evaluate(_ov_doc, rubric, _specs("compliant"))
check(_ov_out.get("overlay_coverage") == {"status": "measured", "present": 4, "walked": 2,
                                          "not_walked": ["aside#itemSheet", "div#tipPop"], "not_walked_count": 2},
      f"evaluate lists the overlay surfaces present on a walked screen that none showed open (#1353) -- {_ov_out.get('overlay_coverage')}")
check([{k: r[k] for k in ("id", "status", "measured")} for r in _ov_out["rules"]]
      == [{k: r[k] for k in ("id", "status", "measured")} for r in out_c["rules"]]
      and _ov_out["categories"] == out_c["categories"] and _ov_out["overall"] == out_c["overall"],
      "the coverage line never moves a rule, a category or the grade (#1353)")
check(ev.evaluate(_doc("compliant"), rubric, _specs("compliant")).get("overlay_coverage")
      == {"status": "unknown", "present": None, "walked": None, "not_walked": [], "not_walked_count": None},
      "metrics with no surface listing (an older run) leave overlay coverage unknown, never 'nothing missed' (#1353)")
_ov_many = _with_overlays({"desktop-light-home": [(f"dialog#d{i:02d}", "dialog", False) for i in range(12)]})
_ov_many_out = ev.evaluate(_ov_many, rubric, _specs("compliant")).get("overlay_coverage") or {}
check(_ov_many_out.get("not_walked_count") == 12 and len(_ov_many_out.get("not_walked") or []) == getattr(ev, "OVERLAY_SAMPLE", None),
      f"the not-walked sample is capped and the count stays true (#1353) -- {_ov_many_out}")
_ov_html = report_mod._method(_ov_out)
check("not walked: 2 overlay surfaces (aside#itemSheet, div#tipPop)" in _ov_html and "extra_steps" in _ov_html
      and "does not count" in _ov_html,
      "the report's method list carries the not-walked line, the extra_steps hint, and says it is outside the grade (#1353)")
check("+4 more" in report_mod._method(ev.evaluate(_ov_many, rubric, _specs("compliant"))),
      "the report says how many more surfaces the capped sample left out (#1353)")
_ov_all = _with_overlays({"desktop-light-home": [("dialog#edit", "dialog", False)],
                          "desktop-light-dialog-edit": [("dialog#edit", "dialog", True)]})
check(not re.search(r"not walked: \d+ overlay", report_mod._method(ev.evaluate(_ov_all, rubric, _specs("compliant")))),
      "no not-walked line when every present surface was open on some walked screen (#1353)")
check("overlay coverage not established" in report_mod._method(out_c),
      "an unknown overlay coverage is said, never left silent (#1353)")

# ---- #995: the synthetic instance: contract, validation, lifecycle ------------

import shutil  # noqa: E402
import urllib.request  # noqa: E402


def _synthetic_target(command, name="synthetic-target"):
    root = STATE / name
    root.mkdir(parents=True, exist_ok=True)
    for f in ("synthetic_launcher.py", "fixture.html"):
        shutil.copy(FIX / f, root / f)
    return plan.Target(name=name, root=root, base_url=plan.SYNTHETIC_URL,
                       review={"synthetic": {"command": command}} if command is not None else {})


def _plan_error(target):
    try:
        plan.synthetic_block(target)
    except plan.PlanError as exc:
        return str(exc)
    return None


check("declares no [design.review.synthetic]" in (_plan_error(_synthetic_target(None)) or ""), "no block -> a named PlanError")
check("inside the target checkout" in (_plan_error(_synthetic_target(["../synthetic_launcher.py"])) or ""),
      "a command outside the target root is refused")
check("does not exist" in (_plan_error(_synthetic_target(["missing.py"])) or ""), "a command that is not there is refused")
check("non-empty list" in (_plan_error(_synthetic_target([])) or ""), "an empty command is refused")
_blk = plan.synthetic_block(_synthetic_target(["synthetic_launcher.py", "--x"]))
check(_blk["command"] == [str((STATE / "synthetic-target" / "synthetic_launcher.py").resolve()), "--x"]
      and _blk["no_go"] is None and _blk["startup_timeout_s"] == plan.SYNTHETIC_STARTUP_S,
      "a valid block resolves the script inside the root and keeps its arguments")


_run = STATE / "synthetic-lifecycle"
_run.mkdir(parents=True, exist_ok=True)
_inst = capture.SyntheticInstance(sys.executable, _synthetic_target(["synthetic_launcher.py"]),
                                  plan.synthetic_block(_synthetic_target(["synthetic_launcher.py"])), _run)
_st = _inst.start()
_served = bool(_st["url"]) and b"design_review fixture" in urllib.request.urlopen(_st["url"], timeout=5).read()
_how = _inst.stop()
check(_served and _how == "stopped" and not _port_open(_st["url"]),
      f"the launcher prints its URL, serves it, and exits when its stdin closes (#995) -- {_st} {_how}")
check("synthetic fixture: stopped" in (_run / "synthetic.log").read_text(encoding="utf-8"), "the launcher's output lands in synthetic.log")
_fail = capture.SyntheticInstance(sys.executable, _synthetic_target(["synthetic_launcher.py", "--fail"]),
                                  plan.synthetic_block(_synthetic_target(["synthetic_launcher.py", "--fail"])), _run)
_fs = _fail.start()
check(_fs["url"] is None and "exited (3) before printing URL=" in str(_fs["error"]) and _fail.stop() == "stopped",
      f"a launcher that exits first is reported with its exit code, never a URL -- {_fs}")
_saved_stop = capture.SYNTHETIC_STOP_S
capture.SYNTHETIC_STOP_S = 1.0
try:
    _hang = capture.SyntheticInstance(sys.executable, _synthetic_target(["synthetic_launcher.py", "--hang"]),
                                      plan.synthetic_block(_synthetic_target(["synthetic_launcher.py", "--hang"])), _run)
    _hs = _hang.start()
    _hh = _hang.stop()
finally:
    capture.SYNTHETIC_STOP_S = _saved_stop
check(_hs["url"] and _hh == "killed" and not _port_open(_hs["url"]),
      f"a launcher that ignores stdin EOF is tree-killed after the grace (#995) -- {_hh}")

_doc_s = _doc("compliant")
_doc_s["mode"] = "synthetic"
_out_s = ev.evaluate(_doc_s, rubric, _specs("compliant"))
check(_out_s["mode"] == "synthetic" and ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))["mode"] == "live",
      "evaluate carries the run's mode; a document without one is live")

# ---- #1498: measure --e2e: the block, the capture directory, one word per suite outcome ------------

import time  # noqa: E402


def _e2e_target(review=None, hook=True, name="e2e-target"):
    root = STATE / name
    if root.exists():
        shutil.rmtree(root)
    (root / "tests" / "e2e").mkdir(parents=True)
    if hook:
        (root / "tests" / "e2e" / "_surface.py").write_text("# stand-in: these suites never import it\n", encoding="utf-8")
    return plan.Target(name=name, root=root, base_url="file:///x.html", review=review or {})


def _e2e_run(command, timeout_s=60, run_name="e2e-run", doc=None, review_extra=None, **target_kw):
    target = _e2e_target({"e2e": {"command": command, "timeout_s": timeout_s}, **(review_extra or {})}, **target_kw)
    run = STATE / run_name
    run.mkdir(parents=True, exist_ok=True)
    doc = doc or {"interpreter": sys.executable, "unmeasured": None, "screens": [],
                  "params": {"script": measure.default_params()}}
    return doc, capture.measure_e2e(doc, target, run, str(STATE / "no-scaffold")), run


# Stands in for a suite whose tests call surface(): writes the hook's two files per theme, then exits with argv[1].
_E2E_WRITES = (
    "import json, os, pathlib, sys\n"
    "d = pathlib.Path(os.environ['DESIGN_SURFACE_CAPTURE'])\n"
    "for t in ('dark', 'light'):\n"
    "    m = None if len(sys.argv) > 2 else {'text': {'sizes': {}}}\n"
    "    (d / f'jobs.sort-sheet.{t}.json').write_text(json.dumps({'surface': 'jobs.sort-sheet', 'theme': t, "
    "'engine': 'webkit', 'phone': True, 'viewport': {'width': 390, 'height': 664}, "
    "'observed': {'attr': None, 'dark': t == 'dark'}, 'error': 'boom' if m is None else None, 'metrics': m}))\n"
    "    (d / f'jobs.sort-sheet.{t}.png').write_bytes(b'png')\n"
    "sys.exit(int(sys.argv[1]))\n")

_eb = plan.e2e_block(_e2e_target())
check(_eb == {"command": ["-m", "pytest", "tests/e2e"], "timeout_s": 900.0},
      f"no [design.review.e2e] block is the default suite, never an error (#1498) -- {_eb}")


def _e2e_block_error(block):
    try:
        plan.e2e_block(_e2e_target({"e2e": block}))
    except plan.PlanError as exc:
        return str(exc)
    return None


check("must be a table" in (_e2e_block_error("pytest") or "")
      and "non-empty list of strings" in (_e2e_block_error({"command": "-m pytest"}) or "")
      and "non-empty list of strings" in (_e2e_block_error({"command": []}) or "")
      and "positive number" in (_e2e_block_error({"timeout_s": 0}) or "")
      and "positive number" in (_e2e_block_error({"timeout_s": True}) or ""),
      "a malformed [design.review.e2e] block is a named PlanError, never a guessed command")

check([capture.e2e_device(c) for c in ({"phone": False, "engine": "webkit"}, {"phone": True, "engine": "webkit"},
                                       {"phone": True, "engine": "chromium"}, {})] == ["desktop", "iphone", "android", "desktop"],
      "a capture's device comes from the test's own context: phone UA + engine, else desktop")

_, _e_skip, _ = _e2e_run(["-c", "pass"], doc={"interpreter": sys.executable, "unmeasured": {"reason": "NOT_LISTENING"},
                                               "screens": [], "params": {"script": {}}})
check((_e_skip["unmeasured"] or {}).get("reason") == "E2E_SKIPPED" and "NOT_LISTENING" in _e_skip["unmeasured"]["detail"],
      f"a walk that measured nothing runs no suite: E2E_SKIPPED, naming the walk's reason -- {_e_skip['unmeasured']}")
_e_bad_t = _e2e_target({"e2e": {"command": "x"}})
_e_bad = capture.measure_e2e({"interpreter": sys.executable, "unmeasured": None, "screens": [], "params": {"script": {}}},
                             _e_bad_t, STATE / "e2e-run", str(STATE / "no-scaffold"))
check((_e_bad["unmeasured"] or {}).get("reason") == "E2E_BAD_CONFIG", f"a malformed block is E2E_BAD_CONFIG -- {_e_bad['unmeasured']}")
_, _e_hook, _ = _e2e_run(["-c", "pass"], hook=False)
check((_e_hook["unmeasured"] or {}).get("reason") == "E2E_HOOK_MISSING" and "_surface.py" in _e_hook["unmeasured"]["detail"],
      f"no vendored tests/e2e/_surface.py is E2E_HOOK_MISSING -- {_e_hook['unmeasured']}")
_, _e_fail, _e_fail_run = _e2e_run(["-c", "import sys; print('suite went red'); sys.exit(3)"])
check((_e_fail["unmeasured"] or {}).get("reason") == "E2E_FAILED" and "exit 3" in _e_fail["unmeasured"]["detail"]
      and "suite went red" in _e_fail["unmeasured"]["detail"] and "suite went red" in (_e_fail_run / "e2e.log").read_text(encoding="utf-8"),
      f"a non-zero exit before any capture is E2E_FAILED, with the output tail, and the output lands in e2e.log -- {_e_fail['unmeasured']}")
_t0 = time.monotonic()
_, _e_slow, _ = _e2e_run(["-c", "import time; time.sleep(60)"], timeout_s=2)
check((_e_slow["unmeasured"] or {}).get("reason") == "E2E_TIMEOUT" and _e_slow["timed_out"] is True and time.monotonic() - _t0 < 30,
      f"a suite past its timeout_s is killed and reported as E2E_TIMEOUT -- {_e_slow['unmeasured']}")
_, _e_none, _ = _e2e_run(["-c", "pass"])
_, _e_none5, _ = _e2e_run(["-c", "import sys; sys.exit(5)"])
check((_e_none["unmeasured"] or {}).get("reason") == "E2E_NO_SURFACES" and (_e_none5["unmeasured"] or {}).get("reason") == "E2E_NO_SURFACES",
      f"a suite that runs (exit 0, or pytest's 5: nothing collected) and captures nothing is E2E_NO_SURFACES -- {_e_none['unmeasured']} {_e_none5['unmeasured']}")
check(len({str((r["unmeasured"] or {}).get("reason")) for r in (_e_skip, _e_bad, _e_hook, _e_fail, _e_slow, _e_none)}) == 6,
      "every suite outcome is its own word")

_e_doc, _e_ok, _e_ok_run = _e2e_run(["-c", _E2E_WRITES, "0"])
_e_cap = Path(str(_e_ok["capture_dir"]))
_e_ids = [s["id"] for s in _e_doc["screens"]]
check(_e_ok["unmeasured"] is None and _e_ok["note"] is None and _e_ok["surfaces"] == ["jobs.sort-sheet"] and _e_ok["screens"] == 2
      and _e_ids == ["iphone-light-e2e-jobs-sort-sheet", "iphone-dark-e2e-jobs-sort-sheet"],
      f"each capture becomes a screen <device>-<theme>-e2e-<surface>, light before dark (#1498) -- {_e_ids}")
_e_s = _e_doc["screens"][0]
check(_e_s["status"] == "ok" and _e_s["kind"] == "step" and _e_s["source"] == "e2e" and _e_s["surface"] == "jobs.sort-sheet"
      and _e_s["engine"] == "webkit" and _e_s["viewport"] == {"width": 390, "height": 664} and _e_s["metrics"] == {"text": {"sizes": {}}}
      and _e_s["theme_applied"] is True and _e_s["theme_observed"]["rendered"] == "light",
      f"an e2e screen carries its metrics, source, surface, engine, viewport and the theme it rendered -- {_e_s}")
check(_e_s["screenshot"] == "iphone-light-e2e-jobs-sort-sheet.png" and (_e_ok_run / "shots" / _e_s["screenshot"]).is_file()
      and not list(_e_cap.glob("*.png")),
      "each capture's PNG moves into shots/ under its screen id, beside the walk's")
check((_e_cap / "script.js").is_file() and "__GEOMETRY__" not in (_e_cap / "script.js").read_text(encoding="utf-8")
      and json.loads((_e_cap / "params.json").read_text(encoding="utf-8")) == measure.default_params()
      and not (_e_cap / "capture.json").exists() and _e_ok["geometry"] == "GEOMETRY_MISSING",
      f"the capture dir holds the walk's own page script and params, no capture.json without a theme key -- geometry {_e_ok['geometry']}")
_e_doc_p, _e_part, _ = _e2e_run(["-c", _E2E_WRITES, "1"], run_name="e2e-run-partial")
check(_e_part["unmeasured"] is None and len(_e_doc_p["screens"]) == 2 and "exited 1 after capturing" in str(_e_part["note"]),
      f"a suite that fails after capturing keeps its screens, with a note -- {_e_part['note']}")
_e_doc_x, _e_err, _ = _e2e_run(["-c", _E2E_WRITES, "0", "throw"], run_name="e2e-run-throw",
                                review_extra={"theme_storage_key": "app.theme"})
check(all(s["status"] == "error" and s["reason"] == "E2E_MEASURE_FAILED" and s["error"] == "boom" and s["metrics"] is None
          for s in _e_doc_x["screens"]) and len(_e_doc_x["screens"]) == 2,
      "a capture whose page script threw is a screen in error (E2E_MEASURE_FAILED), never an empty success")
check(json.loads((Path(str(_e_err["capture_dir"])) / "capture.json").read_text(encoding="utf-8")) == {"theme_storage_key": "app.theme"},
      "a declared theme_storage_key reaches the hook through capture.json")
_e_doc_r, _e_rerun, _ = _e2e_run(["-c", "import sys; sys.exit(3)"])  # same run dir as the success above
check((_e_rerun["unmeasured"] or {}).get("reason") == "E2E_FAILED" and not _e_doc_r["screens"],
      "a capture left in the run dir by an earlier run never reads as this run's")
check(capture.e2e_theme("dark", {"attr": None, "dark": False}) == (False, {"attr": "", "luminance": None, "rendered": "light"})
      and capture.e2e_theme("light", {"attr": "dark", "dark": False})[0] is False and capture.e2e_theme("light", None) == (None, None),
      "an e2e screen whose page kept the other theme is theme_applied false; nothing recorded is unknown")

# ---- #1507: a theme mismatch is its own state, the target tree is left as found, --e2e never walks the live app ----

# Like _E2E_WRITES, but the page stamped `data-theme="light"` at load and kept it: every dark capture is light.
_E2E_STAMPED = _E2E_WRITES.replace("'attr': None", "'attr': 'light'")
_, _e_stamped, _ = _e2e_run(["-c", _E2E_STAMPED, "0"], run_name="e2e-run-stamped")
_e_warn = _e_stamped["warning"] or {}
check(_e_stamped["unmeasured"] is None and _e_warn.get("reason") == "E2E_THEME_MISMATCH" and "theme_storage_key" in _e_warn.get("detail", "")
      and _e_stamped["theme_mismatch"] == [{"id": "iphone-dark-e2e-jobs-sort-sheet", "requested": "dark", "rendered": "light"}]
      and _e_stamped["screens"] == 2 and _e_ok["warning"] is None and _e_ok["theme_mismatch"] == [],
      f"a dark capture the page kept light is a distinct warning beside a measured suite, never folded into success (#1507) -- {_e_stamped['warning']}")

def _e2e_git_target(command, name="e2e-git-target", dirty_before=False):
    """A git checkout (clean unless `dirty_before`) with one tracked gallery PNG, ready for `measure_e2e`."""
    target = _e2e_target({"e2e": {"command": command, "timeout_s": 60}}, name=name)
    (target.root / "docs").mkdir()
    (target.root / "docs" / "shot.png").write_bytes(b"original")

    def git(*a):
        capture.git_run.run_git(["-C", str(target.root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", f"core.hooksPath={STATE / 'no-hooks'}", *a], check=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    if dirty_before:
        (target.root / "docs" / "shot.png").write_bytes(b"my own edit")
    run = STATE / (name + "-run")
    run.mkdir(parents=True, exist_ok=True)
    doc = {"interpreter": sys.executable, "unmeasured": None, "screens": [], "params": {"script": measure.default_params()}}
    return target, run, doc


def _porcelain(root):
    return capture.git_run.run_git(["-C", str(root), "status", "--porcelain", "--untracked-files=all"]).stdout


# The suite rewrites a tracked gallery PNG and leaves a new one in the tree, as a screenshot-writing e2e suite does.
_E2E_DIRTIES = ("import pathlib\npathlib.Path('docs/shot.png').write_bytes(b'rewritten')\n"
                "pathlib.Path('docs/new.png').write_bytes(b'new')\n" + _E2E_WRITES)
_g_target, _g_run, _g_doc = _e2e_git_target(["-c", _E2E_DIRTIES, "0"])
_g_before = _porcelain(_g_target.root)
_g_rec = capture.measure_e2e(_g_doc, _g_target, _g_run, str(STATE / "no-scaffold"))
check(_g_rec["tree"]["state"] == "restored" and _g_rec["tree"]["restored"] == ["docs/new.png", "docs/shot.png"]
      and _porcelain(_g_target.root) == _g_before == "" and (_g_target.root / "docs" / "shot.png").read_bytes() == b"original"
      and not (_g_target.root / "docs" / "new.png").exists() and _g_rec["screens"] == 2,
      f"after a run the target's tree is exactly as found: the rewritten PNG restored, the new one removed (#1507) -- {_g_rec['tree']}")
_c_target, _c_run, _c_doc = _e2e_git_target(["-c", _E2E_WRITES, "0"], name="e2e-clean-target")
check(capture.measure_e2e(_c_doc, _c_target, _c_run, str(STATE / "no-scaffold"))["tree"]["state"] == "clean",
      "a suite that changes nothing in the tree reports it clean, not restored")
_d_target, _d_run, _d_doc = _e2e_git_target(["-c", _E2E_DIRTIES, "0"], name="e2e-dirty-target", dirty_before=True)
_d_before = _porcelain(_d_target.root)
_d_rec = capture.measure_e2e(_d_doc, _d_target, _d_run, str(STATE / "no-scaffold"))
check(_d_rec["tree"]["state"] == "restore_failed" and _d_rec["tree"]["unrestored"] == ["docs/shot.png"]
      and "docs/shot.png" in _d_rec["tree"]["detail"] and not (_d_target.root / "docs" / "new.png").exists()
      and (_d_target.root / "docs" / "shot.png").read_bytes() == b"rewritten" and _d_rec["screens"] == 2 and _porcelain(_d_target.root) == _d_before,
      f"a path the user had already edited that the run rewrote is reported restore_failed, never silently overwritten or swallowed -- {_d_rec['tree']}")
check((_e_ok["tree"] or {}).get("state") == "unchecked" and "not read" in _e_ok["tree"]["detail"],
      f"a target that is no git checkout is unchecked (nothing confirmed), not clean -- {_e_ok['tree']}")

import contextlib  # noqa: E402
import io  # noqa: E402
from design_review import cli as _cli  # noqa: E402


def _cli_measure(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _cli.main(["measure", *argv])
    return rc, dict(l.split("=", 1) for l in out.getvalue().splitlines() if "=" in l)


_real_probe, _real_spawn, _real_target = capture.probe_listening, capture.spawn_walk, capture.measure_target
_real_interp = capture.resolve_interpreter
_probed, _walked = [], []


def _spy_probe(url, *a, **k):
    _probed.append(url)
    return _real_probe(url, *a, **k)


def _spy_spawn(python, target, *a, **k):
    _walked.append(target.base_url)
    return {"returncode": 1, "stderr_tail": "fake walk", "timed_out": False}


capture.probe_listening, capture.spawn_walk = _spy_probe, _spy_spawn
capture.resolve_interpreter = lambda *a, **k: {"status": "ok", "python": sys.executable, "detail": ""}  # no browser needed: the walk is spied
try:
    _live_root = STATE / "e2e-live-target"
    _live_root.mkdir(parents=True, exist_ok=True)
    (_live_root / ".fleet.toml").write_text('port = ":1"\n', encoding="utf-8")
    _rc_u, _out_u = _cli_measure(str(_live_root), "--e2e", "--run-dir", str(STATE / "e2e-cli-undeclared"))
    check(not _probed and not _walked and _out_u.get("MODE") == "synthetic" and _out_u.get("UNMEASURED") == "SYNTHETIC_UNDECLARED"
          and _out_u.get("E2E") == "E2E_SKIPPED",
          f"--e2e without --url on a target with no synthetic launcher is unmeasured, and never probes or walks the live port (#1507) -- "
          f"{_out_u} probed {_probed}")
    (_live_root / "synthetic_launcher.py").write_bytes((FIX / "synthetic_launcher.py").read_bytes())
    (_live_root / "fixture.html").write_bytes((FIX / "fixture.html").read_bytes())
    (_live_root / ".fleet.toml").write_text('port = ":1"\n[design.review.synthetic]\ncommand = ["synthetic_launcher.py"]\n', encoding="utf-8")
    _rc_s, _out_s2 = _cli_measure(str(_live_root), "--e2e", "--run-dir", str(STATE / "e2e-cli-synthetic"))
    check(_out_s2.get("MODE") == "synthetic" and _walked and all(u.startswith("http://127.0.0.1:") and not u.startswith("http://127.0.0.1:1/")
                                                                  for u in _probed + _walked),
          f"--e2e without --url walks the target's own disposable instance, not its declared live port -- probed {_probed} walked {_walked}")
    _probed.clear()
    _rc_p, _out_p = _cli_measure(str(_live_root), "--e2e", "--url", "http://127.0.0.1:1", "--run-dir", str(STATE / "e2e-cli-url"))
    check(_out_p.get("MODE") == "live" and _probed == ["http://127.0.0.1:1"],
          f"an explicit --url still wins: --e2e does not override a URL the caller named -- {_out_p} {_probed}")

    def _fake_measure(tree):
        def run(target, *a, **k):
            return {"target": "t", "base_url": "u", "run_dir": str(STATE), "screens": [], "unmeasured": None,
                    "e2e": {"unmeasured": None, "warning": None, "surfaces": [], "screens": 0, "note": None, "tree": tree}}
        return run

    capture.measure_target = _fake_measure({"state": "restore_failed", "restored": [], "unrestored": ["docs/shot.png"], "detail": "1 path(s) differ"})
    _rc_f, _out_f = _cli_measure(str(_live_root), "--e2e", "--url", "http://127.0.0.1:1")
    check(_rc_f == 1 and _out_f.get("E2E_TREE") == "restore_failed" and "differ" in _out_f.get("E2E_TREE_DETAIL", ""),
          f"a tree the run could not restore exits non-zero and prints its own state line -- rc {_rc_f} {_out_f}")
    capture.measure_target = _fake_measure({"state": "restored", "restored": ["a"], "unrestored": [], "detail": ""})
    _rc_k, _out_k = _cli_measure(str(_live_root), "--e2e", "--url", "http://127.0.0.1:1")
    check(_rc_k == 0 and _out_k.get("E2E_TREE") == "restored" and _out_k.get("E2E") == "ok" and "E2E_TREE_DETAIL" not in _out_k,
          "a restored tree exits 0 and says so")
    _warned = _fake_measure({"state": "clean", "restored": [], "unrestored": [], "detail": ""})(None)
    _warned["e2e"]["warning"] = {"reason": "E2E_THEME_MISMATCH", "detail": "1 of 2 e2e screens kept light"}
    capture.measure_target = lambda *a, **k: _warned
    _rc_w, _out_w = _cli_measure(str(_live_root), "--e2e", "--url", "http://127.0.0.1:1")
    check(_rc_w == 0 and _out_w.get("E2E") == "E2E_THEME_MISMATCH" and "kept light" in _out_w.get("E2E_DETAIL", ""),
          f"measure prints a theme mismatch as its own E2E state, not ok -- {_out_w}")
finally:
    capture.probe_listening, capture.spawn_walk, capture.measure_target = _real_probe, _real_spawn, _real_target
    capture.resolve_interpreter = _real_interp

_cov_screens = [{"id": "w", "status": "ok", "metrics": {"coverage": {"overlays": [{"key": "aside#sheet", "open": False}]}}},
                {"id": "e", "status": "ok", "source": "e2e", "metrics": {"coverage": {"overlays": [{"key": "aside#sheet", "open": True}]}}}]
check(ev.overlay_coverage(_cov_screens)["not_walked_count"] == 0 and ev.overlay_coverage(_cov_screens[:1])["not_walked_count"] == 1,
      "overlay coverage is the union over walk and e2e screens: a surface only the e2e suite opened counts as walked")

_ev_plain = ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))
_doc_e = _doc("compliant")
_doc_e["e2e"] = dict(_e_fail)
_ev_e = ev.evaluate(_doc_e, rubric, _specs("compliant"))
check("e2e" not in _ev_plain and _ev_e["e2e"]["unmeasured"]["reason"] == "E2E_FAILED"
      and [r["status"] for r in _ev_e["rules"]] == [r["status"] for r in _ev_plain["rules"]],
      "evaluate carries the e2e record through only on an --e2e run; a failed suite never moves the walk's rules")
check("e2e suite not captured: E2E_FAILED" in report_mod._method(_ev_e) and "e2e suite" not in report_mod._method(_ev_plain),
      "the report's method section names the suite's own outcome, and says nothing on a plain run")
_doc_e["e2e"] = dict(_e_ok)
check("1 surface captured by the app&#x27;s own e2e suite" in report_mod._method(ev.evaluate(_doc_e, rubric, _specs("compliant"))),
      "the report counts the surfaces the e2e suite captured")

_h.report_and_exit("test_plan_and_e2e")
