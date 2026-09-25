"""Unit tests for skills/_lib/design_review's report renderer + mock-up library (fleet-config#972).

Pure-logic: placeholder filling (format specs, literal `{{ }}`, a missing
value keeps its token and never raises), the values a finding exposes from
the evaluate document, the rubric <-> mock-up library agreement in both
directions, every template rendering from the violating fixture, the
no-mock-up note when a needed value is absent, the fixed section order,
the reserved `judgment` / `diff` slots (omitted when absent, rendered when
present), never a screenshot or an external request, render determinism,
and the `probe` / `evaluate --out` / `render` CLI legs. No browser.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_report.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import re
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

from design_review import evaluate as ev, mockups, report, rubric as rb  # noqa: E402

_h = CheckHarness()
check = _h.check

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"
STATE = Path(tempfile.mkdtemp(prefix="design-report-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
PKG = REPO / "skills" / "_lib" / "design_review"


def _specs(name: str) -> dict:
    return rb.load_specs(FIX / f"spec_{name}.md", FIX / f"spec_{name}.md")


def _doc(name: str) -> dict:
    return json.loads((FIX / f"metrics_{name}.json").read_text(encoding="utf-8"))


rubric = rb.load_rubric(RUBRIC)
out_v = ev.evaluate(_doc("violating"), rubric, _specs("violating"))
out_c = ev.evaluate(_doc("compliant"), rubric, _specs("compliant"))
by_id = {r["id"]: r for r in out_v["rules"]}

# ---- fill_template: deterministic, never raises ------------------------------

check(report.fill_template("{count} of {total} on {screen}", {"count": 3, "total": 10, "screen": "s"}) == "3 of 10 on s", "plain placeholders")
check(report.fill_template("{share:.0%} under", {"share": 0.7692}) == "77% under", "format spec honoured")
check(report.fill_template("add `x {{ font: inherit; }}`", {}) == "add `x { font: inherit; }`", "literal {{ }} braces (TYPE-02)")
check(report.fill_template("{count} of {total}", {"count": 3}) == "3 of {total}", "a missing value keeps its token")
check(report.item_label({"box": "22x22", "count": 5, "elements": [{"glyph": "play", "host": "button.job-run", "label": "Run"},
                                                               {"glyph": "", "host": "summary", "label": ""}]})
      == "22x22px ×5 (play in button.job-run, svg in summary, +3 more)"
      and report.item_label({"box": "22x22", "count": 2}) == "22x22px ×2", "COMP-02 sample names each icon's glyph and host (#1020)")
check(report.fill_template("{share:.0%} of {x}", {}) == "{share:.0%} of {x}", "a missing value with a spec keeps token + spec")
check(report.fill_template("{share:.0%}", {"share": "abc"}) == "{share:.0%}", "an unformattable value falls back to the template, no raise")
check(report.unfilled("3 of {total} ({sample}) { font: inherit; }") == ["{sample}", "{total}"], "unfilled lists tokens, not CSS braces")

# ---- finding_values: everything the templates need comes from the JSON -------

t01 = report.finding_values(by_id["TOUCH-01"], out_v)
check(t01.get("count") == 3 and t01.get("total") == 10 and t01.get("share") == 0.3 and t01.get("hit_min") == 48,
      f"TOUCH-01: count/total from evidence facts, hit_min from doc params ({t01})")
s = report.finding_sentence(by_id["TOUCH-01"], out_v)
check(s.startswith("3 of 10 controls on ") and "48px" in s and not report.unfilled(s), f"TOUCH-01 sentence fully filled: {s}")
c01 = report.finding_values(by_id["COLOR-01"], out_v)
check(c01.get("component") == "button-tint" and c01.get("ratio") == 4.13 and c01.get("threshold") == 4.5,
      f"COLOR-01: component/ratio + the pair's own AA threshold, not the rule's count threshold ({c01})")
check("4.13:1" in report.finding_sentence(by_id["COLOR-01"], out_v) and "needs 4.5:1" in report.finding_sentence(by_id["COLOR-01"], out_v),
      "COLOR-01 sentence names the pair and the AA floor")
l02 = report.finding_values(by_id["LAYOUT-02"], out_v)
check(l02.get("viewports") == 5, "LAYOUT-02: {viewports} from the rule's params")
l06 = report.finding_values(by_id["LAYOUT-06"], out_v)
check("inner_w" in l06 and "share" in l06 and "%" in report.finding_sentence(by_id["LAYOUT-06"], out_v), "LAYOUT-06: inner_w + share as a percentage")
for rid, r in by_id.items():
    sent = report.finding_sentence(r, out_v)
    check(not report.unfilled(sent), f"{rid}: every placeholder filled from the violating fixture ({sent[:80]})")
worst_rule = {"fail_when": "lt", "evidence": [{"screen": "a", "value": 10.0}, {"screen": "b", "value": 9.5}, {"screen": "c", "value": 9.5}]}
check(report.worst_screen(worst_rule)["screen"] == "b", "worst screen for lt is the lowest value, first on ties")
worst_rule["fail_when"] = "gt"
check(report.worst_screen(worst_rule)["screen"] == "a", "worst screen for gt is the highest value")
check(report.finding_values({"threshold": {"value": None}, "evidence": []}) == {}, "no evidence, no threshold -> no values, no raise")

# ---- rubric <-> mock-up library, both directions -----------------------------

used = {r.mockup for r in rubric.rules if r.mockup != mockups.NONE}
check(used == set(mockups.MOCKUP_IDS), f"every rubric mockup id is a library template and every template is referenced: rubric={sorted(used)} library={sorted(mockups.MOCKUP_IDS)}")
expected = {"TYPE-01": "type-scale", "TOUCH-01": "hit-target", "NAV-01": "nav-five", "LAYOUT-02": "row-density",
            "LAYOUT-03": "action-row", "LAYOUT-04": "destructive-in-menu", "TOUCH-03": "form-primary", "COLOR-03": "form-primary",
            "COLOR-01": "contrast-swatches", "LAYOUT-06": "wide-desktop"}
check({r.id: r.mockup for r in rubric.rules if r.mockup != mockups.NONE} == expected, "the rule -> mockup map is the #972 decision")
check(rubric.version == "1.5.0", "rubric at 1.5.0 (mock-up alignment in 1.1.0, judgment checklist in 1.2.0, #964 type rules in 1.3.0, #996 spec alignment in 1.4.0, #1019 layers + clearance in 1.5.0)")
_base = {"meta": {"version": "x"}, "weights": {"c": 1.0}, "grades": {"A": 90, "F": 0},
         "penalties": {"P0": 25, "P1": 12, "P2": 6, "P3": 2},
         "rules": [{"id": "R-1", "category": "c", "title": "t", "metric": "layout.overflow_x", "fail_when": "true",
                    "severity": "P0", "standard": "s", "fix_template": "f", "owner": "app", "mockup": "list-filter"}]}
try:
    rb.validate_rubric(_base)
    check(False, "rubric validation refuses a mockup id the library lacks")
except rb.RubricError as exc:
    check("list-filter" in str(exc), "rubric validation refuses a mockup id the library lacks, naming it")
_base["rules"][0]["mockup"] = "hit-target"
check(rb.validate_rubric(_base).rules[0].mockup == "hit-target", "a library id validates")

# ---- every template renders from the violating fixture ------------------------

for rid, mid in expected.items():
    r = by_id[rid]
    mk, note = mockups.render_mockup(r, report.finding_values(r, out_v))
    check(mk is not None and note is None and mk["now"] and mk["proposed"] and mk["caption"], f"{rid}: {mid} renders now + proposed ({note})")
    if mk:
        check("<img" not in mk["now"] + mk["proposed"] and ".png" not in mk["now"] + mk["proposed"], f"{mid}: no image in the mock-up")
mk, note = mockups.render_mockup(by_id["TYPE-02"], report.finding_values(by_id["TYPE-02"], out_v))
check(mk is None and note is None, "a rule with mockup=none renders no mock-up and no note")
stripped = json.loads(json.dumps(by_id["TOUCH-01"]))
for e in stripped["evidence"]:
    e["facts"] = {}
mk, note = mockups.render_mockup(stripped, report.finding_values(stripped, out_v))
check(mk is None and note is not None and "total" in note and "did not measure" in note,
      f"a template whose needed value is absent renders nothing and says which value: {note}")
mk, note = mockups.render_mockup({"mockup": "nope", "id": "X"}, {})
check(mk is None and "not in the library" in str(note), "an unknown template id is a note, not a crash")
fixed = mockups.aa_background("#0969da", "#d8e7f9", 4.5)
check(fixed is not None and fixed[1] >= 4.5 and re.fullmatch(r"#[0-9a-f]{6}", fixed[0]) and fixed[2] in ("black", "white"),
      f"aa_background computes a background that measures AA ({fixed})")
check(mockups.aa_background("#808080", "#808080", 21.0) is None, "aa_background reports None when no axis reaches the floor")

# ---- the page: sections, ordering, ids, no screenshots, no requests ----------

page = report.render_report(out_v)
order = [m.group(1) for m in re.finditer(r'<section id="([a-z]+)"', page)]
check(order == ["scorecard", "method", "strong", "findings", "mockups", "owners"], f"section order without judgment/diff: {order}")
check("rubric v1.5.0" in page and f"rubric v{out_v['rubric_version']}" in page, "rubric version printed in the header")
for rid in by_id:
    check(f'id="{rid}"' in page and f'<code class="rid">{rid}</code>' in page, f"{rid}: finding anchored + id visible")
check('<span class="grade grade-F">F</span>' in page and f"{out_v['overall']['score']}" in page, "overall grade + score printed from the JSON")
for cat, v in out_v["categories"].items():
    check(f'id="cat-{cat}"' in page and f'{v["grade"]}</span>' in page, f"category {cat} grade printed")
typo = page.index('id="cat-typography"')
ids_in_typo = [m.group(1) for m in re.finditer(r'<article class="finding sev-P\d" id="(TYPE-\d\d)"', page[typo:page.index('id="cat-color"')])]
check(ids_in_typo == ["TYPE-02", "TYPE-01", "TYPE-03", "TYPE-04", "TYPE-05"], f"findings ordered by severity then id within a category: {ids_in_typo}")
shot_paths = [s.get(k) for s in _doc("violating")["screens"] for k in ("screenshot", "screenshot_full") if s.get(k)]
check(shot_paths and all(str(p) not in page and Path(str(p)).name not in page for p in shot_paths) and "<img" not in page and ".png" not in page,
      "no screenshot embedded or linked: none of the metrics' screenshot paths reach the page")
check(not re.search(r'(src|href)="https?://', page) and "<script" not in page and "<link" not in page, "no external request, no script, no stylesheet link")
check("prefers-color-scheme" in page and '[data-theme="dark"]' in page and ':root:not([data-theme="light"])' in page, "theme tokens with media + data-theme overrides")
check('name="viewport"' in page, "viewport meta for phone width")
check("WCAG 2.2 SC 1.4.3" in page and "Apple HIG 44x44pt" in page, "standards printed from the rules")
check(f"the {out_v['target']} repo" in page, "owner block names the target repo")
check("worst of" in page, "multi-screen findings say the sentence is for the worst screen")
check(report.render_report(out_v) == page, "rendering the same document twice is byte-identical")
clean = report.render_report(out_c)
check("No findings." in clean and '<span class="grade grade-A">A</span>' in clean and "No failing rule has a mock-up" in clean,
      "a compliant document renders an A with empty findings and no mock-ups")

# ---- #995: an absent step target is a caveat, not an unmeasured screen --------

absent_doc = _doc("compliant")
absent_doc["screens"].append({"id": "desktop-light-home-row-menu", "device": "desktop", "theme": "light", "view": "home-row-menu",
                              "kind": "step", "status": "absent", "reason": "STEP_TARGET_ABSENT", "metrics": None})
page_a = report.render_report(ev.evaluate(absent_doc, rubric, _specs("compliant")))
check("desktop-light-home-row-menu" in page_a and "step target absent" in page_a and "partly unmeasured" not in page_a,
      "an absent step screen is listed under method and caveats and does not mark the run unmeasured")

# ---- #995: a synthetic run says so under method and caveats -----------------

synth_doc = _doc("compliant")
synth_doc["mode"] = "synthetic"
page_syn = report.render_report(ev.evaluate(synth_doc, rubric, _specs("compliant")))
check("synthetic instance" in page_syn and "synthetic instance" not in clean,
      "a synthetic run is labelled as measured on the target's synthetic instance, a live run is not")

# ---- unmeasured rendering ----------------------------------------------------

down = _doc("compliant")
down["screens"] = []
down["unmeasured"] = {"reason": "NOT_LISTENING", "detail": "127.0.0.1:9999 refused the connection"}
page_d = report.render_report(ev.evaluate(down, rubric, _specs("compliant")))
check("partly unmeasured" in page_d and "NOT_LISTENING" in page_d and page_d.count('class="badge badge-unm">unmeasured') == len(rubric.rules),
      "an unmeasured run shows the flag, the reason, and every rule as unmeasured")

# ---- reserved slots for #973 / #974 -------------------------------------------

check("judgment" not in order and "diff" not in order and 'id="judgment"' not in page and 'id="diff"' not in page, "judgment/diff omitted when absent")
with_slots = json.loads(json.dumps(out_v))
with_slots["judgment"] = {"status": "complete",
                          "answers": [{"id": "J-01", "question": "Does the primary action read first?", "answer": "no", "evidence": "board tab"}],
                          "uncatalogued": [{"title": "Placeholder finding", "severity": "P2", "detail": "outside the rubric", "owner": "app"}]}
with_slots["diff"] = {"previous_run": "20260101T000000Z", "fixed": ["TYPE-03"], "regressed": [], "new": ["COMP-04"], "unchanged": ["TOUCH-01"]}
page_s = report.render_report(with_slots)
order_s = [m.group(1) for m in re.finditer(r'<section id="([a-z]+)"', page_s)]
check(order_s == ["scorecard", "diff", "method", "strong", "findings", "judgment", "mockups", "owners"], f"slots render in their fixed positions: {order_s}")
check("J-01" in page_s and "Does the primary action read first?" in page_s and "Placeholder finding" in page_s and "status: complete" in page_s,
      "judgment answers + uncatalogued rendered from the documented keys")
check("20260101T000000Z" in page_s and 'class="pill pill-ok">TYPE-03' in page_s and 'class="pill pill-bad">COMP-04' in page_s
      and 'class="pill pill-mute">TOUCH-01' in page_s, "diff buckets rendered from the documented keys")

# ---- summary + CLI ------------------------------------------------------------

summ = report.report_summary(out_v)
check(summ["grade"] == "F" and summ["failed"] == len(rubric.rules) and summ["total"] == len(rubric.rules) and summ["unmeasured_rules"] == 0
      and set(summ["mockups"]) == set(mockups.MOCKUP_IDS), f"summary: grade, counts and the drawn mock-up set ({summ})")

tmp = Path(tempfile.mkdtemp(prefix="design-report-cli-"))
PY = sys.executable
common = ["--rubric", str(RUBRIC), "--spec", str(FIX / "spec_violating.md"), "--spec-dark", str(FIX / "spec_violating.md")]
env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(PKG), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)


def _kv(proc: subprocess.CompletedProcess) -> dict:
    return dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)


p1 = _run("evaluate", str(FIX / "metrics_violating.json"), "--out", str(tmp / "evaluate.json"), *common)
k1 = _kv(p1)
check(p1.returncode == 0 and (tmp / "evaluate.json").is_file() and k1.get("GRADE") == "F" and k1.get("RUBRIC") == "1.5.0"
      and k1.get("FAILED") == f"{len(rubric.rules)}/{len(rubric.rules)}", f"evaluate --out writes the document and prints summary lines ({p1.stdout[-200:]}{p1.stderr[-200:]})")
p2 = _run("render", str(tmp / "evaluate.json"))
k2 = _kv(p2)
check(p2.returncode == 0 and k2.get("REPORT") == str(tmp / "report.html") and (tmp / "report.html").is_file()
      and k2.get("EVALUATE") == str(tmp / "evaluate.json") and k2.get("GRADE") == "F" and k2.get("MOCKUPS"),
      f"render <evaluate.json> writes report.html beside it (no run_dir on disk) ({p2.stdout[-300:]}{p2.stderr[-300:]})")
cli_doc = json.loads((tmp / "evaluate.json").read_text(encoding="utf-8"))
check((tmp / "report.html").read_text(encoding="utf-8") == report.render_report(cli_doc), "the CLI page equals render_report of the same document")
tmp2 = tmp / "from-metrics"
tmp2.mkdir()
metrics_copy = tmp2 / "metrics.json"
metrics_copy.write_text((FIX / "metrics_violating.json").read_text(encoding="utf-8"), encoding="utf-8")
p3 = _run("render", str(metrics_copy), "--out", str(tmp2 / "custom.html"), *common)
k3 = _kv(p3)
check(p3.returncode == 0 and k3.get("REPORT") == str(tmp2 / "custom.html") and (tmp2 / "custom.html").is_file()
      and k3.get("EVALUATE") == str(tmp2 / "evaluate.json") and (tmp2 / "evaluate.json").is_file(),
      f"render <metrics.json> evaluates first, writes evaluate.json beside it, honours --out ({p3.stdout[-300:]}{p3.stderr[-300:]})")
p4 = _run("render", str(tmp / "nope.json"))
check(p4.returncode == 2 and p4.stdout.startswith("ERROR="), "render on a missing file exits 2 with ERROR=")
p5 = _run("probe", str(REPO), "--url", (FIX / "fixture.html").as_uri())
k5 = _kv(p5)
check(p5.returncode == 0 and k5.get("PROBE") == "listening" and k5.get("TARGET") == "fleet-config" and k5.get("CLAUDE_MD", "").endswith("CLAUDE.md"),
      f"probe with a file:// url is listening and names the target's CLAUDE.md ({p5.stdout}{p5.stderr[-200:]})")
p6 = _run("probe", str(REPO), "--url", "http://127.0.0.1:1")
check(p6.returncode == 0 and _kv(p6).get("PROBE") == "NOT_LISTENING", "probe on a dead port reports NOT_LISTENING, exit 0")
p7 = _run("probe", "no-such-repo-zzz")
check(p7.returncode == 2 and p7.stdout.startswith("ERROR="), "probe on an unknown target exits 2")

_h.report_and_exit("test_design_report", skip_code=SKIP_EXIT)
