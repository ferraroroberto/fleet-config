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
from owner_check import backfill, block, cli, github, reclassify, rule  # noqa: E402

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

check(rule.decide(ENH, "Sign-in: lock out after six wrong passwords", "").verdict == rule.NONE,
      "the app's own sign-in is drivable with test credentials: no owner check")
d = rule.decide(ENH, "Calendar: Google sign-in for the import", "")
check(any("third-party account" in r for r in d.reasons), f"a third-party sign-in fires, got {d}")
check(rule.decide(ENH, "Add OAuth for the mail sync", "").verdict == rule.REQUIRED, "OAuth still fires")
check(rule.decide(ENH, "Sign up for the newsletter provider", "").verdict == rule.REQUIRED, "a sign-up still fires")
check(rule.decide(["chore"], "Add OAuth for the mail sync", "").verdict == rule.NONE,
      "the account clause is enhancement-only")

check(rule.decide(ENH, "Tidy", "", [("added", "content/pages/privacy.html")]).verdict == rule.NONE,
      "a new page is browser-provable: never an owner reason")
check(rule.decide(ENH, "Tidy", "Owner validation: required", [("added", "content/pages/privacy.html")]).verdict
      == rule.REQUIRED, "an explicit required line still fires beside a new page")
US = rule.unproven_surface
check(US(ENH, [("added", "content/pages/privacy.html")], False) == "content/pages/privacy.html",
      "a new page without proof is an unproven surface")
check(US(ENH, [("added", "webapp/static/js/views/card.js")], False) is not None, "a new view module is one")
check(US(ENH, [("added", "webapp/static/conversation-view.js")], False) is not None, "a *-view.js module is one")
check(US(ENH, [("added", "webapp/static/views/new.html")], True) is None, "a proofed new view is not")
check(US(ENH, [("added", "app/webapp/static/_vendored/sign-in/README.md"),
               ("added", "app/webapp/static/_vendored/modal/modal.html")], False) is None,
      "a vendored README or file is not a new surface")
check(US(ENH, [("added", "webapp/static/links/links-merge.js")], False) is None, "a helper script is not a new surface")
check(US(ENH, [("added", "tests/fixtures/page.html")], False) is None, "a test fixture page is not a new surface")
check(US(["chore"], [("added", "webapp/static/views/new.html")], False) is None,
      "an added UI file only counts for enhancements")
check(US(ENH, [("modified", "webapp/static/views/app.html")], False) is None, "a modified UI file is not a new surface")
check(US(ENH, None, False) is None, "no diff, no surface")
check(rule.ux_proven("- UX: VISUAL_PROOF=fresh (kept locally)") and not rule.ux_proven("VISUAL_PROOF=stale")
      and not rule.ux_proven(""), "only a fresh VISUAL_PROOF line counts as proof")

d = rule.decide(["chore"], "Tidy", "", [("modified", "src/capture/wasapi_loop.py")])
check(any("live-input path" in r for r in d.reasons), "a live-input path fires for any type")

d = rule.decide(["chore"], "Tidy", "Owner validation: required — try it on the tower")
check(d.reasons == ("the issue says Owner validation: required",), f"explicit required fires alone, got {d}")

check(rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_YES).verdict == rule.NONE,
      "a bug verified in-session needs nobody")
try:
    rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_NO)
    check(False, "'no' with no named gap must raise: a generic could-not-verify is no reason")
except ValueError:
    pass
try:
    rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_YES, gaps=[("device", "x")])
    check(False, "a gap with 'yes' must raise")
except ValueError:
    pass
for cls, phrase in rule.OWNER_CLASSES.items():
    d = rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_NO, gaps=[(cls, "the overlay opens")])
    check(d.verdict == rule.REQUIRED and d.reasons == (rule.gap_reason(cls, "the overlay opens"),)
          and phrase in d.reasons[0] and "the overlay opens" in d.reasons[0],
          f"owner-only class {cls} is required with its reason text, got {d}")
check(rule.parse_gap(" Device : the  mic level moves ") == ("device", "the mic level moves"), "a gap parses")
for bad in ("device", "looks:fine", "taste:", ""):
    try:
        rule.parse_gap(bad)
        check(False, f"gap {bad!r} must be refused")
    except ValueError:
        pass
check(rule.decide(["bug"], "Fix crash", "", [], rule.VERIFIED_SCHEDULED).verdict == rule.NONE,
      "a criterion only a later scheduled run can show is the chief's to read, not the owner's")
check(rule.decide(["bug"], "Android: nav label contrast", "", [], rule.VERIFIED_YES).verdict == rule.NONE,
      "a title word alone never asks the owner once the lane recorded in-session proof")
