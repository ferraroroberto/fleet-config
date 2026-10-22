"""Unit tests for design_review's component, text and layout rules, then evaluate on the compliant and violating fixtures (fleet-config#971).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_component_rules_and_evaluate.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

from _suite import STATE, _h, check  # noqa: E402

from design_review import capture, evaluate as ev, measure, plan  # noqa: E402
from design_review import mockups as mockups_mod, report as report_mod  # noqa: E402
from design_review_support import (  # noqa: E402
    L10_MISSING as _L10_MISSING, MISSING as _MISSING, rubric,
    c18_of as _c18_of, comp as _comp, doc as _doc, out_c, specs as _specs, src_specs, statuses_c,
)


# ---- COMP-10: an empty-state block holds a button or link; a populated screen is not measured, never a pass (#1367) ----
def _feedback(patches) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], patches):
        s["metrics"]["feedback"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-10")


_bare = {"empty_state_count": 2, "empty_states_no_action": [{"sel": "div.empty-state", "text": "No history yet"}]}
_c10 = _feedback([_bare, {"empty_state_count": 1, "empty_states_no_action": []}, {}])
check(_c10["status"] == "fail" and [i["text"] for i in _c10["evidence"][0]["items"]] == ["No history yet"]
      and _c10["severity"] == "P3", f"COMP-10: a block with a reason and no action fails -- {_c10['status']} {_c10['evidence']}")
check(_feedback([{"empty_state_count": 1, "empty_states_no_action": []}] * 3)["status"] == "pass",
      "COMP-10: blocks that each hold an action pass (the dialog screen is outside the rule)")
_c10_pop = _feedback([{"empty_state_count": 0, "empty_states_no_action": []}, {"empty_state_count": 1, "empty_states_no_action": []}, {}])
check(_c10_pop["status"] == "unmeasured" and "synthetic instance" in _c10_pop["reason"],
      f"COMP-10: a populated tab screen with no empty block is not measured, never a pass -- {_c10_pop['status']} {_c10_pop['reason']}")
_old10 = _doc("compliant")
for _s in _old10["screens"]:
    for _k in ("empty_state_count", "empty_states_no_action"):
        _s["metrics"]["feedback"].pop(_k)
check(next(r for r in ev.evaluate(_old10, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-10")["status"] == "unmeasured",
      "COMP-10: a run from before the metric existed is unmeasured, never a pass")
_c10_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-10"), None)
check("No history yet" in _c10_sentence and "{" not in _c10_sentence, f"COMP-10: the fix sentence names the block and fills every placeholder -- {_c10_sentence}")

# ---- COMP-11: a raw identifier rendered as ordinary text (#1336) ----
def _text_rule(rule_id: str, patches) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], patches):
        s["metrics"]["text"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == rule_id)


_ident = {"raw_identifier_count": 2, "raw_identifiers": [{"sel": "span.row", "kind": "ip", "text": "192.0.2.14"},
                                                          {"sel": "span.key", "kind": "snake", "text": "quiet_hours_start"}]}
_c11 = _text_rule("COMP-11", [_ident, {}, {}])
check(_c11["status"] == "fail" and [i["text"] for i in _c11["evidence"][0]["items"]] == ["192.0.2.14", "quiet_hours_start"]
      and _c11["severity"] == "P2", f"COMP-11: an address and a snake_case key rendered as text fail and are listed -- {_c11['status']} {_c11['evidence']}")
check(_text_rule("COMP-11", [{}, {}, {}])["status"] == "pass", "COMP-11: a screen with no raw identifier passes")
_old11 = _doc("compliant")
for _s in _old11["screens"]:
    for _k in ("raw_identifier_count", "raw_identifiers"):
        _s["metrics"]["text"].pop(_k)
check(next(r for r in ev.evaluate(_old11, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-11")["status"] == "unmeasured",
      "COMP-11: a run from before the metric existed is unmeasured, never a pass")
_c11_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-11"), None)
check("192.0.2.14" in _c11_sentence and "{" not in _c11_sentence, f"COMP-11: the fix sentence names the identifier and fills every placeholder -- {_c11_sentence}")
check("COMP-11" in next(j for j in rubric.judgment if j.id == "J-07").maps_to and "COMP-11" in next(j for j in rubric.judgment if j.id == "J-07").question,
      "J-07 names COMP-11 and maps to it, so a `no` and the rule never double-count")

# ---- COMP-17: code is at its size and never on a surface darker than its card (#1342) ----
_bad_code = {"sel": "code.c12", "text": "git status", "px": 12, "why": "inline at caption size"}
_c17 = _text_rule("COMP-17", [{"code_off_spec_count": 1, "code_off_spec": [_bad_code]}, {}, {}])
check(_c17["status"] == "fail" and _c17["severity"] == "P2" and _c17["category"] == "components" and _c17["owner"] == "app"
      and _c17["evidence"][0]["items"] == [_bad_code],
      f"COMP-17: a 12px inline code fails and names the element and why -- {_c17['status']} {_c17['evidence']}")
check(_text_rule("COMP-17", [{"code_off_spec_count": 0, "code_off_spec": []}] * 3)["status"] == "pass", "COMP-17: a screen with no off-spec code passes")
_c17_doc = _doc("compliant")
for _s in _c17_doc["screens"]:
    _s["metrics"]["text"].update({"code_off_spec_count": 1, "code_off_spec": [_bad_code]})
_c17_out = ev.evaluate(_c17_doc, rubric, _specs("compliant"))
_c17_sentence = report_mod.finding_sentence(next(r for r in _c17_out["rules"] if r["id"] == "COMP-17"), _c17_out)
check("code.c12" in _c17_sentence and "caption size" in _c17_sentence and "{" not in _c17_sentence, f"COMP-17: the fix sentence names the code and why -- {_c17_sentence}")

# ---- TYPE-07: a heading is never rendered smaller than the body text it introduces (#1341) ----
_small_h = {"sel": "h2.title", "text": "Recent takes", "px": 14, "body_sel": "p.lede", "body_px": 16}
_t07 = _text_rule("TYPE-07", [{"heading_smaller_than_body_count": 1, "heading_smaller_than_body": [_small_h]}, {}, {}])
_t07_ok = {"heading_smaller_than_body_count": 0, "heading_smaller_than_body": []}
check(_t07["status"] == "fail" and _t07["severity"] == "P2" and _t07["category"] == "typography" and _t07["owner"] == "app"
      and _t07["evidence"][0]["items"] == [_small_h],
      f"TYPE-07: a 14px heading above 16px body text fails and names both sizes -- {_t07['status']} {_t07['evidence']}")
check(_text_rule("TYPE-07", [_t07_ok, _t07_ok, _t07_ok])["status"] == "pass", "TYPE-07: a screen with no inverted heading passes")
_t07_doc = _doc("compliant")
for _s in _t07_doc["screens"]:
    _s["metrics"]["text"].update({"heading_smaller_than_body_count": 1, "heading_smaller_than_body": [_small_h]})
_t07_out = ev.evaluate(_t07_doc, rubric, _specs("compliant"))
_t07_sentence = report_mod.finding_sentence(next(r for r in _t07_out["rules"] if r["id"] == "TYPE-07"), _t07_out)
check("h2.title" in _t07_sentence and "{" not in _t07_sentence, f"TYPE-07: the fix sentence names the heading and fills every placeholder -- {_t07_sentence}")
_old07 = _doc("compliant")
for _s in _old07["screens"]:
    for _k in ("heading_smaller_than_body_count", "heading_smaller_than_body"):
        _s["metrics"]["text"].pop(_k, None)
check(next(r for r in ev.evaluate(_old07, rubric, _specs("compliant"))["rules"] if r["id"] == "TYPE-07")["status"] == "unmeasured",
      "TYPE-07: a run from before the metric existed is unmeasured, never a pass")

# ---- COMP-12: an expanded section with no text and no control (#1337) ----
def _layout_rule(patches, rule_id: str = "COMP-12") -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], patches):
        s["metrics"]["layout"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == rule_id)


_c12 = _layout_rule([{"empty_open_section_count": 2, "empty_open_sections": [{"sel": "details#advanced", "label": "Advanced"},
                                                                              {"sel": "div#panel", "label": "More"}]}, {}, {}])
check(_c12["status"] == "fail" and [i["label"] for i in _c12["evidence"][0]["items"]] == ["Advanced", "More"] and _c12["severity"] == "P2",
      f"COMP-12: an open section with nothing in it fails and is listed -- {_c12['status']} {_c12['evidence']}")
check(_layout_rule([{}, {}, {}])["status"] == "pass", "COMP-12: a screen whose open sections all show something passes")
_old12 = _doc("compliant")
for _s in _old12["screens"]:
    for _k in ("open_section_count", "empty_open_sections", "empty_open_section_count"):
        _s["metrics"]["layout"].pop(_k)
check(next(r for r in ev.evaluate(_old12, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-12")["status"] == "unmeasured",
      "COMP-12: a run from before the metric existed is unmeasured, never a pass")
_c12_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-12"), None)
check("Filters" in _c12_sentence and "{" not in _c12_sentence, f"COMP-12: the fix sentence names the section and fills every placeholder -- {_c12_sentence}")
_j09 = next(j for j in rubric.judgment if j.id == "J-09")
check("COMP-12" in _j09.maps_to and "COMP-12" in _j09.question and "COMP-10" in _j09.maps_to,
      "J-09 names COMP-12 and maps to it, so a `no` and the rule never double-count")

# ---- COMP-13: a list row with more than one secondary line (#1338) ----
_c13 = _layout_rule([{"multi_secondary_row_count": 2, "multi_secondary_rows": [{"sel": "li#a", "label": "Kitchen light", "lines": 2},
                                                                              {"sel": "li#b", "label": "Hall sensor", "lines": 3}]}, {}, {}], "COMP-13")
check(_c13["status"] == "fail" and [i["label"] for i in _c13["evidence"][0]["items"]] == ["Kitchen light", "Hall sensor"] and _c13["severity"] == "P2",
      f"COMP-13: rows with more than one secondary line fail and are listed -- {_c13['status']} {_c13['evidence']}")
check(_layout_rule([{}, {}, {}], "COMP-13")["status"] == "pass", "COMP-13: a screen whose rows carry one secondary line at most passes")
_old13 = _doc("compliant")
for _s in _old13["screens"]:
    for _k in ("multi_secondary_rows", "multi_secondary_row_count"):
        _s["metrics"]["layout"].pop(_k)
check(next(r for r in ev.evaluate(_old13, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-13")["status"] == "unmeasured",
      "COMP-13: a run from before the metric existed is unmeasured, never a pass")
_c13_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-13"), None)
check("Kitchen light" in _c13_sentence and "{" not in _c13_sentence, f"COMP-13: the fix sentence names the row and fills every placeholder -- {_c13_sentence}")
_j10 = next(j for j in rubric.judgment if j.id == "J-10")
check("COMP-13" in _j10.maps_to and "COMP-13" in _j10.question and "at most one secondary line" not in _j10.question
      and "more than two lines" not in _j10.question,
      "J-10 names COMP-13, maps to it and no longer asks the secondary-line count, so a `no` and the rule never double-count")

# ---- COMP-14: a verb inside a list row draws a fill, border or shadow at rest (#1420) ----
_c14 = _layout_rule([{"painted_row_verb_count": 2, "painted_row_verbs": [{"sel": "button.tint", "label": "Start", "via": "fill+border", "row": "li#a"},
                                                                          {"sel": "button.outl", "label": "Stop", "via": "border", "row": "li#b"}]}, {}, {}], "COMP-14")
check(_c14["status"] == "fail" and [i["label"] for i in _c14["evidence"][0]["items"]] == ["Start", "Stop"] and _c14["severity"] == "P2",
      f"COMP-14: painted or bordered row verbs fail and are listed -- {_c14['status']} {_c14['evidence']}")
check(_layout_rule([{}, {}, {}], "COMP-14")["status"] == "pass", "COMP-14: a screen whose row verbs are plain icon-buttons passes")
_old14 = _doc("compliant")
for _s in _old14["screens"]:
    for _k in ("painted_row_verbs", "painted_row_verb_count"):
        _s["metrics"]["layout"].pop(_k, None)
check(next(r for r in ev.evaluate(_old14, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-14")["status"] == "unmeasured",
      "COMP-14: a run from before the metric existed is unmeasured, never a pass")
_c14_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-14"), None)
check("Stop" in _c14_sentence and "{" not in _c14_sentence, f"COMP-14: the fix sentence names the verb and fills every placeholder -- {_c14_sentence}")
# ---- COMP-15: a delete or remove control is visible on a list row (#1420, owner decision 2026-10-14) ----
_c15 = _layout_rule([{"delete_row_verb_count": 2, "delete_row_verbs": [{"sel": "button.ib", "label": "Remove Milk", "row": "li#a"},
                                                                       {"sel": "button.ib", "label": "Delete", "row": "li#b"}]}, {}, {}], "COMP-15")
check(_c15["status"] == "fail" and [i["label"] for i in _c15["evidence"][0]["items"]] == ["Remove Milk", "Delete"] and _c15["severity"] == "P2",
      f"COMP-15: visible delete or remove controls on rows fail and are listed -- {_c15['status']} {_c15['evidence']}")
check(_layout_rule([{}, {}, {}], "COMP-15")["status"] == "pass", "COMP-15: rows with no visible delete or remove control pass")
_old15 = _doc("compliant")
for _s in _old15["screens"]:
    for _k in ("delete_row_verbs", "delete_row_verb_count"):
        _s["metrics"]["layout"].pop(_k, None)
check(next(r for r in ev.evaluate(_old15, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-15")["status"] == "unmeasured",
      "COMP-15: a run from before the metric existed is unmeasured, never a pass")
_c15_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-15"), None)
check("Remove Milk" in _c15_sentence and "{" not in _c15_sentence, f"COMP-15: the fix sentence names the control and fills every placeholder -- {_c15_sentence}")
_l04 = next(r for r in rubric.rules if r.id == "LAYOUT-04")
check("#1420" in _l04.standard and "COMP-14" in _l04.standard and "outlined" in _l04.standard,
      "LAYOUT-04 names COMP-14 as the gate on a row verb's paint, so the outlined Stop it used to contradict is retired by the rubric too")

# ---- COMP-07 / LAYOUT-08: a label fits its control, a control fits its container (#1325) ----
def _layout(rule_id: str, layout_by_screen) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], layout_by_screen):
        s["metrics"]["layout"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == rule_id)


_wrapped = {"label_fit_bad_count": 2, "label_fit_bad": [{"sel": "button.btn", "label": "Zoom in", "lines": 2, "spill_px": 0, "w": 79},
                                                         {"sel": "button.btn.nowrap", "label": "Screenshot", "lines": 1, "spill_px": 7, "w": 79}]}
_c07 = _comp("COMP-07", [_wrapped, {}, {}])
check(_c07["status"] == "fail" and [i["label"] for i in _c07["evidence"][0]["items"]] == ["Zoom in", "Screenshot"],
      f"COMP-07: a wrapped label and a spilled label fail and are listed -- {_c07['status']}")
check(_comp("COMP-07", [{"label_fit_bad_count": 0, "label_fit_bad": []}, {}, {}])["status"] == "pass", "COMP-07: labels that fit pass")
_spilled = {"control_overflow_count": 1, "control_overflow": [{"sel": "button.btn", "label": "Record clip", "container": "div.card", "over_px": 70, "side": "right"}]}
_l08 = _layout("LAYOUT-08", [_spilled, {}, {}])
check(_l08["status"] == "fail" and _l08["evidence"][0]["items"][0]["container"] == "div.card",
      f"LAYOUT-08: a control past its card's side fails and names the container -- {_l08['status']}")
check(_layout("LAYOUT-08", [{"control_overflow_count": 0, "control_overflow": []}, {}, {}])["status"] == "pass", "LAYOUT-08: controls inside their container pass")
# ---- LAYOUT-09: a single-line row's own ellipsis never cuts a count, a status or a duration at phone width (#1343) ----
_cut = {"sel": "span.lbl", "text": "Ran 2 commands, read 3 files +128", "why": "cuts failed 1m30s", "over_px": 44}
_l09 = _layout("LAYOUT-09", [{}, {"clipped_trailing_count": 1, "clipped_trailing": [_cut]}, {}])
check(_l09["status"] == "fail" and _l09["severity"] == "P2" and _l09["category"] == "layout" and _l09["owner"] == "app"
      and _l09["evidence"][0]["items"] == [_cut] and _l09["measured"] == {"iphone-light-home": 1.0},
      f"LAYOUT-09: a phone-width row whose ellipsis cuts a figure fails, names the row and what it cuts -- {_l09['status']} {_l09['evidence']}")
check(_layout("LAYOUT-09", [{}, {"clipped_trailing_count": 0, "clipped_trailing": []}, {}])["status"] == "pass", "LAYOUT-09: a phone with no cut figure passes")
check(_layout("LAYOUT-09", [{"clipped_trailing_count": 3, "clipped_trailing": [_cut]}, {"clipped_trailing_count": 0, "clipped_trailing": []}, {}])["status"] == "pass",
      "LAYOUT-09: a desktop hit is not read, the rule is the phone width's")
_l09_doc = _doc("compliant")
for _s in _l09_doc["screens"]:
    _s["metrics"]["layout"].update({"clipped_trailing_count": 1, "clipped_trailing": [_cut]})
_l09_out = ev.evaluate(_l09_doc, rubric, _specs("compliant"))
_l09_sentence = report_mod.finding_sentence(next(r for r in _l09_out["rules"] if r["id"] == "LAYOUT-09"), _l09_out)
check("span.lbl" in _l09_sentence and "cuts failed 1m30s" in _l09_sentence and "{" not in _l09_sentence,
      f"LAYOUT-09: the fix sentence names the row and the figure it cuts -- {_l09_sentence}")
check(next(r for r in rubric.rules if r.id == "LAYOUT-09").devices == ["iphone"], "LAYOUT-09 is limited to the iphone leg")
check(not any("ellipsis" in j.question.lower() for j in rubric.judgment), "no [[judgment]] question covers a figure cut by an ellipsis, so none double-counts LAYOUT-09")

# ---- COMP-18: one who-when header per chat turn, never one card per text fragment (#1344) ----


def _c18_layout(layout_by_screen) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], layout_by_screen):
        s["metrics"]["layout"].update(patch)
    return _c18_of(d)


_run = {"sel": "ol.parts", "text": "Agent · <time>", "who": "Agent", "blocks": 3}
_none_seen = {"turn_headers": [], "repeated_headers": [], "repeated_header_count": 0}
_c18 = _c18_layout([{"turn_headers": [{"who": "Agent", "count": 3}], "repeated_headers": [_run], "repeated_header_count": 1}, {}, {}])
check(_c18["status"] == "fail" and _c18["severity"] == "P2" and _c18["category"] == "components" and _c18["owner"] == "app"
      and _c18["evidence"][0]["items"] == [_run] and _c18["measured"].get("desktop-light-home") == 1.0,
      f"COMP-18: a run of consecutive blocks repeating one header fails, names the parent and the header -- {_c18['status']} {_c18['evidence']}")
_c18_one = _c18_layout([{"turn_headers": [{"who": "Agent", "count": 1}]}, {}, {}])
check(_c18_one["status"] == "pass", f"COMP-18: one header over a turn's parts passes -- {_c18_one['status']} {_c18_one['reason']}")
_c18_unm = _c18_layout([_none_seen, _none_seen, _none_seen])
check(_c18_unm["status"] == "unmeasured" and "no rendered chat transcript" in _c18_unm["reason"],
      f"COMP-18: a run that never reaches a transcript is unmeasured, never a pass -- {_c18_unm['status']} {_c18_unm['reason']}")
_c18_na = _c18_layout([{"turn_headers": [{"who": "Agent", "count": 1}]}, _none_seen, _none_seen])
check(_c18_na["status"] == "pass" and "n/a on 2" in _c18_na["reason"],
      f"COMP-18: once one screen showed a transcript the others are n/a, not unmeasured -- {_c18_na['status']} {_c18_na['reason']}")

# `[design] no_chat_surface` (#1443): an app that renders no chat transcript reads COMP-18 as n/a, with the reason stated
def _c18_declared(layout_by_screen, **doc_keys) -> dict:
    d = _doc("compliant")
    d.update(doc_keys)
    for s, patch in zip(d["screens"], layout_by_screen):
        s["metrics"]["layout"].update(patch)
    return _c18_of(d)


_c18_decl = _c18_declared([_none_seen, _none_seen, _none_seen], no_chat_surface=True)
check(_c18_decl["status"] == "pass" and "no chat surface" in _c18_decl["reason"] and "not applicable" in _c18_decl["reason"],
      f"COMP-18: a declared no-chat app is n/a on every screen with the reason stated, not unmeasured -- {_c18_decl['status']} {_c18_decl['reason']}")
_c18_undecl = _c18_declared([_none_seen, _none_seen, _none_seen], no_chat_surface=False)
check(_c18_undecl["status"] == "unmeasured" and "no rendered chat transcript" in _c18_undecl["reason"],
      f"COMP-18: an app that declares nothing keeps unmeasured -- {_c18_undecl['status']} {_c18_undecl['reason']}")
_c18_conflict = _c18_declared([{"turn_headers": [{"who": "Agent", "count": 3}], "repeated_headers": [_run], "repeated_header_count": 1}, _none_seen, _none_seen],
                              no_chat_surface=True)
check(_c18_conflict["status"] == "fail" and _c18_conflict["measured"].get("desktop-light-home") == 1.0
      and "no chat surface" in _c18_conflict["reason"],
      f"COMP-18: a declared app whose walk reaches a turn is still measured, and the report names the conflict -- {_c18_conflict['status']} {_c18_conflict['reason']}")
_c18_bad = _c18_declared([_none_seen, _none_seen, _none_seen], no_chat_surface=False, no_chat_surface_error="[design] no_chat_surface must be true or false")
check(_c18_bad["status"] == "unmeasured" and "no_chat_surface" in _c18_bad["reason"],
      f"COMP-18: a malformed declaration is unmeasured with its reason, never read as an opt-out -- {_c18_bad['status']} {_c18_bad['reason']}")
_c18_doc = _doc("compliant")
for _s in _c18_doc["screens"]:
    _s["metrics"]["layout"].update({"turn_headers": [{"who": "Agent", "count": 3}], "repeated_headers": [_run], "repeated_header_count": 1})
_c18_out = ev.evaluate(_c18_doc, rubric, _specs("compliant"))
_c18_sentence = report_mod.finding_sentence(next((r for r in _c18_out["rules"] if r["id"] == "COMP-18"), _MISSING), _c18_out)
check("ol.parts" in _c18_sentence and "{" not in _c18_sentence, f"COMP-18: the fix sentence names the parent of the run -- {_c18_sentence}")
check(not any("header" in j.question.lower() and "turn" in j.question.lower() for j in rubric.judgment),
      "no [[judgment]] question covers a header repeated inside a turn, so none double-counts COMP-18")
check(getattr(next((r for r in rubric.rules if r.id == "COMP-18"), None), "metric", None) == "layout.repeated_turn_header"
      and all(k in measure.metric_paths() for k in ("layout.turn_headers", "layout.repeated_headers", "layout.repeated_header_count")),
      "COMP-18: the capped sample, the count and the transcript-reached list are all in metric_paths()")
_c18_old = _doc("compliant")
for _s in _c18_old["screens"]:
    for _k in ("turn_headers", "repeated_headers", "repeated_header_count"):
        _s["metrics"]["layout"].pop(_k, None)
check(_c18_of(_c18_old)["status"] == "unmeasured",
      "COMP-18: a run from before the metric existed is unmeasured, never a pass")

# ---- LAYOUT-10: a glance card that changes height as its late reads land (fleet-config#1354) --------------------------


def _l10_of(mutate) -> dict:
    """LAYOUT-10 on the compliant doc with each tab screen's `glance` section changed by `mutate`."""
    d = _doc("compliant")
    for s in d["screens"]:
        if s["kind"] == "tab":
            mutate(s["metrics"])
    return next((r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "LAYOUT-10"), _L10_MISSING)


