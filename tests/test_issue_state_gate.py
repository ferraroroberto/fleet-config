"""Unit tests for the pure logic in skills/_lib/issue_state_gate.py.

Exercises `classify_state` directly against synthetic `gh` outcomes, then
`partition_working_set` against a stubbed lookup -- this is the test that
proves fleet-config#623's acceptance criterion mechanically: given a
working set containing a closed issue, that issue never lands in the
`dispatch` bucket, so no code path that only iterates `dispatch` can ever
spawn an agent for it. Also exercises the `check`/`partition` CLI wiring
with the real `gh` call monkeypatched out -- no network touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_issue_state_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import issue_state_gate as isg  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- classify_state ----

state, detail = isg.classify_state(0, '{"state": "OPEN"}', "")
check(state == "open" and detail == "", "classify_state: OPEN -> open, no detail")

state, detail = isg.classify_state(0, '{"state": "CLOSED"}', "")
check(state == "closed" and detail == "", "classify_state: CLOSED -> closed, no detail")

state, detail = isg.classify_state(1, "", "rate limit exceeded")
check(state == "unknown" and "rate limit exceeded" in detail,
      "classify_state: non-zero exit -> unknown, stderr surfaces in detail")

state, detail = isg.classify_state(1, "", "")
check(state == "unknown" and detail, "classify_state: non-zero exit with no output still -> unknown, non-empty detail")

state, detail = isg.classify_state(0, "not json", "")
check(state == "unknown" and "could not parse" in detail,
      "classify_state: malformed JSON -> unknown")

state, detail = isg.classify_state(0, "{}", "")
check(state == "unknown" and "could not parse" in detail,
      "classify_state: valid JSON missing 'state' key -> unknown")

state, detail = isg.classify_state(0, '{"state": "MERGED"}', "")
check(state == "unknown" and "unrecognized state" in detail,
      "classify_state: an unrecognized state value -> unknown, never silently open/closed")


# ---- partition_working_set: the fleet-config#623 acceptance test ----
# "given a working set containing a closed issue, no agent is dispatched
# for it" -- proven structurally: the closed issue must be absent from
# `dispatch`, the only bucket any real dispatcher iterates.

_LOOKUP = {
    ("alpha", 1): ("open", ""),
    ("bravo", 2): ("closed", ""),
    ("charlie", 3): ("unknown", "gh issue view failed: network error"),
}


def _stub_lookup(repo, number):
    return _LOOKUP[(repo, number)]


working_set = [
    {"repo": "alpha", "number": 1, "title": "still open"},
    {"repo": "bravo", "number": 2, "title": "closed five weeks ago"},
    {"repo": "charlie", "number": 3, "title": "network blip"},
]
result = isg.partition_working_set(working_set, _stub_lookup)

check([i["repo"] for i in result["dispatch"]] == ["alpha"],
      "partition: only the open issue reaches dispatch")
check(all(i["repo"] != "bravo" for i in result["dispatch"]),
      "partition: the closed issue is absent from dispatch (the skip-path guarantee)")
check([i["repo"] for i in result["skipped_closed"]] == ["bravo"],
      "partition: the closed issue lands in skipped_closed, not merged into dispatch or unresolved")
check([i["repo"] for i in result["unresolved"]] == ["charlie"],
      "partition: an unresolvable check gets its own bucket, never folded into skipped_closed or dispatch")
check(result["unresolved"][0]["detail"] == "gh issue view failed: network error",
      "partition: the unresolved reason is carried through, not discarded")
check(result["dispatch"][0]["title"] == "still open",
      "partition: original keys survive annotation")

empty = isg.partition_working_set([], _stub_lookup)
check(empty == {"dispatch": [], "skipped_closed": [], "unresolved": []},
      "partition: an empty working set -> three empty buckets, not an error")


# ---- CLI wiring: check + partition, gh monkeypatched out (no network) ----

def _run_check_cli(monkey_result):
    with patch.object(isg, "check", return_value=monkey_result):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            isg.main(["check", "alpha", "1"])
        return buf.getvalue()

out = _run_check_cli(("open", ""))
check("STATE=open" in out and "DETAIL=" not in out, "check CLI: open state -> STATE= only, no DETAIL line")

out = _run_check_cli(("unknown", "gh timed out"))
check("STATE=unknown" in out and "DETAIL=gh timed out" in out, "check CLI: unknown state -> STATE= and DETAIL=")


def _run_partition_cli(issues, lookup_map):
    def fake_check(repo, number):
        return lookup_map[(repo, number)]

    with patch.object(isg, "check", side_effect=fake_check):
        stdin = io.StringIO(json.dumps(issues))
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", stdin), \
             contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            isg.main(["partition"])
        return out_buf.getvalue(), err_buf.getvalue()

stdout, stderr = _run_partition_cli(working_set, _LOOKUP)
parsed = json.loads(stdout)
check(len(parsed["dispatch"]) == 1 and len(parsed["skipped_closed"]) == 1 and len(parsed["unresolved"]) == 1,
      "partition CLI: stdout JSON matches the pure partition result")
check("DISPATCH=1 SKIPPED_CLOSED=1 UNRESOLVED=1" in stderr,
      "partition CLI: stderr summary line matches the bucket counts")

_h.report_and_exit("test_issue_state_gate")
