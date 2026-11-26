"""Unit tests for skills/_lib/proposal_report: quadrant placement, the page's chart and filter
attributes, and the Board-ask helper's dry run, posting and no-repeat rule -- against a temp plan
file and ledger, never the live Board.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/proposal_report/test_proposal_report.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_ops  # noqa: E402
import chief_plan  # noqa: E402
from proposal_report import asks, cli, model, page  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
SAMPLE = REPO / "tests" / "fixtures" / "proposal_report" / "sample.json"
URL = "https://example.test/report"


def plan_cli_in_process(cmd: list, **_: object) -> subprocess.CompletedProcess:
    """Stand-in for the `chief_ops.py plan ask` spawn: the same CLI, run in this process."""
    check(Path(cmd[1]) == asks.CHIEF_OPS and cmd[2] == "plan", f"the ask goes through chief_ops.py plan, got {cmd[:3]}")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = chief_ops.main(cmd[2:])
    return subprocess.CompletedProcess(cmd, code, out.getvalue(), err.getvalue())


asks.subprocess.run = plan_cli_in_process


def run_cli(*argv: str) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


# ---- quadrants ----

Q = model.Quadrant
for impact, effort, want in ((5, 1, Q.QUICK_WIN), (3, 2, Q.QUICK_WIN), (3, 3, Q.BIG_BET), (5, 5, Q.BIG_BET),
                             (2, 2, Q.FILL_IN), (1, 1, Q.FILL_IN), (2, 3, Q.MONEY_PIT), (1, 5, Q.MONEY_PIT)):
    check(model.quadrant(impact, effort) == want, f"impact {impact} effort {effort} is {want}")

report = model.load(SAMPLE)
check(len(report.suggestions) == 5, "the sample loads five suggestions")

bad_docs = (
    ({"id": "r", "title": "T", "suggestions": []}, "non-empty list"),
    ({"id": "r", "title": "T", "suggestions": [{"id": "a", "title": "x", "summary": "s", "repo": "r",
                                                "category": "c", "impact": 6, "effort": 1}]}, "impact"),
    ({"id": "r", "title": "T", "suggestions": [{"id": "A b", "title": "x", "summary": "s", "repo": "r",
                                                "category": "c", "impact": 1, "effort": True}]}, "effort"),
    ({"id": "R", "title": "T", "suggestions": [{"id": "a"}]}, "report.id"),
)
for doc, needle in bad_docs:
    try:
        model.parse(doc)
    except ValueError as exc:
        check(needle in str(exc), f"invalid report names {needle!r}, got {exc}")
    else:
        check(False, f"invalid report ({needle}) must be refused")
dup = json.loads(SAMPLE.read_text(encoding="utf-8"))
dup["suggestions"].append(dict(dup["suggestions"][0]))
try:
    model.parse(dup)
except ValueError as exc:
    check("used twice" in str(exc), f"a repeated id is refused, got {exc}")
else:
    check(False, "a repeated suggestion id must be refused")

# ---- page ----

html = page.render(report)
check(html.startswith("<!doctype html>") and 'name="viewport"' in html, "a full page with a viewport")
check(not re.search(r'(src|href)="https?:', html), "the page fetches nothing from outside")
check("prefers-color-scheme:dark" in html and "#0d1117" in html and "#ffffff" in html, "both themes are inlined")

dots = re.findall(r'<a class="dot" href="#(s-[a-z0-9-]+)" data-id="([a-z0-9-]+)" data-repo="([^"]+)" '
                  r'data-category="([^"]+)" data-quadrant="([a-z-]+)" data-impact="(\d)" data-effort="(\d)" '
                  r'data-cx="([\d.]+)" data-cy="([\d.]+)"', html)
check(len(dots) == 5, f"one chart dot per suggestion, got {len(dots)}")
by_id = {item.id: item for item in report.suggestions}
for anchor, item_id, repo, category, quad, impact, effort, cx, cy in dots:
    item = by_id[item_id]
    check(anchor == item.anchor and f'id="{item.anchor}"' in html, f"{item_id}: the dot links to its list row")
    check((repo, category, quad) == (item.repo, item.category, item.quadrant.value),
          f"{item_id}: the dot carries the row's filter values")
    x, y = page.point(item.impact, item.effort)
    check(abs(float(cx) - x) <= page.SPREAD + 0.1 and abs(float(cy) - y) <= page.SPREAD + 0.1,
          f"{item_id}: the dot sits in its score cell ({cx},{cy} vs {x},{y})")
    left = page.LEFT + (item.effort - 1) * page.CELL
    top = page.TOP + (model.SCORE_MAX - item.impact) * page.CELL
    check(left <= float(cx) <= left + page.CELL and top <= float(cy) <= top + page.CELL,
          f"{item_id}: the dot is inside the impact {item.impact} / effort {item.effort} cell")
shared = [(float(d[7]), float(d[8])) for d in dots if d[1] in ("cache-board", "dedupe-alerts")]
check(len(set(shared)) == 2, "two suggestions with the same scores do not cover each other")

rows = re.findall(r'<li class="item" id="(s-[a-z0-9-]+)" data-repo="([^"]+)" data-category="([^"]+)" '
                  r'data-quadrant="([a-z-]+)"', html)
check(sorted(r[0] for r in rows) == sorted(d[0] for d in dots), "the list and the chart hold the same items")
chips = set(re.findall(r'data-group="(\w+)" data-value="([^"]+)"', html))
want_chips = ({("repo", i.repo) for i in report.suggestions} | {("category", i.category) for i in report.suggestions}
              | {("quadrant", i.quadrant.value) for i in report.suggestions})
check(chips == want_chips, f"one filter chip per repo, category and occupied quadrant, got {chips}")

xss = model.parse({"id": "r", "title": "<b>T</b>", "suggestions": [
    {"id": "a", "title": "<script>x</script>", "summary": "s & t", "repo": "r", "category": "c\"",
     "impact": 1, "effort": 1}]})
xss_html = page.render(xss)
check("<script>x" not in xss_html and "&lt;script&gt;" in xss_html and 'data-category="c&quot;"' in xss_html,
      "report text is escaped in text and attributes")

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "nested" / "page.html"
    code, stdout, _ = run_cli("render", str(SAMPLE), "--out", str(out))
    check(code == 0 and out.is_file() and "items=5 quick_wins=2" in stdout, f"render writes the page ({code}, {stdout})")
    code, _, err = run_cli("render", str(Path(tmp) / "missing.json"), "--out", str(out))
    check(code == 2 and "cannot read" in err, f"a missing report is bad input ({code}, {err})")

# ---- asks ----

with tempfile.TemporaryDirectory() as tmp:
    plan, ledger = Path(tmp) / "plan.json", Path(tmp) / "ledger.json"
    common = ("--plan-file", str(plan), "--ledger", str(ledger))

    code, stdout, _ = run_cli("ask", str(SAMPLE), "--page-url", URL, "--dry-run", *common)
    check(code == 0 and stdout.count("ASK=dry-run ") == 5 and not plan.exists() and not ledger.exists(),
          f"dry run lists five asks and writes nothing ({code}, {stdout})")
    check(f"link={URL}#s-cache-board" in stdout, "each ask line carries the page link with the item anchor")

    code, stdout, _ = run_cli("ask", str(SAMPLE), "--page-url", URL, *common)
    check(code == 0 and "ASKS=posted:5 " in stdout, f"a real run posts five asks ({code}, {stdout})")
    waiting = chief_plan.load(plan)["waiting_on_roberto"]
    check(len(waiting) == 5, f"five asks on the plan, got {len(waiting)}")
    first = next(row for row in waiting if row["id"] == asks.ask_id(report.id, "cache-board"))
    check(f"{URL}#s-cache-board" in first["detail"], "the ask detail links to the suggestion's anchor")
    check([o["label"] for o in first["options"]] == ["Accept", "Decline", "Later"], "accept / decline / later")
    check(any(o.get("recommended") and o["label"] == "Accept" for o in first["options"]),
          "a quick win recommends accept")
    pit = next(row for row in waiting if row["id"] == asks.ask_id(report.id, "rewrite-sync"))
    check(any(o.get("recommended") and o["label"] == "Decline" for o in pit["options"]),
          "a money pit recommends decline")
    big = next(row for row in waiting if row["id"] == asks.ask_id(report.id, "split-index"))
    check(not any(o.get("recommended") for o in big["options"]), "a big bet recommends nothing")
    check(set(json.loads(ledger.read_text(encoding="utf-8"))) == {row["id"] for row in waiting},
          "every posted ask is in the ledger")

    code, stdout, _ = run_cli("ask", str(SAMPLE), "--page-url", URL, *common)
    check(code == 0 and "already-open:5 " in stdout and len(chief_plan.load(plan)["waiting_on_roberto"]) == 5,
          f"a re-run adds nothing while the asks are open ({stdout})")

    chief_plan.update(plan, lambda doc: doc.update(waiting_on_roberto=[]))
    code, stdout, _ = run_cli("ask", str(SAMPLE), "--page-url", URL, *common)
    check(code == 0 and "already-asked:5 " in stdout and chief_plan.load(plan)["waiting_on_roberto"] == [],
          f"answered asks are not asked again ({stdout})")

    for bad in ("file:///C:/x.html", "example.test/p", f"{URL}#top"):
        code, _, err = run_cli("ask", str(SAMPLE), "--page-url", bad, *common)
        check(code == 2 and "--page-url" in err, f"page url {bad!r} is refused ({code}, {err})")

    ledger.write_text("{not json", encoding="utf-8")
    code, _, err = run_cli("ask", str(SAMPLE), "--page-url", URL, *common)
    check(code == 2 and "ledger" in err, f"an unreadable ledger refuses instead of risking repeats ({err})")

with tempfile.TemporaryDirectory() as tmp:
    plan, ledger = Path(tmp) / "plan.json", Path(tmp) / "ledger.json"
    odd = model.parse({"id": "r", "title": "T", "suggestions": [
        {"id": "a", "title": "x", "summary": "s", "repo": "bad repo", "category": "c", "impact": 1, "effort": 1}]})
    outcome = asks.run(odd, URL, dry_run=False, plan_path=plan, ledger_path=ledger)
    check(outcome.failed and outcome.asks[0].state == asks.AskState.FAILED and outcome.asks[0].reason,
          "a refused ask is reported as failed with its reason")
    check(not ledger.exists(), "a failed ask is not recorded as asked")

long = model.parse({"id": "r", "title": "T", "suggestions": [
    {"id": "a", "title": "x", "summary": "w " * 900, "repo": "r", "category": "c", "impact": 1, "effort": 1}]})
built = asks.build(long, URL)[0]
check(len(built.detail) <= chief_plan.MAX_PROSE and built.detail.endswith(f"{URL}#s-a"),
      "a long summary is cut, never the link")
check(re.fullmatch(r"[a-z][a-z0-9-]{0,31}", built.ask_id) is not None, "the ask id fits the plan's id rule")

_h.report_and_exit("test_proposal_report")
