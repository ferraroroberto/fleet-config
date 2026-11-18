"""LAYOUT-11 (fleet-config#2110): an open sheet, dialog or menu must fit the phone viewport.

The pure half: the rule's shape and `evaluate` over hand-built `metrics.fit` readings, so a too-tall sheet fails, a
fitting one passes and every surface the walk could not read is `unmeasured`, never a pass. The browser half (a real
too-tall fixture sheet through the walk) is `tests/design_review_browser/sheet_fit.py`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_overlay_fit.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

from _suite import _h, check  # noqa: E402
from design_review import evaluate as ev
from design_review import measure_js_fit, plan
from design_review_support import MISSING, rubric
from design_review_support import doc as _doc
from design_review_support import specs as _specs

rule = next((r for r in rubric.rules if r.id == "LAYOUT-11"), None)
check(rule is not None and rule.metric == "fit.violation_count" and rule.fail_when == "gt" and rule.threshold == 0
      and rule.screens == [] and rule.devices == ["iphone", "android"] and rule.absent_unmeasured and rule.owner == "app",
      f"LAYOUT-11: fails over zero, every screen kind on phone legs, an unopenable overlay is unmeasured -- {rule}")
check(plan.FIT_VIEWPORTS == ({"width": 390, "height": 664}, {"width": 390, "height": 844}),
      "the two phone viewports a sheet is judged at are 390x664 and 390x844")
check("params.primary" in measure_js_fit.FIT_JS and "elementFromPoint" in measure_js_fit.FIT_JS,
      "the fit script reads the declared primary and checks it is on top, not only inside the box")


def root(vh: int, top: int = 100, bottom: int = 600, primary: dict | None = None, overflow: int = 0, key: str = "dialog#sheet",
         ident: str | None = "strip.sheet", menu: bool = False) -> dict:
    return {"key": key, "id": ident, "top": top, "bottom": bottom, "left": 0, "right": 390, "overflow": overflow,
            "inside": top >= 0 and bottom <= vh,
            "primary": None if menu else (primary or {"state": "visible", "sel": "button.done", "top": bottom - 60, "bottom": bottom - 10})}


def view(vh: int, *roots: dict) -> dict:
    return {"viewport": f"390x{vh}", "vw": 390, "vh": vh, "roots": list(roots)}


def screen(sid: str, fit: object, kind: str = "dialog", device: str = "iphone", **extra: object) -> dict:
    metrics = {} if fit is None else {"fit": fit}
    return {"id": sid, "device": device, "theme": "light", "view": sid, "kind": kind, "status": "ok", "reason": None,
            "metrics": metrics, **extra}


def l11(*screens: dict) -> dict:
    d = _doc("compliant")
    d["screens"] = list(screens)
    return next((r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "LAYOUT-11"), MISSING)


fits = {"viewports": [view(664, root(664)), view(844, root(844))]}
ok = l11(screen("iphone-light-dialog-a", fits))
check(ok["status"] == "pass" and not ok["evidence"], f"LAYOUT-11: a sheet inside both viewports with its Done on screen passes -- {ok['reason']}")

tall = {"viewports": [view(664, root(664, top=0, bottom=700, primary={"state": "cut", "sel": "button.filter-done", "top": 640, "bottom": 700})),
                      view(844, root(844, top=0, bottom=700))]}
bad = l11(screen("iphone-light-dialog-a", tall))
why = [i["why"] for e in bad["evidence"] for i in e["items"]]
check(bad["status"] == "fail" and bad["measured"] == {"iphone-light-dialog-a": 1.0}
      and any("390x664, light" in w and "box 0..700 past the 664px viewport" in w and "button.filter-done cut off at 640..700" in w for w in why),
      f"LAYOUT-11: a sheet taller than the short phone fails, naming the surface, viewport, theme and measured edges; the roomy one passes -- {why}")
check(bad["evidence"][0]["items"][0]["sel"] == "strip.sheet", "LAYOUT-11: the finding names the surface by its data-surface id")

cut_only = {"viewports": [view(664, root(664, primary={"state": "cut", "sel": "button.done", "top": 650, "bottom": 700}))]}
check(l11(screen("iphone-light-dialog-a", cut_only))["status"] == "fail", "LAYOUT-11: a box that fits with its primary action cut off still fails")
wide = {"viewports": [view(664, root(664, overflow=14))]}
check(l11(screen("iphone-light-dialog-a", wide))["status"] == "fail", "LAYOUT-11: a sheet that scrolls sideways fails")
no_primary = {"viewports": [view(664, root(664, primary={"state": "none", "sel": None, "top": None, "bottom": None}))]}
check(l11(screen("iphone-light-dialog-a", no_primary))["status"] == "pass",
      "LAYOUT-11: a sheet with no footer action is held to its box and overflow, and passes when they fit")
menu_ok = {"viewports": [view(664, root(664, top=300, bottom=500, menu=True, key="div.row-menu", ident=None))]}
check(l11(screen("iphone-light-step-menu", menu_ok, kind="step"))["status"] == "pass", "LAYOUT-11: a menu inside the viewport passes")
menu_low = {"viewports": [view(664, root(664, top=500, bottom=700, menu=True, key="div.row-menu", ident=None))]}
check(l11(screen("iphone-light-step-menu", menu_low, kind="step"))["status"] == "fail", "LAYOUT-11: a menu that runs past the bottom fails")
declared_missing = {"viewports": [view(664, root(664, primary={"state": "missing", "sel": "#nope", "top": None, "bottom": None}))]}
missing = l11(screen("iphone-light-step-a", declared_missing, kind="step"))
check(missing["status"] == "fail" and any("declared primary #nope not in the overlay" in i["why"] for e in missing["evidence"] for i in e["items"]),
      "LAYOUT-11: a declared primary the overlay does not hold is reported, never read as fitting")

# Not confirmed: never folded into a pass.
empty_dialog = l11(screen("iphone-light-dialog-a", {"viewports": [view(664)]}))
check(empty_dialog["status"] == "unmeasured" and "rendered no box" in empty_dialog["reason"],
      f"LAYOUT-11: a dialog the walk opened that rendered no box is not confirmed -- {empty_dialog['reason']}")
declared = l11(screen("iphone-light-step-a", {"viewports": [view(664)]}, kind="step", overlay=True))
check(declared["status"] == "unmeasured" and "opened no overlay" in declared["reason"],
      f"LAYOUT-11: a step declared `overlay` that opened nothing is not confirmed -- {declared['reason']}")
plain = l11(screen("iphone-light-step-select", {"viewports": [view(664)]}, kind="step"))
check(plain["status"] == "pass" and "no overlay open" in plain["reason"],
      f"LAYOUT-11: a step that opens no declared overlay (Select mode, a tab view) has nothing to fit: n/a -- {plain['reason']}")
tab = l11(screen("iphone-light-home", {"viewports": [view(664)]}, kind="tab"))
check(tab["status"] == "pass" and "no overlay open" in tab["reason"], f"LAYOUT-11: a tab with nothing open has nothing to fit -- {tab['reason']}")
open_on_tab = l11(screen("iphone-light-home", tall, kind="tab"))
check(open_on_tab["status"] == "fail", "LAYOUT-11: an overlay left open on a tab screen is read like any other")
absent = {"id": "iphone-light-step-a", "device": "iphone", "theme": "light", "view": "step-a", "kind": "step", "status": "absent",
          "reason": "STEP_TARGET_ABSENT", "metrics": None, "overlay": True}
unopenable = l11(absent)
check(unopenable["status"] == "unmeasured" and "could not be opened" in unopenable["reason"] and "not confirmed" in unopenable["reason"],
      f"LAYOUT-11: an overlay step whose target never attached is not confirmed -- {unopenable['reason']}")
quiet = l11(dict(absent, overlay=False))
check(quiet["status"] == "unmeasured" and "step target absent on 1" in quiet["reason"],
      f"LAYOUT-11: an absent step that declares no overlay stays absent, as before: nothing to measure -- {quiet['reason']}")
check(l11(screen("iphone-light-dialog-a", None))["status"] == "unmeasured", "LAYOUT-11: a screen with no fit reading is unmeasured, never a pass")
check(l11(screen("desktop-light-dialog-a", tall, device="desktop"))["status"] == "unmeasured",
      "LAYOUT-11: a desktop screen is not read (phone legs only), so it leaves no verdict of its own")
check(l11(screen("iphone-light-e2e-sheet", None, kind="step", source="e2e"))["status"] == "pass",
      "LAYOUT-11: an e2e capture carries no fit reading and is n/a, not unmeasured")
dark_tall = screen("iphone-dark-dialog-a", tall)
dark_tall["theme"] = "dark"
mixed = l11(screen("iphone-light-dialog-a", fits), dark_tall)
check(mixed["status"] == "fail" and set(mixed["measured"]) == {"iphone-light-dialog-a", "iphone-dark-dialog-a"},
      f"LAYOUT-11: one failing theme fails the rule, the fitting one is still measured -- {mixed['measured']}")

_h.report_and_exit("test_overlay_fit")
