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

# ---- routing report (step 2): the repo's own classifier, imported ------------------------------

FAKE_CLASSIFIER = '''
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class Category(IntEnum):
    NONE = 0
    STATIC = 1
    FULL = 2


@dataclass
class Rule:
    tier: Category
    prefix: object = None
    path: object = None
    extensions: object = None
    label: str = "rule"

    def matches(self, path, ext):
        if self.path is not None:
            return path == self.path
        if self.prefix is not None and not path.startswith(self.prefix):
            return False
        if self.extensions is not None and ext not in self.extensions:
            return False
        return self.prefix is not None or self.extensions is not None


@dataclass
class Routing:
    tier: str
    surface: str = ""


@dataclass
class Config:
    rules: list
    source: str = "declared"


def load_config(path):
    static = Rule(Category.FULL, prefix="static/", label="static-code")
    docs = Rule(Category.NONE, extensions=("md",), label="docs")
    tests = Rule(Category.NONE, prefix="tests/", label="tests")
    e2e = Rule(Category.FULL, prefix="tests/e2e/", label="e2e-test")
    order = [docs, static] if "docs_first" in Path(path).read_text() else [static, docs]
    return Config([e2e, tests] + order)


def _classify_one(path, rules):
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    for r in rules:
        if r.matches(path, ext):
            return r.tier, r.label
    return Category.FULL, "unclassified"


def classify(paths, config):
    top = max((_classify_one(p, config.rules)[0] for p in paths), default=Category.FULL)
    return Routing({Category.FULL: "full", Category.STATIC: "static", Category.NONE: "skip"}[top])
'''

rt = Path(tempfile.mkdtemp(prefix="e2e-value-routing-"))
check(v.routing_report(rt, pr_list=[])["status"] == "unknown", "no scripts/classify_e2e.py -> unknown, never an empty report")
(rt / "scripts").mkdir()
(rt / "scripts" / "classify_e2e.py").write_text(FAKE_CLASSIFIER, encoding="utf-8")
(rt / ".fleet.toml").write_text("[e2e]\n", encoding="utf-8")
(rt / "proposed.toml").write_text("docs_first = true\n", encoding="utf-8")
prs = [
    {"number": 1, "files": ["static/styles.css", "tests/e2e/test_a.py"]},
    {"number": 2, "files": ["static/_vendored/nav/README.md"]},
    {"number": 3, "files": ["docs/guide.md"]},
    {"number": 4, "files": ["tools/build.sh"]},
    {"number": 5, "files": ["tests/e2e/test_b.py", "tests/test_unit.py"]},
]
rr = v.routing_report(rt, pr_list=prs, proposed_path=rt / "proposed.toml")
check(rr["status"] == "ok" and rr["tiers"] == {"skip": 1, "static": 0, "surface": 0, "full": 4} and rr["browser_relevant"] == 4,
      f"tier distribution through the imported classifier -- {rr.get('tiers')}")
check(rr["full_classes"] == {"static-code": 2, "e2e-test": 2, "unclassified": 1}
      and rr["single_cause"] == {"static-code": 1, "unclassified": 1, "e2e-test": 1},
      f"forcing classes: every PR containing one, and single-cause PRs -- {rr['full_classes']} / {rr['single_cause']}")
check(rr["unclassified"] == [{"path": "tools/build.sh", "prs": 1}], "unclassified paths listed for a table rule")
check([s["path"] for s in rr["shadowed"]] == ["static/_vendored/nav/README.md"] and rr["shadowed"][0]["shadowed"] == ["docs"],
      f"a README a broad prefix rule took over the later *.md rule is shadowed; tests/ after tests/e2e/ is not -- {rr['shadowed']}")
check(rr["counterfactual"]["changed"] == [{"pr": 2, "from": "full", "to": "skip", "surface": ""}],
      f"--proposed lists every PR whose tier changes -- {rr['counterfactual']}")

# ---- parallelisability (step 2) ---------------------------------------------------------------------

check(v.lpt([5, 4, 3, 3], 2) == 8 and v.lpt([5, 4, 3, 3], 4) == 5 and v.lpt([], 3) == 0, "LPT makespans")
pj = v.projection({"a.py::t1": 10.0, "a.py::t2": 10.0, "b.py::t1": 4.0})
check(pj["serial_s"] == 24.0 and pj["table"][0] == {"workers": 2, "load_s": [16.1, 18.2, 21.0], "loadscope_s": [23.0, 26.0, 30.0]}
      and pj["floor_loadscope"] == {"module": "a.py", "seconds": 20.0} and pj["floor_load"]["seconds"] == 10.0,
      f"projection: per-test and per-module LPT with inflation, and both floors -- {pj.get('table')}")
