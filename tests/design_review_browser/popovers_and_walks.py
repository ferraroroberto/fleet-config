import json
import os
import shutil
import subprocess
import sys

from _suite import REPO, STATE, check, interp, scaffold
from design_review import plan
from design_review_support import FIX, RUBRIC, port_open as _port_open


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