check(rule.decide(["bug"], "Android: nav label contrast", "", [], rule.VERIFIED_SCHEDULED).verdict == rule.NONE,
      "scheduled proof also silences a title word alone")
check(rule.decide(["chore"], "Dictation: fix", "", [("modified", "src/mic_loop.py")], rule.VERIFIED_YES).verdict
      == rule.REQUIRED, "a changed live-input path still needs the owner even when the lane verified what it could")
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


# ---- reclassify ----

def _item(reason, proof=("https://github.com/o/demo/pull/9",), block_=True):
    return {"repo": "demo", "number": 7, "ref": "demo#7", "reason": reason, "proof": list(proof), "block": block_}


def _iss(title="Lock out after six wrong passwords", labels=("enhancement",), body=""):
    return {"title": title, "labels": list(labels), "body": body}


def _re(item, issue, files=()):
    return reclassify.reclassify(item, issue, lambda pr: list(files))


v = _re(_item(backfill.BACKFILL_REASON), _iss())
check(v.state == reclassify.CLEAR and v.dropped == ["backfill"], f"a back-fill-only entry clears, got {v}")
v = _re(_item("involves an account only the owner holds (sign-in); adds a new user-facing surface "
              "(app/webapp/static/_vendored/sign-in/README.md)"), _iss(),
        [("added", "app/webapp/static/_vendored/sign-in/README.md")])
check(v.state == reclassify.CLEAR and v.dropped == ["own-sign-in", "surface"],
      f"own sign-in + vendored README clear, got {v}")
v = _re(_item("adds a new page or view no UX proof covered (content/pages/privacy.html)"), _iss(),
        [("added", "content/pages/privacy.html")])
check(v.state == reclassify.CLEAR and v.dropped == ["surface"] and "browser-provable" in reclassify.DROPPED_WORDS["surface"],
      f"a new page with no proof clears: a browser proves it, not the owner, got {v}")
v = _re(_item("the lane could not verify the work in its own session"), _iss())
check(v.state == reclassify.CLEAR and v.dropped == ["generic"], f"a generic could-not-verify clears, got {v}")
v = _re(_item("the lane did not state that it verified the work in its own session"), _iss())
check(v.state == reclassify.CLEAR and v.dropped == ["generic"], f"a generic did-not-state clears, got {v}")
v = _re(_item(rule.gap_reason("device", "the mic level moves")), _iss())
check(v.state == reclassify.KEEP, "a named gap with an owner-only class is kept as recorded")
v = _re(_item("the lane could not verify the work in its own session; needs a real device or live input (mic), "
              "which a session cannot drive"), _iss("Mic: tune the gain"))
check(v.state == reclassify.KEEP, f"a recorded device reason keeps the item beside a generic one, got {v}")
v = _re(_item("needs a real device or live input (android), which a session cannot drive"), _iss("Android: nav"))
check(v.state == reclassify.KEEP, "history never infers proof: a recorded device reason is never cleared")
v = _re(_item("a hand-written reason", proof=()), _iss())
check(v.state == reclassify.KEEP and v.kept == ["a hand-written reason"], "hand-written text is never re-derived")
v = _re(_item("needs a real device or live input (mic), which a session cannot drive", proof=()), _iss("Mic: tune"))
check(v.state == reclassify.UNKNOWN, "a rule reason with no PR to re-read is unknown, not cleared")
v = _re(_item("", block_=False), _iss())
check(v.state == reclassify.UNKNOWN, "an item with no block is unknown, not cleared")


# ---- CLI against a fake gh ----

class FakeGh:
    def __init__(self, issue=None, files="", fail=None):
        self.calls = []
        self.issue = issue or {}
        self.files = files
        self.fail = fail or ()
        self.comments = []
        self.pr_body = ""

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
        if key.startswith("api repos/") and "pulls/" in args[1]:
            return self.pr_body
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
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "scheduled"], fake)
check(code == 0 and "SCHEDULED_READ=chief" in out and "OWNER_CHECK=none" in out and not fake.writes(),
      f"a criterion only a scheduled run shows: none, the chief reads it ({code}, {out})")

fake = FakeGh(issue(labels=("enhancement",)), "added\twebapp/static/views/new.html\n")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes", "--try", "open it"], fake)
check(code == 2 and "OWNER_CHECK=refused" in out and "VISUAL_PROOF=fresh" in out and not fake.writes(),
      f"a new view without a visual proof is refused for the lane to prove, never sent to the owner ({code}, {out})")
fake = FakeGh(issue(labels=("enhancement",)), "added\twebapp/static/views/new.html\n")
fake.pr_body = "Test plan\nVISUAL_PROOF=fresh"
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 0 and "OWNER_CHECK=none" in out, f"the same view with VISUAL_PROOF=fresh in the PR is none ({code}, {out})")

