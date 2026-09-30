"""Unit tests for skills/_lib/stall_probe.py (fleet-config#1106).

A stall can't be caused on demand, so each detector is driven with an injected
delay: a late `sleep` for the scheduler leg, a slow loopback server for the
HTTP leg. Evidence capture is faked for the rate-limit logic, and its cheap
real sources (PDH, memory status, netstat) are run once for their shape.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_stall_probe.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import http.server
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import stall_probe as sp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


def records(folder: Path) -> list:
    path = folder / "stalls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


tmp = Path(tempfile.mkdtemp(prefix="stall_probe_"))
try:
    # ---- scheduler leg: a sleep that wakes late is a stall; heartbeat proves the probe ran ----
    folder = sp.probe_dir(tmp / "a")
    folder.mkdir(parents=True)
    calls = []
    probe = sp.Probe(sp.Log(folder), threshold=0.3, evidence=lambda: calls.append(1) or {"fake": True})
    late = iter([0.8])
    probe.sleep = lambda s: time.sleep(next(late, s))
    probe.run(duration_s=1.2)
    check(wait_for(lambda: any(r["kind"] == "sleep" for r in records(folder))), f"a late sleep is logged -- {records(folder)}")
    sleep_rec = next((r for r in records(folder) if r["kind"] == "sleep"), {})
    check(UTC.fullmatch(sleep_rec.get("start_utc", "")) and UTC.fullmatch(sleep_rec.get("end_utc", ""))
          and 0.7 <= sleep_rec.get("gap_s", 0) < 2 and sleep_rec.get("evidence") == {"fake": True},
          f"the stall record carries UTC start/end, the gap and the captured evidence -- {sleep_rec}")
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    check(UTC.fullmatch(status.get("heartbeat_utc", "")) and status.get("threshold_s") == 0.3,
          f"status.json carries a UTC heartbeat, so a quiet log can be told from a dead probe -- {status}")

    # ---- evidence is rate-limited: a second stall inside the cooldown is logged without it ----
    probe.stall("http", time.time(), 2.0)
    check(wait_for(lambda: any(r["kind"] == "http" for r in records(folder))), "a second stall is still logged")
    http_rec = next((r for r in records(folder) if r["kind"] == "http"), {})
    check(str(http_rec.get("evidence", "")).startswith("skipped") and len(calls) == 1,
          f"evidence is captured once per cooldown, not per stall -- {http_rec} calls={len(calls)}")

    # ---- HTTP leg: a slow loopback response is a stall ----
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(0.6)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_a):
            pass
    slow = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    folder_b = sp.probe_dir(tmp / "b")
    folder_b.mkdir(parents=True)
    probe_b = sp.Probe(sp.Log(folder_b), threshold=0.3, evidence=lambda: {})
    probe_b.url = f"http://127.0.0.1:{slow.server_address[1]}/probe.txt"
    probe_b.run(duration_s=2.5)
    slow.shutdown()
    check(any(r["kind"] == "http" and r["gap_s"] >= 0.5 for r in records(folder_b)),
          f"a slow loopback GET is logged as an http stall -- {records(folder_b)}")
    check(not any(r["kind"] == "sleep" for r in records(folder_b)), "no scheduler stall is invented on a quiet run")

    # ---- one instance at a time ----
    first = sp.single_instance(folder_b)
    second = sp.single_instance(folder_b)
    check(first is not None and second is None, "a second probe on the same state dir is refused")
    first.close()

    # ---- the real cheap evidence sources have the expected shape ----
    pdh = sp._safely(sp.pdh_counters)
    check(isinstance(pdh.get(r"\Memory\Committed Bytes"), float) and r"\Process(VmmemWSL)\Working Set" in pdh,
          f"PDH counters read by English name -- {pdh}")
    mem = sp._safely(sp.memory_status)
    check(mem.get("commit_limit_gb", 0) > 0 and "commit_used_gb" in mem, f"GlobalMemoryStatusEx shape -- {mem}")
    tcp = sp._safely(sp.tcp_states)
    check(isinstance(tcp, dict) and "error" not in tcp and sum(tcp.values()) > 0, f"netstat TCP state counts -- {tcp}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_stall_probe")
