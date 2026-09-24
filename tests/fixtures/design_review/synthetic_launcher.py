"""Test double for a target's `[design.review.synthetic]` launcher (fleet-config#995).

Speaks the contract `capture.SyntheticInstance` expects: serves this directory
over loopback HTTP on a free port, prints `URL=<the fixture page>`, and runs
until its stdin reaches EOF. `--fail` exits without printing a URL; `--hang`
ignores the EOF, so the caller has to kill its process tree. Stdlib only.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the stdlib's signature
        pass


def main(argv: list) -> int:
    if "--fail" in argv:
        print("synthetic fixture: refusing to start", flush=True)
        return 3
    handler = functools.partial(_Quiet, directory=str(Path(__file__).resolve().parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"URL=http://127.0.0.1:{server.server_address[1]}/fixture.html", flush=True)
    if "--hang" in argv:
        threading.Event().wait()
    sys.stdin.read()
    server.shutdown()
    print("synthetic fixture: stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