for argv, what in (([], "no --verified"), (["--verified", "no"], "no with no gap"),
                  (["--verified", "no", "--gap", "looks:fine"], "an unknown class"),
                  (["--verified", "no", "--gap", "device:"], "a class with no criterion"),
                  (["--verified", "yes", "--gap", "device:x"], "a gap with yes")):
    fake = FakeGh(issue(), "modified\tsrc/app.py\n")
    code, out = run(["finish", "demo#7", "--pr", "9", *argv, "--try", "open it"], fake)
    check(code == 2 and "OWNER_CHECK=refused APPLIED=no" in out and not fake.calls,
          f"{what}: refused (exit 2) before any read or write ({code}, {out})")

fake = FakeGh(issue(), "modified\tsrc/app.py\n")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--gap", "device:the mic level moves",
                 "--try", "open it", "--proof", "https://a"], fake)
check(code == 1 and "APPLIED=yes label=needs-validation" in out, f"required + try: marked ({code}, {out})")
check([c[:2] for c in fake.writes()] == [["label", "create"], ["issue", "edit"], ["issue", "comment"]],
      f"label ensured, applied, then the block posted: {fake.writes()}")
posted = block.parse_block(fake.comments[0])
check(posted["proof"] == ["https://github.com/ferraroroberto/demo/pull/9", "https://a"],
      f"the PR url leads the proof list, got {posted['proof']}")
check(posted["reason"] == rule.gap_reason("device", "the mic level moves") and posted["try"] == "open it",
      f"the named gap and try land in the block, got {posted}")

own = block.format_block(block.VALIDATE_AFTER, "the lane could not verify the work in its own session", "t",
                         ["https://github.com/ferraroroberto/demo/pull/9"], "2026-11-06")
fake = FakeGh(issue(labels=("chore", "needs-validation"), comments=(own,)), "modified\tsrc/app.py\n")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
edit = next((c for c in fake.calls if c[:2] == ["issue", "edit"]), [])
check(code == 0 and "OWNER_CHECK=none" in out and "RETRACTED=yes label=needs-validation" in out
      and "--remove-label" in edit and block.parse_block(fake.comments[0])["kind"] == block.CLEARED,
      f"a none re-run for the same PR removes the label its earlier finish added ({code}, {out})")
other = block.format_block(block.VALIDATE_AFTER, "hand", "t", ["https://github.com/ferraroroberto/demo/pull/3"])
for comments, what in (((other,), "another PR's block"), ((), "a bare label with no block")):
    fake = FakeGh(issue(labels=("chore", "needs-validation"), comments=comments), "modified\tsrc/app.py\n")
    code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
    check(code == 0 and "RETRACTED" not in out and not fake.writes(), f"{what} is left for the Board ({code}, {out})")
fake = FakeGh(issue(labels=("chore", "needs-validation"), comments=(own,)), "modified\tsrc/app.py\n",
              fail=("issue edit",))
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 3 and "RETRACTED=unknown" in out, f"a retract that does not land is unknown ({code}, {out})")

already = block.format_block(block.VALIDATE_AFTER, "r", "t")
fake = FakeGh(issue(labels=("bug", "needs-validation"), comments=(already,)), "")
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--gap", "taste:x", "--try", "x"], fake)
check(code == 1 and "APPLIED=already" in out and not fake.writes(), "an already-marked issue is not marked twice")

fake = FakeGh(issue(), "", fail=("api --paginate",))
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 3 and out.startswith("OWNER_CHECK=unknown"), f"an unreadable PR is unknown, never none ({code}, {out})")

fake = FakeGh(issue(), "", fail=("issue comment",))
code, out = run(["finish", "demo#7", "--pr", "9", "--verified", "no", "--gap", "physical:x", "--try", "x"], fake)
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
code, out = run(["mark", "demo#7", "--kind", "before-start", "--reason", "r", "--try",
                 "plug the tower mic in, then answer yes on the Board", "--related", "#8"], fake)
check(code == 0 and "MARKED=demo#7 label=needs-owner" in out, f"mark applies the kind's label ({out})")
for vague in ("t", "TBD", "see the issue", "decide which way"):
    fake = FakeGh()
    code, out = run(["mark", "demo#7", "--kind", "before-start", "--reason", "r", "--try", vague], fake)
    check(code == 2 and out.startswith("MARKED=refused") and not fake.calls,
          f"a before-start mark with no concrete owner action ({vague!r}) is refused ({code}, {out})")
