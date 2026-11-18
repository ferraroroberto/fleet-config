"""LAYOUT-11 through the real walk (fleet-config#2110): a too-tall fixture sheet fails, a fitting one passes, and a
sheet that cannot be opened is not confirmed.

A bottom sheet is opened by a declared extra step on the phone legs. The walk reads it where it opened and re-reads it at
390x664 and 390x844, resizing the page only while the sheet is open and putting it back; `evaluate` then scores the
screens it recorded. The pure-logic half is `tests/design_review/test_overlay_fit.py`.
"""
import json

from _suite import REPO, STATE, _walk_run, check, interp, scaffold
from design_review import evaluate as ev
from design_review import plan
from design_review_support import FIX, rubric
from design_review_support import doc as _doc
from design_review_support import specs as _specs

_STEP = {"tab": "home", "id": "sheet", "click": "#open", "overlay": True, "primary": ".done"}
_GHOST = {"tab": "home", "id": "ghost", "click": "#nope", "overlay": True, "wait_ms": 500}


def _walk_sheet(case: str, steps: list) -> list:
    out_dir = STATE / f"fixture-sheet-fit-{case}"
    review = STATE / f"sheet-fit-review-{case}.json"
    review.write_text(json.dumps({"extra_steps": steps}), encoding="utf-8")
    proc = _walk_run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "sheet_fit.html").as_uri() + f"?case={case.split('-')[0]}",
         "--out", str(out_dir), "--devices", "iphone,android", "--scaffold", str(scaffold),
         "--review", str(review), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if proc.returncode != 0:
        return [{"error": proc.stderr[-300:]}]
    return [s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s["view"] != plan.SETTINGS_GEAR_VIEW]


def _l11(screens: list) -> dict:
    d = _doc("compliant")
    d["screens"] = screens
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "LAYOUT-11")


def _sheets(screens: list) -> dict:
    return {s["id"]: s for s in screens if s.get("view", "").endswith("-sheet")}


tall = _walk_sheet("tall", [_STEP])
fits = _walk_sheet("fits", [_STEP])
ghost = _walk_sheet("fits-ghost", [_GHOST])

_expected = {f"{d}-{t}-home-sheet" for d in ("iphone", "android") for t in ("light", "dark")}
check(set(_sheets(tall)) == _expected and all(s["status"] == "ok" and s.get("overlay") for s in _sheets(tall).values()),
      f"the sheet step is walked on both phones in both themes and carries its overlay declaration -- {sorted(_sheets(tall))} {tall[:1]}")

_tall_view = {sid: {v["viewport"]: v for v in s["metrics"]["fit"]["viewports"]} for sid, s in _sheets(tall).items()}
check(all(set(v) == {"390x664", "390x844"} for v in _tall_view.values()),
      f"an open sheet is read at both phone viewports, on the iPhone's own 390x664 and the Pixel's 412x839 alike -- {[sorted(v) for v in _tall_view.values()]}")
_short = next(iter(_tall_view.values()))["390x664"]["roots"][0]
_roomy = next(iter(_tall_view.values()))["390x844"]["roots"][0]
check(_short["id"] == "strip.sheet" and _short["inside"] is False and _short["bottom"] == 840 and _short["primary"]["state"] == "cut"
      and _roomy["inside"] is True and _roomy["primary"]["state"] == "visible",
      f"the too-tall sheet runs to 840 and loses Done on the short phone, and fits the tall one -- {_short} {_roomy}")

_tall_rule = _l11(tall)
_why = [i["why"] for e in _tall_rule["evidence"] for i in e["items"]]
check(_tall_rule["status"] == "fail" and len(_why) == 4 and all(w.startswith("390x664, ") for w in _why)
      and all("box 80..840 past the 664px viewport" in w and "button.done cut off" in w for w in _why),
      f"LAYOUT-11: a too-tall fixture sheet fails, once per phone x theme, at 390x664 only -- {_tall_rule['status']} {_why}")

_fit_rule = _l11(fits)
_fit_roots = [r for s in _sheets(fits).values() for v in s["metrics"]["fit"]["viewports"] for r in v["roots"]]
check(_fit_rule["status"] == "pass" and not _fit_rule["evidence"] and len(_fit_roots) == 8 and all(r["inside"] and r["primary"]["state"] == "visible" for r in _fit_roots),
      f"LAYOUT-11: a sheet that fills to the bottom edge with a flex footer passes at both viewports -- {_fit_rule['status']} {_fit_rule['reason']}")

_ghost_sheets = {s["id"]: s for s in ghost if s["view"].endswith("-ghost")}
_ghost_rule = _l11(ghost)
check(len(_ghost_sheets) == 4 and all(s["status"] == "absent" and s.get("overlay") for s in _ghost_sheets.values()),
      f"a declared overlay step whose target never attaches is recorded absent and marked overlay -- {[(i, s['status']) for i, s in _ghost_sheets.items()]}")
check(_ghost_rule["status"] == "unmeasured" and "not confirmed" in _ghost_rule["reason"] and "could not be opened" in _ghost_rule["reason"],
      f"LAYOUT-11: an overlay that cannot be opened is not confirmed, never a pass -- {_ghost_rule['status']} {_ghost_rule['reason']}")
