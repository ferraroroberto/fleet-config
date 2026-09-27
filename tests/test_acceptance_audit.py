"""Unit tests for skills/_lib/acceptance_audit.py (fleet-config#958).

`parse_criteria` against synthetic issue bodies, `tally` across every state
and the unattended rule, and the CLI wiring with the `gh` read stubbed out --
no network touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_acceptance_audit.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import acceptance_audit as aa  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

BODY = """## What & why

- [ ] a stray checkbox outside the section

## Acceptance criteria

- [ ] the helper exists
- [x] already ticked still counts
  - [ ] nested item is its own criterion
* [ ] star bullets count

```
- [ ] inside a fence, not a criterion
```

## How to verify

- [ ] not an acceptance item
"""

# ---- parse_criteria ----

crit = aa.parse_criteria(BODY)
check([c["criterion"] for c in crit] == [
    "the helper exists", "already ticked still counts",
    "nested item is its own criterion", "star bullets count"],
    "parse: only the Acceptance section's checkboxes, ticked + nested included, fence skipped")
check([c["id"] for c in crit] == [1, 2, 3, 4], "parse: numbered from 1")
check([c["criterion"] for c in aa.parse_criteria("intro\n- [ ] one\n- [X] two\n")] == ["one", "two"],
      "parse: no Acceptance heading -> every checkbox in the body")
check(aa.parse_criteria("no boxes here") == [], "parse: no checkboxes -> empty")

# ---- tally ----

two = [{"id": 1, "criterion": "code change"}, {"id": 2, "criterion": "works on the iPhone"}]


def entry(cid, location, verdict, evidence="seen", verifier="", post_merge=False):
    return {"id": cid, "proof_location": location, "verdict": verdict, "evidence": evidence,
            "verifier": verifier, "post_merge": post_merge}


state, lines = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "DIFF", "DONE")])
check(state == "done" and "PR_TEST_PLAN:" not in lines, "tally: all DONE -> done, no test-plan block")

state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "DIFF", "DONE")], unattended=True)
check(state == "done", "tally: a diff-only issue still ships unattended without a prompt")

state, lines = aa.tally(two, [entry(1, "DIFF", "DONE")])
check(state == "incomplete" and any(l.startswith("MISSING: #2") for l in lines),
      "tally: a criterion with no verdict -> incomplete, never read as met")

state, lines = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "DIFF", "DONE", evidence="")])
check(state == "incomplete", "tally: empty evidence -> incomplete")
state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "SOMEWHERE", "DONE")])
check(state == "incomplete", "tally: unknown proof_location -> incomplete")
state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "EXTERNAL", "MAYBE")])
check(state == "incomplete", "tally: unknown verdict -> incomplete")
state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), entry(2, "EXTERNAL", "UNVERIFIABLE")])
check(state == "incomplete", "tally: UNVERIFIABLE without a verifier -> incomplete")

state, lines = aa.tally(two, [entry(1, "DIFF", "not_done", evidence="no test"), entry(2, "DIFF", "DONE")])
check(state == "not_done" and any("NOT_DONE: #1 code change -- no test" in l for l in lines),
      "tally: NOT DONE (any spelling) -> not_done with its evidence")

phone = entry(2, "EXTERNAL", "UNVERIFIABLE", evidence="needs the device",
              verifier="Roberto on his iPhone", post_merge=True)
state, lines = aa.tally(two, [entry(1, "DIFF", "DONE"), phone])
check(state == "unverifiable", "tally: DONE + UNVERIFIABLE -> unverifiable")
check("- [ ] works on the iPhone -- not verifiable from this session (EXTERNAL); "
      "verify: Roberto on his iPhone" in lines,
      "tally: each UNVERIFIABLE item renders as an unticked test-plan box naming the verifier")

state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), phone], unattended=True)
check(state == "unverifiable", "tally unattended: an EXTERNAL post-merge item still ships")
cross = entry(2, "CROSS-REPO", "UNVERIFIABLE", verifier="the sister repo's PR", post_merge=True)
state, lines = aa.tally(two, [entry(1, "DIFF", "DONE"), cross], unattended=True)
check(state == "blocked" and any(l.startswith("BLOCKED: #2") for l in lines),
      "tally unattended: a CROSS-REPO unverifiable item blocks")
pre = entry(2, "EXTERNAL", "UNVERIFIABLE", verifier="the jobs.json on this box", post_merge=False)
state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), pre], unattended=True)
check(state == "blocked", "tally unattended: an EXTERNAL item that is not post-merge blocks")
state, _ = aa.tally(two, [entry(1, "DIFF", "DONE"), cross])
check(state == "unverifiable", "tally interactive: the same CROSS-REPO item is unverifiable, not blocked")

state, _ = aa.tally(two, [entry(1, "DIFF", "NOT DONE"), entry(2, "DIFF", "DONE")] + [{"id": "x"}])
check(state == "not_done", "tally: junk entries are ignored, the real verdicts still count")
state, _ = aa.tally([], [])
check(state == "no_criteria", "tally: no criteria -> no_criteria")

# ---- CLI wiring (gh stubbed) ----


def cli(argv, body):
    out = io.StringIO()
    with patch.object(aa, "fetch_body", return_value=body), contextlib.redirect_stdout(out):
        code = aa.main(argv)
    return code, out.getvalue()


code, out = cli(["extract", "5"], (BODY, ""))
tmpl = json.loads(out)
check(code == 0 and len(tmpl) == 4 and tmpl[0]["verdict"] == "" and tmpl[0]["post_merge"] is False,
      "extract CLI: a fill-in template, one entry per criterion")

with tempfile.TemporaryDirectory() as tmp:
    verdicts = Path(tmp) / "v.json"
    for item in tmpl:
        item.update(proof_location="DIFF", verdict="DONE", evidence="in the diff")
    verdicts.write_text(json.dumps(tmpl[:3]), encoding="utf-8")
    code, out = cli(["tally", "5", str(verdicts)], (BODY, ""))
    check(code == 1 and out.startswith("ACCEPTANCE=incomplete"),
          "tally CLI: an entry dropped from the file is caught against the re-read issue")
    verdicts.write_text(json.dumps(tmpl), encoding="utf-8")
    code, out = cli(["tally", "5", str(verdicts), "--unattended"], (BODY, ""))
    check(code == 0 and out.startswith("ACCEPTANCE=done"), "tally CLI: all DONE -> exit 0")

code, out = cli(["extract", "5"], (None, "network down"))
check(code == 2 and out.strip() == "ACCEPTANCE=unknown reason=network down",
      "CLI: an unreadable issue is unknown, its own state")

_h.report_and_exit("test_acceptance_audit")
