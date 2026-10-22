"""Unit tests for design_review's rubric, spec resolution and the layout, type, action-row and navigation rules (fleet-config#971).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_rubric_and_nav_rules.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from _suite import REPO, _h, check  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

import design_review as dr  # noqa: E402
from design_review import capture, evaluate as ev, measure, plan, rubric as rb  # noqa: E402
from design_review import mockups as mockups_mod, report as report_mod  # noqa: E402
from design_lint.colormath import composite, contrast  # noqa: E402
from design_review_support import (  # noqa: E402
    rubric,
    comp as _comp, doc as _doc, light_c, specs as _specs, src_specs,
)

check(dr.evaluate is ev and callable(ev.evaluate), "the package exports the evaluate module; the function is evaluate.evaluate (#972)")


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

_wv_dir = Path(throwaway_dir("wv-"))
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
_nc_dir = Path(throwaway_dir("nc-"))
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

_h.report_and_exit("test_rubric_and_nav_rules")
