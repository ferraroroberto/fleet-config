"""Unit tests for skills/_lib/design_review (fleet-config#971).

Pure-logic legs (rubric load/validate, threshold + param resolution from a
spec, evaluate on fixture metrics — one compliant, one violating every
rule — scoring, determinism, the unmeasured lattice, the liveness probe
classification, target/plan resolution) run with no browser. The browser
leg lives in `tests/test_design_review_browser.py`, which the gate runs only
when the design-review surface changed (fleet-config#1481).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_review.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402

import design_review as dr  # noqa: E402
from design_review import capture, evaluate as ev, measure, plan, rubric as rb  # noqa: E402
from design_review import mockups as mockups_mod, report as report_mod  # noqa: E402
from design_lint.colormath import composite, contrast  # noqa: E402
from design_review_support import (  # noqa: E402
    FIX, L10_MISSING as _L10_MISSING, MISSING as _MISSING, rubric,
    c18_of as _c18_of, doc as _doc, port_open as _port_open, specs as _specs,
)

_h = CheckHarness()
check = _h.check

check(dr.evaluate is ev and callable(ev.evaluate), "the package exports the evaluate module; the function is evaluate.evaluate (#972)")


# Every hooks-state write in this file lands in a throwaway dir, never in the live one.
STATE = Path(tempfile.mkdtemp(prefix="design-review-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)


# ---- rubric: the real file loads and names only metrics that exist ----------

check(rubric.version == "1.42.0", "rubric meta.version stamped")
check(len(rubric.rules) == 50, f"50 seed rules loaded (got {len(rubric.rules)})")
check(rubric.categories == ["typography", "color", "touch", "navigation", "layout", "components", "a11y"],
      "categories in rubric order")
check(rb.check_metric_names(rubric, measure.metric_paths()) == [], "every rule metric is a script path or a derived metric")
check(all(r.severity in rb.SEVERITIES and r.owner in rb.OWNERS for r in rubric.rules), "severity/owner vocab")

# ---- rules the static (design_lint) and rendered legs share are defined once (#1062) ----
from design_lint import markup as _lint_markup, rules as _lint_rules  # noqa: E402
from design_lint.contracts import typography as _lint_typography  # noqa: E402

_type04 = next(r for r in rubric.rules if r.id == "TYPE-04")
check(_type04.params.get("allow") == _lint_rules.BREAK_ALL_OK,
      "rubric TYPE-04 allow-list equals design_lint's break-all allow-list")
check(_lint_typography._BREAK_ALL_OK_RE.pattern == _lint_rules.BREAK_ALL_OK,
      "the static break-all check uses the shared allow-list")
_token_sel = [{"sel": ".token-value"}]
check(ev._filtered_count({"text": {"break_all": _token_sel}}, "text", "break_all", _type04,
                         _lint_rules.BREAK_ALL_OK)[0] == 0,
      "a .token-* selector passes the rendered break-all check like the static one")
check(measure.GLYPH_ICON_RE == _lint_rules.GLYPH_ICON_RENDERED_RE, "measure's glyph regex is the shared one")
check(_lint_markup._GLYPH_ICON_RE.pattern == f"[{_lint_rules.GLYPH_ICON_CLASS}]",
      "the static glyph scan uses the shared arrow/shape class")
_rendered_probe = "←⇿■◿⋮⬀⯿✖✕☰"  # one glyph per rendered-leg range, endpoints included
check(all(_lint_markup._EMOJI_RE.search(c) or _lint_markup._GLYPH_ICON_RE.search(c) for c in _rendered_probe)
      and re.search(measure.GLYPH_ICON_RE, "×"),
      "every rendered-leg glyph but the documented × is owned by a static scan (no static blind spot)")
check({"P0": 25.0, "P1": 12.0, "P2": 6.0, "P3": 2.0} == rubric.penalties, "penalties 25/12/6/2")
check(set(rubric.weights) == set(rubric.categories), "every category weighted")

# ---- rubric: validation refuses the shapes that would make evaluate lie ----

_base = {"meta": {"version": "x"}, "weights": {"c": 1.0}, "grades": {"A": 90, "F": 0},
         "penalties": {"P0": 25, "P1": 12, "P2": 6, "P3": 2},
         "rules": [{"id": "R-1", "category": "c", "title": "t", "metric": "layout.overflow_x", "fail_when": "true",
                    "severity": "P0", "standard": "s", "fix_template": "f", "owner": "app", "mockup": "none"}]}


def _refuses(mutate, label: str) -> None:
    data = json.loads(json.dumps(_base))
    mutate(data)
    try:
        rb.validate_rubric(data)
        check(False, f"rubric validation refuses {label}")
    except rb.RubricError:
        check(True, label)


_refuses(lambda d: d["meta"].pop("version"), "missing meta.version")
_refuses(lambda d: d["rules"].append(dict(d["rules"][0])), "duplicate rule id")
_refuses(lambda d: d["rules"][0].update(severity="P9"), "unknown severity")
_refuses(lambda d: d["rules"][0].update(owner="vendor"), "unknown owner")
_refuses(lambda d: d["rules"][0].update(fail_when="between"), "unknown fail_when")
_refuses(lambda d: d["rules"][0].update(category="zzz"), "category without a weight")
_refuses(lambda d: d["rules"][0].update(fail_when="gt"), "comparison without a threshold")
_refuses(lambda d: d["rules"][0].update(screens=["popup"]), "unknown screen kind")
_refuses(lambda d: d["penalties"].pop("P3"), "missing penalty")
check(rb.validate_rubric(_base).rules[0].id == "R-1", "a well-formed rubric validates")

# ---- thresholds + params resolve from the spec, else the literal ------------

light_c = _specs("compliant")["light"]
touch03 = next(r for r in rubric.rules if r.id == "TOUCH-03")
check(rb.resolve_threshold(touch03, light_c) == {"value": 48.0, "source": "spec:components.button-primary.height"},
      "threshold_token resolves from the spec")
check(rb.resolve_threshold(touch03, {}) == {"value": 48.0, "source": "rubric"}, "no spec token -> literal threshold")
type03 = next(r for r in rubric.rules if r.id == "TYPE-03")
check(rb.resolve_threshold(type03, light_c) == {"value": 11.0, "source": "rubric"}, "rule without token uses literal")
params_c = rb.resolve_params(rubric, light_c)
check(params_c["hit_min"] == 44.0 and params_c["primary_min"] == 48.0, "hit_min/primary_min from spec tokens")
check(params_c["icon_steps"] == [16.0, 18.0, 24.0], "icons.size group -> sorted px steps")
check(rb.resolve_params(rubric, {}) == {"hit_min": 44, "primary_min": 48, "icon_steps": [16, 18, 20, 24],
                                        "measure": 772, "gutter": 12, "row_leading_toggles": 1,
                                        "row_trailing_accessories": 1, "row_extra_actions": 1},
      "no spec -> [params] defaults")
check(rb.resolve_params(rubric, _specs("violating")["light"])["hit_min"] == 48.0, "a 48px spec floor is honoured")
check(rb.px_value("44px") == 44.0 and rb.px_value("1.5rem") is None and rb.px_value(12) == 12.0, "px_value parsing")

# ---- spec pairs: this checkout's design*.md (not ~/.claude, which is the primary's) ----

src_specs = rb.load_specs(REPO / "design.md", REPO / "design.dark.md")


def _pair(tokens, fg_tok, bg_tok):
    """WCAG ratio of `fg_tok` on `bg_tok`, both composited over `colors.card`; 0.0 if either is undefined."""
    card = ev.parse_color(tokens["colors.card"], tokens)
    fg, bg = ev.parse_color(tokens.get(fg_tok, ""), tokens), ev.parse_color(tokens.get(bg_tok, ""), tokens)
    if fg is None or bg is None:
        return 0.0
    bg = composite(bg, card)
    return round(contrast(composite(fg, bg), bg), 2)


# the audit's measurement, reproduced from tokens: accent text on its own tint
check(_pair(src_specs["light"], "colors.accent", "colors.accent-soft") == 4.13, "light accent on accent-soft = 4.13 (the audit's number)")
check(_pair(src_specs["dark"], "colors.accent", "colors.accent-soft") == 3.79, "dark accent on accent-soft = 3.79 (the audit's number)")
# the fix (#963): every spec text/background pair clears AA, and the named roles hold their floors
for _theme, _tokens in src_specs.items():
    _pairs = {p["component"]: p for p in ev.spec_pairs(_tokens)}
    for _name in ("button-primary", "button-tint", "nav-tab-active", "chip"):
        _p = _pairs.get(_name) or {}
        check((_p.get("ratio") or 0) >= _p.get("threshold", 4.5), f"{_theme} {_name} text clears AA (got {_p.get('ratio')})")
    _low = [f"{n} {p['ratio']}" for n, p in _pairs.items() if p["ratio"] is not None and p["ratio"] < p["threshold"]]
    check(not _low, f"{_theme}: no spec component pair below AA (got {_low})")
    check("button-disabled" not in _pairs, f"{_theme}: the disabled recipe is exempt from the AA pair check (WCAG 1.4.3, #1368)")
    for _role in ("accent", "success", "danger", "attention"):
        _tok = dict(_tokens, **{"colors._tint": f"color-mix(in srgb, var(--{_role}) 16%, transparent)"})
        _r = _pair(_tok, f"colors.{_role}-text", "colors._tint")
        check(_r >= 4.5, f"{_theme} {_role}-text on its 16% tint >= 4.5 (got {_r})")
    _cb = _pair(_tokens, "colors.control-border", "colors.card")
    check(_cb >= 3.0, f"{_theme} control-border vs card >= 3:1 (got {_cb})")
    check(_tokens.get("components.control.borderColor") == _tokens.get("colors.control-border")
          and _tokens.get("components.switch.trackOff") == _tokens.get("colors.control-border"),
          f"{_theme}: control + switch off-track use control-border")
    check(_tokens.get("components.button-primary.backgroundColor") == _tokens.get("colors.accent-fill"),
          f"{_theme}: button-primary fills with accent-fill")
    for _tile in ("green", "blue", "purple", "orange", "yellow"):
        _r = _pair(_tokens, "colors.accent-fg", f"colors.tile-{_tile}")
        check(_r >= 3.0, f"{_theme} glyph on tile-{_tile} >= 3:1 (non-text, got {_r})")
_new = {k for k in src_specs["light"] if k.startswith("colors.")} ^ {k for k in src_specs["dark"] if k.startswith("colors.")}
check(not _new, f"both themes define the same colour token names (differs: {sorted(_new)})")

# ---- type scale (#964): whole pixels, body-sm for secondary lines, one caps role ----

_typo = {t: {k: v for k, v in s.items() if k.startswith("typography.")} for t, s in src_specs.items()}
check(_typo["light"] == _typo["dark"], "typography is identical in both themes")
_px = {k.split(".")[1]: float(v.replace("rem", "")) * 16 for k, v in _typo["light"].items() if k.endswith(".fontSize")}
check(all(p == int(p) for p in _px.values()), f"every role lands on a whole pixel at a 16px root (got {_px})")
check(sorted({int(p) for p in _px.values()}) == [12, 14, 16, 20, 24, 32], f"scale is 12/14/16/20/24/32 (got {sorted(set(_px.values()))})")
check(_px.get("body-sm") == 14 and _typo["light"].get("typography.body-sm.fontWeight") == "400",
      "body-sm is a 14px regular role for secondary lines")
_caps = [k.split(".")[1] for k, v in _typo["light"].items() if k.endswith(".textTransform") and v == "uppercase"]
check(_caps == ["overline"], f"overline is the only uppercase role (got {_caps})")

# ---- action-row contract (#965): one trailing accessory, destructive in the menu ----

_ar = {t: {k: v for k, v in s.items() if k.startswith("components.action-row.")} for t, s in src_specs.items()}
check(bool(_ar["light"]) and set(_ar["light"]) == set(_ar["dark"]), "action-row is defined with the same keys in both themes")
_arl = src_specs["light"]
check(_arl.get("components.action-row.minHeight") == _arl.get("rows.md")
      and _arl.get("components.action-row.accessorySize") == _arl.get("components.hit-target.min"),
      "action-row height comes from rows.md and its accessory is the 44px hit target")
check(_arl.get("components.action-row.trailingAccessories") == "1"
      and _arl.get("components.action-row.extraVisibleActions") == "1",
      "action-row allows one trailing accessory and at most one other visible action")
for _theme, _s in src_specs.items():
    check(_s.get("components.action-row.destructiveColor") == _s.get("colors.danger-text"),
          f"{_theme}: destructive menu items use danger-text")
    check(_s.get("components.action-row.filterBorder") == _s.get("colors.control-border"),
          f"{_theme}: the list filter field uses control-border")

# ---- text-size escape (#967) ----

_ts = {t: {k: v for k, v in s.items() if k.startswith("text-size.")} for t, s in src_specs.items()}
check(_ts["light"] == _ts["dark"] and _ts["light"].get("text-size.key-suffix") == ".textsize",
      "text-size contract is identical in both themes with the .textsize key suffix")
check([_ts["light"].get(f"text-size.{k}") for k in ("small", "default", "large")] == ["93.75%", "100%", "112.5%"],
      f"text-size steps are 93.75% / 100% / 112.5% (got {_ts['light']})")

# ---- wide desktop layout (#968) ----

_lay = {t: {k: v for k, v in s.items() if k.startswith("layout.")} for t, s in src_specs.items()}
check(_lay["light"] == _lay["dark"], "layout tokens are identical in both themes")
check(_lay["light"].get("layout.measure") == "772px" and _lay["light"].get("layout.wide") == "1100px",
      f"772px measure below a 1100px wide breakpoint (got {_lay['light']})")
check(rb.px_value(_lay["light"].get("layout.rail", "")) is not None
      and _lay["light"].get("layout.list-pane") and _lay["light"].get("layout.detail-pane"),
      "wide layout declares the rail width and the list:detail split")
check(float(rubric.rules[[r.id for r in rubric.rules].index("LAYOUT-06")].params.get("min_viewport", 0))
      == rb.px_value(_lay["light"].get("layout.wide", "")),
      "the rubric's wide-desktop rule measures from the spec's wide breakpoint")

# ---- #996: LAYOUT-06, LAYOUT-03 and COLOR-03 follow the spec ------------------

src_params = rb.resolve_params(rubric, src_specs["light"])
check(src_params.get("measure") == 772.0 and src_params.get("gutter") == 12.0, "LAYOUT-06 reads layout.measure + spacing.gutter from the spec")
check(capture.script_params(rubric, src_specs["light"])["script"].get("rowControlsMax") == 3,
      "LAYOUT-03's per-row budget is action-row's leadingToggles + trailingAccessories + extraVisibleActions (1+1+1)")
_bsel = [part.strip() for part in measure.default_params().get("boundaryControls", "button").split(",")]
check(any(x.startswith("input") for x in _bsel) and not any(x.startswith("button") for x in _bsel) and "[role=switch]" in _bsel,
      "COLOR-03 measures the controls the spec gives a boundary (input, select, textarea, switch), not text-labelled buttons")


def _layout06(content_w, content_span, wide_views=None, wide_views_error=None, pane_split=None):
    doc = _doc("compliant")
    lay = doc["screens"][0]["metrics"]["layout"]
    lay["content_w"] = content_w
    if pane_split is not None:
        lay["pane_split"] = pane_split
    if content_span is None:
        lay.pop("content_span", None)
    else:
        lay["content_span"] = content_span
    if wide_views is not None:
        doc["wide_views"] = wide_views
    if wide_views_error is not None:
        doc["wide_views_error"] = wide_views_error
    return next(r for r in ev.evaluate(doc, rubric, src_specs)["rules"] if r["id"] == "LAYOUT-06")


_md = _layout06(514, 1360)
check(_md["status"] == "pass" and _md["measured"].get("desktop-light-home") == round(1360 / 1440, 4),
      f"LAYOUT-06: a list pane plus its detail pane count together (#996) -- {_md['status']} {_md['measured']}")
_ms = _layout06(748, 748)
check(_ms["status"] == "pass" and "desktop-light-home" not in _ms["measured"] and "not applicable" in _ms["reason"],
      f"LAYOUT-06: a no-detail tab holding the 772px measure (less its gutters) is exempt (#996) -- {_ms['reason']}")
_nw = _layout06(480, 480)
check(_nw["status"] == "fail", "LAYOUT-06: a phone-width column at 1440 still fails")
check(_layout06(700, None)["status"] == "fail", "LAYOUT-06: metrics without content_span fall back to content_w")

# ---- #1113: width follows the shape of the view --------------------------------
_st = _layout06(1400, 1400)
check(_st["status"] == "fail" and _st["evidence"][0]["items"][0].get("arm") == "stretched",
      f"LAYOUT-06: an undeclared one-dimensional view stretched past the measure fails -- {_st['status']} {_st['reason']}")
check(_layout06(1400, 1400, wide_views=["home"])["status"] == "pass",
      "LAYOUT-06: a view declared in wide_views may span the window")
check(_layout06(1400, 1400, wide_views=["other"])["status"] == "fail",
      "LAYOUT-06: a declaration for another view exempts nothing")
_dn = _layout06(748, 748, wide_views=["home"])
check(_dn["status"] == "fail" and "desktop-light-home" in _dn["measured"],
      f"LAYOUT-06: a declared wide view still has to fill the window, so a lone measure column fails -- {_dn['status']}")
check(_layout06(514, 1360)["status"] == "pass", "LAYOUT-06: an undeclared list + detail span is not a stretched column")

# ---- #1361: a master-detail docked inside the tab's pane ------------------------
check(_layout06(1400, 1400, pane_split=True)["status"] == "pass",
      "LAYOUT-06: an undeclared in-pane master-detail is not a stretched column")
check(_layout06(1400, 1400, pane_split=True, wide_views=["home"])["status"] == "pass",
      "LAYOUT-06: a declared in-pane master-detail still passes")
check(_layout06(1400, 1400, pane_split=False)["status"] == "fail",
      "LAYOUT-06: an undeclared single stretched column (no split) still fails")
check(_layout06(1400, 1400, pane_split="yes")["status"] == "fail",
      "LAYOUT-06: only a boolean true counts as a split")
_ps_narrow = _layout06(800, 800, pane_split=True)
check(_ps_narrow["status"] == "fail" and not _ps_narrow["evidence"][0]["items"][0].get("arm"),
      f"LAYOUT-06: an undeclared in-pane split narrower than the share floor still fails on share -- {_ps_narrow['status']}")
check(_layout06(748, 748, wide_views=["home"], pane_split=True)["status"] == "fail",
      "LAYOUT-06: a declared view with a split still has to fill the window")
_bad = _layout06(1400, 1400, wide_views=["home"], wide_views_error="unparseable .fleet.toml: boom")
check(_bad["status"] == "unmeasured" and "wide_views" in _bad["reason"],
      f"LAYOUT-06: a malformed declaration is unmeasured, never a pass or an exemption -- {_bad['status']} {_bad['reason']}")
_vals = report_mod.finding_values(_st, {"params": {"measure": 772}})
check(_vals.get("arm") == "stretched" and _vals.get("content_w") == 1400 and _vals.get("measure") == 772,
      f"LAYOUT-06: the stretched arm reaches the report values -- {_vals}")
_mk, _mk_err = mockups_mod.render_mockup(_st, _vals)
check(_mk_err is None and _mk and "held to the 772px measure" in _mk["proposed"],
      f"LAYOUT-06: the wide-desktop mock-up proposes the measure for a stretched view -- {_mk_err}")
check("1400px" in report_mod.finding_sentence(_st, {"params": {"measure": 772}}),
      "LAYOUT-06: the fix sentence names the column width")

# `[design] wide_views` loads through one shared reader (#1113)
import wide_views as wv  # noqa: E402

_wv_dir = Path(tempfile.mkdtemp(prefix="wv-"))
check(wv.load_wide_views(None) == ([], None) and wv.load_wide_views(_wv_dir) == ([], None), "wide_views: no root / no file is the normal, empty case")
(_wv_dir / ".fleet.toml").write_text('layer = "working-web"\n[design]\nwide_views = ["board", " table "]\n', encoding="utf-8")
check(wv.load_wide_views(_wv_dir) == (["board", "table"], None), "wide_views: a declared list loads, ids stripped")
(_wv_dir / ".fleet.toml").write_text('[design]\nwide_views = "board"\n', encoding="utf-8")
check(wv.load_wide_views(_wv_dir)[0] == [] and "list of non-empty" in (wv.load_wide_views(_wv_dir)[1] or ""), "wide_views: a non-list is an error, not a guess")
(_wv_dir / ".fleet.toml").write_text('[design]\nwide_views = [\n', encoding="utf-8")
check("unparseable" in (wv.load_wide_views(_wv_dir)[1] or ""), "wide_views: an unparseable file is an error, not an empty list")
(_wv_dir / ".fleet.toml").write_text('[design]\nwide_views = ["board"]\n', encoding="utf-8")
_tgt = plan.resolve_target(str(_wv_dir), url_override="https://127.0.0.1:1")
check(_tgt.wide_views == ["board"] and _tgt.wide_views_error is None, "plan.resolve_target carries the declaration")

# `[design] no_chat_surface` is validated like the other [design] keys (#1443)
_nc_dir = Path(tempfile.mkdtemp(prefix="nc-"))
check(plan.load_no_chat_surface(None) == (False, None) and plan.load_no_chat_surface(_nc_dir) == (False, None), "no_chat_surface: no root / no file is the normal, undeclared case")
(_nc_dir / ".fleet.toml").write_text('layer = "working-web"\n[design]\nwide_views = ["board"]\n', encoding="utf-8")
check(plan.load_no_chat_surface(_nc_dir) == (False, None), "no_chat_surface: a [design] table without the key is undeclared")
(_nc_dir / ".fleet.toml").write_text('[design]\nno_chat_surface = true\n', encoding="utf-8")
check(plan.load_no_chat_surface(_nc_dir) == (True, None), "no_chat_surface: true declares it")
for _bad_toml, _what in (('[design]\nno_chat_surface = "yes"\n', "a string"), ('[design]\nno_chat_surface = 1\n', "an integer"), ('[design]\nno_chat_surface = [\n', "an unparseable file")):
    (_nc_dir / ".fleet.toml").write_text(_bad_toml, encoding="utf-8")
    _nc = plan.load_no_chat_surface(_nc_dir)
    check(_nc[0] is False and _nc[1] and ("must be true or false" in _nc[1] or "unparseable" in _nc[1]), f"no_chat_surface: {_what} is an error, never an opt-out -- {_nc}")
(_nc_dir / ".fleet.toml").write_text('[design]\nno_chat_surface = true\n', encoding="utf-8")
_nc_tgt = plan.resolve_target(str(_nc_dir), url_override="https://127.0.0.1:1")
check(_nc_tgt.no_chat_surface is True and _nc_tgt.no_chat_surface_error is None, "plan.resolve_target carries the no_chat_surface declaration")

# ---- nav cap + page header (#966) ----

for _theme, _s in src_specs.items():
    check(_s.get("components.nav-bar.maxTabs") == "5", f"{_theme}: primary nav is capped at five tabs")
    check(_s.get("components.page-header.minHeight") == _s.get("rows.md")
          and _s.get("components.page-header.actionSize") == _s.get("components.hit-target.min")
          and _s.get("components.page-header.trailingActions") == "2"
          and _s.get("components.page-header.statusReadout") == "1",
          f"{_theme}: page-header is a rows.md row with at most two 44px trailing actions plus one status readout")
check(ev.parse_color("#fff", {}) == (255.0, 255.0, 255.0, 1.0), "short hex")
check(ev.parse_color("color-mix(in srgb, var(--accent) 16%, transparent)", {"colors.accent": "#0969da"}) == (9.0, 105.0, 218.0, 0.16),
      "color-mix derivative -> accent at 16% alpha")
check(ev.parse_color("transparent", {})[3] == 0.0 and ev.parse_color("oklch(0.5 0.1 200)", {}) is None, "transparent / unknown")
check(round(contrast((255, 255, 255, 1), (0, 0, 0, 1)), 1) == 21.0, "WCAG 21:1")

# ---- NAV-03: Settings is never a primary tab (#1200) ----
def _nav03(mutate) -> dict:
    d = _doc("compliant")
    for s in d["screens"]:
        mutate(s["metrics"])
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "NAV-03")


def _settings_tab(m):
    m["nav"]["settings_tab_count"] = 1
    m["nav"]["settings_tabs"] = [{"sel": "button#tabSettings", "label": "Settings"}]


_n3 = _nav03(_settings_tab)
check(_n3["status"] == "fail" and _n3["evidence"][0]["items"] == [{"sel": "button#tabSettings", "label": "Settings"}],
      f"NAV-03: a Settings tab fails and names the tab -- {_n3['status']}")
check(_nav03(lambda m: None)["status"] == "pass", "NAV-03: no Settings tab (the header carries the gear) passes")


def _nav_errored(m):
    m["nav"] = {"error": "boom"}


check(_nav03(_nav_errored)["status"] == "unmeasured", "NAV-03: an errored nav section is unmeasured, never a pass")


def _nav_pre_1200(m):
    for k in ("settings_tab_count", "settings_tabs"):
        m["nav"].pop(k)


check(_nav03(_nav_pre_1200)["status"] == "unmeasured", "NAV-03: a run from before the metric existed is unmeasured, never a pass")

# ---- NAV-04: the home tab's header title is the tab's name, never the app's (#1360) ----
def _nav04(home) -> dict:
    """NAV-04 on every tab screen of the compliant doc, each carrying `home` (a dict, or None to drop the key)."""
    d = _doc("compliant")
    for s in d["screens"]:
        if home is None:
            s["metrics"]["nav"].pop("home_title", None)
        else:
            s["metrics"]["nav"]["home_title"] = dict(home)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "NAV-04")


_HOME = {"is_home": True, "manifest": "ok", "app_names": ["Spot Keeper", "Spots"], "tab": "Today", "title": "Today"}
_n4_fail = _nav04({**_HOME, "title": "Spot Keeper"})
check(_n4_fail["status"] == "fail" and _n4_fail["severity"] == "P3"
      and _n4_fail["evidence"][0]["items"] == [{"title": "Spot Keeper", "tab": "Today", "app_names": ["Spot Keeper", "Spots"]}],
      f"NAV-04: a home tab titled with the manifest name fails and names the title -- {_n4_fail['status']} {_n4_fail['evidence']}")
check(_nav04({**_HOME, "title": "  spots "})["status"] == "fail",
      "NAV-04: the short_name counts as the app's name, case and spacing aside")
check(_nav04(_HOME)["status"] == "pass", "NAV-04: a home tab titled with the tab's own name passes")
_n4_na = _nav04({**_HOME, "is_home": False, "title": "Spot Keeper"})
check(_n4_na["status"] == "pass" and "not applicable" in _n4_na["reason"],
      f"NAV-04: a tab other than the first is not judged (vacuous pass, said so) -- {_n4_na['reason']}")
check("not applicable" in _nav04({**_HOME, "tab": "Spot Keeper", "title": "Spot Keeper"})["reason"],
      "NAV-04: a tab genuinely called the app's name is exempt")
for _why, _home in (("a missing manifest", {**_HOME, "manifest": "absent", "app_names": []}),
                    ("an unreadable manifest", {**_HOME, "manifest": "unreadable", "app_names": []}),
                    ("a nameless manifest", {**_HOME, "manifest": "unnamed", "app_names": []}),
                    ("a header with no .home-title", {**_HOME, "title": None})):
    _n4 = _nav04(_home)
    check(_n4["status"] == "unmeasured", f"NAV-04: {_why} leaves the app's name unestablished -- unmeasured, never a pass -- {_n4['status']}")
check(_nav04(None)["status"] == "unmeasured", "NAV-04: a run from before the metric existed is unmeasured, never a pass")

# ---- NAV-05: a tab's page-header context line is not one fixed sentence across its captured states (#1395) ----
def _nav05(readings, *, keep=None) -> dict:
    """NAV-05 on the compliant doc's tab screens (both the `home` tab), each carrying the matching reading from `readings`
    (a dict, or None to drop the key). `keep` limits the doc to its first N screens."""
    d = _doc("compliant")
    if keep is not None:
        d["screens"] = d["screens"][:keep]
    for s, hc in zip(d["screens"], readings):
        if hc is None:
            s["metrics"]["nav"].pop("header_context", None)
        else:
            s["metrics"]["nav"]["header_context"] = dict(hc)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "NAV-05")


def _hc(text, header=True, static=True):
    return {"header": header, "tab": "Today", "text": text, "static": static}


_n5_fail = _nav05([_hc("Manage your jobs."), _hc("manage  your jobs.")])
check(_n5_fail["status"] == "fail" and _n5_fail["severity"] == "P3"
      and _n5_fail["evidence"][0]["items"] == [{"tab": "Today", "text": "Manage your jobs.", "states": 2}],
      f"NAV-05: one fixed sentence in every captured state fails and names the line -- {_n5_fail['status']} {_n5_fail['evidence']}")
check(_nav05([_hc("3 running"), _hc("2 running")])["status"] == "pass", "NAV-05: a count that changes between two states passes")
check(_nav05([_hc("7 jobs"), _hc("7 jobs")])["status"] == "pass", "NAV-05: a fixed line that carries a count is live, so it passes")
check(_nav05([_hc("Build deadbee"), _hc("Build deadbee")])["status"] == "pass", "NAV-05: a build line passes")
check(_nav05([_hc("Last run 06:00"), _hc("Last run 06:00")])["status"] == "pass", "NAV-05: a time passes")
_n5_script = _nav05([_hc("English", static=False), _hc("English", static=False)])
check(_n5_script["status"] == "unmeasured" and "written by script" in _n5_script["reason"],
      f"NAV-05: a constant line a script wrote could be derived, so it is not measured, never a pass or a fail -- {_n5_script['status']} {_n5_script['reason']}")
check(_nav05([_hc("English", static=None), _hc("English", static=None)])["status"] == "unmeasured",
      "NAV-05: a constant line whose page source could not be read is not measured")
_n5_one = _nav05([_hc("Manage your jobs.")], keep=1)
check(_n5_one["status"] == "unmeasured" and "only 1 captured state" in _n5_one["reason"],
      f"NAV-05: a tab with one captured state is not measured, never a pass -- {_n5_one['status']} {_n5_one['reason']}")
_n5_blank = _nav05([_hc("Manage your jobs."), _hc("")])
check(_n5_blank["status"] == "unmeasured", f"NAV-05: a state with no context line leaves the tab unmeasured -- {_n5_blank['status']} {_n5_blank['reason']}")
_n5_nohead = _nav05([_hc(None, header=False), _hc(None, header=False)])
check(_n5_nohead["status"] == "unmeasured" and "no page header" in _n5_nohead["reason"],
      f"NAV-05: a tab with no page header is not measured -- {_n5_nohead['status']} {_n5_nohead['reason']}")
check(_nav05([None, None])["status"] == "unmeasured", "NAV-05: a run from before the metric existed is unmeasured, never a pass")
_n5_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "NAV-05"), None)
check("Manage your jobs." in _n5_sentence and "{" not in _n5_sentence, f"NAV-05: the fix sentence names the line and fills every placeholder -- {_n5_sentence}")

# ---- COMP-08: a destructive control in a dialog or menu is unfilled and not the largest there (#1364) ----
def _comp08(overlay) -> dict:
    """COMP-08 on every screen of the compliant doc, each carrying `overlay` (a dict, or None to drop the section)."""
    d = _doc("compliant")
    for s in d["screens"]:
        if overlay is None:
            s["metrics"].pop("overlay", None)
        else:
            s["metrics"]["overlay"] = dict(overlay)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-08")


_BAD_DEL = {"sel": "button.danger", "label": "Delete", "surface": "dialog#take", "filled": True, "largest": False,
            "w": 120, "h": 44, "why": "filled"}
_c8_fail = _comp08({"surfaces": 1, "destructive": [_BAD_DEL], "destructive_bad": [_BAD_DEL], "destructive_bad_count": 1})
check(_c8_fail["status"] == "fail" and _c8_fail["severity"] == "P2" and _c8_fail["category"] == "components"
      and _c8_fail["evidence"][0]["items"] == [_BAD_DEL],
      f"COMP-08: a filled destructive control in a dialog fails and names it -- {_c8_fail['status']} {_c8_fail['evidence']}")
_c8_doc = _doc("compliant")
for _s in _c8_doc["screens"]:
    _s["metrics"]["overlay"] = {"surfaces": 1, "destructive": [_BAD_DEL], "destructive_bad": [_BAD_DEL], "destructive_bad_count": 1}
_c8_out = ev.evaluate(_c8_doc, rubric, _specs("compliant"))
_c8_sentence = report_mod.finding_sentence(next(r for r in _c8_out["rules"] if r["id"] == "COMP-08"), _c8_out)
check("button.danger" in _c8_sentence and "{" not in _c8_sentence, f"COMP-08: the fix sentence names the control and fills every placeholder -- {_c8_sentence}")
check(_comp08({"surfaces": 1, "destructive": [], "destructive_bad": [], "destructive_bad_count": 0})["status"] == "pass",
      "COMP-08: a menu or dialog whose destructive control is quiet passes")
check(_comp08({"surfaces": 0, "destructive": [], "destructive_bad": [], "destructive_bad_count": 0})["status"] == "pass",
      "COMP-08: a screen with no dialog or menu open has nothing to fail")
check(_comp08(None)["status"] == "unmeasured", "COMP-08: a run from before the metric existed is unmeasured, never a pass")
check(_comp08({"error": "boom"})["status"] == "unmeasured", "COMP-08: a failed overlay section is unmeasured, never a pass")

# ---- COMP-16: a dialog's commit is not quieter than another footer control (#1339) ----
def _comp16(dialogs) -> dict:
    """COMP-16 on every screen of the compliant doc, each carrying `dialogs` (a dict, or None to drop the section)."""
    d = _doc("compliant")
    for s in d["screens"]:
        if dialogs is None:
            s["metrics"].pop("dialogs", None)
        else:
            s["metrics"]["dialogs"] = dict(dialogs)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-16")


_QUIET = {"dialog": "dialog#take", "commit": "button.save", "label": "Save", "filled": False, "w": 120, "h": 44,
          "rival": "button.danger", "rival_label": "Delete", "why": "filled"}
_c16_fail = _comp16({"count": 1, "with_commit": 1, "quiet_commit": [_QUIET], "quiet_commit_count": 1})
check(_c16_fail["status"] == "fail" and _c16_fail["severity"] == "P2" and _c16_fail["category"] == "components"
      and _c16_fail["evidence"][0]["items"] == [_QUIET],
      f"COMP-16: a dialog whose Save is quieter than another footer control fails and names it -- {_c16_fail['status']} {_c16_fail['evidence']}")
_c16_doc = _doc("compliant")
for _s in _c16_doc["screens"]:
    _s["metrics"]["dialogs"] = {"count": 1, "with_commit": 1, "quiet_commit": [_QUIET], "quiet_commit_count": 1}
_c16_out = ev.evaluate(_c16_doc, rubric, _specs("compliant"))
_c16_sentence = report_mod.finding_sentence(next(r for r in _c16_out["rules"] if r["id"] == "COMP-16"), _c16_out)
check("button.save" in _c16_sentence and "{" not in _c16_sentence, f"COMP-16: the fix sentence names the commit and fills every placeholder -- {_c16_sentence}")
check(_comp16({"count": 1, "with_commit": 1, "quiet_commit": [], "quiet_commit_count": 0})["status"] == "pass",
      "COMP-16: a dialog whose commit is the most prominent footer control passes")
check(_comp16({"count": 0, "with_commit": 0, "quiet_commit": [], "quiet_commit_count": 0})["status"] == "pass",
      "COMP-16: a screen with no dialog open has nothing to fail")
check(_comp16(None)["status"] == "unmeasured", "COMP-16: a run from before the metric existed is unmeasured, never a pass")
check(_comp16({"error": "boom"})["status"] == "unmeasured", "COMP-16: a failed dialogs section is unmeasured, never a pass")

# ---- COLOR-04: a switch is on in the accent, never another colour (#1200) ----
def _col04(controls_by_screen) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], controls_by_screen):
        s["metrics"]["controls"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COLOR-04")


_ACCENT = "#0550ae"   # spec_compliant.md's accent
_on = lambda track: {"switch_count": 1, "switch_on_count": 1, "switches_on": [{"sel": "button.toggle", "track": track}]}
_off = {"switch_count": 1, "switch_on_count": 0, "switches_on": []}
_c4_green = _col04([_on("#1a7f37"), {}, {}])
check(_c4_green["status"] == "fail" and _c4_green["evidence"][0]["items"][0]["track"] == "#1a7f37",
      f"COLOR-04: a green on-state fails and names the colour -- {_c4_green['status']}")
check(_col04([_on(_ACCENT), _on(_ACCENT), _on(_ACCENT)])["status"] == "pass", "COLOR-04: an on-state in the accent passes")
check(_col04([_on("#0550af"), {}, {}])["status"] == "pass", "COLOR-04: a one-step rounding off the accent still passes")
check(_col04([{}, {}, {}])["status"] == "pass", "COLOR-04: no switch on any screen is not applicable, a vacuous pass")
check(_col04([_off, _off, _off])["status"] == "unmeasured",
      "COLOR-04: switches that are all off prove nothing about the on-state -- unmeasured, never a pass")
check(_col04([_on(_ACCENT), _off, _off])["status"] == "pass",
      "COLOR-04: an on-state seen on one screen serves the app; an off-only screen is then not applicable")
check(_col04([_on(None), {}, {}])["status"] == "unmeasured", "COLOR-04: a track colour that cannot be read is unmeasured")
_c4_old = _doc("compliant")
for _s in _c4_old["screens"]:
    for _k in ("switch_count", "switch_on_count", "switches_on"):
        _s["metrics"]["controls"].pop(_k)
check(next(r for r in ev.evaluate(_c4_old, rubric, _specs("compliant"))["rules"] if r["id"] == "COLOR-04")["status"] == "unmeasured",
      "COLOR-04: a run from before the metric existed is unmeasured, never a pass")

# ---- COLOR-05: a toast is neutral, a success tint fails (#1200) ----
def _col05(feedback_by_screen) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], feedback_by_screen):
        s["metrics"]["feedback"] = patch
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "COLOR-05")


_toast_ok = {"toast_count": 1, "toasts_tinted": [], "toasts_tinted_count": 0}
_toast_green = {"toast_count": 1, "toasts_tinted": [{"sel": "div.toast", "label": "Saved", "via": "border"}], "toasts_tinted_count": 1}
_toast_none = {"toast_count": 0, "toasts_tinted": [], "toasts_tinted_count": 0}
_c5_green = _col05([_toast_green, _toast_none, _toast_none])
check(_c5_green["status"] == "fail" and _c5_green["evidence"][0]["items"][0]["via"] == "border",
      f"COLOR-05: a toast with a green border fails and says where the tint is -- {_c5_green['status']}")
check(_col05([_toast_ok, _toast_none, _toast_none])["status"] == "pass", "COLOR-05: a neutral toast passes")
_c5_none = _col05([_toast_none, _toast_none, _toast_none])
check(_c5_none["status"] == "pass" and "not applicable" in _c5_none["reason"],
      f"COLOR-05: no toast on screen is a vacuous pass that says so, not a measured one -- {_c5_none['reason']}")
_c5_gone = _doc("compliant")
for _s in _c5_gone["screens"]:
    _s["metrics"].pop("feedback")
check(next(r for r in ev.evaluate(_c5_gone, rubric, _specs("compliant"))["rules"] if r["id"] == "COLOR-05")["status"] == "unmeasured",
      "COLOR-05: a run from before the section existed is unmeasured, never a pass")
check(_col05([{"error": "boom"}, _toast_none, _toast_none])["status"] == "unmeasured", "COLOR-05: an errored section is unmeasured")

# ---- COMP-05 / COMP-06: icon buttons draw nothing at rest, reference pills are one shape (#1259) ----
def _comp(rule_id: str, controls_by_screen) -> dict:
    d = _doc("compliant")
    for s, patch in zip(d["screens"], controls_by_screen):
        s["metrics"]["controls"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == rule_id)


_painted = {"icon_button_count": 2, "icon_buttons_painted_count": 1,
            "icon_buttons_painted": [{"sel": "button.home-toggle", "label": "Switch to dark", "via": "fill", "fill": "#f6f8fa"}]}
_c05 = _comp("COMP-05", [_painted, {}, {}])
check(_c05["status"] == "fail" and _c05["evidence"][0]["items"][0]["via"] == "fill",
      f"COMP-05: an icon-only button filled at rest fails and says how it is painted -- {_c05['status']}")
check(_comp("COMP-05", [{}, {}, {}])["status"] == "pass", "COMP-05: unpainted icon buttons pass")
_pill = lambda h, radius, n: {"h": h, "radius": radius, "padding": "2px 8px 2px 8px", "font": "12px", "count": n, "sel": "a.chip", "label": "x"}
_c06 = _comp("COMP-06", [{"reference_pill_count": 3, "reference_pill_variants": 2,
                          "reference_pill_styles": [_pill(22, "pill", 2), _pill(36, "12px", 1)]}, {}, {}])
check(_c06["status"] == "fail" and [i["h"] for i in _c06["evidence"][0]["items"]] == [22, 36],
      f"COMP-06: two reference-pill shapes on one screen fail and list both -- {_c06['status']}")
check(_comp("COMP-06", [{"reference_pill_count": 0, "reference_pill_styles": [], "reference_pill_variants": 0}, {}, {}])["status"] == "pass",
      "COMP-06: a screen with no reference pill passes")
for _rid, _keys in (("COMP-05", ("icon_button_count", "icon_buttons_painted", "icon_buttons_painted_count")),
                    ("COMP-06", ("reference_pill_count", "reference_pill_styles", "reference_pill_variants"))):
    _old = _doc("compliant")
    for _s in _old["screens"]:
        for _k in _keys:
            _s["metrics"]["controls"].pop(_k)
    check(next(r for r in ev.evaluate(_old, rubric, _specs("compliant"))["rules"] if r["id"] == _rid)["status"] == "unmeasured",
          f"{_rid}: a run from before the metric existed is unmeasured, never a pass")

# ---- COMP-09: an icon-only control with no label beside it needs a conventional name (#1366) ----
_c09 = _comp("COMP-09", [{"icon_only_count": 4, "icon_only_unlabelled": [
    {"sel": "button.incognito", "name": "Incognito"}, {"sel": "button.edit", "name": "Edit take"},
    {"sel": "button.theme", "name": "Switch to dark"}, {"sel": "button.nameless", "name": ""}]}, {}, {}])
check(_c09["status"] == "fail" and [i["name"] for i in _c09["evidence"][0]["items"]] == ["Incognito"],
      f"COMP-09: an unlabelled Incognito toggle fails; a pencil, a theme toggle and a nameless control (A11Y-01's) are not counted -- {_c09['status']} {_c09['evidence']}")
check(_comp("COMP-09", [{}, {}, {}])["status"] == "pass", "COMP-09: no unlabelled icon-only control passes")
check(_comp("COMP-09", [{"icon_only_unlabelled": [{"sel": "button.x", "name": "Close dialog"}, {"sel": "button.y", "name": "Copy text"},
                                                    {"sel": "button.z", "name": "Search takes"}, {"sel": "button.l", "name": "Pan left"}, {"sel": "button.t", "name": "Lower the set temperature"}]}, {}, {}])["status"] == "pass",
      "COMP-09: the conventional names (close, copy, search) pass whatever follows them")
_old09 = _doc("compliant")
for _s in _old09["screens"]:
    for _k in ("icon_only_count", "icon_only_unlabelled"):
        _s["metrics"]["controls"].pop(_k)
check(next(r for r in ev.evaluate(_old09, rubric, _specs("compliant"))["rules"] if r["id"] == "COMP-09")["status"] == "unmeasured",
      "COMP-09: a run from before the metric existed is unmeasured, never a pass")
_c09_sentence = report_mod.finding_sentence(next(r for r in ev.evaluate(_doc("violating"), rubric, _specs("violating"))["rules"] if r["id"] == "COMP-09"), None)
check("button.incognito" in _c09_sentence and "{" not in _c09_sentence, f"COMP-09: the fix sentence names the control and fills every placeholder -- {_c09_sentence}")

# ---- COMP-09: a row verb from the action-row glyph map is conventional with its glyph (#1428) ----
_verbs09 = [("Start", "i-play"), ("Live view", "i-video"), ("Wake", "i-power"), ("Move", "i-calendar-days"), ("Complete", "i-circle-check"),
            ("Complete task", "i-circle")]
_c09_map = _comp("COMP-09", [{"icon_only_unlabelled": [{"sel": f"button.{n[:4].lower()}", "name": n, "glyph": g} for n, g in _verbs09]}, {}, {}])
check(_c09_map["status"] == "pass",
      f"COMP-09: Start, Live view, Wake, Move and Complete drawn with their mapped glyphs pass -- {_c09_map['status']} {_c09_map['evidence']}")
_c09_mix = _comp("COMP-09", [{"icon_only_unlabelled": [
    {"sel": "button.start", "name": "Start", "glyph": "i-play"}, {"sel": "button.start2", "name": "Start", "glyph": "i-ghost"},
    {"sel": "button.inline", "name": "Wake"}, {"sel": "button.incog", "name": "Incognito", "glyph": "i-play"},
    {"sel": "button.gal", "name": "Gallery", "glyph": "i-ghost"}]}, {}, {}])
check(_c09_mix["status"] == "fail" and sorted(i["sel"] for i in _c09_mix["evidence"][0]["items"]) == ["button.gal", "button.incog", "button.start2"],
      f"COMP-09: a verb with another glyph and an unknown name with any glyph still fail; an unread glyph leaves a mapped name to speak -- {_c09_mix['status']} {_c09_mix['evidence']}")
# ---- COMP-09: the conventional leftovers join the map: Launch, Attach, About, Open, Dictate (#1431) ----
_verbs09b = [("Launch weekly review", "i-rocket"), ("Attach", "i-paperclip"), ("About these figures", "i-info"),
             ("Open buy link for dorada", "i-external-link"), ("Dictate a product", "i-mic")]
_c09_new = _comp("COMP-09", [{"icon_only_unlabelled": [{"sel": f"button.n{i}", "name": n, "glyph": g} for i, (n, g) in enumerate(_verbs09b)]}, {}, {}])
check(_c09_new["status"] == "pass",
      f"COMP-09: Launch, Attach, About, Open and Dictate drawn with their mapped glyphs pass -- {_c09_new['status']} {_c09_new['evidence']}")
_c09_new_bad = _comp("COMP-09", [{"icon_only_unlabelled": [
    {"sel": "button.launch", "name": "Launch weekly review", "glyph": "i-play"}, {"sel": "button.attach", "name": "Attach", "glyph": "i-link"},
    {"sel": "button.about", "name": "About activity", "glyph": "i-star"}, {"sel": "button.open", "name": "Open", "glyph": "i-circle"},
    {"sel": "button.dict", "name": "Dictate", "glyph": "i-search"}, {"sel": "button.buy", "name": "Buy dorada", "glyph": "i-external-link"},
    {"sel": "button.got", "name": "Got dorada", "glyph": "i-circle"}]}, {}, {}])
check(_c09_new_bad["status"] == "fail" and sorted(i["sel"] for i in _c09_new_bad["evidence"][0]["items"])
      == ["button.about", "button.attach", "button.buy", "button.dict", "button.got", "button.launch", "button.open"],
      f"COMP-09: a new mapped name with any other glyph, and Buy and Got (not conventional), still fail -- {_c09_new_bad['status']} {_c09_new_bad['evidence']}")
_c09_two = _comp("COMP-09", [{"icon_only_unlabelled": [
    {"sel": "button.ok", "name": "Dictate (click to start)", "glyph": "i-mic"}, {"sel": "button.ok2", "name": "Dictate (click to start)", "glyph": "i-play"},
    {"sel": "button.bad", "name": "Dictate (click to start)", "glyph": "i-search"}]}, {}, {}])
check(_c09_two["status"] == "fail" and [i["sel"] for i in _c09_two["evidence"][0]["items"]] == ["button.bad"],
      f"COMP-09: a name carrying two mapped verbs passes with the glyph of either, and fails with neither -- {_c09_two['status']} {_c09_two['evidence']}")
_dm = re.sub(r"\s+", " ", (REPO / "design.md").read_text(encoding="utf-8"))
_dm_map = _dm[_dm.index("One glyph per verb"):_dm.index("Delete and Remove are never")]
_vg09 = next(r for r in rubric.rules if r.id == "COMP-09").params["verb_glyphs"]
check(all(re.search(rf"\b{v.title()}\b", _dm_map) and all(re.search(rf"\b{w}\b", _dm_map) for w in ([g] if isinstance(g, str) else g))
          for v, g in _vg09.items()),
      f"design.md's row-verb glyph map names every verb and glyph of COMP-09's verb_glyphs constant, so the two cannot drift -- {_vg09}")

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

out_c = ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))
statuses_c = {r["id"]: r["status"] for r in out_c["rules"]}
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

# ---- plan: target resolution from projects.toml + .fleet.toml ----------------

tmp = Path(tempfile.mkdtemp(prefix="design-review-plan-"))
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

# ---- capture: liveness probe, interpreter, run dir ---------------------------

check(capture.probe_listening("file:///x.html")["status"] == "listening", "file:// is always listening")
# Windows surfaces the refusal after ~2s of SYN retries (see probe_listening); the default 5s covers it.
check(capture.probe_listening("http://127.0.0.1:1")["status"] == "NOT_LISTENING", "refused port -> NOT_LISTENING")
check(capture.probe_listening("nonsense")["status"] == "BAD_URL", "bad url -> BAD_URL")
_real = socket.create_connection


def _hang(*a, **k):
    raise socket.timeout("timed out")


socket.create_connection = _hang  # type: ignore[assignment]
try:
    probe_t = capture.probe_listening("http://127.0.0.1:8445", timeout=0.1)
finally:
    socket.create_connection = _real  # type: ignore[assignment]
check(probe_t["status"] == "TIMEOUT" and "did not answer" in str(probe_t["detail"]), "connect timeout -> TIMEOUT, distinct wording")

no_venv = plan.Target(name="x", root=tmp / "absent", base_url="http://127.0.0.1:1")
check(capture.resolve_interpreter(no_venv)["status"] == "PLAYWRIGHT_MISSING", "no .venv -> PLAYWRIGHT_MISSING")
stdlib_only = capture.resolve_interpreter(no_venv, sys.executable)
check(stdlib_only["status"] in ("ok", "PLAYWRIGHT_MISSING"), "probe returns a verdict for any interpreter")
if stdlib_only["status"] == "PLAYWRIGHT_MISSING":
    check("import playwright" in str(stdlib_only["detail"]), "PLAYWRIGHT_MISSING names the failed import")
rd = capture.run_dir_for("demo-app")
check(rd.is_dir() and rd.parent == STATE / "design-review" / "demo-app" and rd.name.endswith("Z"), "run dir under the hooks state dir")

down_doc = capture.measure_target(plan.Target(name="down", root=None, base_url="http://127.0.0.1:1"), rubric, light_c, ["desktop"])
check(down_doc["unmeasured"] == {"reason": "NOT_LISTENING", "detail": down_doc["unmeasured"]["detail"]} and down_doc["screens"] == []
      and (Path(down_doc["run_dir"]) / "metrics.json").is_file(), "measure_target on a dead port writes an unmeasured metrics.json")
check({"schema_version", "rubric_version", "target", "commit", "generated_at", "base_url", "run_dir", "devices",
       "params", "unmeasured", "screens"} <= set(down_doc), "metrics.json envelope keys")

# ---- walk.classify_error (no Playwright needed) -------------------------------

sys.path.insert(0, str(REPO / "skills" / "_lib" / "design_review"))
import walk  # noqa: E402

check(walk.classify_error(TimeoutError("Timeout 15000ms exceeded")) == "TIMEOUT", "timeout classified")
check(walk.classify_error(RuntimeError("net::ERR_CONNECTION_REFUSED at https://127.0.0.1:8445")) == "NOT_LISTENING", "refused classified")
check(walk.classify_error(RuntimeError("Element is not visible")) == "RENDER_FAILED", "other -> RENDER_FAILED")

# ---- walk: a full-page capture over the engine's size limit keeps the screen's metrics (#1085) ----

_TOO_TALL = "Page.screenshot: Cannot take screenshot larger than 32767 pixels on any dimension"


class _FakePage:
    """A Playwright page stub: one tab-less root screen; `full` decides how a full-page capture behaves."""

    def __init__(self, full: str) -> None:
        self.full, self.shots = full, []

    def set_default_timeout(self, _ms): pass
    def goto(self, *_a, **_k): pass
    def wait_for_timeout(self, _ms): pass
    def on(self, *_a): pass
    def locator(self, _sel): return type("L", (), {"count": lambda _self: 0})()

    def evaluate(self, js, *_args):
        if js is walk._TABS_JS or js is walk._DIALOG_IDS_JS:
            return []
        if js is walk._OPEN_DETAILS_JS:
            return 1
        if "devicePixelRatio" in js:
            return {"dpr": 3, "width": 390, "height": 11338}
        return {"measured": True} if js == "MEASURE" else None

    def screenshot(self, path, full_page=False, clip=None):
        self.shots.append({"full_page": full_page, "clip": clip})
        if full_page and (self.full == "raise" or (self.full == "clip" and clip is None)):
            raise RuntimeError(_TOO_TALL)


def _walk_one(full: str):
    page = _FakePage(full)
    ctx = type("C", (), {"add_init_script": lambda *_: None, "new_page": lambda _s: page, "close": lambda _s: None})()
    browser = type("B", (), {"new_context": lambda _s, **_k: ctx, "close": lambda _s: None})()
    pw = type("P", (), {"webkit": type("E", (), {"launch": lambda _s: browser})(), "devices": {"iPhone 13": {}}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": False})()
    with tempfile.TemporaryDirectory() as tmp:
        screens = walk.walk_context(pw, "iphone", "light", args, "MEASURE", {}, {}, Path(tmp))
    # The stub has no Settings gear to find (the Settings step is covered by the browser leg below, #1217).
    return [s for s in screens if s["view"] != plan.SETTINGS_GEAR_VIEW], page


for _mode in ("raise", "clip"):
    (_scr,), _pg = _walk_one(_mode)
    check(_scr["status"] == "ok" and {k: v for k, v in _scr["metrics"].items() if k != "glance"} == {"measured": True},
          f"a full-page capture over the 32767px limit ({_mode}) keeps the screen ok with its metrics -- {_scr}")
_scr_raise = _walk_one("raise")[0][0]
check(_scr_raise["screenshot_full"] is None and "skipped" in (_scr_raise.get("note") or ""),
      f"a full-page capture that can't be taken even clipped is skipped with a note -- {_scr_raise}")
_scr_clip, _pg_clip = _walk_one("clip")
_scr_clip = _scr_clip[0]
check(_scr_clip["screenshot_full"] == "iphone-light-root-full.png" and "clipped" in (_scr_clip.get("note") or "")
      and _pg_clip.shots[-1]["clip"] == {"x": 0, "y": 0, "width": 390, "height": 32767 // 3},
      f"a too-tall page is captured clipped to the limit's first device px, with a note -- {_scr_clip} {_pg_clip.shots}")
_scr_ok = _walk_one("ok")[0][0]
check(_scr_ok["status"] == "ok" and _scr_ok["screenshot_full"] == "iphone-light-root-full.png" and _scr_ok.get("note") is None,
      f"a page within the limit is captured whole, without a note -- {_scr_ok}")

# ---- walk: every ok/error/absent arm of walk_context, records + log lines (#1244) ----


class _ArmLoc:
    """A Playwright locator stub: actions on `sel` raise whatever the page's `fail` map names."""

    def __init__(self, page, sel: str) -> None:
        self.page, self.sel = page, sel

    first = property(lambda self: self)
    def count(self): return 1
    def nth(self, i): return _ArmLoc(self.page, f"{self.sel}#{i}")
    def locator(self, sel): return _ArmLoc(self.page, sel) if self.sel == "[role=tablist]" else self
    def wait_for(self, **_k): self.page.act("wait", self.sel)
    def click(self): self.page.act("click", self.sel)

    def evaluate(self, js, *_args):
        return self.sel in self.page.no_go_hits if js is walk._NO_GO_JS else None