_l10_rule = next((r for r in rubric.rules if r.id == "LAYOUT-10"), None)
check(_l10_rule is not None and _l10_rule.metric == "glance.card_growth_px" and _l10_rule.fail_when == "gt" and _l10_rule.threshold == 8
      and _l10_rule.owner == "app" and _l10_rule.screens == ["tab"] and _l10_rule.mockup in mockups_mod.MOCKUP_IDS
      and "glance card" in _l10_rule.standard,
      f"LAYOUT-10: reads glance.card_growth_px, fails over 8px, app-owned, tab screens only, cites the glance card contract -- {_l10_rule}")
check(all(f"glance.{k}" in measure.metric_paths() for k in ("state", "grew_px", "below", "changes", "change_count", "idle", "pending")),
      "LAYOUT-10: the state, the growth, the capped change sample and the idle facts are all in metric_paths()")
_l10_grow = lambda px: (lambda m: m["glance"].update(grew_px=px, settled_px=176 + px))
check(_l10_of(_l10_grow(8))["status"] == "pass" and _l10_of(_l10_grow(9))["status"] == "fail",
      "LAYOUT-10: 8px of growth passes (rounding, a font's metric swap), 9px fails")
check(_l10_of(_l10_grow(-120))["status"] == "pass", "LAYOUT-10: a card that only got shorter is never a finding")
_l10_last = _l10_of(lambda m: m["glance"].update(grew_px=132, below=False))
check(_l10_last["status"] == "pass" and "nothing under the card" in _l10_last["reason"] and not _l10_last["evidence"],
      f"LAYOUT-10: a card with nothing under it moves nothing as it grows: n/a with the reason -- {_l10_last['reason']}")