fake = FakeGh()
code, out = run(["mark", "demo#7", "--kind", "validate-after", "--reason", "r", "--try", "t"], fake)
check(code == 0, "the concrete-action bar is for before-start marks only")

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

class DisabledIssues(FakeGh):
    def __call__(self, args):
        if list(args[:2]) == ["issue", "list"]:
            raise github.GhError("the 'ferraroroberto/demo' repository has disabled issues")
        return super().__call__(args)


github._gh = DisabledIssues()
try:
    result = github.list_items(["demo"])
    closed_rows = github.closed_issues("demo")
finally:
    github._gh = original
check(result == {"items": [], "unknown": []} and closed_rows == [],
      f"a repo with Issues switched off holds no items and is not unknown, got {result}")

# ---- reclassify command ----

class ReclassFake(FakeGh):
    """One labelled backfill-only issue, plus one still-firing hand-written one."""

    def __call__(self, args):
        args = list(args)
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1]
            if label != "needs-validation":
                return "[]"
            rows = []
            for number, reason in ((5, backfill.BACKFILL_REASON), (6, "a hand-written reason")):
                text = block.format_block(block.VALIDATE_AFTER, reason, "t", ["https://github.com/ferraroroberto/demo/pull/9"],
                                          "2026-11-01")
                rows.append({"number": number, "title": "Lock out", "state": "CLOSED", "url": "u",
                             "labels": [], "comments": [{"body": text}]})
            return json.dumps(rows)
        return super().__call__(args)


fake = ReclassFake(issue(labels=("enhancement", "needs-validation")))
code, out = run(["reclassify", "--repo", "demo"], fake)
check(code == 0 and "RECLASS would-clear demo#5" in out and "RECLASS keep demo#6" in out
      and "RECLASSIFY=dry-run before=2 clear=1 keep=1 unknown=0" in out and not fake.writes(),
      f"a dry run counts and writes nothing ({code}, {out})")
fake = ReclassFake(issue(labels=("enhancement", "needs-validation")))
code, out = run(["reclassify", "--repo", "demo", "--apply"], fake)
cleared = block.parse_block(fake.comments[0]) if fake.comments else {}
check(code == 0 and "RECLASSIFY=applied before=2 clear=1 keep=1" in out and cleared.get("kind") == block.CLEARED
      and "back-fill-only" in cleared.get("answer", "") and len(fake.comments) == 1,
      f"apply clears with a public-safe cleared block ({code}, {out}, {fake.comments})")


# ---- ref parsing: bare and owner-qualified ----

for sub, extra in (("gate", []), ("mark", ["--kind", "validate-after", "--reason", "r", "--try", "open it on the tower"]),
                   ("clear", ["--answer", "ok"])):
    for ref in ("demo#7", f"{github.OWNER}/demo#7"):
        fake = FakeGh(issue(labels=("enhancement",)))
        code, out = run([sub, ref, *extra], fake)
        repos = [c[c.index("--repo") + 1] for c in fake.calls if "--repo" in c]
        check(repos and set(repos) == {f"{github.OWNER}/demo"},
              f"{sub} {ref}: gh is addressed as {github.OWNER}/demo, got {fake.calls} ({code}, {out})")
        check("could not read" not in out, f"{sub} {ref}: no read failure ({out})")

fake = FakeGh(issue(), "modified\tsrc/app.py\n")
code, out = run(["finish", f"{github.OWNER}/demo#7", "--pr", "9", "--verified", "yes"], fake)
check(code == 0 and "OWNER_CHECK=none" in out and all(
        c[c.index("--repo") + 1] == f"{github.OWNER}/demo" for c in fake.calls if "--repo" in c),
      f"finish takes the qualified ref too ({code}, {out}, {fake.calls})")

for bad in ("demo", "demo#", "#7", "demo#x", "a/b/demo#7", "/demo#7", f"{github.OWNER}/#7"):
    try:
        cli._ref(bad)
    except cli.argparse.ArgumentTypeError as exc:
        check("<repo>#<N>" in str(exc) and "<owner>/<repo>#<N>" in str(exc),
              f"malformed {bad!r} names both accepted forms, got {exc}")
    else:
        check(False, f"malformed {bad!r} must be rejected")
try:
    cli._ref("someone-else/demo#7")
except cli.argparse.ArgumentTypeError as exc:
    check("someone-else" in str(exc) and github.OWNER in str(exc), f"a foreign owner is its own error, got {exc}")
else:
    check(False, "a foreign owner must be rejected, not silently re-owned")
check(cli._ref(f"{github.OWNER}/demo#7") == ("demo", 7) and cli._ref("demo#7") == ("demo", 7),
      "both forms parse to the same bare (repo, number)")

_h.report_and_exit("test_owner_check")