class _ArmPage:
    """Three tabs (one fine, one broken, one timing out), two dialogs (one won't open), a scripted gear."""

    def __init__(self, fail: dict, no_go_hits: set, goto_error=None) -> None:
        self.fail, self.no_go_hits, self.goto_error = fail, no_go_hits, goto_error

    def act(self, kind, sel):
        if (kind, sel) in self.fail:
            raise self.fail[(kind, sel)]

    def set_default_timeout(self, _ms): pass
    def wait_for_timeout(self, _ms): pass
    def on(self, *_a): pass
    def screenshot(self, **_k): pass
    def locator(self, sel): return _ArmLoc(self, sel)
    def get_by_role(self, *_a, **_k): return _ArmLoc(self, "gear")

    def goto(self, *_a, **_k):
        if self.goto_error:
            raise self.goto_error

    def evaluate(self, js, *args):
        if js is walk._TABS_JS:
            return [{"index": 0, "id": "home"}, {"index": 1, "id": "broken"}, {"index": 2, "id": "slow"}]
        if js is walk._DIALOG_IDS_JS:
            return ["fine", "stuck"]
        if "showModal" in js and args == ("stuck",):
            raise RuntimeError("dialog stuck would not open")
        if js is walk.measure.RENDERED_THEME_JS:
            return {"attr": "light", "luminance": 0.95}
        return {"m": 1} if js == "MEASURE" else 0