_l10_none = _l10_of(lambda m: m.update(glance={"state": "no_card", "idle": True, "idle_ms": 400}))
check(_l10_none["status"] == "pass" and "no card under the page header" in _l10_none["reason"],
      f"LAYOUT-10: a tab with no card under its header is n/a -- {_l10_none['reason']}")
_l10_busy = _l10_of(lambda m: m.update(glance={"state": "not_idle", "first_px": 44, "idle": False, "idle_ms": 10000, "pending": ["/api/late"]}))
check(_l10_busy["status"] == "unmeasured" and "never went quiet" in _l10_busy["reason"] and "/api/late" in _l10_busy["reason"],
      f"LAYOUT-10: requests that never go quiet are unmeasured, naming what was in flight, never a pass -- {_l10_busy['reason']}")
_l10_old = _l10_of(lambda m: m.pop("glance"))
check(_l10_old["status"] == "unmeasured", "LAYOUT-10: a run from before the tracker existed is unmeasured, never a pass")
_l10_dead = _l10_of(lambda m: m.update(glance={"state": "unavailable", "idle": True, "idle_ms": 400}))
check(_l10_dead["status"] == "unmeasured" and "unavailable" in _l10_dead["reason"],
      f"LAYOUT-10: a page where the tracker never ran is unmeasured -- {_l10_dead['reason']}")
