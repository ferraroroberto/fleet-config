"""Keep the browser suites' fixture servers off the loopback ephemeral-port pool.

Every closed loopback connection parks an (ephemeral port, server port) pair in TIME_WAIT, and Windows
fails a `connect()` that lands on a parked pair with `net::ERR_ADDRESS_IN_USE` instead of trying another
port. The stdlib static handlers speak HTTP/1.0, so a browser opened one socket per request; HTTP/1.1
keep-alive lets one page load share a few sockets. The pattern is project-scaffolding's e2e loopback
harness (docs/playwright-ui-testing.md there); this file carries the parts that apply to a harness
serving fixtures rather than an app.
"""
from __future__ import annotations

from http.server import SimpleHTTPRequestHandler

PORT_POOL_MARKER = "ERR_ADDRESS_IN_USE"
PORT_POOL_HEADLINE = (
    "PORT POOL EXHAUSTED -- this is the host running out of loopback ephemeral ports "
    f"({PORT_POOL_MARKER}), not a page failure. Another gate or a recent run is holding "
    "ports in TIME_WAIT; wait a few minutes and re-run rather than debugging the page."
)


def is_port_pool_failure(text: str) -> bool:
    return PORT_POOL_MARKER in text


class QuietStaticHandler(SimpleHTTPRequestHandler):
    """Static handler that keeps the connection open between requests and logs nothing.

    HTTP/1.1 keep-alive needs every response to carry a Content-Length, which the stdlib file, directory-redirect
    and error responses all do; a subclass answering with its own body must send one too or the client waits for EOF.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *a: object) -> None:
        pass
