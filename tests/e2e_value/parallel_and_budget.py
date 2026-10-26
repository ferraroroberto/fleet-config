import tempfile
from pathlib import Path

from _suite import _log, check, v


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

# ---- second-projection fit (fleet-config#1026) --------------------------------------------------------

pf_root = Path(tempfile.mkdtemp(prefix="e2e-value-projfit-"))
(pf_root / "tests" / "e2e").mkdir(parents=True)
(pf_root / "tests" / "e2e" / "test_mixed.py").write_text(
    "def _open_board(page):\n    return page.locator('#row').bounding_box()\n\n\n"
    "def test_row_fits(page):\n    assert _open_board(page)['width'] <= 390\n\n\n"
    "def test_poll_payload(page):\n    assert page.request.get('/api/board').ok\n\n\n"
    "class TestNav:\n    def test_tab_switch(self, page):\n        page.locator('[role=tablist] button').first.click()\n", encoding="utf-8")
(pf_root / "tests" / "e2e" / "test_phone_only.py").write_text(
    "import pytest\n\npytestmark = pytest.mark.usefixtures('iphone_viewport')\n\n\ndef test_menu(page):\n    page.click('#menu')\n", encoding="utf-8")
pf_nodes = {
    "tests/e2e/test_mixed.py::test_row_fits[webkit]": 3.0, "tests/e2e/test_mixed.py::test_row_fits[chromium]": 2.0,
    "tests/e2e/test_mixed.py::test_poll_payload[webkit]": 4.0, "tests/e2e/test_mixed.py::test_poll_payload[chromium]": 2.5,
    "tests/e2e/test_mixed.py::TestNav::test_tab_switch[webkit-390]": 1.5,
    "tests/e2e/test_phone_only.py::test_menu[webkit]": 2.0,
    "tests/e2e/test_gone.py::test_x[webkit]": 1.0,
}
pf = v.projection_fit(pf_nodes, pf_root)
check(pf["candidates"] == [{"module": "tests/e2e/test_mixed.py", "tests": 1, "nodes": 1, "seconds": 4.0}] and pf["candidate_seconds"] == 4.0,
      f"a behavioural test with no engine or viewport signal is a Chromium-only candidate; Chromium nodes are not counted -- {pf['candidates']}")
check(pf["kept_nodes"] == 4 and {r["module"] for r in pf["kept"]} == {"tests/e2e/test_mixed.py", "tests/e2e/test_phone_only.py", "tests/e2e/test_gone.py"},
      "kept: a signal in a called helper, a nav tablist in a class test, a module-level viewport fixture, and an unreadable module")
check(v.projection_fit({"tests/e2e/test_mixed.py::test_poll_payload[chromium]": 2.5}, pf_root)["candidates"] == [],
      "a Chromium-only suite has nothing to move")