_l10_bad = next((r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "LAYOUT-10"), None)
check(_l10_bad is not None, "LAYOUT-10: the violating fixture reaches the rule")
if _l10_bad is not None:
    _l10_sentence = report_mod.finding_sentence(_l10_bad, None)
    check(_l10_bad["status"] == "fail" and _l10_bad["measured"] == {"desktop-light-home": 132.0, "iphone-light-home": 132.0}
          and "section.card.glance" in _l10_sentence and "132px" in _l10_sentence and "{" not in _l10_sentence,
          f"LAYOUT-10: the violating card grew 132px; the fix sentence names the card and its growth -- {_l10_sentence}")
    _l10_mock, _l10_note = mockups_mod.render_mockup({"id": "LAYOUT-10", "mockup": "glance-steady"}, report_mod.finding_values(_l10_bad))
    check(_l10_mock is not None and "&ndash;" in _l10_mock["proposed"] and "132px" in _l10_mock["now"],
          f"LAYOUT-10: the glance-steady mock-up draws the dash rows against the 132px growth -- {_l10_note}")

for _rid, _sect, _keys in (("COMP-07", "controls", ("label_fit_bad", "label_fit_bad_count")),
                           ("LAYOUT-08", "layout", ("control_overflow", "control_overflow_count")),
                           ("LAYOUT-09", "layout", ("clipped_trailing", "clipped_trailing_count"))):
    _old = _doc("compliant")
    for _s in _old["screens"]:
        for _k in _keys:
            _s["metrics"][_sect].pop(_k, None)
    check(next(r for r in ev.evaluate(_old, rubric, _specs("compliant"))["rules"] if r["id"] == _rid)["status"] == "unmeasured",
          f"{_rid}: a run from before the metric existed is unmeasured, never a pass")

