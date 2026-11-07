"""Unit tests for skills/_lib/pr_body/check.py (fleet-config#1913).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_pr_body.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from pr_body import check as pb  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

DANGER = "**Merge danger:** two-way door · blast radius: local"
CLEAN = f"## Summary\nDoes a thing.\n\n{DANGER}\n\nCloses #7\n"
DONE = [{"id": 1, "criterion": "The checker refuses each violation", "verdict": "DONE"},
        {"id": 2, "criterion": "No baseline", "verdict": "UNVERIFIABLE"}]


def rules(body: str, refs=(7,), issue: int = 7, verdicts=None, gate: str = "FRESH", allow_images: bool = False):
    refused, unconfirmed = pb.evaluate(body, closing=list(refs), issue=issue, verdicts=verdicts or [],
                                       gate_state=gate, allow_images=allow_images)
    return [r.split(":", 1)[0] for r in refused], unconfirmed


# --- a clean body passes
check(rules(CLEAN) == ([], []), "a clean body passes")

# --- closing refs: GitHub's own list must equal the branch's issue
check(rules(CLEAN, refs=())[0] == ["closing-refs"], "a PR that closes nothing and says nothing else is refused")
check(rules(CLEAN.replace("Closes #7", "Part of #7"), refs=()) == ([], []), "a Part-of PR that closes nothing passes")
check(rules(CLEAN.replace("Closes #7", "Progresses #7"), refs=()) == ([], []), "a Progresses PR that closes nothing passes")
check(rules(CLEAN.replace("Closes #7", "Part of #9"), refs=())[0] == ["closing-refs"], "a Part-of line for another issue does not excuse it")
check(rules(CLEAN, refs=(9,))[0] == ["closing-refs"], "a PR closing a different issue is refused")
check(rules(CLEAN, refs=(7, 9))[0] == ["closing-refs"], "a PR closing the branch's issue plus another is refused")
check(rules(CLEAN.replace("Closes #7", "Part of #7"), refs=(7,)) == ([], []), "GitHub's list decides, not the prose: closing the branch's issue passes")
msg = pb.evaluate(CLEAN, closing=[9], issue=7, verdicts=[], gate_state="FRESH", allow_images=False)[0][0]
check("#9" in msg and "#7" in msg and "Part of" in msg, "the closing-refs refusal names the issue closed, the expected one and the fix")

# --- images
for label, snippet in (("markdown image", "![shot](https://example.com/a.png)"),
                       ("html img", '<img src="a.png" width="300">'),
                       ("user-attachments url", "https://github.com/user-attachments/assets/abcd"),
                       ("legacy upload url", "https://user-images.githubusercontent.com/1/2.png"),
                       ("reference image", "![shot][1]")):
    check(rules(CLEAN + "\n" + snippet)[0] == ["images"], f"{label} is refused")
check(rules(CLEAN + "\n![shot](a.png)", allow_images=True) == ([], []), "a private repo can opt out of the image rule")
check(rules(CLEAN + "\nSee `![x](y)` and\n```\n<img src=x>\n```\n") == ([], []), "an image shown as code is not an image")
check(rules(CLEAN + "\nA [link](https://example.com) is fine.\n") == ([], []), "a plain link passes")

# --- attribution and session links
for label, snippet in (("session link", "https://claude.ai/code/session_01ABCdefGHI"),
                       ("bare session id", "session_01N68GfqEPvbiLux558oMndi"),
                       ("generated-with line", "🤖 Generated with [Claude Code](https://claude.com/claude-code)"),
                       ("co-author trailer", "Co-Authored-By: Claude <noreply@anthropic.com>"),
                       ("codex co-author", "co-authored-by: Codex <x@openai.com>"),
                       ("session trailer", "Claude-Session: abc")):
    check(rules(CLEAN + "\n" + snippet)[0] == ["attribution"], f"{label} is refused")
check(rules(CLEAN + "\nCo-Authored-By: Roberto <r@example.com>\n") == ([], []), "a human co-author passes")
check(rules(CLEAN + "\nThe hook blocks a `Co-Authored-By: Claude` trailer.\n") == ([], []), "an attribution quoted as code passes")

# --- merge danger absorbed from merge_danger/check.py
two = DANGER
check(pb.find_danger_line(f"## Summary\nx\n\n{two}\n\nCloses #1") == two, "a two-way line inside a body is found")
check(pb.find_danger_line("**Merge danger:** two-way door · blast radius: hooks-live") is not None, "hyphenated radius ok")
one = "**Merge danger:** one-way door · blast radius: fleet-wide · drops a table"
check(pb.find_danger_line(one) == one, "a one-way line with a reason is found")
check(pb.find_danger_line("**Merge danger:** one-way door · blast radius: fleet-wide") is None, "one-way without a reason is refused")
check(pb.find_danger_line("## Summary\nCloses #1") is None, "a body without the line is refused")
check(pb.find_danger_line("Merge danger: two-way door · blast radius: local") is None, "an unbolded line is refused")
check(pb.find_danger_line("**Merge danger:** two-way door") is None, "a line without a radius is refused")
check(pb.find_danger_line("**Merge danger:** maybe door · blast radius: local") is None, "an unknown door is refused")
check(rules(CLEAN.replace(DANGER, ""))[0] == ["merge-danger"], "a body with no danger line is refused by the checker")

# --- ticked boxes need a DONE verdict or a FRESH gate
box_done = CLEAN + "\n- [x] The checker refuses each violation\n"
check(rules(box_done, verdicts=DONE) == ([], []), "a ticked box matching a DONE criterion passes")
check(rules(CLEAN + "\n- [x] **The checker refuses each violation**.\n", verdicts=DONE) == ([], []), "matching ignores emphasis, case and trailing punctuation")
check(rules(CLEAN + "\n- [x] the checker refuses each violation -- 14 fixtures\n", verdicts=DONE) == ([], []), "a box that extends the criterion text still matches")
check(rules(box_done)[0] == ["ticked-box"], "a ticked box with no verdicts supplied is refused")
check(rules(CLEAN + "\n- [x] No baseline\n", verdicts=DONE)[0] == ["ticked-box"], "a ticked box matching only an UNVERIFIABLE criterion is refused")
check(rules(CLEAN + "\n- [x] Something never audited\n", verdicts=DONE)[0] == ["ticked-box"], "a ticked box matching nothing is refused")
check(rules(CLEAN + "\n- [ ] No baseline\n- [ ] Something never audited\n", verdicts=DONE) == ([], []), "unticked boxes are never refused")
check(rules(CLEAN + "\n- [X] Something never audited\n", verdicts=DONE)[0] == ["ticked-box"], "an uppercase X counts as ticked")
check(rules(CLEAN + "\n```\n- [x] Something never audited\n```\n", verdicts=DONE) == ([], []), "a ticked box inside a code fence is not a box")
check(rules(CLEAN + "\n- [x] Gate green: 41 checks\n", gate="FRESH") == ([], []), "a ticked gate box passes on a FRESH gate record")
check(rules(CLEAN + "\n- [x] Gate green: 41 checks\n", gate="STALE")[0] == ["ticked-box"], "a ticked gate box on a STALE record is refused")
check(rules(CLEAN + "\n- [x] Gate green: 41 checks\n", gate="MISSING")[0] == ["ticked-box"], "a ticked gate box with no record is refused")
refused, unconfirmed = rules(CLEAN + "\n- [x] Gate green: 41 checks\n", gate="UNKNOWN")
check(refused == [] and len(unconfirmed) == 1, "an unreadable gate record is NOT CONFIRMED, neither pass nor refusal")
check(rules(CLEAN + "\n- [x] Gate green\n- [x] Something never audited\n", verdicts=DONE, gate="STALE")[0] == ["ticked-box", "ticked-box"],
      "every offending ticked box is reported on its own")
refused, unconfirmed = rules(CLEAN, gate="UNKNOWN")
check((refused, unconfirmed) == ([], []), "an unreadable gate record matters only when a box leans on it")

# --- red-first (fleet-config#1915): a bug PR needs a red record on a different tree, or an exempt line with a reason
def bug_rules(body: str, red: str = "RED"):
    refused, unconfirmed = pb.evaluate(body, closing=[7], issue=7, verdicts=[], gate_state="FRESH",
                                       allow_images=False, is_bug=True, red_state=red)
    return refused, unconfirmed


EXEMPT = "**Red-first:** exempt · the symptom is a Windows console flash with no automatable assertion"
check(bug_rules(CLEAN, "RED") == ([], []), "a bug PR with a red record on a different tree passes")
for state in ("MISSING", "NOT_RED", "SAME_TREE"):
    refused, _ = bug_rules(CLEAN, state)
    check([r.split(":", 1)[0] for r in refused] == ["red-first"], f"a bug PR whose red record is {state} is refused")
refused, _ = bug_rules(CLEAN, "MISSING")
check("gate_evidence.py run --label red" in refused[0] and "git stash" in refused[0] and "Red-first:** exempt" in refused[0],
      "the refusal names the command to run, how to get the pre-fix tree and the exempt line")
check("different tree" in bug_rules(CLEAN, "SAME_TREE")[0][0], "a same-tree refusal says the red run must be on the pre-fix tree")
check("exit 0" in bug_rules(CLEAN, "NOT_RED")[0][0], "a not-red refusal says the recorded run passed")
check(bug_rules(CLEAN + "\n" + EXEMPT + "\n", "MISSING") == ([], []), "an exempt line with a reason passes without a red record")
check(bug_rules(CLEAN + "\n" + EXEMPT + "\n", "UNKNOWN") == ([], []), "an exempt line passes even when the record cannot be read")
for label, line in (("no reason", "**Red-first:** exempt"), ("blank reason", "**Red-first:** exempt ·   "),
                    ("unbolded", "Red-first: exempt · no test possible"), ("other verb", "**Red-first:** skipped · no test possible")):
    check([r.split(":", 1)[0] for r in bug_rules(CLEAN + "\n" + line + "\n", "MISSING")[0]] == ["red-first"], f"{label} is not an exempt line")
check(bug_rules(CLEAN + "\n`" + EXEMPT + "`\n", "MISSING")[0] != [] and bug_rules(CLEAN + "\n```\n" + EXEMPT + "\n```\n", "MISSING")[0] != [],
      "an exempt line quoted as code does not exempt")
refused, unconfirmed = bug_rules(CLEAN, "UNKNOWN")
check(refused == [] and len(unconfirmed) == 1, "an unreadable red record on a bug PR is NOT CONFIRMED, neither pass nor refusal")
check(rules(CLEAN) == ([], []), "a non-bug PR never needs a red record")
check(pb.find_exempt_line(CLEAN + "\n" + EXEMPT) == EXEMPT, "find_exempt_line returns the exempt line")
check(pb.find_exempt_line(CLEAN) is None, "find_exempt_line is None without one")

# --- everything wrong at once is reported rule by rule
multi = "no danger\n![x](y.png)\nCo-Authored-By: Claude <a@b.c>\n- [x] unaudited\n"
check(sorted(set(rules(multi, refs=(9,))[0])) == ["attribution", "closing-refs", "images", "merge-danger", "ticked-box"],
      "each violated rule is reported")

# --- reading the PR: an unreadable PR is NOT CONFIRMED, never a pass
good = json.dumps({"body": CLEAN, "closingIssuesReferences": [{"number": 7}]})
check(pb.parse_pr(good) == (CLEAN, [7]), "a PR payload parses to body and closing issue numbers")
check(pb.parse_pr(json.dumps({"body": CLEAN, "closingIssuesReferences": []})) == (CLEAN, []), "an empty closing list is a real, empty answer")
for label, raw in (("not json", "oops"), ("no closing key", json.dumps({"body": CLEAN})),
                   ("closing not a list", json.dumps({"body": CLEAN, "closingIssuesReferences": None})),
                   ("no body key", json.dumps({"closingIssuesReferences": []})),
                   ("not an object", "[]")):
    try:
        pb.parse_pr(raw)
        check(False, f"{label} must not parse")
    except ValueError:
        check(True, label)


def run_main(pr_stdout: str, returncode: int = 0, extra=(), gate=("FRESH", "ok"), labels=("enhancement",), red=("RED", "ok")):
    done = subprocess.CompletedProcess(["gh"], returncode, stdout=pr_stdout, stderr="HTTP 502 from the API\n")
    out: list = []
    labels_patch = (mock.patch.object(pb, "_read_issue_labels", side_effect=labels) if isinstance(labels, Exception)
                    else mock.patch.object(pb, "_read_issue_labels", return_value=list(labels)))
    with mock.patch.object(pb, "run_gh", return_value=done), \
         mock.patch.object(pb.gate_evidence, "check", return_value=gate), \
         mock.patch.object(pb.gate_evidence, "check_red", return_value=red) as red_mock, \
         labels_patch, \
         mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
        code = pb.main(["--pr", "5", "--issue", "7", *extra])
    run_main.red_calls = red_mock.call_count
    return code, "\n".join(out)


code, out = run_main(good)
check(code == 0 and "PR_BODY=pass" in out, "main passes a clean PR with exit 0")
code, out = run_main(json.dumps({"body": CLEAN, "closingIssuesReferences": []}))
check(code == 1 and "PR_BODY=refused" in out and "REFUSED closing-refs:" in out, "main refuses with exit 1 and a REFUSED line per finding")
code, out = run_main("", returncode=1)
check(code == 3 and "PR_BODY=not_confirmed" in out and "pass" not in out.replace("not_confirmed", ""), "a failed gh read exits 3 as NOT CONFIRMED")
code, out = run_main("garbage")
check(code == 3 and "PR_BODY=not_confirmed" in out, "an unparseable gh answer exits 3")
code, out = run_main(json.dumps({"body": CLEAN + "\n- [x] Gate green\n", "closingIssuesReferences": [{"number": 7}]}), gate=("UNKNOWN", "hash failed"))
check(code == 3 and "PR_BODY=not_confirmed" in out, "main exits 3 when a ticked gate box cannot be confirmed")
code, out = run_main(json.dumps({"body": CLEAN + "\n![x](y.png)\n", "closingIssuesReferences": [{"number": 7}]}), extra=["--allow-images"])
check(code == 0, "--allow-images lifts only the image rule")
with tempfile.TemporaryDirectory() as tmp:
    verdicts = Path(tmp) / "v.json"
    verdicts.write_text(json.dumps(DONE), encoding="utf-8")
    code, out = run_main(json.dumps({"body": box_done, "closingIssuesReferences": [{"number": 7}]}), extra=["--verdicts", str(verdicts)])
    check(code == 0, "main reads the acceptance verdicts file")
    verdicts.write_text("not json", encoding="utf-8")
    code, out = run_main(json.dumps({"body": box_done, "closingIssuesReferences": [{"number": 7}]}), extra=["--verdicts", str(verdicts)])
    check(code == 3 and "PR_BODY=not_confirmed" in out, "an unreadable verdicts file is NOT CONFIRMED when a box needs it")

# --- main wires the issue's labels and the red record into the red-first rule
bug_pr = json.dumps({"body": CLEAN, "closingIssuesReferences": [{"number": 7}]})
code, out = run_main(bug_pr, labels=("bug", "p1"), red=("MISSING", "no recorded failing run for red"))
check(code == 1 and "PR_BODY=refused" in out and "REFUSED red-first:" in out, "main refuses a bug PR without a red record")
code, out = run_main(bug_pr, labels=("bug",), red=("RED", "ok"))
check(code == 0 and "PR_BODY=pass" in out, "main passes a bug PR with a red record")
code, out = run_main(json.dumps({"body": CLEAN + "\n" + EXEMPT + "\n", "closingIssuesReferences": [{"number": 7}]}), labels=("bug",), red=("MISSING", "x"))
check(code == 0 and run_main.red_calls == 0, "main passes an exempt bug PR without reading the red record")
code, out = run_main(bug_pr, labels=("enhancement",), red=("MISSING", "x"))
check(code == 0 and run_main.red_calls == 0, "main never reads the red record for a non-bug issue")
code, out = run_main(bug_pr, labels=("bug",), red=("UNKNOWN", "hash failed"))
check(code == 3 and "PR_BODY=not_confirmed" in out, "main exits 3 when a bug PR's red record cannot be read")
code, out = run_main(bug_pr, labels=ValueError("HTTP 502"))
check(code == 3 and "PR_BODY=not_confirmed" in out and "labels" in out, "main exits 3 when the issue's labels cannot be read")
code, out = run_main(bug_pr, labels=("bug",), red=("SAME_TREE", "x"), extra=["--red-label", "red-2"])
check(code == 1 and "REFUSED red-first:" in out, "main refuses a same-tree red record")

_h.report_and_exit("test_pr_body")
