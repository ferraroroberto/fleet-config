"""Unit tests for skills/_lib/design_review (fleet-config#971).

Pure-logic legs (rubric load/validate, threshold + param resolution from a
spec, evaluate on fixture metrics — one compliant, one violating every
rule — scoring, determinism, the unmeasured lattice, the liveness probe
classification, target/plan resolution) run with no browser. The browser
leg drives `measure` against the static fixture page
`tests/fixtures/design_review/fixture.html` through whichever sibling
fleet checkout has Playwright in its `.venv` (project-scaffolding, then
app-launcher) and is reported as a skip — never a pass — when none does.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_review.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from acceptance.shared import SKIP_EXIT  # noqa: E402

import design_review as dr  # noqa: E402
from design_review import capture, evaluate as ev, measure, plan, rubric as rb  # noqa: E402
from design_review import mockups as mockups_mod, report as report_mod  # noqa: E402
from design_lint.colormath import composite, contrast  # noqa: E402

_h = CheckHarness()
check = _h.check

check(dr.evaluate is ev and callable(ev.evaluate), "the package exports the evaluate module; the function is evaluate.evaluate (#972)")

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"

# Every hooks-state write in this file lands in a throwaway dir, never in the live one.
STATE = Path(tempfile.mkdtemp(prefix="design-review-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)


def _specs(name: str) -> dict:
    return rb.load_specs(FIX / f"spec_{name}.md", FIX / f"spec_{name}.md")


def _doc(name: str) -> dict:
    return json.loads((FIX / f"metrics_{name}.json").read_text(encoding="utf-8"))


# ---- rubric: the real file loads and names only metrics that exist ----------

rubric = dr.load_rubric(RUBRIC)
check(rubric.version == "1.17.0", "rubric meta.version stamped")
check(len(rubric.rules) == 31, f"31 seed rules loaded (got {len(rubric.rules)})")
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


def _layout06(content_w, content_span, wide_views=None, wide_views_error=None):
    doc = _doc("compliant")
    lay = doc["screens"][0]["metrics"]["layout"]
    lay["content_w"] = content_w
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

# ---- nav cap + page header (#966) ----

for _theme, _s in src_specs.items():
    check(_s.get("components.nav-bar.maxTabs") == "5", f"{_theme}: primary nav is capped at five tabs")
    check(_s.get("components.page-header.minHeight") == _s.get("rows.md")
          and _s.get("components.page-header.actionSize") == _s.get("components.hit-target.min")
          and _s.get("components.page-header.trailingActions") == "2",
          f"{_theme}: page-header is a rows.md row with at most two 44px trailing actions")
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

# ---- evaluate: compliant fixture passes every rule ---------------------------

out_c = ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))
statuses_c = {r["id"]: r["status"] for r in out_c["rules"]}
check(all(s == "pass" for s in statuses_c.values()), f"compliant: every rule passes ({[k for k, v in statuses_c.items() if v != 'pass']})")
check(all(v["score"] == 100.0 and v["grade"] == "A" and not v["unmeasured"] for v in out_c["categories"].values()),
      "compliant: every category 100/A, measured")
check(out_c["overall"] == {"score": 100.0, "grade": "A", "unmeasured": False}, "compliant: overall A")
check(out_c["schema_version"] == 1 and out_c["rubric_version"] == "1.17.0" and out_c["target"] == "fixture-app"
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
check(cat_v["typography"]["score"] == 100 - (12 + 25 + 12 + 12 + 6) and cat_v["typography"]["grade"] == "F",
      "typography: penalties once per rule (P1+P0+P1+P1+P2)")
check(cat_v["touch"]["score"] == 100 - (25 + 25 + 12) and cat_v["navigation"]["score"] == 100 - (12 + 6 + 12),
      "touch / navigation scores")
check(cat_v["typography"]["failed"] == ["TYPE-01", "TYPE-02", "TYPE-03", "TYPE-04", "TYPE-05"], "failed ids listed in rule order")
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
check(t05["measured"]["desktop-light-home"] == 2.0
      and [i["sel"] for i in t05["evidence"][0]["items"]] == ["span.badge", "span.chip"],
      f"TYPE-05 counts an uppercase badge and chip, not the overline (got {t05['measured']})")

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


def _port_open(url):
    from urllib.parse import urlsplit
    p = urlsplit(url)
    try:
        with socket.create_connection((p.hostname, p.port), timeout=1):
            return True
    except OSError:
        return False


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
    def locator(self, _sel): return type("L", (), {"count": lambda _self: 0})()

    def evaluate(self, js, *_args):
        if js is walk._TABS_JS or js is walk._DIALOG_IDS_JS:
            return []
        if js is walk._OPEN_DETAILS_JS:
            return 1
        if "devicePixelRatio" in js:
            return {"dpr": 3, "width": 430, "height": 11338}
        return {"measured": True} if js == "MEASURE" else None

    def screenshot(self, path, full_page=False, clip=None):
        self.shots.append({"full_page": full_page, "clip": clip})
        if full_page and (self.full == "raise" or (self.full == "clip" and clip is None)):
            raise RuntimeError(_TOO_TALL)


def _walk_one(full: str):
    page = _FakePage(full)
    ctx = type("C", (), {"add_init_script": lambda *_: None, "new_page": lambda _s: page, "close": lambda _s: None})()
    browser = type("B", (), {"new_context": lambda _s, **_k: ctx, "close": lambda _s: None})()
    pw = type("P", (), {"webkit": type("E", (), {"launch": lambda _s: browser})(), "devices": {"iPhone 15 Pro Max": {}}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": False})()
    with tempfile.TemporaryDirectory() as tmp:
        screens = walk.walk_context(pw, "iphone", "light", args, "MEASURE", {}, {}, Path(tmp))
    # The stub has no Settings gear to find (the Settings step is covered by the browser leg below, #1217).
    return [s for s in screens if s["view"] != plan.SETTINGS_GEAR_VIEW], page


for _mode in ("raise", "clip"):
    (_scr,), _pg = _walk_one(_mode)
    check(_scr["status"] == "ok" and _scr["metrics"] == {"measured": True},
          f"a full-page capture over the 32767px limit ({_mode}) keeps the screen ok with its metrics -- {_scr}")
_scr_raise = _walk_one("raise")[0][0]
check(_scr_raise["screenshot_full"] is None and "skipped" in (_scr_raise.get("note") or ""),
      f"a full-page capture that can't be taken even clipped is skipped with a note -- {_scr_raise}")
_scr_clip, _pg_clip = _walk_one("clip")
_scr_clip = _scr_clip[0]
check(_scr_clip["screenshot_full"] == "iphone-light-root-full.png" and "clipped" in (_scr_clip.get("note") or "")
      and _pg_clip.shots[-1]["clip"] == {"x": 0, "y": 0, "width": 430, "height": 32767 // 3},
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
    ("desktop-light-home", "tab", "ok", None, None, {"m": 1}, True),
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

# ---- browser leg: the static fixture page through a sibling venv --------------

FLEET = REPO.parent
interp = None
for sibling in ("project-scaffolding", "app-launcher"):
    cand = capture.resolve_interpreter(plan.Target(name=sibling, root=FLEET / sibling, base_url="file:///"))
    if cand["status"] == "ok":
        interp = cand["python"]
        break
scaffold = FLEET / "project-scaffolding"
if interp is None:
    _h.skip("browser leg: no sibling .venv with Playwright (project-scaffolding, app-launcher) -- fixture walk NOT verified")
else:
    run_dir = STATE / "fixture-run"
    proc = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(REPO),
         "--url", (FIX / "fixture.html").as_uri(), "--devices", "desktop", "--python", str(interp),
         "--scaffold", str(scaffold), "--run-dir", str(run_dir), "--rubric", str(RUBRIC),
         "--spec", str(FIX / "spec_compliant.md")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)},
    )
    lines = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)
    check(proc.returncode == 0 and lines.get("UNMEASURED") == "none" and lines.get("SCREENS") == "6/8" and lines.get("ABSENT") == "2",
          f"measure CLI walks the fixture: 2 tabs + 1 dialog x light/dark, plus the absent Settings gear (#1217) ({proc.stdout[-300:]}{proc.stderr[-300:]})")
    check(lines.get("RUN_DIR") == str(run_dir) and Path(lines.get("METRICS", "")).is_file(), "RUN_DIR/METRICS lines point at the run dir")
    doc = json.loads(Path(lines["METRICS"]).read_text(encoding="utf-8"))
    check(doc["interpreter"] == str(interp) and doc["schema_version"] == 1 and doc["rubric_version"] == "1.17.0", "metrics.json records the interpreter + versions")
    if not (scaffold / "tests" / "e2e" / "_geometry.py").is_file():
        _h.skip("browser leg: project-scaffolding/tests/e2e/_geometry.py absent -- hit-target assertions NOT verified")
    check(doc["walk"]["info"]["geometry"] == ("loaded" if (scaffold / "tests" / "e2e" / "_geometry.py").is_file() else "GEOMETRY_MISSING"),
          "walk reports whether the scaffold geometry JS loaded")
    by_id = {s["id"]: s for s in doc["screens"]}
    check(set(by_id) == {f"desktop-{t}-{v}" for t in ("light", "dark") for v in ("home", "list", "dialog-editdialog", "gear-settings")}
      and by_id["desktop-light-gear-settings"]["reason"] == "SETTINGS_GEAR_ABSENT", "screen ids from tabs + dialog + the Settings gear step (absent here)")
    home = by_id["desktop-light-home"]["metrics"]
    tx, ct, tg, ic, nv, ly, ay = (home[k] for k in ("text", "controls", "targets", "icons", "nav", "layout", "a11y"))
    check(tx["min_px"] == 10 and tx["under11_count"] == 1 and {"10", "12", "14", "16", "24"} <= set(tx["sizes"]), "text size histogram + 10px floor")
    check(tx["low_contrast_count"] == 2 and {r["sel"] for r in tx["low_contrast"]} == {"p.faint", "pre.log.faint"} and all(r["ratio"] < 3 for r in tx["low_contrast"]),
          f"low-contrast runs found, log text in <pre> still measured for contrast (#1119) -- {tx['low_contrast']}")
    check(tx["glyph_icon_count"] == 3
          and {g["sel"] for g in tx["glyph_icons"]} == {"button.big.glyph-btn", "button.big.glyph-lone", "button.big.glyph-run"},
          f"glyph icons: a control label ending or starting with a glyph, or a lone one, counts; an arrow inside a sentence "
          f"or a title (p.glyph) and <pre>/<code>/<samp>/<kbd> text do not (#1119, #1261) -- {tx['glyph_icons']}")
    check(ct["font_family_mismatch_count"] == 1 and ct["font_family_mismatch"][0]["sel"] == "select.ua-font", "UA-font select found")
    if "error" not in tg:
        small = {s["sel"] for s in tg["small"]}
        check("button.small-btn" in small and "button.hit-target" not in small, "24px button is small; 34px + ::before inset -5px measures 44 effective")
        check(tg["overlap_count"] == 0, "home: no expanded rects overlap")
        check(by_id["desktop-light-list"]["metrics"]["targets"]["overlap_count"] == 1, "list: two touching expanded targets overlap")
    lay_list, lay_home = by_id["desktop-light-list"]["metrics"]["layout"], ly
    check(lay_list["rows_over_limit"] == [{"sel": "li.arow.arow-over", "controls": 4}],
          f"LAYOUT-03: star + Run + kebab beside the row's main control is within budget; a fourth extra is over (#996) -- {lay_list['rows_over_limit']}")
    check(lay_list["content_w"] <= 500 and lay_list.get("content_span", 0) >= 1400,
          f"LAYOUT-06: content_span runs from the list pane across the detail pane (#996) -- {lay_list['content_w']} / {lay_list.get("content_span")}")
    check(lay_home.get("content_span") == lay_home["content_w"], "LAYOUT-06: with no detail pane the span is the pane itself")
    check({b["sel"] for b in ct["boundary_low"]} == {"input.faint-border", "button.bare-switch", "input.inset-faint"},
          f"COLOR-03: a faint input, a faint inset-shadow field and a bare switch track fail; text-labelled buttons and switch pills are exempt; a wrapper-drawn boundary counts (#996) and so does a 3:1 inset-shadow boundary (#1185) -- {[b['sel'] for b in ct['boundary_low']]}")
    via = {b["sel"]: b["via"] for b in ct["boundary_low"]}
    check(via.get("input.inset-faint") == "inset-shadow" and via.get("input.faint-border") == "border" and "input.inset-ok" not in via,
          f"COLOR-03: the boundary read from an inset shadow says so, and a 4.8:1 inset boundary passes -- {via}")
    for theme in ("light", "dark"):
        seg = by_id[f"desktop-{theme}-list"]["metrics"]["controls"]["segmented_bad"]
        check(seg == [{"sel": "div.segmented.seg-wrap", "options": 2, "wrapped": True}],
              f"COMP-03 {theme}: a 44px single-line segment is not wrapped; a label broken onto two lines is (#997) -- {seg}")
    check(ic["boxes"] == {"16x16": 1, "22x22": 1}, "icon boxes")
    check(ic["elements"] == {"16x16": [{"glyph": "i16", "host": "section#paneHome", "label": ""}],
                             "22x22": [{"glyph": "play", "host": "section#paneHome", "label": ""}]},
          f"COMP-02: each icon box names its glyph (sprite <use> first, else a class) and its host (#1020) -- {ic.get('elements')}")
    check(nv["primary_count"] == 2 and nv["pane_header_visible"] is True, "nav: 2 primary tabs, header visible")
    check(ly["overflow_x"] is False and ly["inner_w"] == 1440, "no overflow at 1440")
    check(ay["unnamed"] == ["button.big"] and ay["zoom_locked"] is True, "unnamed button + locked zoom")
    check(ay["text_size_control"] is True and ay["text_size_stamped"] is True,
          f"a11y probe: a hidden group named by aria-labelledby is the text-size control; html[data-textsize] is the stamp (#1185) -- {ay}")
    dlg = by_id["desktop-light-dialog-editdialog"]["metrics"]
    check(dlg["controls"]["total"] == 2 and dlg["a11y"]["unnamed_count"] == 0,
          f"dialog scope measures only the dialog, its folded Options field opened and measured (#995) -- {dlg['controls']['total']}")
    if "error" not in dlg["targets"]:
        small_d = {s["sel"] for s in dlg["targets"]["small"]}
        check(dlg["targets"]["total"] == 4 and dlg["targets"]["overlap_count"] == 0
              and "input#openedField" in small_d and "input#hiddenField" not in small_d,
              f"dialog targets: two summaries, the opened field and Close; the field the accordion re-closed is excluded (#995, #998) -- {dlg['targets']}")
    check(dlg["text"]["low_contrast_count"] == 0, f"text inside a details that stays closed is not measured (#998) -- {dlg['text']['low_contrast']}")
    check((run_dir / "shots" / "desktop-light-home.png").is_file() and (run_dir / "shots" / "desktop-light-home-full.png").is_file(),
          "screenshots in the run dir")
    dark = by_id["desktop-dark-home"]["metrics"]["text"]
    check(dark["low_contrast_count"] == 0 or dark["low_contrast"][0]["bg"] != "#ffffff", "dark leg renders the dark canvas")
    # evaluate CLI on the real run
    proc2 = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "evaluate", lines["METRICS"],
                            "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md"), "--spec-dark", str(FIX / "spec_compliant.md")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    res = json.loads(proc2.stdout)
    check(proc2.returncode == 0 and {r["id"] for r in res["rules"]} == {r.id for r in rubric.rules}, "evaluate CLI prints every rule")
    rs = {r["id"]: r["status"] for r in res["rules"]}
    check(rs["TYPE-03"] == "fail" and rs["COMP-01"] == "fail" and rs["A11Y-01"] == "fail" and rs["LAYOUT-01"] == "pass",
          "evaluate on the fixture walk: planted defects fail, overflow passes")
    proc3 = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "evaluate", lines["METRICS"],
                            "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md"), "--spec-dark", str(FIX / "spec_compliant.md")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    res2 = json.loads(proc3.stdout)
    check(res2["rules"] == res["rules"] and res2["categories"] == res["categories"], "two evaluate runs -> identical rules and grades")
    check(all(s.get("theme_applied") is True for s in doc["screens"] if s["status"] == "ok"),
          f"a page that honours the stamped theme reads theme_applied true on every measured screen -- {[(s['id'], s.get('theme_applied')) for s in doc['screens']]}")
    # a theme the app picks for itself (fleet-config#1216)
    _tf = STATE / "theme-fight-run"
    _p = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(REPO),
         "--url", (FIX / "theme_fight.html").as_uri(), "--devices", "desktop", "--python", str(interp),
         "--scaffold", str(scaffold), "--run-dir", str(_tf), "--rubric", str(RUBRIC), "--spec", str(FIX / "spec_compliant.md")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)})
    _kv = dict(l.split("=", 1) for l in _p.stdout.splitlines() if "=" in l)
    if "METRICS" not in _kv:
        check(False, f"theme-fight fixture walk produced metrics -- {_p.stdout[-300:]}{_p.stderr[-300:]}")
    else:
        _tdoc = json.loads(Path(_kv["METRICS"]).read_text(encoding="utf-8"))
        _tby = {s["id"]: s for s in _tdoc["screens"]}
        _tl, _td = _tby["desktop-light-root"], _tby["desktop-dark-root"]
        check(_tl["theme_applied"] is False and _tl["theme_observed"]["attr"] == "dark" and _td["theme_applied"] is True,
              f"an app that re-applies dark is not applied on its light leg, and is on its dark leg -- {_tl.get('theme_observed')}")
        _tev = ev.evaluate(_tdoc, rubric, rb.load_specs(FIX / "spec_compliant.md", FIX / "spec_compliant.md"))
        _trules = {r["id"]: r for r in _tev["rules"]}
        _c4 = _trules["COLOR-04"]
        check("desktop-light-root" not in _c4["measured"] and "desktop-dark-root" in _c4["measured"]
              and "desktop-light-root" not in [e["screen"] for e in _c4["evidence"]]
              and "1 unmeasured" in _c4["reason"] and _tev["theme_mismatch_screens"] == [{"id": "desktop-light-root", "requested": "light", "rendered": "dark"}],
              f"COLOR-04 reads the light leg that rendered dark as unmeasured, never as a failure against the light spec -- {_c4['status']} {_c4['reason']}")
        check("desktop-light-root" in _trules["TOUCH-01"]["measured"] and "desktop-light-root" in _trules["LAYOUT-01"]["measured"],
              "its touch and layout rules still score that screen")
    # embedded content (fleet-config#1185): a declared [design.review].exclude_selectors keeps a session-themed preview out of scoring
    def _embedded_walk(label: str, fleet_toml: str) -> dict:
        tgt = STATE / f"embedded-{label}"
        tgt.mkdir(parents=True, exist_ok=True)
        (tgt / ".fleet.toml").write_text(fleet_toml, encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(REPO / "skills" / "_lib" / "design_review"), "measure", str(tgt),
             "--url", (FIX / "embedded.html").as_uri(), "--devices", "desktop", "--python", str(interp),
             "--scaffold", str(scaffold), "--run-dir", str(STATE / f"embedded-run-{label}"), "--rubric", str(RUBRIC),
             "--spec", str(FIX / "spec_compliant.md")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
            env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)})
        kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
        return json.loads(Path(kv["METRICS"]).read_text(encoding="utf-8")) if "METRICS" in kv else {"error": p.stdout[-300:] + p.stderr[-300:]}

    emb_all = _embedded_walk("all", '[design]\nwide_views = []\n')
    emb_ex = _embedded_walk("excluded", '[design.review]\nexclude_selectors = [".preview-frame"]\n')
    if "error" in emb_all or "error" in emb_ex:
        check(False, f"embedded fixture walk produced metrics -- {emb_all.get('error')} {emb_ex.get('error')}")
    else:
        m_all, m_ex = emb_all["screens"][0]["metrics"], emb_ex["screens"][0]["metrics"]
        check(m_all["text"]["under11_count"] == 2 and m_all["text"]["uppercase_count"] == 1 and m_all["targets"]["small_count"] >= 1 and "excluded" not in m_all,
              f"without a declaration the embedded preview is measured as app UI -- {m_all['text']['under11']} {m_all['text']['uppercase']} {m_all['targets']['small_count']}")
        check(m_ex["text"]["under11_count"] == 0 and m_ex["text"]["uppercase_count"] == 0 and m_ex["targets"]["small_count"] == 0
              and m_ex["controls"]["total"] == 0 and m_ex["a11y"]["unnamed_count"] == 0,
              f"a declared exclude_selectors leaves the preview out of text, targets and controls -- {m_ex['text']['under11']} {m_ex['targets']['small_count']}")
        check(m_ex["excluded"] == {"selectors": [".preview-frame"], "elements": 1} and m_ex["text"]["runs"] == 1,
              f"the run records what it excluded and still measures the app's own text -- {m_ex.get('excluded')} runs={m_ex['text']['runs']}")
        check(emb_ex["params"]["script"]["excludeSelectors"] == [".preview-frame"] and emb_all["params"]["script"]["excludeSelectors"] == [],
              "metrics.json records the exclusion in the params the run measured with")
    # extra steps, driven through walk.py with a declared review block (the fixture repo has no [design.review])
    step_dir = STATE / "fixture-steps"
    (STATE / "steps-review.json").write_text(json.dumps({"no_go": [".danger-zone", "#revealForbidden"], "extra_steps": [
        {"tab": "home", "id": "reveal", "click": "#revealBtn"},
        {"tab": "list", "id": "row-menu", "open": "details.card", "clicks": [".card .kebab"]},
        {"tab": "list", "id": "row-delete", "open": "details.card", "clicks": [".card .kebab", ".card .danger-item"]},
        {"tab": "home", "id": "forbidden", "click": "#revealForbidden"},
        {"tab": "home", "id": "absent", "click": "#noSuchTarget"},
        {"tab": "list", "id": "absent-second", "open": "details.card", "clicks": [".card .kebab", "#noSuchMenuItem"]},
        {"tab": "home", "id": "synthetic-only", "click": "#revealBtn", "synthetic": True},
    ]}), encoding="utf-8")
    (STATE / "steps-params.json").write_text(json.dumps(measure.default_params()), encoding="utf-8")
    proc4 = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "fixture.html").as_uri(),
         "--out", str(step_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--review", str(STATE / "steps-review.json"), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    steps = {s["id"]: s for s in json.loads((step_dir / "screens.json").read_text(encoding="utf-8"))} if proc4.returncode == 0 else {}
    reveal = steps.get("desktop-light-home-reveal", {})
    check(reveal.get("status") == "ok", f"the reveal step is measured ({proc4.stderr[-300:]})")
    if reveal.get("status") == "ok" and "error" not in reveal["metrics"]["targets"]:
        check("input#foldedField" in {s["sel"] for s in reveal["metrics"]["targets"]["small"]},
              "an extra step opens the closed details it reveals before measuring, so the folded field is measured (#995)")
    menu = steps.get("desktop-light-list-row-menu", {})
    check(menu.get("status") == "ok" and "button.menu-item" in {s["sel"] for s in (menu.get("metrics") or {}).get("targets", {}).get("small", [])},
          f"a multi-click step opens a closed card and taps its row kebab; the menu is measured (#995) -- {menu.get('status')} {menu.get('error')}")
    delete = steps.get("desktop-light-list-row-delete", {})
    check(delete.get("status") == "error" and delete.get("reason") == "NO_GO" and ".card .danger-item" in str(delete.get("error")),
          f"a click inside a no_go zone is vetoed at click time, not only by selector equality (#995) -- {delete.get('reason')} {delete.get('error')}")
    check("desktop-light-home-synthetic-only" not in steps and proc4.returncode == 0,
          "a live walk never runs a step marked synthetic (#995)")
    forbidden = steps.get("desktop-light-home-forbidden", {})
    check(forbidden.get("reason") == "NO_GO", "a step whose click is literally a no_go selector is refused")
    for _sid, _sel in (("desktop-light-home-absent", "#noSuchTarget"), ("desktop-light-list-absent-second", "#noSuchMenuItem")):
        _s = steps.get(_sid, {})
        check(_s.get("status") == "absent" and _s.get("reason") == "STEP_TARGET_ABSENT" and _sel in str(_s.get("error")),
              f"a step target that never appears is recorded STEP_TARGET_ABSENT, naming the selector (#995) -- {_sid}: {_s.get('status')} {_s.get('reason')}")

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
          f"LAYOUT-07: a list padded clear of the pill passes -- {clr.get('clearance')}")
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

    # stretched graphic: a chart SVG with preserveAspectRatio="none" is not an icon; a real off-step icon still is (#1211)
    sg_dir = STATE / "fixture-stretched-graphic"
    proc_sg = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "stretched_graphic.html").as_uri(),
         "--out", str(sg_dir), "--devices", "desktop", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    sgs = json.loads((sg_dir / "screens.json").read_text(encoding="utf-8")) if proc_sg.returncode == 0 else []
    _sg_icons = ((sgs[0].get("metrics") or {}).get("icons") or {}) if sgs else {}
    check(_sg_icons.get("boxes") == {"22x22": 1, "20x20": 1},
          f"COMP-02: the two stretched 162x64 chart SVGs are not icons; the 22px and 20px icons still are (#1211) -- {_sg_icons} ({proc_sg.stderr[-300:]})")

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

_h.report_and_exit("test_design_review", skip_code=SKIP_EXIT)