# ---- evaluate: compliant fixture passes every rule ---------------------------

check(all(s == "pass" for s in statuses_c.values()), f"compliant: every rule passes ({[k for k, v in statuses_c.items() if v != 'pass']})")
check(all(v["score"] == 100.0 and v["grade"] == "A" and not v["unmeasured"] for v in out_c["categories"].values()),
      "compliant: every category 100/A, measured")
check(out_c["overall"] == {"score": 100.0, "grade": "A", "unmeasured": False}, "compliant: overall A")
check(out_c["schema_version"] == 1 and out_c["rubric_version"] == "1.42.0" and out_c["target"] == "fixture-app"
      and out_c["commit"].startswith("0000") and out_c["generated_at"].endswith("Z"), "evaluate envelope keys")
check([s["id"] for s in out_c["screens"]] == ["desktop-light-home", "iphone-light-home", "desktop-light-dialog-edit"],
      "evaluate echoes the screen list")
t03 = next(r for r in out_c["rules"] if r["id"] == "TOUCH-03")
check(set(t03["measured"]) == {"desktop-light-home", "iphone-light-home"} and "n/a on 1" in t03["reason"],
      "a null aggregate over an empty population is N/A, not unmeasured")

# ---- exclude_selectors: embedded content is declared, never guessed (fleet-config#1185) -----------------------------------
check(plan.exclude_selectors({}) == [] and plan.exclude_selectors({"exclude_selectors": ".preview-frame"}) == [".preview-frame"]
      and plan.exclude_selectors({"exclude_selectors": [" .a ", "", 3, "#b"]}) == [".a", "#b"] and plan.exclude_selectors({"exclude_selectors": 7}) == [],
      "plan.exclude_selectors: a string is one selector, blanks and non-strings are dropped, anything else is none")
check(measure.default_params()["excludeSelectors"] == [] and measure.default_params(exclude_selectors=[".x"])["excludeSelectors"] == [".x"]
      and capture.script_params(rubric, src_specs["light"], {"exclude_selectors": [".x"]})["script"]["excludeSelectors"] == [".x"]
      and capture.script_params(rubric, src_specs["light"])["script"]["excludeSelectors"] == [],
      "the declaration rides into the script's params; none declared is an empty list")
check("el.closest(EXCLUDE)" in measure._MEASURE_JS, "every measure's visibility test refuses an element inside an excluded selector")

# ---- inventory: the vendored-component catalog and the read-only section (fleet-config#1497) -----------------------------
check(capture._css_class_uses("/* .commented {} */ html[data-theme=dark] .toggle.on .knob, .x:not(.y) [href$='.css'] { a: b; }"
                              " @media (max-width: 1px) { .toggle { c: d; } }")
      == {"toggle": True, "knob": False, "x": True},
      "CSS class uses: the first class of a selector leads (a classless html[...] does not), a nested one is a part, a chained "
      "state class, a :not() argument, an attribute value and a comment name nothing")
_inv_scaffold = STATE / "inv-scaffold"
for _name, _css in (("switch", ".toggle { } .toggle .knob { } .icon { }"), ("icon-button", ".icon-button { } .icon { }"),
                    ("modal", ".detail-card .icon-button { } .detail-card .row { } .icon { }"), ("base", "button { font: inherit; }")):
    (_inv_scaffold / "app/webapp/static/_vendored" / _name).mkdir(parents=True)
    (_inv_scaffold / "app/webapp/static/_vendored" / _name / f"{_name}.css").write_text(_css, encoding="utf-8")
(_inv_scaffold / ".fleet.toml").write_text(
    "[components]\n" + "".join(f'{n} = {{ src = "app/webapp/static/_vendored/{n}" }}\n' for n in ("switch", "icon-button", "modal", "base"))
    + 'geometry = { src = "tests/e2e/_geometry.py" }\n', encoding="utf-8")
