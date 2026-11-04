"""Unit tests for skills/_lib/grokbot/attention.py: the read-only attention digest.

Sources are faked, so no launcher, `gh` or app is touched. A static scan proves the module has no write path.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/grokbot/test_attention.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from grokbot import attention  # noqa: E402
from grokbot import status as grok_status  # noqa: E402
from grokbot import switch as grok_switch  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# The switch is read from the launcher; these tests never reach the live one. test_switch.py owns its cases.
mock.patch.object(grok_switch, "read_switch", return_value=(grok_switch.ON, "")).start()


_real_gather = attention.gather


def run(sources):
    out = io.StringIO()
    with mock.patch.object(attention, "gather", lambda base_url: _real_gather(base_url, sources)),             contextlib.redirect_stdout(out):
        code = attention.main([])
    return code, out.getvalue()


code, text = run([lambda: ([], []), lambda: ([], [])])
check(code == 0 and text.strip() == "ATTENTION=none", "everything read and quiet prints ATTENTION=none, exit 0")

code, text = run([lambda: (["lane waiting: app-launcher needs-you sid=abcd1234"], []), lambda: (["needs-owner: x#1"], [])])
check(code == 0 and text.count("ATTENTION ") == 2 and "ATTENTION=none" not in text,
      "items print one line each and exit 0 when every source was read")

code, text = run([lambda: ([], ["UNKNOWN board: URLError"]), lambda: ([], [])])
check(code == 3 and "UNKNOWN board" in text and "ATTENTION=none" not in text,
      "an unreadable source is its own line, exits 3 and is never folded into none")

# board source against a fake launcher response
board = {"columns": {
    "your_turn": [{"session_id": "ab" * 16, "alive": True, "project": "life-os", "status": "needs-you"},
                  {"session_id": "cd" * 16, "alive": True, "project": "life-os", "label": "telegram:owner"}],
    "other": [{"kind": "job", "job_name": "audit-fleet", "state": "failed"},
              {"kind": "job", "job_name": "ok", "state": "success"}]}}
with mock.patch.object(attention.launcher, "_request", return_value=board) as req:
    items, unknown = attention._board_lines("http://127.0.0.1:1")
check(len(items) == 2 and "audit-fleet" in items[1] and not unknown
      and all(c.args[1] == "/api/board" and len(c.args) == 2 for c in req.call_args_list),
      "board lane waiting and failed job surface; channel sessions skipped; GET only")
with mock.patch.object(attention.launcher, "_request", side_effect=OSError("down")):
    items, unknown = attention._board_lines("http://127.0.0.1:1")
check(not items and unknown, "an unreachable board is unknown, not empty")

import json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    plan = Path(tmp) / "chief-plan.json"
    doc = attention.chief_plan.empty_plan()
    doc["updated_at"] = "2026-11-04T08:00:00Z"
    with mock.patch.object(attention.chief_plan, "plan_file", return_value=plan):
        check(attention._question_lines()[1], "a missing plan file is unknown, not none")
        plan.write_text("{not json", encoding="utf-8")
        check(attention._question_lines()[1] and not attention._question_lines()[0], "a corrupt plan is unknown")
        plan.write_text(json.dumps(doc), encoding="utf-8")
        check(attention._question_lines() == ([], []), "an empty question list prints none")
        doc["waiting_on_roberto"] = [{"id": "q1", "text": "Merge the thing?", "ref": "repo-a#1"},
                                     {"id": "q2", "text": "Ship it"}]
        plan.write_text(json.dumps(doc), encoding="utf-8")
        before = plan.read_bytes()
        items, unknown = attention._question_lines()
        check(items == ["question q1 repo-a#1: Merge the thing?", "question q2: Ship it"] and not unknown,
              "two open questions print both with id, ref and text")
        check(plan.read_bytes() == before, "reading the plan leaves the file untouched")

with mock.patch.object(attention.grok_status, "read_status", return_value=grok_status.Status("unknown", "x")):
    check(attention._grokbot_lines()[1], "grokbot unknown is reported as unknown")
with mock.patch.object(attention.grok_status, "read_status", return_value=grok_status.Status("down", "x")):
    check(attention._grokbot_lines()[0], "grokbot down is an item")
with mock.patch.object(attention.grok_status, "read_status", return_value=grok_status.Status("up")):
    check(attention._grokbot_lines() == ([], []), "grokbot up is quiet")

with mock.patch.object(attention.owner_github, "list_items", side_effect=RuntimeError("gh")):
    check(attention._owner_lines()[1], "an owner-check read failure is unknown")
with mock.patch.object(attention.owner_github, "list_items",
                       return_value={"items": [{"kind": "needs-owner", "ref": "r#1"}],
                                     "unknown": [{"repo": "z", "reason": "boom"}]}):
    items, unknown = attention._owner_lines()
    check(items == ["needs-owner: r#1"] and "z" in unknown[0], "owner items and per-repo misses both surface")

src = (REPO / "skills" / "_lib" / "grokbot" / "attention.py").read_text(encoding="utf-8")
check(not re.search(r"""method\s*=|post_session_input|["']gh["']|subprocess|write_text|open\(""", src),
      "the verb has no write path: no non-GET request, no gh call, no file write")

_h.report_and_exit("test_grokbot_attention")