check(v.projection({})["status"] == "unknown", "no durations -> unknown")

pt = Path(tempfile.mkdtemp(prefix="e2e-value-parallel-"))
(pt / "tests" / "e2e").mkdir(parents=True)
(pt / "tests" / "e2e" / "conftest.py").write_text(
    "import socket, pytest\n\n\ndef _free_port():\n    s = socket.socket()\n    s.bind(('127.0.0.1', 0))\n    return s\n\n"
    "LOG = 'e2e-autoboot-webapp.log'\n\n\n@pytest.fixture(scope=\"session\")\ndef server(tmp_path_factory):\n    pass\n", encoding="utf-8")
(pt / "tests" / "_progress.py").write_text("def w(p):\n    open(p, 'a').write('x')\n", encoding="utf-8")
(pt / "tests" / "e2e" / "test_agent.py").write_text("import pytest\n\npytestmark = pytest.mark.real_agent\n", encoding="utf-8")
(pt / "tests" / "test_unit_elsewhere.py").write_text("LOG = 'other.log'\n", encoding="utf-8")
pb = v.parallel_blockers(pt, ["tests/e2e"])
check(pb["blockers"] == 4 and pb["free_port_race"][0]["state"] == "blocker"
      and pb["fixed_log_names"][0]["names"] == ["e2e-autoboot-webapp.log"] and pb["shared_append"][0]["file"] == "tests/_progress.py"
      and pb["load_sensitive_ungrouped"][0]["state"] == "blocker", f"the four app-launcher#1220 (c) blockers, pre-#1231 shape -- {pb}")
check(all(e["file"] != "tests/test_unit_elsewhere.py" for e in pb["fixed_log_names"]), "non-e2e test modules outside the plugins are not scanned")
check(pb["session_fixtures"] == [{"file": "tests/e2e/conftest.py", "fixture": "server", "state": "info", "per_worker_tmp": True}],
      "session fixtures listed with per-worker tmp isolation")
check(pb["xdist"] == {"installed": "unknown (no .venv)", "declared": False}, "no venv -> xdist installed is unknown, not False")
(pt / "tests" / "e2e" / "conftest.py").write_text(
    "import os, socket\nW = os.environ.get('PYTEST_XDIST_WORKER', '')\n\n\ndef boot():\n    for attempt in range(3):\n"
    "        port = _free_port()\n\n\ndef _free_port():\n    s = socket.socket()\n    s.bind(('127.0.0.1', 0))\n    return s\n\n"
    "LOG = f'e2e-autoboot-{W}.log' if W else 'e2e-autoboot.log'\n", encoding="utf-8")
(pt / "tests" / "_progress.py").write_text("import os\n\n\ndef w(p):\n    if os.environ.get('PYTEST_XDIST_WORKER'):\n        return\n    open(p, 'a').write('x')\n", encoding="utf-8")
(pt / "tests" / "e2e" / "test_agent.py").write_text("import pytest\n\npytestmark = [pytest.mark.real_agent, pytest.mark.serial]\n", encoding="utf-8")
pb2 = v.parallel_blockers(pt, ["tests/e2e"])
check(pb2["blockers"] == 0 and {e["state"] for k in ("free_port_race", "fixed_log_names", "shared_append", "load_sensitive_ungrouped")
                                 for e in pb2[k]} == {"mitigated"}, f"after the #1231 fixes, every blocker reads mitigated -- {pb2}")

serial_run = v.parse_run(_log("2026-09-23", "10:00:00", [
    "[10:00:00 +    0,0s] START tests/e2e/test_jobs.py::test_list[chromium]",
    "[10:00:02 +    2.0s] DONE  tests/e2e/test_jobs.py::test_list[chromium] (2.0s)",
    "[10:00:02 +    2.0s] pytest session finished (exit status 0)"]))
par_run = v.parse_run(_log("2026-09-24", "10:00:00", [
    "[10:00:00 +    0,0s] START tests/e2e/test_jobs.py::test_list[chromium]",
    "[10:00:03 +    3.0s] FAILED (call) tests/e2e/test_jobs.py::test_list[chromium] [gw2]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_jobs.py::test_list[chromium] (3.0s) [gw2]",
    "[10:00:03 +    3.0s] pytest session finished (exit status 1)"]))
check(par_run["parallel"] and not serial_run["parallel"], "a run with [gwN] DONE lines is parallel")
check(v.shared_state_evidence([(Path("x.log"), [serial_run, par_run])])
      == [{"nodeid": "tests/e2e/test_jobs.py::test_list[chromium]", "parallel_reds": 1, "green_serially": True}],
      "red under workers, green serially -> shared state (app-launcher#1231), never a flake")

