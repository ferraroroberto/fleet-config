"""An all-routes stall is `unknown (machine busy)`, not over budget (fleet-config#2053).

A sweep that shared its window with a backup read every route, `/` included, over budget and filed a finding about
nothing. The stall is injected as a delay on a local fixture server and timed by the real endpoint timer, so no live
app, no perf sweep and no CPU burn is involved.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_perf_stall.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

os.environ["CLAUDE_HOOKS_STATE_DIR"] = tempfile.mkdtemp(prefix="perf-stall-state-")

from check_harness import CheckHarness  # noqa: E402
from perf_review import http_probe, report  # noqa: E402

_h = CheckHarness()
check = _h.check

ROUTES = {"/": 0.05, "/api/fast": 0.05, "/api/slow": 0.05}  # path -> spacing, the timer's own shape


def serve(delay: dict) -> tuple[ThreadingHTTPServer, str]:
    """A fixture app whose every request to a path in `delay` is held that many seconds."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            time.sleep(delay.get(self.path, 0.0))
            body = b'{"ok": true}'
            self.send_response(200 if self.path in ROUTES else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


budgets = report.load_budgets()
modern = {"compressed": True, "etag": True, "revalidates": True, "bytes": 100}
leg = lambda **kw: {"status": "ok", "ready_ms": 500, "data_ms": 900, "bytes": 50 * 1024, **kw}  # noqa: E731
load = {"legs": {"android_cold": leg(), "android_warm": leg(cache="trusted")}}


def verdict_for(probe_rows: dict) -> dict:
    return report.verdict({"index": modern, "endpoints": probe_rows}, load, budgets)


def timed_under(delay: dict) -> dict:
    srv, url = serve(delay)
    try:
        return http_probe.time_endpoints(url, ROUTES, 1.5, stagger_s=0)
    finally:
        srv.shutdown()


def row(p95: float, codes: list | None = None) -> dict:
    return {"n": 5, "p95": p95, "codes": codes or ["200"]}


# ---- a delay on every route is unknown, not a breach ----
stall_v = verdict_for(timed_under({"/": 0.3, "/api/fast": 0.3, "/api/slow": 0.3}))
by_check = {c["id"]: c for c in stall_v["checks"]}
check({e["path"]: e["status"] for e in stall_v["endpoints"]} == {"/": "unmeasured", "/api/fast": "unmeasured", "/api/slow": "unmeasured"},
      f"a delay on every route reads unmeasured on every route ({[(e['path'], e['status'], e.get('p95')) for e in stall_v['endpoints']]})")
check(by_check["endpoints.index_p95_ms"]["status"] == "unmeasured" and by_check["endpoints.api_p95_ms"]["status"] == "unmeasured"
      and by_check["endpoints.api_p95_ms"]["measured"] is None and "machine busy" in by_check["endpoints.api_p95_ms"]["reason"],
      "both endpoint checks are unmeasured with the machine-busy reason, not a measured number")
check(stall_v["summary"]["fail"] == 0 and stall_v["summary"]["overall"] == "unmeasured",
      f"nothing is over budget and the run is unmeasured ({stall_v['summary']})")
check(sorted(stall_v["stall"]["routes"]) == ["/", "/api/fast", "/api/slow"] and "including `/`" in stall_v["stall"]["reason"],
      "the report names the signal: every timed route over budget at once, `/` included")
no_issue = report.filing_decision("", None, stall_v)
check(no_issue["action"] == "none" and "machine busy" in no_issue["reason"], "a stalled window opens no issue, and the reason says why")
check(report.filing_decision("## Fixes\n- [ ] x", 7, stall_v)["action"] == "none", "a stalled window leaves an existing issue alone")
body = report.render_body(stall_v, "r1", "b1")
check("Endpoint timing is unknown (machine busy" in body and "Nothing is filed" in body, "the issue body states the unknown state and the signal")

# ---- a real hot spot is still a breach ----
one_slow_v = verdict_for(timed_under({"/api/fast": 0.4}))
one_slow = {e["path"]: e["status"] for e in one_slow_v["endpoints"]}
check(one_slow == {"/": "pass", "/api/fast": "fail", "/api/slow": "pass"} and one_slow_v["stall"] is None
      and one_slow_v["summary"]["overall"] == "over-budget",
      f"one slow route among healthy ones still reports over budget ({one_slow})")
mixed_v = verdict_for({"/": row(400.0), "/api/a": row(900.0), "/api/ok": row(20.0)})
check(mixed_v["stall"] is None and mixed_v["summary"]["fail"] >= 1, "routes over budget beside a healthy one are real findings, not a stall")
pair_v = verdict_for({"/": row(400.0), "/api/a": row(900.0)})
check(pair_v["stall"] is None and pair_v["summary"]["overall"] == "over-budget", "two routes alone are too few to call a stall")
feed_v = verdict_for({"/": row(400.0), "/api/a": row(900.0), "/api/stream": {"n": 0, "p95": None, "codes": [http_probe.SKIPPED_STREAM]},
                      "/api/broken": row(5.0, ["500"])})
check(feed_v["stall"] is None, "a skipped feed and an erroring route are not timed, so they count neither toward a stall nor against one")

# ---- the ledger and the next run's diff ----
prev = {"run_id": "p", "checks": {}, "endpoints": {"/api/fast": {"status": "pass", "p95": 20.0}}}
now = {"run_id": "n", "checks": {}, "endpoints": {"/api/fast": {"status": "unmeasured", "p95": 400.0, "stalled": True}}}
moved = report.diff(now, prev)
check(moved["slower"] == [] and moved["regressed"] == [], "a stalled run's p95 is neither a regression nor `slower than the last run`")
check(report.record("stall-app", "20261114T030000Z", stall_v, "c", "b")["endpoints"]["/api/fast"].get("stalled") is True,
      "the ledger marks the stalled routes")

_h.report_and_exit("test_perf_stall")
