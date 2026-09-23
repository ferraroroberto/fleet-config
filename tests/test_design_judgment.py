"""Unit tests for skills/_lib/design_review's judgment stage (fleet-config#973).

Pure-logic: the `[[judgment]]` checklist loads and validates (ids, maps_to,
scope), the judge prompt is deterministic and carries only the checklist,
the screen list with local screenshot paths and the metrics path (never
evaluate.json / report.html / another run / an answer), the answer schema is
all-or-nothing (a malformed or partial payload is `unmeasured` with every
error listed, never a partial acceptance), the multi-judge merge keeps only
agreed answers and uncatalogued findings every judge raised, the judgment never
moves a grade, and the `judge-prompt` / `judge-merge` / `render` CLI legs.
No browser, no agent spawned.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_judgment.py`  (also invoked by tests/run_acceptance.py)
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

from design_review import evaluate as ev, judgment as jm, report, rubric as rb  # noqa: E402

_h = CheckHarness()
check = _h.check

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"
STATE = Path(tempfile.mkdtemp(prefix="design-judgment-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
PKG = REPO / "skills" / "_lib" / "design_review"

rubric = rb.load_rubric(RUBRIC)
metrics = json.loads((FIX / "metrics_violating.json").read_text(encoding="utf-8"))
specs = rb.load_specs(FIX / "spec_violating.md", FIX / "spec_violating.md")
ok_payload = json.loads((FIX / "judge_answers_ok.json").read_text(encoding="utf-8"))
bad_payload = json.loads((FIX / "judge_answers_malformed.json").read_text(encoding="utf-8"))
SEED_IDS = [f"J-{i:02d}" for i in range(1, 11)]

# ---- the checklist in the rubric ---------------------------------------------

check(rubric.version == "1.3.0", "rubric at 1.3.0 (the [[judgment]] checklist arrived in 1.2.0)")
check([j.id for j in rubric.judgment] == SEED_IDS, f"the ten seed questions J-01..J-10 in order: {[j.id for j in rubric.judgment]}")
rule_ids = {r.id for r in rubric.rules}
for j in rubric.judgment:
    check(bool(j.question.strip()) and j.screens in rb.JUDGMENT_SCOPES and all(m in rule_ids for m in j.maps_to),
          f"{j.id}: question, scope and maps_to validate")
    check("Answer no if" in j.question, f"{j.id}: the question states what makes it a no")
check(any(not j.maps_to for j in rubric.judgment) and any(j.maps_to for j in rubric.judgment), "some questions map to rules, some must be uncatalogued")

_base = {"meta": {"version": "x"}, "weights": {"c": 1.0}, "grades": {"A": 90, "F": 0},
         "penalties": {"P0": 25, "P1": 12, "P2": 6, "P3": 2},
         "rules": [{"id": "R-1", "category": "c", "title": "t", "metric": "layout.overflow_x", "fail_when": "true",
                    "severity": "P0", "standard": "s", "fix_template": "f", "owner": "app", "mockup": "none"}]}


def _refused(entries: list, needle: str, label: str) -> None:
    data = json.loads(json.dumps(_base))
    data["judgment"] = entries
    try:
        rb.validate_rubric(data)
        check(False, f"rubric validation refuses {label}")
    except rb.RubricError as exc:
        check(needle in str(exc), f"rubric validation refuses {label}, naming it ({exc})")


_refused([{"id": "J-01", "question": "q"}, {"id": "J-01", "question": "q"}], "duplicate", "a duplicate judgment id")
_refused([{"id": "Q-1", "question": "q"}], "J-01", "a judgment id not shaped J-NN")
_refused([{"id": "J-01", "question": "q", "maps_to": ["R-9"]}], "R-9", "maps_to naming an unknown rule")
_refused([{"id": "J-01", "question": "q", "screens": "modals"}], "modals", "an unknown screens scope")
_refused([{"id": "J-01", "question": ""}], "question", "an empty question")
_refused([{"id": "J-01", "question": "q", "maps_to": "R-1"}], "list", "maps_to that is not a list")
data = json.loads(json.dumps(_base))
data["judgment"] = [{"id": "J-01", "question": "q", "maps_to": ["R-1"], "screens": "tabs"}]
loaded = rb.validate_rubric(data)
check(len(loaded.judgment) == 1 and loaded.judgment[0].maps_to == ["R-1"] and loaded.judgment[0].screens == "tabs", "a well-formed entry loads")
check(rb.validate_rubric(_base).judgment == [], "a rubric without [[judgment]] loads with an empty checklist")

# ---- the prompt: deterministic, bounded to the run's own inputs --------------

run_dir = Path(tempfile.mkdtemp(prefix="design-judgment-run-")) / "20260922T000001Z"
run_dir.mkdir(parents=True)
(run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
prompt = jm.judge_prompt(metrics, run_dir, rubric)
check(prompt == jm.judge_prompt(metrics, run_dir, rubric), "the prompt is byte-identical for the same inputs")
for j in rubric.judgment:
    check(j.id in prompt and j.question in prompt, f"{j.id}: id + question in the prompt")
for s in metrics["screens"]:
    check(s["id"] in prompt and str(run_dir / "shots" / s["screenshot"]) in prompt, f"{s['id']}: screen id + local screenshot path in the prompt")
    if s.get("screenshot_full"):
        check(str(run_dir / "shots" / s["screenshot_full"]) in prompt, f"{s['id']}: full-page path in the prompt")
check(str(run_dir / "metrics.json") in prompt, "the metrics.json path is in the prompt")
check("evaluate.json" not in prompt and "report.html" not in prompt and "20260922T000000Z" not in prompt,
      "the prompt never names evaluate.json, report.html or another run")
check("rejected" in prompt and '"answers"' in prompt and '"uncatalogued"' in prompt and '"proposed"' in prompt,
      "the prompt carries the output schema and the free-form-rejected rule")
check(not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", prompt), "no timestamp in the prompt")
check(all(f"`{m}`" in prompt for j in rubric.judgment for m in j.maps_to), "every rule id a question may map to is listed with its title")
check(len(jm.screen_rows(metrics)) == len(metrics["screens"]), "screen_rows lists every measured screen with a screenshot")
no_shot = json.loads(json.dumps(metrics))
no_shot["screens"][0]["screenshot"] = None
check(len(jm.screen_rows(no_shot)) == len(metrics["screens"]) - 1, "a screen without a screenshot is not offered as evidence")

# ---- parse_payload -------------------------------------------------------------

obj, err = jm.parse_payload('```json\n{"answers": []}\n```')
check(obj == {"answers": []} and err is None, "a fenced JSON object is tolerated")
obj, err = jm.parse_payload("Sure! Here is my judgment: {}")
check(obj is None and err and "not a single JSON object" in err, "prose around the object is rejected")
obj, err = jm.parse_payload("")
check(obj is None and err == "empty reply", "an empty reply is rejected")

# ---- validate_answers: all-or-nothing ----------------------------------------

good, errs = jm.validate_answers(ok_payload, rubric, metrics)
check(not errs and good["status"] == "ok" and [a["id"] for a in good["answers"]] == SEED_IDS, f"a complete valid payload validates: {errs}")
check(good["answers"][2]["maps_to"] == ["LAYOUT-04"] and good["answers"][3]["maps_to"] == [] and good["answers"][8]["answer"] == "na"
      and good["answers"][8]["evidence"] is None and good["answers"][0]["question"] == rubric.judgment[0].question,
      "answers carry the question text, the mapped rule ids and a null evidence on na")
check(len(good["uncatalogued"]) == 1 and good["uncatalogued"][0]["question"] == "J-04"
      and good["uncatalogued"][0]["proposed"]["metric"] == "layout.side_by_side_blocks", "the uncatalogued entry is kept with its proposed rule")
check(jm.answer_counts(good) == (7, 2, 1), f"answer counts yes/no/na: {jm.answer_counts(good)}")

bad, errs = jm.validate_answers(bad_payload, rubric, metrics)
check(bad["status"] == "unmeasured" and bad["answers"] == [] and bad["uncatalogued"] == [] and bad["errors"] == errs and len(errs) >= 8,
      f"a malformed payload is unmeasured with no partial answers ({len(errs)} errors)")
joined = "\n".join(errs)
for needle, label in (("unknown top-level keys ['commentary']", "free-form commentary key"),
                      ("J-01: answered more than once", "duplicate id"),
                      ("J-02: answer 'maybe'", "answer outside the enum"),
                      ("J-03: evidence 'nope-screen'", "evidence that is not a screen id"),
                      ("J-04: answered 'no' with no maps_to and no uncatalogued", "an unbacked no"),
                      ("J-05: maps_to ['TYPE-01'] not in the question's list", "maps_to outside the question's rules"),
                      ("unanswered: J-10", "a missing seed id"),
                      ("names J-06 which was answered 'yes'", "uncatalogued on a non-no answer"),
                      ("severity 'P9'", "bad uncatalogued severity"),
                      ("owner 'nobody'", "bad uncatalogued owner"),
                      ("proposed.metric and proposed.threshold are required", "missing proposed rule")):
    check(needle in joined, f"error listed for {label}: {needle}")
check(bad["reason"].startswith(f"{len(errs)} schema violations:"), f"the reason counts the violations: {bad['reason']}")

for payload, label in (("free text", "a string payload"), (["a"], "a list payload"), ({}, "an empty object"), (None, "None")):
    d, e = jm.validate_answers(payload, rubric, metrics)
    check(d["status"] == "unmeasured" and e and d["answers"] == [], f"{label} is unmeasured, never partially accepted")
compliant = json.loads((FIX / "metrics_compliant.json").read_text(encoding="utf-8"))
dlg = json.loads(json.dumps(ok_payload))
dlg["answers"][0]["evidence"] = "desktop-light-dialog-edit"
d, e = jm.validate_answers(dlg, rubric, compliant)
check(d["status"] == "unmeasured" and any("outside the question's scope (tabs)" in x for x in e), "evidence outside a tabs question's scope is refused")
d, e = jm.validate_answers(ok_payload, rubric, compliant)
check(d["status"] == "ok", "the same payload validates against the compliant fixture (its screens are a superset)")
yes_ev = json.loads(json.dumps(ok_payload))
yes_ev["answers"][0]["evidence"] = None
d, e = jm.validate_answers(yes_ev, rubric, metrics)
check(d["status"] == "unmeasured" and any("evidence is required for a 'yes'" in x for x in e), "a yes without evidence is refused")
empty_rubric = rb.validate_rubric(_base)
d, e = jm.validate_answers(ok_payload, empty_rubric, metrics)
check(d["status"] == "unmeasured" and "no [[judgment]] entries" in e[0], "a rubric without a checklist cannot be judged")

# ---- merge_judges --------------------------------------------------------------

second = json.loads(json.dumps(good))
merged = jm.merge_judges([good, second])
check(merged["status"] == "ok" and merged["judges"] == 2 and [a["answer"] for a in merged["answers"]] == [a["answer"] for a in good["answers"]]
      and len(merged["uncatalogued"]) == 1 and merged["disagreements"] == [], "two agreeing judges merge to ok with the finding kept")
second["answers"][1]["answer"] = "no"
second["answers"][1]["maps_to"] = ["TOUCH-03"]
second["uncatalogued"][0]["title"] = "SIDE-BY-SIDE cards, at phone width!"
second["uncatalogued"].append({"question": "J-04", "title": "Only judge two saw this", "severity": "P3", "detail": "x", "owner": "app",
                               "proposed": {"metric": "m", "threshold": "t"}})
merged = jm.merge_judges([good, second])
check(merged["status"] == "not_confirmed" and merged["answers"][1]["answer"] == "not confirmed" and merged["answers"][1]["evidence"] is None
      and merged["disagreements"] == [{"id": "J-02", "answers": ["yes", "no"]}], f"a disagreement is listed as not confirmed: {merged['disagreements']}")
check([a["answer"] for a in merged["answers"] if a["id"] != "J-02"] == [a["answer"] for a in good["answers"] if a["id"] != "J-02"],
      "the other nine agreed answers are kept")
check(len(merged["uncatalogued"]) == 1 and merged["uncatalogued"][0]["title"] == "Side-by-side cards at phone width",
      "uncatalogued kept only when titles match after normalisation; the first judge's wording wins")
check(jm.normalise_title("SIDE-BY-SIDE cards, at phone width!") == jm.normalise_title("Side-by-side cards at phone width"), "title normalisation folds case and punctuation")
third = json.loads(json.dumps(good))
third["uncatalogued"] = [{"question": "J-04", "title": "Completely different wording", "severity": "P3", "detail": "y", "owner": "app",
                          "proposed": {"metric": "m", "threshold": "t"}}]
m3 = jm.merge_judges([good, third])
check(m3["status"] == "ok" and len(m3["uncatalogued"]) == 1 and m3["uncatalogued"][0]["title"] == "Side-by-side cards at phone width",
      "an uncatalogued finding on the same question survives a different title (an agreed unmapped no keeps its finding)")
third["uncatalogued"][0]["question"] = "J-03"
third["answers"][2]["maps_to"] = []
m4 = jm.merge_judges([good, third])
check(m4["uncatalogued"] == [], "a finding neither title- nor question-matched by the other judge is dropped")
merged = jm.merge_judges([good, bad])
check(merged["status"] == "unmeasured" and merged["judges"] == 2 and merged["reason"].startswith("judge 2 unmeasured")
      and all(x.startswith("judge 2: ") for x in merged["errors"]) and merged["answers"] == [], "an unmeasured judge makes the merge unmeasured")
check(jm.merge_judges([good])["judges"] == 1 and jm.merge_judges([good])["status"] == "ok", "one judge passes through")
check(jm.merge_judges([])["status"] == "unmeasured", "no judge is unmeasured")
check(jm.answer_counts(jm.merge_judges([good, second])) == (6, 2, 1), "not-confirmed answers count in no bucket")

# ---- the judgment never moves a grade; the report renders it -----------------

out = ev.evaluate(metrics, rubric, specs)
before = json.dumps({"categories": out["categories"], "overall": out["overall"], "rules": out["rules"]}, sort_keys=True)
with_j = json.loads(json.dumps(out))
with_j["judgment"] = good
page = report.render_report(with_j)
after = json.dumps({"categories": with_j["categories"], "overall": with_j["overall"], "rules": with_j["rules"]}, sort_keys=True)
check(before == after and report.report_summary(with_j)["score"] == out["overall"]["score"] and report.report_summary(with_j)["grade"] == out["overall"]["grade"],
      "grades, scores and rule results are unchanged with a judgment present")
check('id="judgment"' in page and "status: ok" in page and "outside the grade" in page and "Side-by-side cards at phone width" in page
      and "proposed rule: layout.side_by_side_blocks" in page and 'class="answer-no"' in page and "LAYOUT-04" in page,
      "the report renders the answers, the maps_to column, the uncatalogued finding and its proposed rule")
check(f"{out['overall']['score']}" in page and '<span class="grade grade-F">F</span>' in page, "the page still prints the evaluate document's grade")
with_j["judgment"] = bad
page_u = report.render_report(with_j)
check("status: unmeasured" in page_u and "schema violation" in page_u and "No checklist answers." in page_u, "an unmeasured judgment renders its reason and no answers")
with_j["judgment"] = jm.merge_judges([good, second])
page_n = report.render_report(with_j)
check("status: not_confirmed" in page_n and 'class="answer-not-confirmed">not confirmed' in page_n, "a not-confirmed merge renders as such")

# ---- CLI: judge-prompt / judge-merge / render --------------------------------

PY = sys.executable
common = ["--rubric", str(RUBRIC), "--spec", str(FIX / "spec_violating.md"), "--spec-dark", str(FIX / "spec_violating.md")]
env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(PKG), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)


def _kv(proc: subprocess.CompletedProcess) -> dict:
    return dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)


p1 = _run("judge-prompt", str(run_dir), "--rubric", str(RUBRIC))
k1 = _kv(p1)
check(p1.returncode == 0 and k1.get("PROMPT") == str(run_dir / "judge-prompt.md") and k1.get("SCREENS") == "2" and k1.get("QUESTIONS") == "10"
      and (run_dir / "judge-prompt.md").read_text(encoding="utf-8") == prompt,
      f"judge-prompt writes <run_dir>/judge-prompt.md and prints PROMPT/SCREENS/QUESTIONS ({p1.stdout[-200:]}{p1.stderr[-200:]})")
p0 = _run("evaluate", str(run_dir / "metrics.json"), "--out", str(run_dir / "evaluate.json"), *common)
check(p0.returncode == 0, f"evaluate --out into the run dir ({p0.stderr[-200:]})")
ev_before = json.loads((run_dir / "evaluate.json").read_text(encoding="utf-8"))
p2 = _run("judge-merge", str(run_dir), str(FIX / "judge_answers_ok.json"), "--rubric", str(RUBRIC))
k2 = _kv(p2)
check(p2.returncode == 0 and k2.get("JUDGMENT") == "ok" and k2.get("ANSWERS") == "7/2/1" and k2.get("UNCATALOGUED") == "1" and k2.get("ERRORS") == "0"
      and k2.get("EVALUATE") == str(run_dir / "evaluate.json"), f"judge-merge validates, merges and prints its lines ({p2.stdout}{p2.stderr[-200:]})")
ev_after = json.loads((run_dir / "evaluate.json").read_text(encoding="utf-8"))
check(ev_after.get("judgment", {}).get("status") == "ok" and len(ev_after["judgment"]["answers"]) == 10, "judgment written into evaluate.json")
check("RUBRIC_MISMATCH=" not in p2.stdout, "no rubric mismatch line when evaluate.json and the checklist share a version")
stale = json.loads((run_dir / "evaluate.json").read_text(encoding="utf-8"))
stale["rubric_version"] = "0.9.0"
(run_dir / "evaluate.json").write_text(json.dumps(stale), encoding="utf-8")
p2b = _run("judge-merge", str(run_dir), str(FIX / "judge_answers_ok.json"), "--rubric", str(RUBRIC))
check(p2b.returncode == 0 and "RUBRIC_MISMATCH=evaluate:0.9.0 judgment:1.3.0" in p2b.stdout and _kv(p2b).get("JUDGMENT") == "ok",
      f"an evaluate.json scored under another rubric version is named, never silently reused ({p2b.stdout[-200:]})")
(run_dir / "evaluate.json").write_text(json.dumps(ev_after), encoding="utf-8")
check(all(ev_before[k] == ev_after[k] for k in ev_before) and set(ev_after) - set(ev_before) == {"judgment"},
      "judge-merge adds only the judgment key; every other key of evaluate.json is byte-equal")
p3 = _run("render", str(run_dir / "evaluate.json"))
k3 = _kv(p3)
check(p3.returncode == 0 and k3.get("JUDGMENT") == "ok" and k3.get("GRADE") == ev_before["overall"]["grade"] and k3.get("SCORE") == str(ev_before["overall"]["score"])
      and 'id="judgment"' in (run_dir / "report.html").read_text(encoding="utf-8"), f"render picks the judgment up unchanged and the grade line is the same ({p3.stdout[-300:]})")
p4 = _run("judge-merge", str(run_dir), str(FIX / "judge_answers_malformed.json"), "--rubric", str(RUBRIC))
k4 = _kv(p4)
check(p4.returncode == 0 and k4.get("JUDGMENT") == "unmeasured" and k4.get("ANSWERS") == "0/0/0" and int(k4.get("ERRORS", "0")) >= 8
      and "ERROR_DETAIL=" in p4.stdout, f"judge-merge on the malformed fixture reports unmeasured with the errors, exit 0 ({p4.stdout[-300:]})")
ev_bad = json.loads((run_dir / "evaluate.json").read_text(encoding="utf-8"))
check(ev_bad["judgment"]["status"] == "unmeasured" and ev_bad["overall"] == ev_before["overall"] and ev_bad["categories"] == ev_before["categories"],
      "an unmeasured judgment is written and the grades are still untouched")
two = run_dir / "judge-2.json"
alt = json.loads(json.dumps(ok_payload))
alt["answers"][1]["answer"] = "no"
alt["answers"][1]["maps_to"] = ["TOUCH-03"]
two.write_text(json.dumps(alt), encoding="utf-8")
p5 = _run("judge-merge", str(run_dir), str(FIX / "judge_answers_ok.json"), str(two), "--rubric", str(RUBRIC))
k5 = _kv(p5)
check(p5.returncode == 0 and k5.get("JUDGMENT") == "not_confirmed" and k5.get("ANSWERS") == "6/2/1" and "NOT_CONFIRMED=J-02:yes/no" in p5.stdout,
      f"judge-merge with two judges lists the disagreement ({p5.stdout[-300:]})")
prose = run_dir / "judge-prose.json"
prose.write_text("I think the app looks fine overall.\n{\"answers\": []}", encoding="utf-8")
p6 = _run("judge-merge", str(run_dir), str(prose), "--rubric", str(RUBRIC))
check(p6.returncode == 0 and _kv(p6).get("JUDGMENT") == "unmeasured" and "not a single JSON object" in p6.stdout, "a prose reply is unmeasured, exit 0")
p7 = _run("judge-merge", str(run_dir), str(run_dir / "missing.json"), "--rubric", str(RUBRIC))
check(p7.returncode == 2 and p7.stdout.startswith("ERROR="), "a missing answers file exits 2")
p8 = _run("judge-prompt", str(run_dir / "nowhere"))
check(p8.returncode == 2 and p8.stdout.startswith("ERROR="), "judge-prompt on a dir without metrics.json exits 2")
fresh = run_dir.parent / "fresh"
fresh.mkdir()
(fresh / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
p9 = _run("judge-merge", str(fresh), str(FIX / "judge_answers_ok.json"), *common)
check(p9.returncode == 0 and (fresh / "evaluate.json").is_file() and json.loads((fresh / "evaluate.json").read_text(encoding="utf-8"))["judgment"]["status"] == "ok",
      "judge-merge evaluates metrics.json first when evaluate.json is absent")

_h.report_and_exit("test_design_judgment", skip_code=SKIP_EXIT)