# ---- time budget, growth baseline and the audit trigger (step 3) ----------------------------------

check(v.time_budget_limit('[e2e]\ntime_budget_s = 900\n') == (900, "[e2e] time_budget_s"), "time_budget_s read")
for bad in ("true", "0", "-5", '"900"', "1.5"):
    check(v.time_budget_limit(f"[e2e]\ntime_budget_s = {bad}\n")[0] is None, f"invalid time_budget_s {bad} ignored, never a raised bar")
check(v.time_budget_limit(None) == (None, "no [e2e] time_budget_s declared"), "undeclared time budget")

ROUTED = _log("2026-09-24", "09:00:00", [
    "[09:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[09:00:00 +    0.1s] START tests/test_api.py::test_ok",
    "[09:00:01 +    1.0s] DONE  tests/test_api.py::test_ok (1.0s)",
    "[09:03:00 +    0,0s] ==> phase: e2e routing: full (e2e-test: tests/e2e/test_board.py)",
    "[09:03:00 +    0,0s] ==> phase: pytest e2e parallel...",
    "[09:03:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[09:13:00 +  600.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (600.0s) [gw0]",
    "[09:13:00 +  600.0s] ==> phase: pytest e2e serial...",
    "[09:13:00 +    0.1s] START tests/e2e/test_agent.py::test_reconnect[chromium]",
    "[09:15:00 +  120.0s] DONE  tests/e2e/test_agent.py::test_reconnect[chromium] (120.0s)",
    "[09:15:00 +  120.0s] pytest session finished (exit status 0)",
])
rr_run = v.parse_run(ROUTED)
check(rr_run["routed_tier"] == "full" and rr_run["parallel"], "the routed tier is read from the gate's routing line")
check(v.browser_leg_s(rr_run, ["tests/e2e"]) == 720.0, "the browser leg sums every phase that ran e2e nodes (parallel + serial pass)")

tb = Path(tempfile.mkdtemp(prefix="e2e-value-budget-"))
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\ntime_budget_s = 600\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "unknown", "a declared budget with no log is unknown, never within")
(tb / "gate.log").write_text(ROUTED, encoding="utf-8")
tv = v.time_budget(tb, ["tests/e2e"])
check(tv["verdict"] == "over" and tv["seconds"] == 720.0 and tv["limit"] == 600, f"720 s browser leg over a 600 s budget -- {tv}")
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\ntime_budget_s = 900\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "within", "within a 900 s budget")
(tb / "gate.log").write_text(ROUTED.replace("e2e routing: full", "e2e routing: static"), encoding="utf-8")
ts = v.time_budget(tb, ["tests/e2e"])
check(ts["verdict"] == "unknown" and "full-tier" in ts["reason"], f"a run routed below full is not the suite -> unknown -- {ts}")
(tb / "gate.log").write_text(ROUTED, encoding="utf-8")
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "undeclared", "no time_budget_s -> undeclared, not a trigger")

import os  # noqa: E402
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tb / "state")
check(v.read_audit_record(tb) is None, "no record before the first audit")
rec_path = v.write_audit_record(tb, 40, 120)
check(rec_path.parent == tb / "state" / "e2e-audit" and v.read_audit_record(tb)["raw_tests"] == 40, "record lands in hooks state and reads back")
check(v.growth(49, v.read_audit_record(tb))["trigger"] is False and v.growth(50, v.read_audit_record(tb))["trigger"] is True,
      "the growth trigger fires at +10 test functions")
check(v.growth(12, None) == {"state": "none-recorded", "delta": None, "since": None, "trigger": False}, "no baseline -> none-recorded, no trigger")
g10 = v.growth(50, v.read_audit_record(tb))
check(v.audit_trigger("within", "within", g10)[0] == "yes" and "+10 test functions" in v.audit_trigger("within", "within", g10)[1],
      "growth alone triggers the audit")
check(v.audit_trigger("over", "undeclared", v.growth(40, None)) == ("yes", "over the node budget"), "nodes over triggers")
check(v.audit_trigger("within", "over", v.growth(40, None))[0] == "yes", "time over triggers")
check(v.audit_trigger("within", "unknown", v.growth(41, v.read_audit_record(tb)))[0] == "unknown",
      "an unmeasured leg with nothing else firing is unknown, never no")
check(v.audit_trigger("within", "undeclared", v.growth(38, v.read_audit_record(tb))) ==
      ("no", f"within every declared budget; -2 test functions since {v.read_audit_record(tb)['date']}"), "nothing fired -> no, with the delta")

_h.report_and_exit("test_e2e_value")
