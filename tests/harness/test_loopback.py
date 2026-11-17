"""The browser suites' loopback helper: keep-alive on the fixture servers and the named port-pool failure.

A fixture server that answers HTTP/1.0 costs one loopback socket per request, each parked in TIME_WAIT
afterwards; HTTP/1.1 keep-alive lets a browser reuse one. A failure that carries `ERR_ADDRESS_IN_USE` is a
host-capacity problem and the shared check harness must say so instead of reporting a page defect.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/harness/test_loopback.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import functools
import http.client
import io
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests" / "_lib"))

from check_harness import CheckHarness
from loopback import PORT_POOL_HEADLINE, QuietStaticHandler, is_port_pool_failure

_h = CheckHarness()
check = _h.check

# keep-alive: several GETs ride one socket against the shared static handler
with tempfile.TemporaryDirectory(prefix="loopback-test-") as tmp:
    (Path(tmp) / "a.txt").write_text("alpha", encoding="utf-8")
    (Path(tmp) / "b.txt").write_text("bravo", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietStaticHandler, directory=tmp))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    try:
        ports = set()
        bodies = []
        for name in ("a.txt", "b.txt", "a.txt"):
            conn.request("GET", f"/{name}")
            resp = conn.getresponse()
            bodies.append(resp.read())
            check(resp.version == 11, f"{name}: answered HTTP/1.1, got {resp.version}")
            check(conn.sock is not None, f"{name}: the connection stayed open after the response")
            if conn.sock is not None:
                ports.add(conn.sock.getsockname()[1])
        check(bodies == [b"alpha", b"bravo", b"alpha"], f"bodies served intact over one connection: {bodies}")
        check(len(ports) == 1, f"three requests used one client socket, saw ports {ports}")
    finally:
        conn.close()
        server.shutdown()
        server.server_close()

# the marker names the host problem, not a page failure
check(is_port_pool_failure("page.goto: net::ERR_ADDRESS_IN_USE at http://127.0.0.1:5/"), "ERR_ADDRESS_IN_USE is a port-pool failure")
check(not is_port_pool_failure("net::ERR_CONNECTION_REFUSED"), "a refused connection is not a port-pool failure")


def run_harness(msg: str) -> tuple[int, str]:
    h = CheckHarness()
    h.check(False, msg)
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            h.report_and_exit("probe")
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, buf.getvalue()


code, out = run_harness("walk failed: net::ERR_ADDRESS_IN_USE")
check(code == 1, f"a port-pool failure still fails the run, exit {code}")
check("  - PORT POOL EXHAUSTED -- walk failed" in out, f"the failing line leads with the name: {out!r}")
check(PORT_POOL_HEADLINE in out, "the report carries the headline telling the reader to wait and re-run")

code, out = run_harness("walk failed: net::ERR_CONNECTION_REFUSED")
check(code == 1 and "PORT POOL EXHAUSTED" not in out, f"any other failure is reported as itself: {out!r}")

_h.report_and_exit("test_loopback")
