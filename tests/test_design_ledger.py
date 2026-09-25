"""Unit tests for skills/_lib/design_review's ledger, filing and fleet legs (fleet-config#974).

Pure-logic, no browser, no `gh`: the ledger entry shape (ids, statuses,
grades, stamps — never a screenshot path, captured text or a judge's
prose), record / trim / previous, the rule-id diff (identical documents ->
all unchanged; one flipped rule -> exactly that id fixed or regressed; an
unmeasured side -> never fixed or regressed; a new failing id -> new; a
rubric change noted), the report's diff slot, `[[design.accepted]]` rule
entries (suppression, unmatched, `design_lint` leaves them alone), owner
routing (spec / scaffold never on the app), the issue-body merge
(checkbox preserved, fixed tag, identical rerun reports no change, nothing
captured in the body), the dry-run upsert against a fake `gh` runner, the
fleet digest (a rule failing in two apps promoted once, not filed twice;
an accepted app excluded), and the `ledger` / `file` / `fleet` CLI legs
against fixtures (two dead-port apps -> unmeasured, never started).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_ledger.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import re
import shutil
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

import audit_issue as ai  # noqa: E402
from design_lint import accepted as lint_accepted  # noqa: E402
from design_review import evaluate as ev, filing, fleet, ledger, report, rubric as rb  # noqa: E402

_h = CheckHarness()
check = _h.check

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"
PKG = REPO / "skills" / "_lib" / "design_review"
PY = sys.executable
STATE = Path(tempfile.mkdtemp(prefix="design-ledger-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
WORK = Path(tempfile.mkdtemp(prefix="design-ledger-test-work-"))


def _specs(name: str) -> dict:
    return rb.load_specs(FIX / f"spec_{name}.md", FIX / f"spec_{name}.md")


def _doc(name: str, target: str = "fixture-app", run_id: str = "20260922T000001Z") -> dict:
    metrics = json.loads((FIX / f"metrics_{name}.json").read_text(encoding="utf-8"))
    metrics["target"] = target
    out = ev.evaluate(metrics, rubric, _specs(name))
    out["run_dir"] = str(STATE / "design-review" / target / run_id)
    return out


def _clone(doc: dict) -> dict:
    return json.loads(json.dumps(doc))


def _flip(doc: dict, rid: str, status: str) -> dict:
    out = _clone(doc)
    r = next(x for x in out["rules"] if x["id"] == rid)
    r["status"] = status
    if status != "fail":
        r["evidence"] = []
    return out


rubric = rb.load_rubric(RUBRIC)
violating = _doc("violating")
compliant = _doc("compliant")
# JSON documents: no evidence/judge keys; rendered text: no screenshot path, no quoted page text.
FORBIDDEN = re.compile(r"shots[/\\]|\.png|\"note\"|\"detail\"|\"items\"|\"text\"|\"screenshot|“|”", re.I)
N_FAIL = sum(1 for r in ev.evaluate(json.loads((FIX / "metrics_violating.json").read_text(encoding="utf-8")),
                                     rb.load_rubric(RUBRIC), rb.load_specs(FIX / "spec_violating.md", FIX / "spec_violating.md"))["rules"] if r["status"] == "fail")

# ---- ledger entry: shape and what it never carries ---------------------------

entry = ledger.entry_from_doc(violating, "20260922T000001Z", live=None)
check(sorted(entry) == ["categories", "commit", "generated_at", "judgment_rubric_version", "live_build", "mode", "overall",
                        "rubric_version", "rules", "run_id", "uncatalogued"], f"entry keys as decided: {sorted(entry)}")
check(entry["mode"] == "live", "a document without a mode is a live run")
check(entry["rules"] and all(v in ("pass", "fail", "unmeasured") for v in entry["rules"].values())
      and len(entry["rules"]) == len(rubric.rules), "rules{id: status} covers every rubric rule")
check(entry["overall"] == {"score": violating["overall"]["score"], "grade": violating["overall"]["grade"]}, "overall carries score + grade only")
check(set(entry["categories"]) == set(rubric.categories) and all(isinstance(g, str) for g in entry["categories"].values()), "categories{name: grade}")
check(entry["commit"] == violating["commit"] and entry["live_build"] is None and entry["judgment_rubric_version"] is None,
      "commit from the envelope, live_build null when not fetched, no judgment -> null")
check(not FORBIDDEN.search(json.dumps(entry)), "the entry carries no screenshot path, captured text, evidence item or judge prose")

judged = _clone(violating)
judged["judgment"] = {"status": "ok", "rubric_version": "9.9.9", "judges": 1, "answers": [], "errors": [], "disagreements": [],
                      "uncatalogued": [{"question": "J-04", "title": "Danger, Button!!", "severity": "P1", "detail": "the page shows a big red button next to the row title",
                                        "owner": "app", "proposed": {"metric": "x", "threshold": 1}}]}
e2 = ledger.entry_from_doc(judged, "r")
check(e2["judgment_rubric_version"] == "9.9.9" and e2["uncatalogued"] == [{"question": "J-04", "title_norm": "danger button", "severity": "P1", "owner": "app"}],
      f"uncatalogued recorded as question + normalised title + severity + owner, never detail: {e2['uncatalogued']}")
judged["judgment"]["status"] = "not_confirmed"
check(ledger.entry_from_doc(judged, "r")["uncatalogued"] == [], "uncatalogued recorded only from a status-ok judgment")

# ---- record / trim / previous --------------------------------------------------

for i in range(1, 24):
    d = _clone(violating)
    rid = f"20260922T{i:06d}Z"
    d["run_dir"] = str(STATE / "design-review" / "fixture-app" / rid)
    ledger.record(Path(d["run_dir"]), d)
entries = ledger.load("fixture-app")
check(len(entries) == ledger.LEDGER_KEEP and entries[0]["run_id"] == "20260922T000004Z" and entries[-1]["run_id"] == "20260922T000023Z",
      f"the last {ledger.LEDGER_KEEP} entries are kept, oldest dropped first ({len(entries)}: {entries[0]['run_id']}..{entries[-1]['run_id']})")
check(ledger.ledger_path("fixture-app") == STATE / "design-review" / "fixture-app" / "ledger.json" and ledger.ledger_path("fixture-app").is_file(),
      "ledger.json lives under <state>/design-review/<target>/")
d = _clone(violating)
d["run_dir"] = str(STATE / "design-review" / "fixture-app" / "20260922T000023Z")
ledger.record(Path(d["run_dir"]), d, live="abc123")
entries = ledger.load("fixture-app")
check(len(entries) == ledger.LEDGER_KEEP and entries[-1]["live_build"] == "abc123", "re-recording the same run_id replaces its entry (idempotent)")
check(ledger.previous("fixture-app", "20260922T000023Z")["run_id"] == "20260922T000022Z", "previous(before_run_id) is the entry just before it")
check(ledger.previous("fixture-app", "20260922T999999Z")["run_id"] == "20260922T000023Z", "an unrecorded run compares against the latest earlier entry")
check(ledger.previous("fixture-app", "20260101T000000Z") is None, "a run older than every entry has no previous run — never a later one")
check(ledger.previous("fixture-app", None)["run_id"] == "20260922T000023Z" and ledger.previous("never-seen") is None, "previous without a run id = latest; unknown target = None")
check(not FORBIDDEN.search(ledger.ledger_path("fixture-app").read_text(encoding="utf-8")), "the ledger file carries nothing captured")

# ---- #995: a synthetic run only ever compares with synthetic runs -------------

for rid, mode in (("20260923T000001Z", "synthetic"), ("20260923T000002Z", None), ("20260923T000003Z", "synthetic")):
    d = _clone(violating)
    d["run_dir"] = str(STATE / "design-review" / "modes-app" / rid)
    d["target"] = "modes-app"
    if mode:
        d["mode"] = mode
    ledger.record(Path(d["run_dir"]), d)
check(ledger.previous("modes-app", "20260923T999999Z")["run_id"] == "20260923T000002Z",
      "a live run's previous run skips synthetic entries (#995)")
check(ledger.previous("modes-app", "20260923T999999Z", "synthetic")["run_id"] == "20260923T000003Z"
      and ledger.previous("modes-app", "20260923T000003Z", "synthetic")["run_id"] == "20260923T000001Z",
      "a synthetic run's previous run is the latest earlier synthetic one (#995)")
check(ledger.previous("modes-app", "20260923T000002Z") is None, "no earlier live run -> nothing to compare, never a synthetic one")

# ---- diff semantics --------------------------------------------------------------

base = ledger.entry_from_doc(violating, "prev")
same = ledger.diff(violating, base)
check(same["previous_run"] == "prev" and not same["fixed"] and not same["regressed"] and not same["new"] and not same["unmeasured"]
      and sorted(same["unchanged"]) == sorted(r["id"] for r in violating["rules"]) and same["rubric_changed"] is None,
      "identical documents -> every rule unchanged, nothing else")
one = ledger.diff(_flip(violating, "TOUCH-01", "pass"), base)
check(one["fixed"] == ["TOUCH-01"] and not one["regressed"] and not one["new"] and len(one["unchanged"]) == len(rubric.rules) - 1,
      f"one rule fail->pass flips exactly that id to fixed: {one['fixed']}")
back = ledger.diff(violating, ledger.entry_from_doc(_flip(violating, "TOUCH-01", "pass"), "prev"))
check(back["regressed"] == ["TOUCH-01"] and not back["fixed"], "pass->fail is regressed")
unm_now = ledger.diff(_flip(violating, "TOUCH-01", "unmeasured"), base)
unm_then = ledger.diff(violating, ledger.entry_from_doc(_flip(violating, "TOUCH-01", "unmeasured"), "prev"))
check(unm_now["unmeasured"] == ["TOUCH-01"] and not unm_now["fixed"] and not unm_now["regressed"]
      and unm_then["unmeasured"] == ["TOUCH-01"] and not unm_then["fixed"] and not unm_then["regressed"],
      "unmeasured on either side -> unmeasured, never fixed or regressed")
prev_short = ledger.entry_from_doc(violating, "prev")
prev_short["rules"].pop("TOUCH-01")
prev_short["rules"].pop("TYPE-01")
short = ledger.diff(_flip(violating, "TYPE-01", "pass"), prev_short)
check(short["new"] == ["TOUCH-01"] and "TYPE-01" in short["unchanged"], "an id absent before: new when it fails now, unchanged when it passes")
first = ledger.diff(violating, None)
check(first["previous_run"] is None and sorted(first["new"]) == sorted(r["id"] for r in violating["rules"] if r["status"] == "fail")
      and first["rubric_changed"] is None, "first recorded run: previous_run null, every failing rule new, no rubric note")
older = ledger.entry_from_doc(violating, "prev")
older["rubric_version"] = "1.0.0"
check(ledger.diff(violating, older)["rubric_changed"] == {"from": "1.0.0", "to": violating["rubric_version"]}, "a rubric change is noted from -> to")
check(ledger.diff_counts(one) == {"fixed": 1, "regressed": 0, "new": 0, "unchanged": len(rubric.rules) - 1, "unmeasured": 0}, "diff_counts")

# ---- the report's diff slot ------------------------------------------------------

doc_d = _flip(violating, "TOUCH-02", "unmeasured")
doc_d["diff"] = ledger.diff(doc_d, older)
page = report.render_report(doc_d)
check('id="diff"' in page and "compared against run prev" in page and "unmeasured (1)" in page and "TOUCH-02" in page
      and "rubric changed from v1.0.0 to v1.5.0" in page, "the report renders the unmeasured bucket and the rubric-change note")
doc_f = _clone(violating)
doc_f["diff"] = ledger.diff(doc_f, None)
check("first recorded run for this target" in report.render_report(doc_f), "a first run says so instead of 'compared against unknown'")
check('id="diff"' not in report.render_report(violating), "no diff slot -> no diff section (unchanged behaviour)")

# ---- [[design.accepted]] rule entries ---------------------------------------------

acc_root = WORK / "accepting-app"
acc_root.mkdir()
(acc_root / ".fleet.toml").write_text(
    'layer = "working-web"\n'
    '[[design.accepted]]\ncheck = "app-icon-family"\ntarget = "x.html"\ndetail = "d"\nreason = "lint entry, not ours"\n'
    '[[design.accepted]]\nrule = "COMP-02"\nreason = "icon steps are the upstream set"\nrecord = "https://github.com/o/r/issues/1"\n'
    '[[design.accepted]]\nrule = "NAV-02"\nreason = "  "\n'
    '[[design.accepted]]\nrule = "ZZZ-99"\nreason = "fails nowhere"\n', encoding="utf-8")
acc, probs = filing.load_accepted_rules(acc_root)
check(set(acc) == {"COMP-02", "ZZZ-99"} and acc["COMP-02"]["reason"] == "icon steps are the upstream set"
      and acc["COMP-02"]["record"] == "https://github.com/o/r/issues/1", f"rule entries loaded with reason + record: {acc}")
check(len(probs) == 1 and "NAV-02" in probs[0], f"a rule entry without a reason is a problem, not a suppression: {probs}")
lint_entries, lint_problems = lint_accepted.load_accepted(acc_root)
check(len(lint_entries) == 1 and lint_entries[0]["check"] == "app-icon-family" and lint_problems == [],
      "design_lint reads only the check entry and raises no WARN row for the rule entries")
check(filing.load_accepted_rules(WORK / "nowhere") == ({}, []) and filing.load_accepted_rules(None) == ({}, []), "no .fleet.toml -> nothing accepted, no problem")

# ---- routing by owner --------------------------------------------------------------

routed = filing.route(violating, acc)
owners = {r["id"]: r["owner"] for r in violating["rules"]}
check(all(owners[s["id"]] == "app" for s in routed["app"]) and all(owners[s["id"]] == "spec" for s in routed["spec"])
      and all(owners[s["id"]] == "scaffold" for s in routed["scaffold"]) and routed["spec"] and routed["scaffold"],
      "spec- and scaffold-owned rules are routed away from the app list")
check([s["id"] for s in routed["suppressed"]] == ["COMP-02"] and "COMP-02" not in [s["id"] for s in routed["app"]]
      and routed["unmatched"] == ["ZZZ-99"], "an accepted rule is suppressed from the app list; an accepted id failing nowhere is unmatched")
check(routed["app"] == sorted(routed["app"], key=lambda s: (report.SEVERITY_ORDER[s["severity"]], s["id"])), "app findings ordered by severity then id")
s = next(s for s in routed["app"] if s["id"] == "TOUCH-01")
check(set(s) == {"id", "severity", "owner", "title", "standard", "screens", "worst", "apps"} and s["screens"] == 2 and s["worst"].startswith("desktop-"),
      f"a summary carries ids, counts, the worst screen id and the standard only: {sorted(s)}")
promoted = filing.route(violating, {}, promoted={"TOUCH-01"})
check([s["id"] for s in promoted["promoted"]] == ["TOUCH-01"] and "TOUCH-01" not in [s["id"] for s in promoted["app"]], "a promoted id leaves the app list")
check(filing.route(violating, {"TOUCH-01": {"reason": "r", "record": None}}, promoted={"TOUCH-01"})["promoted"] == [], "accepted wins over promoted")

# ---- issue-body merge ----------------------------------------------------------------

fb1 = filing.file_body(violating, "20260922T000001Z", acc_root, "", today="2026-09-22")
body1 = fb1["body"]
line = next(l for l in body1.splitlines() if "**TOUCH-01**" in l)
check(line.startswith("- [ ] **TOUCH-01** (P0, app) — ") and " — 2 screens, worst desktop-" in line and " · Apple HIG 44x44pt" in line,
      f"finding line format: {line}")
check("- **COMP-02** (P3, app)" in body1 and "accepted: icon steps are the upstream set (https://github.com/o/r/issues/1)" in body1
      and "- [ ] **COMP-02**" not in body1, "an accepted rule sits under Accepted, never under Findings")
check(not any(f"**{s['id']}**" in body1 for s in routed["spec"] + routed["scaffold"]), "no spec/scaffold-owned line on the app body")
check("## Review run log" in body1 and "run 20260922T000001Z" in body1 and "accepted: COMP-02" in body1 and "owned elsewhere:" in body1
      and "accepted but not failing: ZZZ-99" in body1, "the run log names the run, the accepted ids and what is owned elsewhere")
check(not FORBIDDEN.search(body1), "the body carries no screenshot path, captured text sample or judge prose")
check(fb1["changed"] is True, "a fresh body is a change")

ticked = body1.replace("- [ ] **A11Y-01**", "- [x] **A11Y-01**")
fixed_doc = _flip(violating, "TOUCH-01", "pass")
fixed_doc["diff"] = ledger.diff(fixed_doc, ledger.entry_from_doc(violating, "20260922T000001Z"))
fb2 = filing.file_body(fixed_doc, "20260922T000002Z", acc_root, ticked, today="2026-09-23")
body2 = fb2["body"]
check("- [x] **A11Y-01**" in body2, "a ticked line keeps its checkbox across runs")
t1 = next(l for l in body2.splitlines() if "**TOUCH-01**" in l)
check(t1.startswith("- [ ] **TOUCH-01**") and t1.endswith("_(fixed — passes since 2026-09-23 @ 1111111)_"), f"a rule that passes now is marked fixed in place, unticked: {t1}")
check("1 fixed · 0 regressed · 0 new" in body2 and body2.count("- 2026-") == 2, "the run log gains one dated bullet per filing and reads the diff")
fb3 = filing.file_body(fixed_doc, "20260922T000003Z", acc_root, body2, today="2026-09-24")
t2 = next(l for l in fb3["body"].splitlines() if "**TOUCH-01**" in l)
check(t2.endswith("_(fixed — passes since 2026-09-23 @ 1111111)_"), "the first fixed date is kept on later runs")
check(fb3["changed"] is False, "an unchanged app files nothing new: Findings/Uncatalogued/Accepted identical -> changed=False")
fb4 = filing.file_body(violating, "20260922T000004Z", acc_root, fb3["body"], today="2026-09-25")
t3 = next(l for l in fb4["body"].splitlines() if "**TOUCH-01**" in l)
check("_(fixed" not in t3 and t3.startswith("- [ ] **TOUCH-01** (P0, app)") and fb4["changed"] is True, "a regression drops the fixed tag and is a change")
unm_doc = _flip(violating, "TOUCH-01", "unmeasured")
t4 = next(l for l in filing.file_body(unm_doc, "r", acc_root, body1, today="2026-09-26")["body"].splitlines() if "**TOUCH-01**" in l)
check(t4.endswith("_(carried — unmeasured this run)_"), "a listed rule unmeasured this run is carried, never marked fixed")
fbp = filing.file_body(violating, "r", acc_root, body1, today="2026-09-26", promoted={"TOUCH-01"})
t5 = next(l for l in fbp["body"].splitlines() if "**TOUCH-01**" in l)
check(t5.endswith("_(carried — promoted to the fleet scaffold list this run)_") and "promoted to the fleet scaffold list: TOUCH-01" in fbp["body"],
      "a promoted rule is carried with its reason and logged")

fbj = filing.file_body(judged | {"judgment": {**judged["judgment"], "status": "ok"}}, "r", None, "", today="2026-09-22")
check("- [ ] **J-04** (P1, app) — Danger, Button!!" in fbj["body"] and "big red button" not in fbj["body"], "uncatalogued: question, severity, owner, title — never the detail")
fbj2 = filing.file_body(judged | {"judgment": {**judged["judgment"], "status": "ok"}}, "r", None,
                        fbj["body"].replace("- [ ] **J-04**", "- [x] **J-04**"), today="2026-09-23")
check("- [x] **J-04** (P1, app) — Danger, Button!!" in fbj2["body"] and fbj2["changed"] is False, "uncatalogued lines are keyed by question + normalised title and keep their checkbox")

# ---- audit_issue: the kind, the title, the dry run against a fake gh --------------------

check("design-review" in ai.KINDS and ai.title_matches("design-review: rendered findings", "design-review")
      and not ai.title_matches("audit: design-drift findings", "design-review"), "design-review is a managed kind adopted by its stable title")
check(filing.TITLE == ai.DESIGN_REVIEW_TITLE and filing.KIND == "design-review" and filing.LABEL == "design-review", "filing uses the audit_issue identity")
_calls: list = []
_orig_list_open, _orig_gh, _orig_run = ai._list_open, ai.gh, ai._run
try:
    ai._list_open = lambda repo: [{"number": 41, "title": "other", "body": "x"}, {"number": 42, "title": ai.DESIGN_REVIEW_TITLE, "body": "old"}]
    ai.gh = lambda args, **kw: (_calls.append(list(args)), "")[1]
    ai._run = lambda args: (_calls.append(list(args)), subprocess.CompletedProcess(args, 0, "", ""))[1]
    lines = ai.dry_run_lines("o/r", "design-review", filing.TITLE, body1)
    check(lines[0] == "DRY_RUN=1" and "ACTION=edit" in lines and "ISSUE=42" in lines and "DUPLICATES=none" in lines
          and lines[-1].startswith(ai.marker_for("design-review")), f"upsert --dry-run plans the edit and stamps the marker: {lines[:5]}")
    check(_calls == [], "a dry run makes no gh write (the open-issue listing is stubbed; nothing else was called)")
    got = ai.get_managed("o/r", "design-review")
    check(got["number"] == 42 and got["duplicates"] == [] and _calls and _calls[-1][:3] == ["issue", "view", "42"], "get_managed reads the managed issue body through gh")
finally:
    ai._list_open, ai.gh, ai._run = _orig_list_open, _orig_gh, _orig_run

# file_run: dry run never upserts; --file upserts once with the label
run_a = STATE / "design-review" / "fixture-app" / "20260922T000101Z"
run_a.mkdir(parents=True)
doc_a = _clone(violating)
doc_a["run_dir"] = str(run_a)
(run_a / "evaluate.json").write_text(json.dumps(doc_a), encoding="utf-8")
_ups: list = []
fetch_stub = lambda repo, kind: {"number": 7, "body": body1, "duplicates": []}  # noqa: E731
upsert_stub = lambda repo, kind, title, body, label: (_ups.append((repo, kind, title, label)), "https://x/issues/7")[1]  # noqa: E731
out = filing.file_run(run_a, "o/r", dry_run=True, root=acc_root, fetch=fetch_stub, upsert=upsert_stub, today="2026-09-22")
check(out["dry_run"] and out["url"] is None and _ups == [] and out["issue"] == 7 and (run_a / "issue-body.md").is_file()
      and out["changed"] is False, "file_run dry run: body written beside evaluate.json, no upsert, identical run -> changed=False")
out2 = filing.file_run(run_a, "o/r", dry_run=False, root=acc_root, fetch=fetch_stub, upsert=upsert_stub, today="2026-09-22")
check(_ups == [("o/r", "design-review", filing.TITLE, "design-review")] and out2["url"] == "https://x/issues/7", "file_run with --file upserts exactly once, kind + label design-review")
try:
    filing.file_run(run_a, None, dry_run=True, fetch=fetch_stub, upsert=upsert_stub)
    check(False, "no repo -> ValueError")
except ValueError as exc:
    check("--repo" in str(exc), "no resolvable repo is an error, never a guessed owner")
check(filing.slug_from_url("https://github.com/ferraroroberto/app-launcher.git") == "ferraroroberto/app-launcher"
      and filing.slug_from_url("git@github.com:o/r") == "o/r" and filing.slug_from_url("https://gitlab.com/o/r") is None, "origin url -> owner/name")

# ---- fleet digest: promotion once, accepted excluded, spec deduped -------------------------

def _review(name: str, doc: dict, root=None) -> dict:
    d = _clone(doc)
    d["target"] = name
    rules = d["rules"]
    return {"row": {"target": name, "root": str(root) if root else None, "run_id": "r1", "run_dir": "x", "probe": "listening", "probe_detail": None,
                    "unmeasured": None, "commit": d.get("commit"), "live_build": None, "overall": dict(d["overall"]),
                    "failed": sorted(r["id"] for r in rules if r["status"] == "fail"), "unmeasured_rules": 0,
                    "diff": {"fixed": 0, "regressed": 0, "new": 0, "unchanged": 0, "unmeasured": 0}, "previous_run": None,
                    "filed": [], "accepted": [], "problems": []}, "doc": d, "root": root}


app_b = _flip(_flip(violating, "TOUCH-01", "pass"), "LAYOUT-01", "pass")  # b fails the rest, passes two
reviews = [_review("app-a", violating), _review("app-b", app_b), _review("app-c", compliant)]
dig = fleet.digest(reviews, {}, "20260922T000000Z", rubric.version)
app_failing_in_both = sorted({r["id"] for r in violating["rules"] if r["status"] == "fail" and r["owner"] == "app"} & {r["id"] for r in app_b["rules"] if r["status"] == "fail"})
check(dig["promoted"] == app_failing_in_both and "TOUCH-01" not in dig["promoted"] and "TOUCH-02" in dig["promoted"],
      f"app-owned rules failing in two apps are promoted, one failing in a single app is not: {dig['promoted']}")
rows = {a["target"]: a for a in dig["apps"]}
check(rows["app-a"]["filed"] == ["LAYOUT-01", "TOUCH-01"] and rows["app-b"]["filed"] == [] and rows["app-c"]["filed"] == [],
      f"promoted rules leave every app issue; only the single-app failures stay: {rows['app-a']['filed']} / {rows['app-b']['filed']}")
sc = {s["id"]: s for s in dig["scaffold"]}
check(all(sc[rid]["promoted"] and sc[rid]["apps"] == ["app-a", "app-b"] for rid in dig["promoted"])
      and sum(1 for s in dig["scaffold"] if s["id"] == "TOUCH-02") == 1, "each promoted rule is listed once on the scaffold list with both apps")
sp = {s["id"]: s for s in dig["spec"]}
check(sp and all(s["apps"] == ["app-a", "app-b"] and not s["promoted"] for s in sp.values()) and "COLOR-01" in sp,
      f"spec-owned rules are deduped by id and list the failing apps: {list(sp)}")
check(all(sc[s]["owner"] == "scaffold" for s in sc if not sc[s]["promoted"]) and "TYPE-02" in sc, "scaffold-owned rules land on the scaffold list unpromoted")
dig2 = fleet.digest(reviews, {"app-b": {"TOUCH-02": {"reason": "accepted here", "record": None}}}, "s", rubric.version)
check("TOUCH-02" not in dig2["promoted"] and "TOUCH-02" in {a["target"]: a for a in dig2["apps"]}["app-a"]["filed"]
      and {a["target"]: a for a in dig2["apps"]}["app-b"]["accepted"] == ["TOUCH-02"],
      "an app that accepted a rule is not counted for promotion: the other app keeps it as its own finding")
check(dig["judgment"].startswith("skipped in fleet mode") and dig["filing"] == {"mode": "dry-run", "issues": {}}, "the digest says judgment was skipped; filing defaults to dry-run")
html = fleet.render_digest(dig)
check(html.startswith("<!doctype html>") and "app-a" in html and "promoted" in html and "skipped in fleet mode" in html and not FORBIDDEN.search(html),
      "the digest page renders every app and the promoted badge, nothing captured")
check(not FORBIDDEN.search(json.dumps(dig)), "fleet-digest.json carries nothing captured")

# run_fleet with a stub reviewer and stub gh legs: bodies written, nothing filed on a dry run
fixture_toml = WORK / "projects.toml"
fixture_toml.write_text(
    f'[app-a]\ncwd_prefix = "{(WORK / "app-a").as_posix()}"\nwebapp_port = 1\nbrowser_scheme = "http"\n'
    f'[app-b]\ncwd_prefix = "{(WORK / "app-b").as_posix()}"\nwebapp_port = 2\nbrowser_scheme = "http"\napi_version_path = "/api/version"\n'
    f'[not-a-web-app]\ncwd_prefix = "{(WORK / "n").as_posix()}"\n', encoding="utf-8")
check([n for n, _ in fleet.fleet_targets(fixture_toml)] == ["app-a", "app-b"], "fleet targets = every table with a webapp_port, in file order")
_fetched: list = []
_upserted: list = []
out_dir = WORK / "fleet-1"
out_dir.mkdir()
dig3 = fleet.run_fleet(rubric, _specs("violating"), ["desktop"], fixture_toml, file_issues=False, out_dir=out_dir,
                       reviewer=lambda n, t: _review(n, violating if n == "app-a" else app_b),
                       fetch=lambda r, k: (_fetched.append(r), {"number": None, "body": "", "duplicates": []})[1],
                       upsert=lambda r, k, t, b, l: (_upserted.append(r), "url")[1], today="2026-09-22")
check(_upserted == [] and (out_dir / "fleet-digest.json").is_file() and (out_dir / "fleet-digest.html").is_file()
      and (out_dir / "issue-app-a.md").is_file() and (out_dir / "issue-fleet-config.md").is_file() and (out_dir / "issue-project-scaffolding.md").is_file(),
      "a dry-run fleet writes the digest and every would-be body and upserts nothing")
check(all("no GitHub remote resolved" in v for v in dig3["filing"]["issues"].values()) and _fetched == [],
      "fixture apps with no origin remote are reported as unresolved, and no gh read happens for them")
spec_body = (out_dir / "issue-fleet-config.md").read_text(encoding="utf-8")
sc_body = (out_dir / "issue-project-scaffolding.md").read_text(encoding="utf-8")
check("- [ ] **COLOR-01** (P0, spec)" in spec_body and "apps: app-a, app-b" in spec_body and "**TOUCH-02**" not in spec_body,
      "fleet-config's body lists the spec-owned rules with the apps")
check("- [ ] **TOUCH-02** (P0, app → scaffold)" in sc_body and "fails in 2 apps: app-a, app-b" in sc_body and "**COLOR-01**" not in sc_body,
      "project-scaffolding's body lists the scaffold-owned and promoted rules")
a_body = (out_dir / "issue-app-a.md").read_text(encoding="utf-8")
check("- [ ] **TOUCH-01** (P0, app)" in a_body and "**TOUCH-02**" not in a_body.split("## Review run log")[0].split("## Findings")[1].split("##")[0],
      "app-a's body keeps only its single-app finding; the promoted rule is not filed there")

# ---- CLI legs -------------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(PKG), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(STATE)}, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kv(proc: subprocess.CompletedProcess) -> dict:
    out: dict = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k, v)
    return out


common = ("--rubric", str(RUBRIC), "--spec", str(FIX / "spec_violating.md"), "--spec-dark", str(FIX / "spec_violating.md"))
cli_target = STATE / "design-review" / "cli-app"
r1 = cli_target / "20260922T100000Z"
r1.mkdir(parents=True)
shutil.copy(FIX / "metrics_violating.json", r1 / "metrics.json")
m = json.loads((r1 / "metrics.json").read_text(encoding="utf-8"))
m["target"], m["run_dir"] = "cli-app", str(r1)
(r1 / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
p1 = _run("ledger", str(r1), "--no-live", "--projects-toml", str(fixture_toml), *common)
k1 = _kv(p1)
check(p1.returncode == 0 and k1.get("PREVIOUS") == "none" and k1.get("NEW", "").startswith(f"{N_FAIL} ") and k1.get("FIXED") == "0" and k1.get("RUBRIC_CHANGED") == "none"
      and k1.get("LEDGER") == str(cli_target / "ledger.json") and (r1 / "evaluate.json").is_file() and k1.get("LIVE_BUILD") == "unknown",
      f"ledger CLI on a first run: evaluates metrics.json, PREVIOUS=none, every failing rule new ({p1.stdout[-300:]}{p1.stderr[-300:]})")
e1 = json.loads((r1 / "evaluate.json").read_text(encoding="utf-8"))
check(e1["diff"]["previous_run"] is None and len(e1["diff"]["new"]) == N_FAIL and "unmeasured" in e1["diff"] and "rubric_changed" in e1["diff"], "the diff is written into evaluate.json")
N_APP = sum(1 for r in e1["rules"] if r["status"] == "fail" and r["owner"] == "app")
r2 = cli_target / "20260922T100100Z"
r2.mkdir()
e2 = _clone(e1)
e2["run_dir"] = str(r2)
e2.pop("diff")
rule = next(x for x in e2["rules"] if x["id"] == "TOUCH-01")
rule["status"], rule["evidence"] = "pass", []
(r2 / "evaluate.json").write_text(json.dumps(e2), encoding="utf-8")
p2 = _run("ledger", str(r2), "--no-live", "--projects-toml", str(fixture_toml))
k2 = _kv(p2)
check(p2.returncode == 0 and k2.get("PREVIOUS") == "20260922T100000Z" and k2.get("FIXED") == "1 TOUCH-01" and k2.get("REGRESSED") == "0" and k2.get("NEW") == "0"
      and k2.get("UNCHANGED") == str(len(rubric.rules) - 1), f"ledger CLI on the next run: PREVIOUS=<first run>, exactly TOUCH-01 fixed ({p2.stdout[-300:]}{p2.stderr[-300:]})")
check(len(ledger.load("cli-app")) == 2, "two entries recorded")
p3 = _run("render", str(r2 / "evaluate.json"))
check(p3.returncode == 0 and "compared against run 20260922T100000Z" in (r2 / "report.html").read_text(encoding="utf-8"), "render from evaluate.json carries the diff into the page")
p4 = _run("file", str(r2), "--repo", "o/r", "--projects-toml", str(fixture_toml), "--existing-body", str(WORK / "none.md"))
k4 = _kv(p4)
check(p4.returncode == 0 and k4.get("FILE") == "dry-run" and k4.get("ISSUE") == "none" and k4.get("URL") == "none" and k4.get("FILED", "").startswith(f"{N_APP - 1} ")
      and k4.get("SPEC") and k4.get("SCAFFOLD") and (r2 / "issue-body.md").is_file(), f"file CLI dry run: FILED/SPEC/SCAFFOLD lines, body beside evaluate.json, no URL ({p4.stdout[-400:]}{p4.stderr[-300:]})")
p5 = _run("file", str(r2), "--repo", "o/r", "--projects-toml", str(fixture_toml), "--existing-body", str(r2 / "issue-body.md"))
check(p5.returncode == 0 and _kv(p5).get("CHANGED") == "no", "file CLI over its own previous body reports CHANGED=no")
p6 = _run("file", str(r2), "--repo", "o/r", "--file", "--existing-body", str(r2 / "issue-body.md"))
check(p6.returncode == 2 and "--existing-body" in p6.stderr, "--file with the test override is refused")
p7 = _run("file", str(WORK / "no-run"))
check(p7.returncode == 2 and p7.stdout.startswith("ERROR="), "file on a missing run dir exits 2 with ERROR=")

fleet_out = WORK / "fleet-cli"
p8 = _run("fleet", "--projects-toml", str(fixture_toml), "--out-dir", str(fleet_out), "--devices", "desktop", "--dry-run", *common)
k8 = _kv(p8)
apps = [l for l in p8.stdout.splitlines() if l.startswith("APP=")]
check(p8.returncode == 0 and k8.get("APPS") == "2" and k8.get("MEASURED") == "0" and k8.get("UNMEASURED_APPS") == "app-a:NOT_LISTENING,app-b:NOT_LISTENING"
      and k8.get("JUDGMENT") == "skipped" and k8.get("FILING") == "dry-run" and k8.get("PROMOTED") == "none" and len(apps) == 2
      and all("probe=NOT_LISTENING" in a and "unmeasured=NOT_LISTENING" in a for a in apps),
      f"fleet CLI on two dead-port fixture apps: both unmeasured, judgment skipped, dry run ({p8.stdout[-500:]}{p8.stderr[-300:]})")
check((fleet_out / "fleet-digest.json").is_file() and (fleet_out / "fleet-digest.html").is_file() and k8.get("DIGEST") == str(fleet_out / "fleet-digest.json"),
      "the digest lands in --out-dir")
dj = json.loads((fleet_out / "fleet-digest.json").read_text(encoding="utf-8"))
check(all(a["unmeasured"] == "NOT_LISTENING" and a["overall"] is None and a["filed"] == [] for a in dj["apps"]) and dj["filing"]["mode"] == "dry-run",
      "each unmeasured app has an unmeasured row, no grade, nothing filed")
for name in ("app-a", "app-b"):
    ents = ledger.load(name)
    check(len(ents) == 1 and ents[0]["overall"]["grade"] is not None and all(v == "unmeasured" for v in ents[0]["rules"].values()),
          f"{name}: an unmeasured ledger entry was recorded (every rule unmeasured), the app was never started")
p9 = _run("fleet", "--projects-toml", str(fixture_toml), "--file", "--dry-run")
check(p9.returncode == 2 and "mutually exclusive" in p9.stderr, "fleet --file --dry-run is refused")

_h.report_and_exit("test_design_ledger", skip_code=SKIP_EXIT)
