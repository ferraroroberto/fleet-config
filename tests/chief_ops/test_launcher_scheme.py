"""chief_ops follows the launcher's HTTP or HTTPS setting instead of assuming one.

Each serve mode is a local fake server on loopback, plain or TLS (a throwaway
self-signed pair built with `openssl`), answering the board route; nothing here
touches the live launcher.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_launcher_scheme.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import http.client
import http.server
import io
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.error
from pathlib import Path

from _suite import _h, check, co  # noqa: E402
from chief import launcher_scheme  # noqa: E402
from dead_code import entry_inventory  # noqa: E402

# isort: split
import stall_probe  # noqa: E402

_BOARD = {"columns": {"claude_turn": [], "your_turn": [], "other": []}, "running_jobs": []}
_JOBS = {"jobs": [
    {"id": "nightly", "running": True, "script_path": "C:/repo/jobs/run.bat", "last_run": {"started_at": "2026-11-24T05:00:00"}},
    {"id": "idle", "running": False, "script_path": "C:/repo/jobs/idle.bat", "paused": True},
]}


def _openssl() -> str:
    found = shutil.which("openssl")
    if found:
        return found
    for cand in ("C:/Program Files/Git/usr/bin/openssl.exe", "C:/Program Files/Git/mingw64/bin/openssl.exe"):
        if Path(cand).is_file():
            return cand
    raise SystemExit("❌ test_launcher_scheme: openssl not found -- cannot build the TLS fixture (NOT CONFIRMED)")


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answers GET /api/board and counts every request it actually handled."""

    handled: list = []

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 -- http.server's naming
        type(self).handled.append(("GET", self.path))
        self._reply({"/api/board": _BOARD, "/api/jobs": _JOBS}.get(self.path, {}))

    def do_POST(self) -> None:  # noqa: N802 -- http.server's naming
        type(self).handled.append(("POST", self.path))
        self._reply({"ok": True})


class _TlsServer(http.server.ThreadingHTTPServer):
    """Accepted sockets speak TLS, handshaking in the handler thread, so a plaintext
    request fails there and its connection drops -- what a real HTTPS launcher does."""

    tls: ssl.SSLContext

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.tls.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def handle_error(self, request, client_address) -> None:
        pass  # a plaintext caller failing the handshake is the case under test


def _start(scheme: str, tls: ssl.SSLContext, port: int = 0) -> http.server.ThreadingHTTPServer:
    if scheme == "https":
        server = _TlsServer(("127.0.0.1", port), _Handler)
        server.tls = tls
    else:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server: http.server.ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _run(argv: list) -> tuple:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = co.main(argv)
        except BaseException as exc:  # noqa: BLE001 -- a traceback is the failure under test
            return -1, f"{type(exc).__name__}: {exc}"
    return rc, out.getvalue()


