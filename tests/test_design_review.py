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
import os
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
check(rubric.version == "1.2.0", "rubric meta.version stamped")
check(len(rubric.rules) == 25, f"25 seed rules loaded (got {len(rubric.rules)})")
check(rubric.categories == ["typography", "color", "touch", "navigation", "layout", "components", "a11y"],
      "categories in rubric order")
check(rb.check_metric_names(rubric, measure.metric_paths()) == [], "every rule metric is a script path or a derived metric")
check(all(r.severity in rb.SEVERITIES and r.owner in rb.OWNERS for r in rubric.rules), "severity/owner vocab")
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
check(rb.resolve_params(rubric, {}) == {"hit_min": 44, "primary_min": 48, "icon_steps": [16, 18, 20, 24]},
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
    bg = ev.composite(bg, card)
    return round(ev.contrast(ev.composite(fg, bg), bg), 2)


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
check(round(ev.contrast((255, 255, 255, 1), (0, 0, 0, 1)), 1) == 21.0, "WCAG 21:1")

# ---- evaluate: compliant fixture passes every rule ---------------------------

out_c = ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))
statuses_c = {r["id"]: r["status"] for r in out_c["rules"]}
check(all(s == "pass" for s in statuses_c.values()), f"compliant: every rule passes ({[k for k, v in statuses_c.items() if v != 'pass']})")
check(all(v["score"] == 100.0 and v["grade"] == "A" and not v["unmeasured"] for v in out_c["categories"].values()),
      "compliant: every category 100/A, measured")
check(out_c["overall"] == {"score": 100.0, "grade": "A", "unmeasured": False}, "compliant: overall A")
check(out_c["schema_version"] == 1 and out_c["rubric_version"] == "1.2.0" and out_c["target"] == "fixture-app"
      and out_c["commit"].startswith("0000") and out_c["generated_at"].endswith("Z"), "evaluate envelope keys")
check([s["id"] for s in out_c["screens"]] == ["desktop-light-home", "iphone-light-home", "desktop-light-dialog-edit"],
      "evaluate echoes the screen list")
t03 = next(r for r in out_c["rules"] if r["id"] == "TOUCH-03")
check(set(t03["measured"]) == {"desktop-light-home", "iphone-light-home"} and "n/a on 1" in t03["reason"],
      "a null aggregate over an empty population is N/A, not unmeasured")
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
check(cat_v["touch"]["score"] == 100 - (25 + 25 + 12) and cat_v["navigation"]["score"] == 100 - (12 + 6),
      "touch / navigation scores")
check(cat_v["typography"]["failed"] == ["TYPE-01", "TYPE-02", "TYPE-03", "TYPE-04", "TYPE-05"], "failed ids listed in rule order")
check(out_v["overall"]["grade"] == "F" and not out_v["overall"]["unmeasured"], "violating: overall F, fully measured")
c01v = next(r for r in out_v["rules"] if r["id"] == "COLOR-01")
check(any(i["component"] == "button-tint" and i["ratio"] == 4.13 for e in c01v["evidence"] for i in e["items"]),
      "COLOR-01 evidence names the 4.13:1 pair")
t01v = next(r for r in out_v["rules"] if r["id"] == "TOUCH-01")
check(t01v["measured"]["desktop-light-home"] == 0.3 and t01v["evidence"][0]["items"][0]["sel"] == "button.icon",
      "TOUCH-01 share 3/10 with the small controls as evidence")
check(next(r for r in out_v["rules"] if r["id"] == "TOUCH-03")["threshold"]["value"] == 40.0,
      "threshold_token: the violating spec's 40px button height is what TOUCH-03 compares against")
check(ev.grade_for(89.99, rubric.grades) == "B" and ev.grade_for(0, rubric.grades) == "F", "grade mapping edges")

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
    check(proc.returncode == 0 and lines.get("UNMEASURED") == "none" and lines.get("SCREENS") == "6/6",
          f"measure CLI walks the fixture: 2 tabs + 1 dialog x light/dark ({proc.stdout[-300:]}{proc.stderr[-300:]})")
    check(lines.get("RUN_DIR") == str(run_dir) and Path(lines.get("METRICS", "")).is_file(), "RUN_DIR/METRICS lines point at the run dir")
    doc = json.loads(Path(lines["METRICS"]).read_text(encoding="utf-8"))
    check(doc["interpreter"] == str(interp) and doc["schema_version"] == 1 and doc["rubric_version"] == "1.2.0", "metrics.json records the interpreter + versions")
    if not (scaffold / "tests" / "e2e" / "_geometry.py").is_file():
        _h.skip("browser leg: project-scaffolding/tests/e2e/_geometry.py absent -- hit-target assertions NOT verified")
    check(doc["walk"]["info"]["geometry"] == ("loaded" if (scaffold / "tests" / "e2e" / "_geometry.py").is_file() else "GEOMETRY_MISSING"),
          "walk reports whether the scaffold geometry JS loaded")
    by_id = {s["id"]: s for s in doc["screens"]}
    check(set(by_id) == {f"desktop-{t}-{v}" for t in ("light", "dark") for v in ("home", "list", "dialog-editdialog")}, "screen ids from tabs + dialog")
    home = by_id["desktop-light-home"]["metrics"]
    tx, ct, tg, ic, nv, ly, ay = (home[k] for k in ("text", "controls", "targets", "icons", "nav", "layout", "a11y"))
    check(tx["min_px"] == 10 and tx["under11_count"] == 1 and {"10", "12", "14", "16", "24"} <= set(tx["sizes"]), "text size histogram + 10px floor")
    check(tx["low_contrast_count"] == 1 and tx["low_contrast"][0]["sel"] == "p.faint" and tx["low_contrast"][0]["ratio"] < 3, "low-contrast run found")
    check(tx["glyph_icon_count"] == 1 and "→" in tx["glyph_icons"][0]["text"], "glyph icon found")
    check(ct["font_family_mismatch_count"] == 1 and ct["font_family_mismatch"][0]["sel"] == "select.ua-font", "UA-font select found")
    if "error" not in tg:
        small = {s["sel"] for s in tg["small"]}
        check("button.small-btn" in small and "button.hit-target" not in small, "24px button is small; 34px + ::before inset -5px measures 44 effective")
        check(tg["overlap_count"] == 0, "home: no expanded rects overlap")
        check(by_id["desktop-light-list"]["metrics"]["targets"]["overlap_count"] == 1, "list: two touching expanded targets overlap")
    check(ic["boxes"] == {"16x16": 1, "22x22": 1}, "icon boxes")
    check(nv["primary_count"] == 2 and nv["pane_header_visible"] is True, "nav: 2 primary tabs, header visible")
    check(ly["overflow_x"] is False and ly["inner_w"] == 1440, "no overflow at 1440")
    check(ay["unnamed"] == ["button.big"] and ay["zoom_locked"] is True, "unnamed button + locked zoom")
    dlg = by_id["desktop-light-dialog-editdialog"]["metrics"]
    check(dlg["controls"]["total"] == 1 and dlg["a11y"]["unnamed_count"] == 0, "dialog scope measures only the dialog")
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

_h.report_and_exit("test_design_review", skip_code=SKIP_EXIT)
