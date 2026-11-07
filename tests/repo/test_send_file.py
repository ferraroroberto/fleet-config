"""Unit tests for skills/_lib/send_file.py (fleet-config#1376).

Drives `send()` / `main()` against a throwaway loopback HTTP server that plays
the launcher's outbox endpoint (app-launcher#1530), so every distinct outcome --
sent, each refusal, each unavailable condition -- is checked through the real
urllib error paths with no live launcher and no real file sent.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_send_file.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import send_file as sf  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SID = "0123456789abcdef0123456789abcdef"
ENV = {"APP_LAUNCHER_SESSION_ID": SID}
seen: list = []
# What the fake launcher answers next: (status, raw body bytes)
reply = {"status": 200, "body": b""}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        seen.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
        self.send_response(reply["status"])
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reply["body"])

    def log_message(self, *args):  # silence
        pass


def answer(status: int, payload) -> None:
    reply["status"] = status
    reply["body"] = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")


server = HTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{server.server_address[1]}"


def run(path="note.txt", caption=None, env=ENV, base=URL):
    return sf.send(path, caption, env=env, base_url=base)


try:
    # ---- sent ----
    answer(200, {"ok": True, "session_id": SID, "entry": {"id": "e1f2", "name": "note.txt"}})
    code, line = run("note.txt", "a caption")
    check(code == 0 and line == "SENT=e1f2", "sent: exit 0 and SENT=<entry id>")
    path, body = seen[-1]
    check(path == f"/api/claude-code/sessions/{SID}/outbox", "sent: posts to the caller's own session outbox")
    check(Path(body["path"]).is_absolute() and body["caption"] == "a caption", "sent: absolute path and caption pass through")
    run("note.txt")
    check("caption" not in seen[-1][1], "sent: no caption key when none given")

    # ---- a 2xx that is not an accepted entry is never SENT ----
    for label, payload in (("no entry", {"ok": True}), ("ok false", {"ok": False, "entry": {"id": "x"}}),
                           ("entry without id", {"ok": True, "entry": {}}), ("not an object", [1])):
        answer(200, payload)
        code, line = run()
        check(code == 2 and line.startswith("UNAVAILABLE=unexpected-reply"), f"2xx {label}: UNAVAILABLE, not SENT")
    answer(200, b"<html>not json</html>")
    code, line = run()
    check(code == 2 and line.startswith("UNAVAILABLE=unexpected-reply"), "2xx non-JSON body: UNAVAILABLE, not SENT")

    # ---- each launcher refusal keeps its own reason ----
    for status, reason in ((403, "out_of_root"), (413, "too_large"), (415, "type_not_allowed"), (404, "not_found"),
                           (422, "not_a_file"), (415, "content_mismatch"), (400, "bad_request"),
                           (403, "loopback_only"), (404, "session_not_found")):
        answer(status, {"detail": {"reason": reason, "message": f"msg for {reason}"}})
        code, line = run()
        check(code == 1 and line == f"REFUSED={reason}: msg for {reason}", f"refused: {reason} relayed with its message (HTTP {status})")

    # ---- unavailable, each distinct ----
    answer(503, {"detail": {"reason": "session_host_unreachable", "message": "the session-host is not answering"}})
    code, line = run()
    check(code == 2 and line.startswith("UNAVAILABLE=session_host_unreachable"), "503: UNAVAILABLE=session_host_unreachable, not a refusal")
    answer(404, {"detail": "Not Found"})
    code, line = run()
    check(code == 2 and line.startswith("UNAVAILABLE=endpoint-missing"), "bare 404 (no outbox route): UNAVAILABLE=endpoint-missing")
    answer(500, b"boom")
    code, line = run()
    check(code == 2 and line.startswith("UNAVAILABLE=unexpected-http-500"), "500: UNAVAILABLE=unexpected-http-500")
    n = len(seen)
    code, line = run(env={})
    check(code == 2 and line.startswith("UNAVAILABLE=no-launcher-session") and len(seen) == n,
          "no session id: UNAVAILABLE=no-launcher-session, nothing posted")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    code, line = run(base=dead)
    check(code == 2 and line.startswith("UNAVAILABLE=launcher-unreachable"), "launcher down: UNAVAILABLE=launcher-unreachable")
    n = len(seen)
    code, line = run(base="http://example.com")
    check(code == 2 and line.startswith("UNAVAILABLE=bad-base-url") and len(seen) == n,
          "non-loopback base url: refused before any request")

    # ---- inside a scheduled Job: no session, the run identity addresses the job outbox ----
    JOB_ENV = {"APP_LAUNCHER_JOB_ID": "job-a", "APP_LAUNCHER_JOB_RUN_ID": "20261107T090000"}
    answer(200, {"ok": True, "entry": {"id": "j1"}})
    code, line = run("chart.png", "cap", env=JOB_ENV)
    check(code == 0 and line == "SENT=j1", "job: exit 0 and SENT=<entry id>")
    path, body = seen[-1]
    check(path == "/api/jobs/job-a/runs/20261107T090000/outbox", "job: posts to the run's own job outbox")
    check(Path(body["path"]).is_absolute() and body["caption"] == "cap", "job: absolute path and caption pass through")
    code, line = run(env={**JOB_ENV, "APP_LAUNCHER_SESSION_ID": SID})
    check(code == 0 and seen[-1][0] == f"/api/claude-code/sessions/{SID}/outbox", "session id wins when both are present")
    n = len(seen)
    for label, partial in (("job id only", {"APP_LAUNCHER_JOB_ID": "job-a"}),
                           ("run id only", {"APP_LAUNCHER_JOB_RUN_ID": "r1"}),
                           ("blank ids", {"APP_LAUNCHER_JOB_ID": " ", "APP_LAUNCHER_JOB_RUN_ID": ""})):
        code, line = run(env=partial)
        check(code == 2 and line.startswith("UNAVAILABLE=no-launcher-session"), f"job identity incomplete ({label}): no-launcher-session")
    check(len(seen) == n, "job identity incomplete: nothing posted")
    code, line = run(env={"APP_LAUNCHER_JOB_ID": "../x", "APP_LAUNCHER_JOB_RUN_ID": "a/b"})
    check(seen[-1][0] == "/api/jobs/..%2Fx/runs/a%2Fb/outbox", "job: ids are percent-encoded into the path")
    answer(404, {"detail": "Not Found"})
    code, line = run(env=JOB_ENV)
    check(code == 2 and line.startswith("UNAVAILABLE=job-outbox-missing"), "job: older launcher without the route is job-outbox-missing")
    for status, reason in ((404, "job_not_found"), (404, "run_not_found"), (409, "run_not_running"),
                           (403, "out_of_root"), (400, "bad_request"), (403, "loopback_only")):
        answer(status, {"detail": {"reason": reason, "message": f"msg for {reason}"}})
        code, line = run(env=JOB_ENV)
        check(code == 1 and line == f"REFUSED={reason}: msg for {reason}", f"job refused: {reason} relayed with its message (HTTP {status})")
    answer(503, {"detail": {"reason": "session_host_unreachable", "message": "x"}})
    code, line = run(env=JOB_ENV)
    check(code == 2 and line.startswith("UNAVAILABLE=session_host_unreachable"), "job: 503 stays UNAVAILABLE")

    # ---- the CLI prints exactly one line and returns the code ----
    answer(200, {"ok": True, "entry": {"id": "cli1"}})
    out = io.StringIO()
    saved = os.environ.get("APP_LAUNCHER_SESSION_ID")
    os.environ["APP_LAUNCHER_SESSION_ID"] = SID
    try:
        with contextlib.redirect_stdout(out):
            code = sf.main(["note.txt", "--caption", "c", "--base-url", URL])
    finally:
        if saved is None:
            os.environ.pop("APP_LAUNCHER_SESSION_ID", None)
        else:
            os.environ["APP_LAUNCHER_SESSION_ID"] = saved
    check(code == 0 and out.getvalue() == "SENT=cli1\n", "CLI: one SENT line, exit 0")
finally:
    server.shutdown()

_h.report_and_exit("test_send_file")