_inv_cat = capture.vendored_components(str(_inv_scaffold))
check(_inv_cat == {"error": None, "components": {
          "icon-button": {"roots": ["icon-button"], "parts": []},
          "modal": {"roots": ["detail-card"], "parts": ["row"]},
          "switch": {"roots": ["toggle"], "parts": ["knob"]}}},
      f"vendored_components: a class goes to the component that leads a selector with it (icon-button, not modal), one used "
      f"nowhere else stays its definer's part, one several lead (.icon) goes to nobody, and a classless or non-web entry is "
      f"left out -- {_inv_cat}")
_inv_missing = capture.vendored_components(str(_inv_scaffold / "nope"))
check(capture.vendored_components(None) == {"components": None, "error": "no scaffold checkout"}
      and _inv_missing["components"] is None and "no catalog at" in _inv_missing["error"],
      f"vendored_components: no scaffold, or no .fleet.toml, is its own error, never an empty catalog -- {_inv_missing}")
_inv_params = capture.script_params(rubric, src_specs["light"], None, _inv_cat)["script"]
check(_inv_params["components"] == _inv_cat["components"] and _inv_params["componentsError"] is None
      and measure.default_params()["components"] is None and measure.default_params()["componentsError"] is None,
      "the catalog rides into the script's params like exclude_selectors; none passed is None, which the section reports as unmeasured")
check(all(f"inventory.{k}" in measure.metric_paths() for k in ("unmeasured", "controls", "controls_count", "kinds", "components", "text", "text_count"))
      and "section('inventory'" in measure._MEASURE_JS and "inventory" not in measure.SECTIONS
      and not any(str(r.metric).startswith("inventory.") for r in rubric.rules),
      "inventory: every key is in metric_paths(), the section is in the page script, and it is no rule's input yet")

# ---- A11Y-02 and the vendored text-size control (fleet-config#1185) -------------
# parking-manager#58 / facilitation-suite#164: the control lives in Settings, which is in the DOM only while open, so a per-screen
# probe fails every other screen by construction. The vendored markers are #textSizeControl + data-textsize buttons (the probe
# looked for data-text-size) and the boot script stamps html[data-textsize].


def _a11y02(mutate) -> dict:
    d = _doc("violating")
    for s in d["screens"]:
        mutate(s["metrics"]["a11y"], s)
    return next(r for r in ev.evaluate(d, rubric, _specs("violating"))["rules"] if r["id"] == "A11Y-02")


base = _a11y02(lambda a, s: None)
check(base["status"] == "fail" and len(base["evidence"]) == 2, "A11Y-02: zoom locked with no control anywhere still fails every screen")
seen = _a11y02(lambda a, s: a.update(text_size_control=(s["id"] == "iphone-light-home")))
check(seen["status"] == "pass" and sorted(seen["measured"]) == ["desktop-light-home", "iphone-light-home"],
      f"A11Y-02: a control seen on one walked screen (Settings open) is the app's, not a failure of the others -- {seen['status']}")
stamped = _a11y02(lambda a, s: a.update(text_size_stamped=True))
check(stamped["status"] == "unmeasured"
      and "data-textsize" in stamped["reason"] and "extra_steps" in stamped["reason"] and not stamped["evidence"],
      f"A11Y-02: only the boot stamp seen -> unmeasured, naming the fix, never a pass or a false fail -- {stamped['status']}: {stamped['reason']}")
unstamped = _a11y02(lambda a, s: a.update(text_size_stamped=False))
check(unstamped["status"] == "fail", "A11Y-02: no stamp and no control is still a real failure")
# Roberto's 2026-10-03 decision (fleet-config#1211): every fleet app carries the text-size control, whether or not it locks zoom.
unlocked = _a11y02(lambda a, s: a.update(zoom_locked=False, text_size_control=False))
check(unlocked["status"] == "fail" and len(unlocked["evidence"]) == 2,
      f"A11Y-02: an app with zoom NOT locked and no text-size control fails -- {unlocked['status']} (#1211)")
check(_a11y02(lambda a, s: a.update(zoom_locked=False, text_size_control=True))["status"] == "pass",
      "A11Y-02: the control present passes with zoom unlocked")
check(_a11y02(lambda a, s: a.update(zoom_locked=True, text_size_control=True))["status"] == "pass",
      "A11Y-02: the control present passes with zoom locked")
check(_a11y02(lambda a, s: a.update(zoom_locked=None))["status"] == "fail",
      "A11Y-02: an unreadable zoom lock no longer hides a missing control (the control is the fact judged)")
check(_a11y02(lambda a, s: a.update(zoom_locked=False, text_size_control=False, text_size_stamped=True))["status"] == "unmeasured",
      "A11Y-02: unlocked, only the boot stamp seen -> unmeasured, the same as locked")
for probe in ("data-textsize", "data-text-size", "aria-labelledby", "#textSizeControl"):
    check(probe in str(measure._MEASURE_JS), f"the a11y probe looks for {probe}")
check('[data-textsize]:not(html)' in measure._MEASURE_JS, "the probe never reads the boot stamp on <html> as the control")
l06 = next(r for r in out_c["rules"] if r["id"] == "LAYOUT-06")
check(set(l06["measured"]) == {"desktop-light-home"}, "devices=[desktop] restricts LAYOUT-06 to desktop screens")
n01 = next(r for r in out_c["rules"] if r["id"] == "NAV-01")
check("desktop-light-dialog-edit" not in n01["measured"], "screens=[tab] keeps NAV-01 off dialogs")
c01 = next(r for r in out_c["rules"] if r["id"] == "COLOR-01")
check(set(c01["measured"]) == {"spec-light", "spec-dark"}, "spec rule measured per theme")

# ---- evaluate: violating fixture fails every rule ----------------------------

out_v = ev.evaluate(_doc("violating"), rubric, _specs("violating"))
statuses_v = {r["id"]: r["status"] for r in out_v["rules"]}
check(all(s == "fail" for s in statuses_v.values()), f"violating: every rule fails ({[k for k, v in statuses_v.items() if v != 'fail']})")
check(all(r["evidence"] and r["evidence"][0]["screen"] for r in out_v["rules"]), "every failing rule carries evidence with a screen id")
check(all(r["measured"] for r in out_v["rules"]), "every failing rule carries measured values keyed by screen")
cat_v = out_v["categories"]
check(cat_v["typography"]["score"] == 100 - (12 + 25 + 12 + 12 + 6 + 6 + 6) and cat_v["typography"]["grade"] == "F",
      "typography: penalties once per rule (P1+P0+P1+P1+P2+P2+P2)")