_tmp = Path(tempfile.mkdtemp(prefix="chief-scheme-"))
try:
    _cert, _key = _tmp / "cert.pem", _tmp / "key.pem"
    subprocess.run(
        [_openssl(), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=127.0.0.1", "-keyout", str(_key), "-out", str(_cert)],
        check=True, capture_output=True, timeout=60,
    )
    _tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _tls.load_cert_chain(str(_cert), str(_key))

    # ---- board reads work against an HTTP-only and an HTTPS-only launcher, whatever the hint ----
    for _serves in ("http", "https"):
        _srv = _start(_serves, _tls)
        _port = _srv.server_address[1]
        try:
            check(launcher_scheme.probe_scheme("127.0.0.1", _port) == _serves,
                  f"probe_scheme names a {_serves}-only port {_serves}")
            for _hint in ("http", "https"):
                launcher_scheme._schemes.clear()
                _Handler.handled.clear()
                rc, out = _run(["board", "--base-url", f"{_hint}://127.0.0.1:{_port}"])
                check(rc == 0 and out.startswith("backlog=") and ("GET", "/api/board") in _Handler.handled,
                      f"board reads a {_serves}-only launcher from a {_hint}:// base URL (rc={rc} {out[:120]!r})")
        finally:
            _stop(_srv)

    # ---- the Jobs API readers follow the serve mode too, whatever the hint ----
    for _serves in ("http", "https"):
        _srv = _start(_serves, _tls)
        _port = _srv.server_address[1]
        try:
            for _hint in ("http", "https"):
                launcher_scheme._schemes.clear()
                _Handler.handled.clear()
                _base = f"{_hint}://127.0.0.1:{_port}"
                check(stall_probe.running_jobs(_base) == ["nightly"],
                      f"stall_probe lists the running jobs of a {_serves}-only launcher from a {_hint}:// base URL")
                _rows = entry_inventory.collect_jobs(Path("C:/repo"), _base)
                check([r["id"] for r in _rows] == ["idle", "nightly"] and _rows[0]["paused"] is True,
                      f"entry_inventory reads the jobs of a {_serves}-only launcher from a {_hint}:// base URL ({_rows})")
                check(_Handler.handled == [("GET", "/api/jobs")] * 2, f"each helper made one Jobs API call ({_Handler.handled})")
        finally:
            _stop(_srv)

    # ---- an unreachable launcher is an unknown with its reason, never an empty job list ----
    _free = socket.socket()
    _free.bind(("127.0.0.1", 0))
    _gone = f"https://127.0.0.1:{_free.getsockname()[1]}"
    _free.close()
    launcher_scheme._schemes.clear()
    _probe = stall_probe._safely(lambda: stall_probe.running_jobs(_gone))
    check(isinstance(_probe, dict) and _probe.get("error"), f"stall_probe records an unreachable launcher as an error ({_probe})")
    _evidence = entry_inventory.collect(Path("C:/repo"), _gone, tasks=False, venv=_tmp / "no-venv", ledger=_tmp / "no-ledger")
    check(_evidence["jobs"] == [] and _evidence["jobs_error"],
          f"entry_inventory records an unreachable launcher as jobs_error, not as no jobs ({_evidence['jobs_error']})")

    # ---- the launcher switched to HTTPS under a cached HTTP answer ----
    _srv = _start("https", _tls)
    _port = _srv.server_address[1]
    _base = f"http://127.0.0.1:{_port}"
    try:
        # A read has no side effect: it re-probes and is retried once over the new scheme.
        launcher_scheme._schemes[("127.0.0.1", _port)] = "http"
        _Handler.handled.clear()
        board = co.launcher._request(_base, "/api/board")
        check(board == _BOARD and _Handler.handled == [("GET", "/api/board")],
              f"a stale-scheme GET is retried once over https ({_Handler.handled})")

        # A write dropped over plain HTTP is never resent: wrong-scheme, not a traceback.
        launcher_scheme._schemes[("127.0.0.1", _port)] = "http"
        _Handler.handled.clear()
        try:
            co.launcher._request(_base, "/api/claude-code/sessions/x/input", method="POST", body={"data": "hi"})
            _exc = None
        except Exception as exc:  # noqa: BLE001 -- the type is what is asserted
            _exc = exc
        check(isinstance(_exc, launcher_scheme.WrongSchemeError),
              f"a stale-scheme POST raises WrongSchemeError ({type(_exc).__name__})")
        check(_Handler.handled == [], f"the dropped POST is not resent ({_Handler.handled})")
        line = co.launcher.transport_failure(_exc) if _exc else None
        check(line is not None and line[0].startswith("WRONG_SCHEME=") and line[1] == 2,
              f"transport_failure reports WRONG_SCHEME= exit 2 ({line})")
        check(launcher_scheme._schemes.get(("127.0.0.1", _port)) == "https",
              "after a wrong-scheme write the next call uses https")

        sent = co.launcher.post_session_input(_base, "x", "hello")
        check(sent["ok"] and _Handler.handled == [("POST", "/api/claude-code/sessions/x/input")],
              f"the next write goes over the new scheme exactly once ({sent} {_Handler.handled})")
    finally:
        _stop(_srv)

    # ---- distinct states: dropped with no answer, refused, non-loopback ----
    check(co.launcher.transport_failure(http.client.RemoteDisconnected("closed"))[0].startswith("UNCONFIRMED="),
          "a connection dropped with no answer reads UNCONFIRMED=, not a traceback")
    _free = socket.socket()
    _free.bind(("127.0.0.1", 0))
    _dead_port = _free.getsockname()[1]
    _free.close()
    launcher_scheme._schemes.clear()
    rc, out = _run(["board", "--base-url", f"https://127.0.0.1:{_dead_port}"])
    check(rc == 2 and out.startswith("UNREACHABLE="), f"a refused port reads UNREACHABLE= exit 2 (rc={rc} {out[:120]!r})")
    try:
        launcher_scheme.request("https://example.invalid:8445", "/api/board")
        _refused = False
    except ValueError:
        _refused = True
    check(_refused, "launcher_scheme.request refuses a non-loopback host before sending")
    try:
        co.launcher._request("http://192.168.1.20:8445", "/api/board")
        _refused = False
    except ValueError:
        _refused = True
    check(_refused, "launcher._request still refuses a LAN host")
    check(isinstance(launcher_scheme.WrongSchemeError("x"), urllib.error.URLError),
          "WrongSchemeError is a URLError, so every existing URLError handler still catches it")
finally:
    launcher_scheme._schemes.clear()
    shutil.rmtree(_tmp, ignore_errors=True)


_h.report_and_exit("test_launcher_scheme")
