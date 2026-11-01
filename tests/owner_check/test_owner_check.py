"""Unit tests for skills/_lib/owner_check: the rule, the block format, the
back-fill parser and the CLI legs, with `gh` replaced by a fake -- no network
touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/owner_check/test_owner_check.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from owner_check import backfill, block, cli, github, rule  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
ENH = ["enhancement"]


# ---- rule ----

d = rule.decide(ENH, "Results: tidy the export columns", "body")
check(d.verdict == rule.NONE and d.reasons == (), f"plain enhancement, no diff: none, got {d}")

d = rule.decide(["bug"], "Dictation: mic needs two taps", "")
check(d.verdict == rule.REQUIRED and "dictation" in d.reasons[0], f"live-only title on a bug is required, got {d}")

d = rule.decide(ENH, "Docs: mention the 390px iPhone viewport", "")
check(d.verdict == rule.REQUIRED, "iPhone in the title is a real-device check")

d = rule.decide(ENH, "Fix", "Tested on iPhone 390px and the loopback 127.0.0.1 report page")
check(d.verdict == rule.NONE, f"body words never fire (only the title is matched), got {d}")

d = rule.decide(ENH, "Sign-in: renew from the app", "")
check(any("account" in r for r in d.reasons), "account word in an enhancement title fires")
check(rule.decide(["chore"], "Sign-in: renew from the app", "").verdict == rule.NONE,
      "the account clause is enhancement-only")

d = rule.decide(ENH, "Tidy", "**Owner validation:** none — nothing to try", [("added", "webapp/static/new.js")])
check(d.verdict == rule.REQUIRED and any("surface" in r for r in d.reasons),
      f"an explicit none never suppresses a diff hit, got {d}")
check(rule.decide(["chore"], "Tidy", "", [("added", "webapp/static/new.js")]).verdict == rule.NONE,
      "an added UI file only counts for enhancements")
check(rule.decide(ENH, "Tidy", "", [("modified", "webapp/static/app.js")]).verdict == rule.NONE,
      "a modified UI file is not a new surface")

d = rule.decide(["chore"], "Tidy", "", [("modified", "src/capture/wasapi_loop.py")])
check(any("live-input path" in r for r in d.reasons), "a live-input path fires for any type")

d = rule.decide(["chore"], "Tidy", "Owner validation: required — try it on the tower")
check(d.reasons == ("the issue says Owner validation: required",), f"explicit required fires alone, got {d}")

check(rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_YES).verdict == rule.NONE,
      "a bug verified in-session needs nobody")
d = rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_NO)
check(d.verdict == rule.REQUIRED and "could not verify" in d.reasons[0], "an unverifiable bug needs the owner")
d = rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_UNSTATED)
check(d.verdict == rule.REQUIRED and "did not state" in d.reasons[0], "unstated verification is never assumed")
check(rule.decide(["bug"], "Dictation: fix", "", [], rule.VERIFIED_YES).verdict == rule.REQUIRED,
      "a live-only path still needs the owner even when the lane verified what it could")
try:
    rule.decide([], "t", "", None, "maybe")
    check(False, "an unknown verification state must raise")
except ValueError:
    pass


# ---- block ----

body = block.format_block(block.VALIDATE_AFTER, "needs a real\ndevice", "open the overlay",
                          ["https://x/pr/1", "", "https://x/a"], "2026-11-01", related=["#2", "#3"])
check(body.startswith(block.MARKER + "\n**Owner check: validate after**"), f"block header, got {body!r}")
parsed = block.parse_block(body)
check(parsed is not None and parsed["kind"] == block.VALIDATE_AFTER, "round-trip kind")
check(parsed["reason"] == "needs a real device", "multi-line values collapse to one line")
check(parsed["proof"] == ["https://x/pr/1", "https://x/a"], f"proof list round-trips, got {parsed['proof']}")
check(parsed["related"] == ["#2", "#3"], "related list round-trips")
check("answer" not in parsed, "empty optional fields are left out")
check(block.parse_block("no marker here") is None, "no marker: no block")
check(block.parse_block(block.MARKER + "\n- kind: bogus\n") is None, "an unknown kind is not a block")
cleared = block.format_block(block.CLEARED, "answered", "", (), "2026-11-02", answer="works")
latest = block.latest_block(["chatter", body, "more chatter", cleared])
check(latest["kind"] == block.CLEARED and latest["answer"] == "works", "the newest block wins")
check(block.latest_block(["a", "b"]) is None, "no blocks: None")
check(block.open_kinds(["bug", "needs-owner", "needs-validation"]) == [block.VALIDATE_AFTER, block.BEFORE_START],
      "open kinds follow the labels")
try:
    block.format_block("later", "r", "t")
    check(False, "an unknown kind must raise")
except ValueError:
    pass


# ---- backfill ----

LIST = """# Back-fill list (fs#1 in a heading counts too)
Top: fs#402 copilot (#408 check) | al#10-12,#20 screen | life-os#385 voice; hub#7,#9
Others: email-archiver#108-110; robertoferraro#1,#2
"""
items = backfill.parse_audit_list(LIST)
refs = [(it.repo, it.numbers) for it in items]
check(("facilitation-suite", [402]) in refs, f"alias + the parenthesised #408 is not swallowed, got {refs}")
check(("app-launcher", [10, 11, 12, 20]) in refs, "range plus a ,#N continuation")
check(("local-llm-hub", [7, 9]) in refs, "';' splits items too")
check(("email-archiver", [108, 109, 110]) in refs, "plain range")
check(("facilitation-suite", [1]) in refs, "a ref in a heading still counts")
al = next(it for it in items if it.repo == "app-launcher")
check(al.anchor == 10 and al.related == ["#11", "#12", "#20"], "the anchor is the lowest number")
backfill.resolve_anchor(al, lambda repo, n: n in (10, 11))
check(al.anchor == 12 and al.related == ["#20"], f"leading PR numbers are dropped, got {al.numbers}")
gone = backfill.resolve_anchor(backfill.Item("x", [5], "audit"), lambda repo, n: True)
check(gone.numbers == [], "an item of only PRs has no anchor")

closed = [
    {"number": 1, "title": "Dictation: warn on silence", "body": "", "labels": ENH,
     "closedAt": "2026-10-05T10:00:00Z", "stateReason": "COMPLETED"},
    {"number": 2, "title": "Dictation: older", "body": "", "labels": ENH,
     "closedAt": "2026-09-01T10:00:00Z", "stateReason": "COMPLETED"},
    {"number": 3, "title": "Dictation: dropped", "body": "", "labels": ENH,
     "closedAt": "2026-10-05T10:00:00Z", "stateReason": "NOT_PLANNED"},
    {"number": 4, "title": "Plain tidy", "body": "", "labels": ENH,
     "closedAt": "2026-10-05T10:00:00Z", "stateReason": "COMPLETED"},
    {"number": 11, "title": "Camera: covered by the audit item", "body": "", "labels": ENH,
     "closedAt": "2026-10-05T10:00:00Z", "stateReason": "COMPLETED"},
]
ruled = backfill.rule_candidates("app-launcher", closed, "2026-10-02", "2026-11-01")
check([it.anchor for it in ruled] == [1, 11], f"window, completed-only and rule filter, got {[it.anchor for it in ruled]}")
merged = backfill.merge([backfill.Item("app-launcher", [10, 11, 12], "audit")], ruled)
check([(it.repo, it.anchor, it.source) for it in merged] == [("app-launcher", 10, "audit"), ("app-launcher", 1, "rule")],
      "a rule hit inside an audit item is dropped")


# ---- CLI against a fake gh ----

class FakeGh:
    def __init__(self, issue=None, files="", fail=None):
        self.calls = []
        self.issue = issue or {}
        self.files = files
        self.fail = fail or ()
        self.comments = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        key = " ".join(args[:2])
        if any(f in key for f in self.fail):
            raise github.GhError(f"boom: {key}")
        if key.startswith("issue view"):
            return json.dumps(self.issue)
        if key.startswith("api --paginate"):
            return self.files
        if key.startswith("issue comment"):
            path = args[args.index("--body-file") + 1]
            self.comments.append(Path(path).read_text(encoding="utf-8"))
        if key.startswith("issue list"):
            return json.dumps([{"number": 5, "title": "t", "state": "CLOSED", "url": "u",
                                "labels": [], "comments": [{"body": self.issue.get("_block", "")}]}])
        return ""

    def writes(self):
        return [c for c in self.calls if c[:2] in (["issue", "edit"], ["issue", "comment"], ["label", "create"])]


def run(argv, fake):
    original = github._gh
    github._gh = fake
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
    finally:
        github._gh = original
    return code, out.getvalue()


def issue(labels=("bug",), title="Fix crash", body="", comments=()):
    return {"number": 7, "title": title, "body": body, "state": "CLOSED", "url": "u",
            "labels": [{"name": n} for n in labels], "comments": [{"body": c} for c in comments],
            "closedByPullRequestsReferences": []}


fake = FakeGh(issue(), "modified\tsrc/app.py\n")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 0 and "OWNER_CHECK=none" in out and not fake.writes(), f"verified bug: none, no writes ({code}, {out})")

fake = FakeGh(issue(), "modified\tsrc/app.py\n")
code, out = run(["finish", "demo#7", "--pr", "9"], fake)
check(code == 2 and "APPLIED=no" in out and not fake.writes(),
      f"unstated verification is required, and without --try nothing is written ({code}, {out})")

fake = FakeGh(issue(), "modified\tsrc/app.py\n")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--try", "open it", "--proof", "https://a"], fake)
check(code == 1 and "APPLIED=yes label=needs-validation" in out, f"required + try: marked ({code}, {out})")
check([c[:2] for c in fake.writes()] == [["label", "create"], ["issue", "edit"], ["issue", "comment"]],
      f"label ensured, applied, then the block posted: {fake.writes()}")
posted = block.parse_block(fake.comments[0])
check(posted["proof"] == ["https://github.com/ferraroroberto/demo/pull/9", "https://a"],
      f"the PR url leads the proof list, got {posted['proof']}")
check("could not verify" in posted["reason"] and posted["try"] == "open it", "reason and try land in the block")

already = block.format_block(block.VALIDATE_AFTER, "r", "t")
fake = FakeGh(issue(labels=("bug", "needs-validation"), comments=(already,)), "")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--try", "x"], fake)
check(code == 1 and "APPLIED=already" in out and not fake.writes(), "an already-marked issue is not marked twice")

fake = FakeGh(issue(), "", fail=("api --paginate",))
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 3 and out.startswith("OWNER_CHECK=unknown"), f"an unreadable PR is unknown, never none ({code}, {out})")

fake = FakeGh(issue(), "", fail=("issue comment",))
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--try", "x"], fake)
check(code == 3 and "did not land" in out, f"a write that fails is unknown, not marked ({code}, {out})")

gate_block = block.format_block(block.BEFORE_START, "needs the tower mic plugged in", "plug it in, answer yes")
code, out = run(["gate", "demo#7"], FakeGh(issue(labels=("enhancement", "needs-owner"), comments=(gate_block,))))
check(code == 1 and "OWNER_GATE=blocked reason=needs the tower mic plugged in" in out and "TRY=plug it in" in out,
      f"gate blocks with reason and try ({code}, {out})")
code, out = run(["gate", "demo#7"], FakeGh(issue(labels=("enhancement", "needs-owner"))))
check(code == 1 and "no owner-check block" in out, "a bare needs-owner label still blocks")
code, out = run(["gate", "demo#7"], FakeGh(issue(labels=("enhancement",))))
check(code == 0 and out.strip() == "OWNER_GATE=clear", "no label: clear")
code, out = run(["gate", "demo#7"], FakeGh(fail=("issue view",)))
check(code == 3 and out.startswith("OWNER_GATE=unknown"), "an unreadable issue is unknown at the gate")

fake = FakeGh(issue(labels=("needs-owner", "bug")))
code, out = run(["clear", "demo#7", "--answer", "done, plugged in"], fake)
edit = next(c for c in fake.calls if c[:2] == ["issue", "edit"])
check(code == 0 and "removed=needs-owner" in out and "--remove-label" in edit, f"clear drops the label ({out})")
check(block.parse_block(fake.comments[0])["answer"] == "done, plugged in", "clear posts the answer")

fake = FakeGh()
code, out = run(["mark", "demo#7", "--kind", "before-start", "--reason", "r", "--try", "t", "--related", "#8"], fake)
check(code == 0 and "MARKED=demo#7 label=needs-owner" in out, f"mark applies the kind's label ({out})")

with tempfile.TemporaryDirectory() as tmp:
    draft = Path(tmp) / "draft.md"
    draft.write_text("What & why\n", encoding="utf-8")
    code, out = run(["classify", "--body-file", str(draft), "--title", "Dictation: x", "--label", "enhancement"], FakeGh())
    check(code == 1 and "OWNER_CHECK=required" in out and "REASON=" in out, f"classify on a draft ({out})")
    code, out = run(["classify", "--body-file", str(Path(tmp) / "missing.md")], FakeGh())
    check(code == 3 and "OWNER_CHECK=unknown" in out, "an unreadable draft is unknown")

listed = FakeGh(issue())
listed.issue["_block"] = block.format_block(block.VALIDATE_AFTER, "why", "try it", ["https://p"], "2026-11-01")
original = github._gh
github._gh = listed
try:
    result = github.list_items(["demo"])
finally:
    github._gh = original
kinds = sorted(it["kind"] for it in result["items"])
check(kinds == [block.BEFORE_START, block.VALIDATE_AFTER] and result["unknown"] == [], f"one item per label, got {result}")
va = next(it for it in result["items"] if it["kind"] == block.VALIDATE_AFTER)
nb = next(it for it in result["items"] if it["kind"] == block.BEFORE_START)
check(va["reason"] == "why" and va["proof"] == ["https://p"], "the matching block fills the item")
check(nb["block"] is False and nb["reason"] == "", "a block of another kind is never borrowed")

github._gh = FakeGh(fail=("issue list",))
try:
    result = github.list_items(["demo"])
finally:
    github._gh = original
check(result["items"] == [] and result["unknown"][0]["repo"] == "demo", "an unreadable repo is unknown, never empty")

_h.report_and_exit("test_owner_check")