check(cat_v["touch"]["score"] == 100 - (25 + 25 + 12) and cat_v["navigation"]["score"] == 100 - (12 + 6 + 12 + 2 + 2),
      "touch / navigation scores (navigation: NAV-01 P1 + NAV-02 P2 + NAV-03 P1 + NAV-04 P3 + NAV-05 P3)")
check(cat_v["typography"]["failed"] == ["TYPE-01", "TYPE-02", "TYPE-03", "TYPE-04", "TYPE-05", "TYPE-06", "TYPE-07"], "failed ids listed in rule order")
check(out_v["overall"]["grade"] == "F" and not out_v["overall"]["unmeasured"], "violating: overall F, fully measured")
c01v = next(r for r in out_v["rules"] if r["id"] == "COLOR-01")
check(any(i["component"] == "button-tint" and i["ratio"] == 4.13 for e in c01v["evidence"] for i in e["items"]),
      "COLOR-01 evidence names the 4.13:1 pair")
t01v = next(r for r in out_v["rules"] if r["id"] == "TOUCH-01")
check(t01v["measured"]["desktop-light-home"] == 0.3 and t01v["evidence"][0]["items"][0]["sel"] == "button.icon",
      "TOUCH-01 share 3/10 with the small controls as evidence")
c02v = {e["screen"]: e["items"] for e in next(r for r in out_v["rules"] if r["id"] == "COMP-02")["evidence"]}
check(c02v.get("desktop-light-home") == [{"box": "22x22", "count": 2, "elements": [
          {"glyph": "play", "host": "button.job-run", "label": "Run"}, {"glyph": "chevron-down", "host": "summary", "label": "More"}]}]
      and c02v.get("iphone-light-home") == [{"box": "22x22", "count": 2, "elements": []}],
      f"COMP-02 evidence names each off-step icon's glyph and host; a run from before icons.elements still evaluates (#1020) -- {c02v}")
check(next(r for r in out_v["rules"] if r["id"] == "TOUCH-03")["threshold"]["value"] == 40.0,
      "threshold_token: the violating spec's 40px button height is what TOUCH-03 compares against")
check(ev.grade_for(89.99, rubric.grades) == "B" and ev.grade_for(0, rubric.grades) == "F", "grade mapping edges")

# TYPE-05 follows #964: overline is the one legal caps role; badges and chips are sentence case.
caps = _doc("violating")
for s in caps["screens"]:
    s["metrics"]["text"]["uppercase"] = [{"sel": "span.badge", "text": "NEW"}, {"sel": "span.chip", "text": "3M"},
                                         {"sel": "div.list-overline", "text": "TODAY"}]
t05 = next(r for r in ev.evaluate(caps, rubric, _specs("violating"))["rules"] if r["id"] == "TYPE-05")
check(t05["measured"]["desktop-light-home"] == 3.0 and t05["measured"]["iphone-light-home"] == 2.0
      and [i["sel"] for i in t05["evidence"][0]["items"]] == ["span.badge", "span.chip", "button.record"],
      f"TYPE-05 counts an uppercase badge and chip and the typed-in-capitals button the fixture carries, not the overline (got {t05['measured']})")

# TYPE-05 also reads capitals typed in the markup; TYPE-06 reads one class of label cased two ways across the run (#1365).
def _lab(cls: str, text: str, style: str, literal: bool = False, sel: str = "button.btn") -> dict:
    return {"sel": sel, "cls": cls, "text": text, "style": style, "literal": literal}


def _casing(by_screen, rule_id: str, upper=None) -> dict:
    d = _doc("compliant")
    for sc, labels in zip(d["screens"], by_screen):
        sc["metrics"]["casing"] = ({"labels": labels, "label_count": len(labels), "labels_seen": len(labels)} if labels is not None else {"error": "boom"})
        if upper is not None:
            sc["metrics"]["text"]["uppercase"] = upper
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == rule_id)


_RECORD = _lab("button", "RECORD", "upper", True, "button.record")
_t05 = _casing([[_RECORD, _lab("button", "PDF", "upper", True), _lab("button", "ES", "upper", True), _lab("button", "EXPORT PDF", "upper", True),
                 _lab("button", "Save changes", "sentence")], [], []], "TYPE-05")
check(_t05["status"] == "fail" and [i["text"] for i in _t05["evidence"][0]["items"]] == ["RECORD", "EXPORT PDF"]
      and "typed in capitals" in _t05["evidence"][0]["items"][0]["why"],
      f"TYPE-05: a button typed in capitals fails, an allow-listed PDF and ES chip and sentence case pass -- {_t05['measured']}")
_t05b = _casing([[_RECORD], [], []], "TYPE-05", upper=[{"sel": "button.record", "text": "RECORD"}])
check(_t05b["measured"]["desktop-light-home"] == 1.0, f"TYPE-05: a label that is both typed and transformed to capitals is counted once -- {_t05b['measured']}")
check(_casing([[_lab("button", "SAVE", "upper", False)], [], []], "TYPE-05")["status"] == "pass",
      "TYPE-05: a label shown in capitals by text-transform is the `uppercase` list's, not read twice from the casing section")
_t05_old = _doc("compliant")
for _s in _t05_old["screens"]:
    _s["metrics"].pop("casing", None)
check(next(r for r in ev.evaluate(_t05_old, rubric, _specs("compliant"))["rules"] if r["id"] == "TYPE-05")["status"] == "pass",
      "TYPE-05: a run from before the casing section is read from `text.uppercase` alone")

_t06 = _casing([[_lab("button", "Open settings", "sentence"), _lab("button", "Save changes", "sentence")],
                [_lab("button", "Open Settings", "title")], []], "TYPE-06")
check(_t06["status"] == "fail" and _t06["measured"] == {"desktop-light-home": 0.0, "iphone-light-home": 1.0, "desktop-light-dialog-edit": 0.0}
      and "title case" in _t06["evidence"][0]["items"][0]["why"] and "sentence" in _t06["evidence"][0]["items"][0]["why"],
      f"TYPE-06: the one Title Case button among sentence-case buttons on another tab is the outlier -- {_t06['measured']}")
_t06s = _casing([[_lab("label:stat-label", "Running", "capitalised", sel="span.stat-label")],
                 [_lab("label:stat-label", "running", "lower", sel="span.stat-label")], []], "TYPE-06")
check(_t06s["status"] == "fail" and _t06s["measured"]["desktop-light-home"] == 1.0 and _t06s["measured"]["iphone-light-home"] == 1.0,
      f"TYPE-06: counters capitalised on one tab and lowercase on another clash, and a tie marks both sides -- {_t06s['measured']}")
