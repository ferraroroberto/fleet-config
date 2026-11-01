"""Fixture preview for the web-page review tests: serves ../site with pretty URLs, _redirects and a 404 page.

The same URL rules as a Cloudflare Pages host (and the scaffold's `site_serve.py`), in the
smallest stdlib form: `/x` serves `x.html`, `/` serves `index.html`, an exact `_redirects`
line redirects, a miss serves `404.html` with status 404. Takes `--host` and `--port`.
"""
from __future__ import annotations

import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

SITE = Path(__file__).resolve().parent.parent / "site"


def load_redirects() -> dict:
    rules = {}
    path = SITE / "_redirects"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#"):
            rules[parts[0]] = (parts[1], int(parts[2]) if len(parts) > 2 else 301)
    return rules


REDIRECTS = load_redirects()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's method name
        path = urlsplit(self.path).path
        if path in REDIRECTS:
            self.send_response(REDIRECTS[path][1])
            self.send_header("Location", REDIRECTS[path][0])
            self.end_headers()
            return
        rel = path.strip("/") or "index"
        for candidate in (SITE / rel, SITE / f"{rel}.html", SITE / rel / "index.html"):
            if candidate.is_file() and SITE in candidate.resolve().parents:
                self.send_file(candidate, 200)
                return
        self.send_file(SITE / "404.html", 404)

    def send_file(self, file: Path, status: int) -> None:
        body = file.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base-class signature
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8130)
    args = ap.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
