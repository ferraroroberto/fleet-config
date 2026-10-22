"""`chief_ops.py` when the launcher is slow or gone: a distinct line and exit code, never a traceback.

A real loopback server stands in for the launcher:
  - a launcher silent past the budget makes `board`, `say` and `stop` print `UNCONFIRMED=timeout ...`
    and exit 1;
  - a refused connection prints `UNREACHABLE=...` and exits 2, distinct from the timeout;
  - `stop` after a timeout reads the board once and reports `STOPPED` or `STILL_LIVE` from it, and
    `UNCONFIRMED` when that read fails too;
  - success output and an HTTP error status keep their existing shape.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_chief_ops_transport.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
import chief_ops as co  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

BUDGET = 0.3
SLOW = 1.2
SID = "a1b2c3d4" * 4
CARD = {"session_id": SID, "alive": True, "kind": "pty", "project": "fleet-config", "status": "working"}
ALIVE = {"columns": {"claude_turn": [CARD]}}
GONE = {"columns": {"claude_turn": [dict(CARD, alive=False)]}}
EMPTY = {"columns": {}}


class Launcher:
    """Loopback stand-in. `slow` paths sleep past the budget; `/api/board` answers `boards[n]`
    on its n-th call (the last entry repeats)."""

    def __init__(self, slow=(), boards=(EMPTY,)):
        self.slow, self.boards, self.board_calls = set(slow), list(boards), 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self):
                path = self.path.split("?")[0]
                if path in outer.slow:
                    time.sleep(SLOW)
                if path == "/api/board":
                    body = outer.boards[min(outer.board_calls, len(outer.boards) - 1)]
                    outer.board_calls += 1
                elif path == "/missing":
                    self.send_error(404)
                    return
                else:
                    body = {}
                payload = json.dumps(body).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # the client gave up on a deliberately slow path

            do_GET = do_POST = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


_real_urlopen = urllib.request.urlopen
_budget = [BUDGET]


def _short_budget(*args, **kwargs):
    kwargs["timeout"] = _budget[0]
    return _real_urlopen(*args, **kwargs)


def run(*argv, budget=BUDGET):
    out, err = io.StringIO(), io.StringIO()
    _budget[0] = budget
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(co.launcher.urllib.request, "urlopen", _short_budget), \
            mock.patch.object(co.launcher, "REQUEST_TIMEOUT", BUDGET, create=True):
        code = co.main(list(argv))
    return code, out.getvalue(), err.getvalue()


# ---- a launcher silent past the budget ------------------------------------------------------
srv = Launcher(slow={"/api/board"})
try:
    code, out, err = run("board", "--base-url", srv.url)
    check(code == 1 and out.strip() == f"UNCONFIRMED=timeout after {BUDGET:g}s" and "Traceback" not in err,
          f"board: a slow launcher prints the timeout line and exits 1 (got {code}, {out!r}, {err!r})")
    code, out, err = run("say", SID, "--base-url", srv.url)
    check(code == 1 and out.startswith("UNCONFIRMED=timeout") and "Traceback" not in err,
          f"say: a slow launcher prints the timeout line and exits 1 (got {code}, {out!r}, {err!r})")
    code, out, err = run("stop", SID, "--base-url", srv.url)
    check(code == 1 and out.startswith("UNCONFIRMED=timeout") and "Traceback" not in err,
          f"stop: a slow launcher prints the timeout line and exits 1 (got {code}, {out!r}, {err!r})")
finally:
    srv.close()

# ---- stop: the POST times out, the follow-up read decides ---------------------------------------
stop_path = f"/api/claude-code/sessions/{SID}/stop"
for after, want_code, want_word in ((GONE, 0, "STOPPED"), (ALIVE, 1, "STILL_LIVE"), (EMPTY, 0, "STOPPED")):
    srv = Launcher(slow={stop_path}, boards=(ALIVE, after))
    try:
        code, out, err = run("stop", SID, "--base-url", srv.url)
        check(code == want_code and out.startswith(f"{want_word} sid={SID} mode=quit") and "timed out" in out
              and "Traceback" not in err,
              f"stop: timeout then a board showing {want_word} reports it (got {code}, {out!r}, {err!r})")
        check(srv.board_calls == 2, f"stop: exactly one follow-up board read (saw {srv.board_calls - 1})")
    finally:
        srv.close()

# The follow-up read is the board's second call; make that one time out too.
srv = Launcher(slow={stop_path}, boards=(ALIVE,))
_real_request = co.launcher._request
_board_reads = {"n": 0}


def _flaky_request(base, path, *args, **kwargs):
    if path == "/api/board":
        _board_reads["n"] += 1
        if _board_reads["n"] > 1:
            raise TimeoutError("timed out")
    return _real_request(base, path, *args, **kwargs)


try:
    with mock.patch.object(co.launcher, "_request", _flaky_request):
        code, out, err = run("stop", SID, "--base-url", srv.url)
    check(code == 1 and out.startswith("UNCONFIRMED=timeout") and "follow-up board read failed" in out,
          f"stop: a follow-up read that also times out stays UNCONFIRMED (got {code}, {out!r}, {err!r})")
finally:
    srv.close()

# ---- a refused connection --------------------------------------------------------------------
_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
_dead_url = f"http://127.0.0.1:{_sock.getsockname()[1]}"
_sock.close()
code, out, err = run("board", "--base-url", _dead_url, budget=10.0)
check(code == 2 and out.startswith("UNREACHABLE=") and "UNCONFIRMED" not in out and "Traceback" not in err,
      f"board: a refused connection prints UNREACHABLE, distinct from the timeout (got {code}, {out!r}, {err!r})")

# ---- unchanged paths -------------------------------------------------------------------------------
srv = Launcher(boards=(ALIVE,))
try:
    code, out, err = run("stop", SID, "--base-url", srv.url)
    check(code == 0 and out == f"STOPPED sid={SID} mode=quit\n" and err == "",
          f"stop: the success line is unchanged (got {code}, {out!r}, {err!r})")
    code, out, _ = run("board", "--base-url", srv.url)
    check(code == 0 and "fleet-config: working" in out and err == "" and "UNCONFIRMED" not in out,
          f"board: the success output is unchanged (got {out!r})")
    try:
        co.launcher._request(srv.url, "/missing")
        check(False, "an HTTP 404 raised nothing")
    except Exception as exc:  # noqa: BLE001
        check(co.launcher.transport_failure(exc) is None,
              "transport_failure: an HTTP status is neither unconfirmed nor unreachable")
finally:
    srv.close()

_h.report_and_exit("test_chief_ops_transport")