_ARM_STEPS = [
    {"id": "declared", "click": "#danger"},
    {"id": "gone", "click": "#gone"},
    {"id": "inside", "click": "#inside"},
    {"id": "crash", "click": "#crash"},
    {"id": "waiterr", "click": "#waiterr"},
    {"id": "fine", "tab": "home", "open": "#det", "clicks": ["#a", "#b"]},
    {"id": "live-only", "click": "#x", "synthetic": True},
    {"id": "noop"},
]
_ARM_FAIL = {
    ("click", "[role=tab]#1"): RuntimeError("Element is not visible"),
    ("click", "[role=tab]#2"): TimeoutError("Timeout 1000ms exceeded"),
    ("wait", "#gone"): TimeoutError("Timeout 5000ms exceeded"),
    ("click", "#crash"): RuntimeError("boom"),
    ("wait", "#waiterr"): RuntimeError("element detached"),
}
_GEAR_MODES = {
    "ok": ({}, set()),
    "absent": ({("wait", "gear"): TimeoutError("Timeout 1000ms exceeded")}, set()),
    "nogo": ({}, {"gear"}),
    "crash": ({("click", "gear"): RuntimeError("gear click failed")}, set()),
}


class _LogTap(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.getMessage()}")


def _walk_arms(gear: str = "ok", launch_error=None, goto_error=None):
    fail, hits = _GEAR_MODES[gear]
    page = _ArmPage({**_ARM_FAIL, **fail}, hits | {"#inside"}, goto_error)
    ctx = type("C", (), {"add_init_script": lambda *_: None, "new_page": lambda _s: page, "close": lambda _s: None})()
    browser = type("B", (), {"new_context": lambda _s, **_k: ctx, "close": lambda _s: None})()

    def launch(_s):
        if launch_error:
            raise launch_error
        return browser
    pw = type("P", (), {"chromium": type("E", (), {"launch": launch})(), "devices": {}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": False})()
    tap, wlog = _LogTap(), logging.getLogger("design_review.walk")
    level = wlog.level
    wlog.addHandler(tap)
    wlog.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            screens = walk.walk_context(pw, "desktop", "light", args, "MEASURE", {},
                                        {"no_go": ["#danger"], "extra_steps": _ARM_STEPS}, Path(tmp))
    finally:
        wlog.removeHandler(tap)
        wlog.setLevel(level)
    return [(s["id"], s["kind"], s["status"], s["reason"], s["error"], s["metrics"], s["theme_applied"]) for s in screens], tap.lines


_arm_screens, _arm_log = _walk_arms("ok")
check(_arm_screens == [
    ("desktop-light-home", "tab", "ok", None, None, {"m": 1, "glance": {"state": "unavailable", "idle": True, "idle_ms": 400}}, True),
    ("desktop-light-broken", "tab", "error", "TAB_FAILED", "Element is not visible", None, None),
    ("desktop-light-slow", "tab", "error", "TIMEOUT", "Timeout 1000ms exceeded", None, None),
    ("desktop-light-dialog-fine", "dialog", "ok", None, None, {"m": 1}, True),
    ("desktop-light-dialog-stuck", "dialog", "error", "DIALOG_FAILED", "dialog stuck would not open", None, None),
    ("desktop-light-gear-settings", "step", "ok", None, None, {"m": 1}, True),
    ("desktop-light-root-declared", "step", "error", "NO_GO", "#danger is declared no_go", None, None),
    ("desktop-light-root-gone", "step", "absent", "STEP_TARGET_ABSENT", "#gone never appeared within 5000 ms", None, None),
    ("desktop-light-root-inside", "step", "error", "NO_GO", "#inside sits inside a no_go selector", None, None),
    ("desktop-light-root-crash", "step", "error", "RENDER_FAILED", "boom", None, None),
    ("desktop-light-root-waiterr", "step", "error", "RENDER_FAILED", "element detached", None, None),
    ("desktop-light-home-fine", "step", "ok", None, None, {"m": 1}, True),
], f"walk_context records every tab/dialog/step arm with its own status and reason -- {_arm_screens}")
check(_arm_log == [
    "INFO ok desktop-light-home",
    "WARNING FAIL desktop-light-broken: Element is not visible",
    "WARNING FAIL desktop-light-slow: Timeout 1000ms exceeded",
    "INFO ok desktop-light-dialog-fine",
    "WARNING FAIL desktop-light-dialog-stuck: dialog stuck would not open",
    "INFO ok desktop-light-gear-settings",
    "INFO absent desktop-light-root-gone: #gone never appeared within 5000 ms",
    "WARNING NO_GO desktop-light-root-inside: #inside sits inside a no_go selector",
    "WARNING FAIL desktop-light-root-crash: boom",
    "WARNING FAIL desktop-light-root-waiterr: element detached",
    "INFO ok desktop-light-home-fine",
], f"walk_context logs one line per arm, at its own level -- {_arm_log}")
_gear_arms = {}
for _mode in ("absent", "nogo", "crash"):
    _scr, _lines = _walk_arms(_mode)
    _gear_arms[_mode] = ([s for s in _scr if s[0] == "desktop-light-gear-settings"], [l for l in _lines if "gear-settings" in l])
check(_gear_arms == {
    "absent": ([("desktop-light-gear-settings", "step", "absent", "SETTINGS_GEAR_ABSENT", "no visible button named Settings", None, None)],
               ["INFO absent desktop-light-gear-settings: no visible button named Settings"]),
    "nogo": ([("desktop-light-gear-settings", "step", "error", "NO_GO", "the Settings gear sits inside a no_go selector", None, None)],
             ["WARNING NO_GO desktop-light-gear-settings: the Settings gear sits inside a no_go selector"]),
    "crash": ([("desktop-light-gear-settings", "step", "error", "RENDER_FAILED", "gear click failed", None, None)],
              ["WARNING FAIL desktop-light-gear-settings: gear click failed"]),
}, f"the Settings gear's absent / no_go / failed arms -- {_gear_arms}")
check(_walk_arms(launch_error=RuntimeError("no chromium")) == (
    [("desktop-light-root", "tab", "error", "BROWSER_FAILED", "no chromium", None, None)], []),
      "a browser that won't launch is one BROWSER_FAILED root screen, nothing logged")
check(_walk_arms(goto_error=RuntimeError("net::ERR_CONNECTION_REFUSED")) == (
    [("desktop-light-root", "tab", "error", "NOT_LISTENING", "net::ERR_CONNECTION_REFUSED", None, None)], []),
      "a base page that won't load is one classified root screen, nothing logged")

# ---- walk: every browser action of every walk_context path, against a golden trace (#1244) ----
# The records and log lines above can't see the order of gotos, clicks, waits, captures and
# evaluates, so a restructure could reorder what a browser sees and still pass them. Each
# scenario here records every call the walk makes on the playwright/browser/context/page/locator
# fakes, plus the full screen records and log lines, and must equal the golden captured from the
# code before the per-leg split. Regenerate only for an intended behaviour change:
# DESIGN_REVIEW_WRITE_WALK_GOLDEN=1.

WALK_GOLDEN = FIX / "walk_trace.json"
_TRACE_JS = {value: name for name, value in vars(walk).items() if name.endswith("_JS") and isinstance(value, str)}
_TRACE_JS[walk.measure.RENDERED_THEME_JS] = "measure.RENDERED_THEME_JS"
for _gl_name in ("GLANCE_TRACK_JS", "GLANCE_READ_JS"):  # the glance tracker (#1354); a source without it fails the golden, never crashes here
    if hasattr(walk.measure, _gl_name):
        _TRACE_JS[getattr(walk.measure, _gl_name)] = f"measure.{_gl_name}"


class _TraceLoc:
    """A locator stub that logs every call to the page's trace."""

    def __init__(self, page, sel: str) -> None:
        self.page, self.sel = page, sel

    @property
    def first(self):
        self.page.log("first", self.sel)
        return self

    def count(self):
        self.page.log("count", self.sel)
        return 1 if self.page.has_tablist or self.sel != "[role=tablist]" else 0

    def nth(self, i):
        self.page.log("nth", self.sel, i)
        return _TraceLoc(self.page, f"{self.sel}#{i}")

    def locator(self, sel):
        self.page.log("loc.locator", self.sel, sel)
        return _TraceLoc(self.page, sel if self.sel == "[role=tablist]" else self.sel)

    def wait_for(self, **kwargs):
        self.page.log("wait_for", self.sel, kwargs)
        self.page.act("wait", self.sel)

    def click(self):
        self.page.log("click", self.sel)
        self.page.act("click", self.sel)

    def evaluate(self, js, *args):
        self.page.log("loc.evaluate", self.sel, _TRACE_JS.get(js, js), list(args))
        return self.sel in self.page.no_go_hits if js is walk._NO_GO_JS else None


class _TracePage:
    """A page stub that logs every call; the scenario decides what the page holds and what fails."""

    def __init__(self, trace: list, cfg: dict) -> None:
        self.trace, self.cfg = trace, cfg
        self.fail = cfg.get("fail", {})
        self.no_go_hits = cfg.get("no_go_hits", set())
        self.has_tablist = cfg.get("has_tablist", True)

    def log(self, *entry):
        self.trace.append(list(entry))

    def act(self, kind, sel):
        if (kind, sel) in self.fail:
            raise self.fail[(kind, sel)]

    def set_default_timeout(self, ms): self.log("set_default_timeout", ms)
    def wait_for_timeout(self, ms): self.log("wait_for_timeout", ms)
    def on(self, event, _handler): self.log("on", event)

    def locator(self, sel):
        self.log("locator", sel)
        return _TraceLoc(self, sel)

    def get_by_role(self, role, name=None):
        self.log("get_by_role", role, getattr(name, "pattern", name))
        return _TraceLoc(self, "gear")

    def goto(self, url, **kwargs):
        self.log("goto", url, kwargs)
        if self.cfg.get("goto_error"):
            raise self.cfg["goto_error"]

    def screenshot(self, path, full_page=False, clip=None):
        self.log("screenshot", Path(path).name, full_page, clip)
        full = self.cfg.get("full", "ok")
        if full_page and (full == "raise" or (full == "clip" and clip is None)):
            raise RuntimeError(_TOO_TALL)

    def evaluate(self, js, *args):
        name = _TRACE_JS.get(js, js)
        self.log("evaluate", name, list(args))
        if name == "_TABS_JS":
            return self.cfg.get("tabs", [])
        if name == "_DIALOG_IDS_JS":
            return self.cfg.get("dialogs", [])
        if name in ("_OPEN_DETAILS_JS", "_OPEN_SCOPE_DETAILS_JS"):
            return self.cfg.get("opened", 0)
        if name == "_PAGE_SIZE_JS":
            return {"dpr": 3, "width": 390, "height": 40000}
        if name == "measure.GLANCE_READ_JS":
            return self.cfg.get("glance")
        if name == "measure.RENDERED_THEME_JS":
            if self.cfg.get("theme_read") == "raise":
                raise RuntimeError("page closed")
            return self.cfg.get("rendered", {"attr": "light", "luminance": 0.95})
        if "showModal" in js and args and args[0] in self.cfg.get("stuck_dialogs", ()):
            raise RuntimeError(f"dialog {args[0]} would not open")
        return {"m": 1} if js == "MEASURE" else None


def _walk_traced(cfg: dict) -> dict:
    trace: list = []
    page = _TracePage(trace, cfg)

    class _Ctx:
        def add_init_script(self, script): trace.append(["ctx.add_init_script", _TRACE_JS.get(script, script)])
        def close(self): trace.append(["ctx.close"])

        def new_page(self):
            trace.append(["ctx.new_page"])
            return page

        def new_cdp_session(self, _page):
            trace.append(["ctx.new_cdp_session"])
            return type("Cdp", (), {"send": lambda _s, method, params: trace.append(["cdp.send", method, params])})()

    class _Browser:
        def close(self): trace.append(["browser.close"])

        def new_context(self, **kwargs):
            trace.append(["browser.new_context", kwargs])
            return _Ctx()

    class _Engine:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        def launch(self):
            trace.append(["launch", self.engine])
            if cfg.get("launch_error"):
                raise cfg["launch_error"]
            return _Browser()

    pw = type("P", (), {"webkit": _Engine("webkit"), "chromium": _Engine("chromium"),
                        "devices": {"iPhone 13": {"viewport": {"width": 390, "height": 664}, "has_touch": True},
                                    "Pixel 7": {"viewport": {"width": 412, "height": 839}, "has_touch": True}}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": cfg.get("synthetic", False)})()
    tap, wlog = _LogTap(), logging.getLogger("design_review.walk")
    level = wlog.level
    wlog.addHandler(tap)
    wlog.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            screens = walk.walk_context(pw, cfg["device"], cfg["theme"], args, "MEASURE", {"p": 1},
                                        cfg.get("review", {}), Path(tmp))
    finally:
        wlog.removeHandler(tap)
        wlog.setLevel(level)
    # A JSON round trip, so the in-memory result compares equal to the golden file's.
    return json.loads(json.dumps({"screens": screens, "log": tap.lines, "trace": trace}))


_ARMS_PAGE = {"tabs": [{"index": 0, "id": "home"}, {"index": 1, "id": "broken"}, {"index": 2, "id": "slow"}],
              "dialogs": ["fine", "stuck"], "stuck_dialogs": ("stuck",), "no_go_hits": {"#inside"}, "fail": _ARM_FAIL,
              "review": {"no_go": ["#danger"], "extra_steps": _ARM_STEPS}}
_TRACE_SCENARIOS = {
    "arms": {**_ARMS_PAGE, "device": "desktop", "theme": "light"},
    "gear-absent": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                    "fail": {**_ARM_FAIL, ("wait", "gear"): TimeoutError("Timeout 1000ms exceeded")}},
    "gear-nogo": {**_ARMS_PAGE, "device": "desktop", "theme": "light", "no_go_hits": {"#inside", "gear"}},
    "gear-crash": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                   "fail": {**_ARM_FAIL, ("click", "gear"): RuntimeError("gear click failed")}},
    "gear-wait-error": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                        "fail": {**_ARM_FAIL, ("wait", "gear"): RuntimeError("gear detached")}},
    "launch-error": {"device": "desktop", "theme": "dark", "launch_error": RuntimeError("no chromium")},
    "goto-error": {"device": "iphone", "theme": "light", "goto_error": RuntimeError("net::ERR_CONNECTION_REFUSED")},
    # Touch restore after every full-page capture, a stored theme, a custom tab selector, an app that
    # re-applied its own theme, clipped full captures, a step whose tab is missing, one with only `open`.
    "android-touch": {"device": "android", "theme": "dark", "opened": 2, "full": "clip",
                      "rendered": {"attr": "light", "luminance": 0.95},
                      "tabs": [{"index": 0, "id": "board"}, {"index": 1, "id": None}], "dialogs": ["edit"],
                      "review": {"theme_storage_key": "app-theme", "tab_selector": ".tab",
                                 "extra_steps": [{"id": "menu", "tab": "missing", "open": "#det"},
                                                 {"id": "pick", "tab": "board", "clicks": ["#a", ""]},
                                                 "not-a-step", {"click": "#no-id"}]}},
    # No tablist and no tabs: the root screen; full captures that fail even clipped; an unreadable theme.
    "iphone-root": {"device": "iphone", "theme": "dark", "has_tablist": False, "opened": 1, "full": "raise",
                    "theme_read": "raise"},
    # A synthetic run swaps in its own no_go and runs the synthetic-only steps.
    "synthetic": {"device": "desktop", "theme": "dark", "synthetic": True,
                  "tabs": [{"index": 0, "id": "home"}],
                  "review": {"no_go": ["#live"], "synthetic": {"no_go": ["#synthdanger"]},
                             "extra_steps": [{"id": "s1", "click": "#synthdanger", "synthetic": True},
                                             {"id": "s2", "click": "#live", "synthetic": True},
                                             {"id": "s3", "click": "#ok"}]}},
    # A slow target declares its readiness (#1385): a tab `ready` selector that attaches, one that never does
    # (the tab is still captured, `met: false`), a tab with none; per-step `wait_ms` honoured, capped, or
    # ignored when it is not a positive integer. The `gone` step stays absent after its longer bound.
    "ready-and-wait": {"device": "desktop", "theme": "light",
                       "tabs": [{"index": 0, "id": "machines"}, {"index": 1, "id": "slow"}, {"index": 2, "id": "plain"}],
                       "fail": {("wait", "#gone"): TimeoutError("Timeout 12000ms exceeded"),
                                ("wait", ".slow-ready"): TimeoutError("Timeout 15000ms exceeded")},
                       "review": {"tab_ready": {"machines": ".row", "slow": ".slow-ready"},
                                  "extra_steps": [{"id": "long", "tab": "machines", "click": "#sheet", "wait_ms": 12000},
                                                  {"id": "gone", "click": "#gone", "wait_ms": 12000},
                                                  {"id": "huge", "click": "#h", "wait_ms": 9999999},
                                                  {"id": "neg", "click": "#n", "wait_ms": -5},
                                                  {"id": "flag", "click": "#f", "wait_ms": True},
                                                  {"id": "none", "click": "#z"}]}},
    # A live run skips synthetic steps and keeps the live no_go even when a synthetic one is declared.
    "live-with-synthetic-block": {"device": "desktop", "theme": "light",
                                  "review": {"no_go": ["#live"], "synthetic": {"no_go": ["#synthdanger"]},
                                             "extra_steps": [{"id": "s1", "click": "#synthdanger", "synthetic": True},
                                                             {"id": "s3", "click": "#live"}]}},
}

_walk_traces = {name: _walk_traced(cfg) for name, cfg in _TRACE_SCENARIOS.items()}
if os.environ.get("DESIGN_REVIEW_WRITE_WALK_GOLDEN") == "1":
    WALK_GOLDEN.write_text(json.dumps(_walk_traces, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
_walk_golden = json.loads(WALK_GOLDEN.read_text(encoding="utf-8"))
check(set(_walk_traces) == set(_walk_golden), f"walk trace scenarios match the golden's -- {sorted(_walk_traces)}")
for _name in _TRACE_SCENARIOS:
    for _part in ("screens", "log", "trace"):
        _got, _want = _walk_traces[_name][_part], _walk_golden.get(_name, {}).get(_part)
        _at = next((i for i, (g, w) in enumerate(zip(_got, _want or [])) if g != w), min(len(_got), len(_want or [])))
        check(_got == _want, f"walk_context {_name}: {_part} matches the golden (first difference at #{_at}: "
                             f"got {_got[_at:_at + 1]} want {(_want or [])[_at:_at + 1]})")

# The readiness declarations (#1385), asserted directly so the golden is not the only witness.
_rw = _walk_traces["ready-and-wait"]
_rw_by = {sc["view"]: sc for sc in _rw["screens"]}
_rw_waits = [e[2] for e in _rw["trace"] if e[0] == "wait_for" and e[1] in ("#sheet", "#gone", "#h", "#n", "#f", "#z")]
_rw_clicks = [e[1] for e in _rw["trace"] if e[0] == "click" and not e[1].startswith("[role=tab]")]
check(_rw_by["machines"]["ready"] == {"selector": ".row", "wait_ms": walk.READY_WAIT_MS, "met": True}
      and _rw_by["slow"]["ready"] == {"selector": ".slow-ready", "wait_ms": walk.READY_WAIT_MS, "met": False}
      and _rw_by["slow"]["status"] == "ok" and "ready" not in _rw_by["plain"],
      f"a tab `ready` selector is recorded met or not met (the tab still captured), and absent when undeclared -- {[(v, sc.get('ready')) for v, sc in _rw_by.items()]}")
check([w["timeout"] for w in _rw_waits] == [12000, 12000, walk.WAIT_MS_MAX, walk.STEP_TARGET_WAIT_MS, walk.STEP_TARGET_WAIT_MS, walk.STEP_TARGET_WAIT_MS]
      and _rw_by["machines-long"]["wait_ms"] == 12000 and _rw_by["root-huge"]["wait_ms"] == walk.WAIT_MS_MAX
      and "wait_ms" not in _rw_by["root-none"],
      f"step `wait_ms` replaces the 5 s wait, is capped at WAIT_MS_MAX, is ignored unless a positive integer, and is recorded only when declared -- {_rw_waits}")
check(_rw_by["root-gone"]["status"] == "absent" and _rw_by["root-gone"]["reason"] == "STEP_TARGET_ABSENT"
      and "12000 ms" in _rw_by["root-gone"]["error"] and _rw_clicks.count("#gone") == 0,
      f"a target absent after its longer bound stays absent, and its click is never made -- {_rw_by['root-gone']}")
check(all(_rw_clicks.count(c) == 1 for c in ("#sheet", "#h", "#n", "#f", "#z")),
      f"a longer wait never retries a click -- {_rw_clicks}")
check([walk.bounded_wait_ms(v, 5000) for v in (None, 0, -1, "9", 1.5, False, 100, walk.WAIT_MS_MAX, walk.WAIT_MS_MAX + 1)]
      == [5000, 5000, 5000, 5000, 5000, 5000, 100, walk.WAIT_MS_MAX, walk.WAIT_MS_MAX],
      "bounded_wait_ms: defaults unless a positive int, caps at WAIT_MS_MAX")

# ---- walk: the glance card's late reads wait for a quiet network, bounded (#1354) ----------------------------------


class _IdlePage:
    """Records the waits and the request listeners; never a real browser."""

    def __init__(self) -> None:
        self.handlers, self.waited = {}, 0

    def on(self, event, handler): self.handlers[event] = handler
    def wait_for_timeout(self, ms): self.waited += ms


_idle_req = lambda url, kind="fetch": type("Req", (), {"url": url, "resource_type": kind})()
check(hasattr(walk, "_Inflight"), "walk counts the requests in flight for the glance read (#1354)")
if hasattr(walk, "_Inflight"):
    _ip = _IdlePage()
    _inflight = walk._Inflight(_ip)
    _ip_read = _idle_req("https://127.0.0.1:9/api/late?token=x")
    _ip.handlers["request"](_idle_req("https://127.0.0.1:9/api/stream", "eventsource"))
    _ip.handlers["request"](_idle_req("wss://127.0.0.1:9/ws", "websocket"))
    _ip.handlers["request"](_ip_read)
    check(_inflight.wait_quiet(_ip) == {"idle": False, "idle_ms": walk.IDLE_WAIT_MS, "pending": ["/api/late"]} and _ip.waited == walk.IDLE_WAIT_MS,
          f"a read still in flight holds the glance read for IDLE_WAIT_MS and no longer, naming its path without host or query -- waited {_ip.waited}")
    _ip.handlers["requestfinished"](_ip_read)
    _ip.waited = 0
    _ip_quiet = _inflight.wait_quiet(_ip)
    check(_ip_quiet["idle"] is True and _ip_quiet["idle_ms"] < walk.IDLE_QUIET_MS and _ip.waited < walk.IDLE_QUIET_MS,
          f"an open EventSource or WebSocket is not a read in flight: quiet after IDLE_QUIET_MS -- {_ip_quiet}")
    _ip.handlers["request"](_ip_read)
    _ip.handlers["requestfailed"](_ip_read)
    check(_inflight.wait_quiet(_ip)["idle"] is True, "a failed request is no longer in flight")

_h.report_and_exit("test_design_review")
