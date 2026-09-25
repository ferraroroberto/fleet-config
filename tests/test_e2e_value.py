"""Unit tests for skills/_lib/e2e_value.py, /e2e-audit's time and failure layer (fleet-config#1018).

Synthetic progress logs only, in the shape app-launcher's tests/_progress_log.py
writes (#534, #943, #1231). No gate is started and no GitHub call is made.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_e2e_value.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import e2e_value as v  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _log(day: str, start: str, lines: list) -> str:
    return f"verify-before-ship run started {day} {start}\n" + "".join(l + "\n" for l in lines)


# Run 1: serial, both projections, a WebKit red that stopped at line 40 of its test.
RUN1 = _log("2026-09-20", "10:00:00", [
    "[10:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[10:00:01 +    0.1s] START tests/test_api.py::test_ok",
    "[10:00:01 +    0.2s] DONE  tests/test_api.py::test_ok (0.1s)",
    "[10:01:00 +    1,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[10:01:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[10:01:02 +    2.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (2.0s)",
    "[10:01:02 +    2.0s] START tests/e2e/test_board.py::test_load[webkit]",
    "[10:01:06 +    6.0s] DONE  tests/e2e/test_board.py::test_load[webkit] (4.0s)",
    "[10:01:06 +    6.0s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[10:01:18 +   18.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "    | tests/e2e/test_chat.py:40: in test_link_opens",
    "    |     expect(row).to_be_visible()",
    "    | E   AssertionError: timed out",
    "[10:01:18 +   18.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (12.0s)",
    "[10:01:18 +   18.0s] START tests/e2e/test_redact.py::test_mask[{'a': 1} b]",
    "[10:01:18 +   18.5s] DONE  tests/e2e/test_redact.py::test_mask[{'a': 1} b] (0.5s)",
    "[10:01:18 +   18.5s] pytest session finished (exit status 1)",
])
# Run 2: the same test red again at a different step, on two xdist workers.
RUN2 = _log("2026-09-21", "23:59:50", [
    "[23:59:50 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[23:59:50 +    0.1s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[00:00:05 +   15.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone] [gw1]",
    "    | tests\\e2e\\test_chat.py:52: in test_link_opens",
    "    | E   AssertionError: wrong conversation",
    "[00:00:05 +   15.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (15.0s) [gw1]",
    "[00:00:05 +   15.0s] pytest session finished (exit status 1)",
])
# An unfinished run: a START with no DONE. Never "the last completed run".
RUN3 = _log("2026-09-22", "09:00:00", [
    "[09:00:00 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[09:00:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
])

# ---- parsing ------------------------------------------------------------------

r1 = v.parse_run(RUN1)
check(r1["complete"] and r1["exit_status"] == 1, "a run with every START done and a session end is complete")
check(r1["nodes"]["tests/e2e/test_redact.py::test_mask[{'a': 1} b]"] == 0.5, "a node id holding spaces parses whole")
check([p["name"] for p in r1["phases"]] == ["pytest (non-e2e)", "pytest e2e (tests/e2e)"]
      and r1["phases"][0]["wall_s"] == 60.0 and r1["phases"][1]["nodes"] == 4, f"phases with wall time and node counts -- {r1['phases']}")
check(r1["failures"] == [{"outcome": "FAILED", "when": "call", "nodeid": "tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
                          "step": "tests/e2e/test_chat.py:40"}], f"a FAILED line keeps the step its excerpt stopped at -- {r1['failures']}")
r2 = v.parse_run(RUN2)
check(r2["nodes"]["tests/e2e/test_chat.py::test_link_opens[webkit-iphone]"] == 15.0 and r2["failures"][0]["step"] == "tests/e2e/test_chat.py:52",
      "an xdist [gwN] tag is dropped and a backslash path normalised")
check(v._wall_s(r2) == 15.0, "a run crossing midnight keeps a positive wall time")
check(not v.parse_run(RUN3)["complete"], "a START without a DONE is not complete")
check(v.last_complete_run(RUN1 + RUN3)["started"].day == 20, "the last completed run skips a trailing unfinished one")
check(v.projection_of("t.py::a[webkit-900]") == "webkit" and v.projection_of("t.py::a[chromium]") == "chromium"
      and v.projection_of("t.py::a[x-1]") == "default" and v.projection_of("t.py::a") == "default", "projection from the param id")
check(v.test_of("t.py::a[webkit-900]") == "t.py::a", "test id without its param id")

# ---- measurements -------------------------------------------------------------------------

e2e = {n: s for n, s in r1["nodes"].items() if v.is_e2e(n, ["tests/e2e/"])}
check(len(e2e) == 4 and "tests/test_api.py::test_ok" not in e2e, "only nodes under the test dirs are e2e")
proj = v.projections(e2e)
check(proj["webkit"] == {"nodes": 2, "seconds": 16.0, "mean_s": 8.0} and proj["chromium"]["seconds"] == 2.0,
      f"per-projection nodes, seconds and mean -- {proj}")
check([b["nodes"] for b in v.buckets(e2e)] == [1, 1, 1, 0, 1], "the #1220 duration buckets")
t = v.tail(e2e, slowest_n=1)
check(t["slowest_share"] == round(12.0 / 18.5, 3) and t["top5pct_n"] == 1 and t["max_s"] == 12.0, f"tail share -- {t}")
check(v.tail({})["slowest_share"] is None, "an empty run has no tail share, not 0")
check(v.cost_drivers("page.goto(u)\npage.reload()\nspawn a PTY\n@pytest.mark.real_agent\n")
      == {"page_loads": 2, "pty_refs": 1, "real_agent": 1}, "static cost drivers")

events = v.failure_events([(Path("a.log"), [r1, r2])])
check(len(events) == 2 and events[0]["date"] == "2026-09-20" and events[0]["projection"] == "webkit", "failure events with date and projection")
rc = v.race_candidates(events)
check(rc == [{"test": "tests/e2e/test_chat.py::test_link_opens", "projections": ["webkit"],
              "steps": ["tests/e2e/test_chat.py:40", "tests/e2e/test_chat.py:52"], "events": 2}],
      f"a test red at two different steps is a race candidate (app-launcher#1222) -- {rc}")
check(v.race_candidates(events[:1]) == [], "one red is not a race candidate")

m = v.text_mentions("- The first had 1 red, `test_compose_bar.py::test_send[chromium]`, which passed alone.\n- test_other passed.")
check([x["nodeid"] for x in m] == ["test_compose_bar", "test_send[chromium]"] and m[1]["projection"] == "chromium",
      f"tests named on a line about a red, with projection; a line with no red word is skipped -- {m}")

# ---- load: overlapping runs in other checkouts ------------------------------------------------

ld = v.load_state(r1, [(Path("wt.log"), [v.parse_run(_log("2026-09-20", "10:00:30", [
    "[10:00:30 +    0,0s] START tests/e2e/x.py::t", "[10:02:00 +    1,0s] DONE  tests/e2e/x.py::t (90.0s)",
    "[10:02:00 +    1,0s] pytest session finished (exit status 0)"]))])])
check(ld["state"] == "loaded" and len(ld["overlaps"]) == 1, "a run overlapping another checkout's run is loaded")
check(v.load_state(r1, [])["state"] == "quiet", "no overlapping run -> quiet (scope: this repo's checkouts)")
check(v.load_state({"started": None, "finished": None}, [])["state"] == "unknown", "no run window -> unknown, never quiet")

# ---- the subcommands against a repo on disk ---------------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="e2e-value-"))
(tmp / "tests" / "e2e").mkdir(parents=True)
(tmp / "tests" / "e2e" / "test_board.py").write_text("def test_load(page):\n    page.goto('/')\n", encoding="utf-8")
none = v.timing(tmp, ["tests/e2e"])
check(none["status"] == "unknown" and "no timing source" in none["reason"], "no log declared -> unknown (no timing source)")
(tmp / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
missing = v.timing(tmp, ["tests/e2e"])
check(missing["status"] == "unknown" and "does not exist" in missing["reason"], "a declared log that is absent -> unknown")
(tmp / "gate.log").write_text(RUN3, encoding="utf-8")
check(v.timing(tmp, ["tests/e2e"])["reason"] == "no completed gate run in any checkout's log", "only an unfinished run -> unknown")
(tmp / "gate.log").write_text(RUN1 + RUN2, encoding="utf-8")
(tmp / "CLAUDE.md").write_text("- The full gate takes ~10 min.\n- Lunch takes 30 min.\n", encoding="utf-8")
tm = v.timing(tmp, ["tests/e2e"])
check(tm["status"] == "ok" and tm["run"]["started"] == "2026-09-21T23:59:50" and tm["run"]["e2e_nodes"] == 1,
      f"timing reads the latest completed run -- {tm['run']}")
check(tm["modules"][0]["module"] == "tests/e2e/test_chat.py", "modules heaviest first")
dr = tm["runtime_drift"]
check(dr["status"] == "candidates" and len(dr["claims"]) == 1 and dr["claims"][0]["claimed_min"] == 10.0 and dr["claims"][0]["candidate"],
      f"a gate runtime far from every measured span is a drift candidate; a line not about the gate is ignored -- {dr}")
fl = v.failures(tmp, use_gh=False)
check(fl["logs"]["status"] == "ok" and len(fl["log_events"]) == 2 and fl["race_candidates"][0]["events"] == 2
      and fl["gh"] == {"prs": "skipped", "bug_issues": "skipped"}, "failures reads every run in the log")
(tmp / "junit.xml").write_text('<testsuite><testcase classname="tests.e2e.test_board" name="test_load[webkit]" time="3.5">'
                               '<failure>tests/e2e/test_board.py:2: in test_load</failure></testcase></testsuite>', encoding="utf-8")
ju = v.timing(tmp, ["tests/e2e"], Path("junit.xml"))
check(ju["status"] == "ok" and ju["projections"]["webkit"]["seconds"] == 3.5 and ju["load"]["state"] == "unknown"
      and ju["runtime_drift"]["status"] == "unknown", "a JUnit XML gives timings, but no window: load and drift unknown")

_h.report_and_exit("test_e2e_value")