for _why, _screens in (("a capitalised word beside sentence case", [[_lab("button", "Save", "capitalised"), _lab("button", "Open settings", "sentence")], [], []]),
                       ("a capitalised word beside confirmed title case", [[_lab("button", "Save", "capitalised")], [_lab("button", "Open Account Settings", "title")], []]),
                       ("an acronym chip beside sentence case", [[_lab("button", "ES", "upper", True), _lab("button", "Save changes", "sentence")], [], []]),
                       ("a heading in sentence case beside a Title Case button", [[_lab("heading", "Recent takes", "sentence")], [_lab("button", "Open Settings", "title")], []]),
                       ("sentence case throughout", [[_lab("button", "Save changes", "sentence")], [_lab("button", "Open settings", "sentence")], []]),
                       ("a capitalised proper noun the run never sets in lowercase", [[_lab("button", "Translate to English", "title"), _lab("button", "Boost amount", "sentence")], [], []])):
    check(_casing(_screens, "TYPE-06")["status"] == "pass", f"TYPE-06: {_why} is not a clash")
_t06t = _casing([[_lab("button", "Save changes", "sentence"), _lab("button", "Open settings", "sentence")], [_lab("button", "Save All Changes Now", "title")], []], "TYPE-06")
check(_t06t["status"] == "fail" and _t06t["measured"]["iphone-light-home"] == 1.0,
      f"TYPE-06: three capitalised words after the first confirm title case, which clashes with sentence case -- {_t06t['measured']}")
# #1463: a label whose capitals rest on two or fewer words after the first (a product name among sentence-case
# buttons) is ambiguous, never counted as a casing choice.
_t06p = _casing([[_lab("button", "Save changes", "sentence"), _lab("button", "Open settings", "sentence"), _lab("button", "Launch Docker + Langfuse", "title")], [], []], "TYPE-06")
check(_t06p["status"] == "pass", f"TYPE-06: product names in a sentence-case label are ambiguous, not title case -- {_t06p['measured']}")
_t06q = _casing([[_lab("button", "Save changes", "sentence"), _lab("button", "Open settings", "sentence"), _lab("button", "Launch Docker + Langfuse", "title")],
                 [_lab("button", "Save All Changes Now", "title")], []], "TYPE-06")
check(_t06q["status"] == "fail" and [i["text"] for e in _t06q["evidence"] for i in e["items"]] == ["Save All Changes Now"],
      f"TYPE-06: an ambiguous product-name label does not hide a real Title Case button beside it -- {_t06q['measured']}")
check(_casing([None, [], []], "TYPE-06")["status"] == "unmeasured", "TYPE-06: a failed casing section is unmeasured, never a pass")
check(next(r for r in ev.evaluate(_t05_old, rubric, _specs("compliant"))["rules"] if r["id"] == "TYPE-06")["status"] == "unmeasured",
      "TYPE-06: a run from before the casing section is unmeasured, never a pass")

# ---- determinism: same inputs, identical rule results ------------------------

again = ev.evaluate(_doc("violating"), rubric, _specs("violating"))
strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}  # noqa: E731
check(strip(again) == strip(out_v), "two evaluations of one metrics.json are identical bar generated_at")

# ---- the unmeasured lattice ---------------------------------------------------

down = _doc("compliant")
down["screens"] = []
down["unmeasured"] = {"reason": "NOT_LISTENING", "detail": "127.0.0.1:9999 refused the connection"}
out_d = ev.evaluate(down, rubric, _specs("compliant"))
check(all(r["status"] == "unmeasured" and "NOT_LISTENING" in r["reason"] for r in out_d["rules"]),
      "a target that is not listening -> every rule unmeasured with that reason")
check(all(v["unmeasured"] and v["score"] == 100.0 for v in out_d["categories"].values()) and out_d["overall"]["unmeasured"],
      "unmeasured categories are flagged, never scored as failing")

broken = _doc("compliant")
broken["screens"][0]["status"] = "error"
broken["screens"][0]["reason"] = "TAB_FAILED"
broken["screens"][0]["metrics"] = None
out_b = ev.evaluate(broken, rubric, _specs("compliant"))
l01 = next(r for r in out_b["rules"] if r["id"] == "LAYOUT-01")
check(l01["status"] == "unmeasured" and "desktop-light-home: TAB_FAILED" in l01["reason"],
      "a tab that failed to open makes its rules unmeasured, never pass, naming the screen")
check(out_b["categories"]["layout"]["unmeasured"], "category flagged when one rule is unmeasured")

sect = _doc("compliant")
for s in sect["screens"]:
    s["metrics"]["targets"] = {"error": "GEOMETRY_MISSING"}
out_s = ev.evaluate(sect, rubric, _specs("compliant"))
st = {r["id"]: r["status"] for r in out_s["rules"]}
check(st["TOUCH-01"] == "unmeasured" and st["TOUCH-02"] == "unmeasured" and st["LAYOUT-05"] == "unmeasured"
      and st["TYPE-01"] == "pass", "a failed section only unmeasures the rules that read it")
check("GEOMETRY_MISSING" in next(r for r in out_s["rules"] if r["id"] == "TOUCH-01")["reason"], "section error text carried")

nospec = ev.evaluate(_doc("compliant"), rubric, {"light": {}, "dark": {}})
check(next(r for r in nospec["rules"] if r["id"] == "COLOR-01")["status"] == "unmeasured", "no spec -> spec rule unmeasured")
mixed = _doc("compliant")
mixed["screens"][1]["metrics"]["layout"]["overflow_x"] = True
out_m = ev.evaluate(mixed, rubric, _specs("compliant"))
check(next(r for r in out_m["rules"] if r["id"] == "LAYOUT-01")["status"] == "fail", "one failing screen fails the rule")

# ---- measure.py helpers -------------------------------------------------------

check(measure.metric_value({"text": {"runs": 3}}, "text.runs") == 3 and measure.metric_value({"text": {"error": "x"}}, "text.runs") is None
      and measure.metric_value({}, "nav.primary_count") is None, "metric_value: dotted path, errored section, missing")
check(measure.section_errors({"text": {"error": "boom"}, "nav": {}}) == {"text": "boom", **{s: "missing" for s in measure.SECTIONS if s not in ("text", "nav")}},
      "section_errors lists errored + missing sections")
check("null" in measure.build_script(None) and "el => {" in measure.build_script("el => { return {}; }"), "build_script splices geometry or null")
check(measure.default_params()["hitMin"] == 44.0 and measure.default_params(hit_min=48)["hitMin"] == 48.0, "default_params floors")

_h.report_and_exit("test_component_rules_and_evaluate")
