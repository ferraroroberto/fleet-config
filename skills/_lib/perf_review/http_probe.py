"""The server-side leg of /perf-review: GET-only endpoint timing (fleet-config#1121).

Generalizes app-launcher's `scripts/probe_endpoint_timing.py` (#1345): every
endpoint gets its own thread and its own spacing — the poll interval the page
itself was seen using — so the probe loads the app the way one phone does,
never harder. The first request per endpoint is the cold one: reported apart,
kept out of the percentiles. Stdlib only, and only ever GET.
"""
from __future__ import annotations

import gzip
import math
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

TIMEOUT_S = 30.0
# Pseudo-statuses, reported where a status goes. A response that never ends (an SSE feed, a long poll
# that keeps pinging) defeats a socket timeout, which only bounds the gap between bytes, so each
# request also carries a deadline on the whole exchange (fleet-config#1221).
SKIPPED_STREAM = "skipped (stream)"
TIMEOUT = "timeout"


def _tls() -> ssl.SSLContext:
    # Loopback against a cert issued for the tailnet name: identity is not the
    # question here, only timing. Never used for anything but this probe.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _tls()


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile (the #1345 definition); `None` on no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def get(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[float, object, bytes, Dict[str, str]]:
    """One GET: `(elapsed_ms, status, body, lowercased headers)`.

    `status` is the HTTP code, or a name for what stopped the request: `SKIPPED_STREAM` for a
    `text/event-stream` response (decided from the headers, the body is never read), `TIMEOUT`
    when `TIMEOUT_S` ran out on the whole request, or the exception name on a transport failure.
    """
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    t0 = time.perf_counter()
    deadline = t0 + TIMEOUT_S
    hdrs: Dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=TIMEOUT_S) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            status: object = resp.status
            if hdrs.get("content-type", "").lower().startswith("text/event-stream"):
                body, status = b"", SKIPPED_STREAM
            else:
                body = _read_until(resp, deadline)
                if body is None:
                    body, status = b"", TIMEOUT
    except urllib.error.HTTPError as exc:  # 304 and 4xx/5xx arrive here
        body, status = exc.read() or b"", exc.code
        hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
    except (TimeoutError, socket.timeout):
        body, status, hdrs = b"", TIMEOUT, {}
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(reason, (TimeoutError, socket.timeout))
        body, status, hdrs = b"", TIMEOUT if timed_out else type(exc).__name__, {}
    return round((time.perf_counter() - t0) * 1000, 1), status, body, hdrs


def _read_until(resp, deadline: float) -> Optional[bytes]:
    """The whole body, or `None` when it was still arriving at `deadline`."""
    chunks: List[bytes] = []
    while True:
        if time.perf_counter() >= deadline:
            return None
        chunk = resp.read1(65536)  # read() would wait for the full 64 KB of a slow dribble
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def index_checks(base_url: str) -> Dict[str, object]:
    """Does `/` arrive compressed, and does a repeat with its ETag cost no body?

    `compressed`/`revalidates` are `None` (unknown) when `/` did not answer
    200 — never folded into a pass or a fail.
    """
    url = base_url.rstrip("/") + "/"
    _, status, body, hdrs = get(url, {"Accept-Encoding": "gzip"})
    out: Dict[str, object] = {"status": status, "compressed": None, "etag": None,
                              "revalidates": None, "bytes": None, "bytes_gzip": None}
    if status != 200:
        return out
    encoded = hdrs.get("content-encoding", "") in ("gzip", "br", "deflate")
    raw = gzip.decompress(body) if hdrs.get("content-encoding") == "gzip" else body
    etag = hdrs.get("etag")
    out.update(compressed=encoded, etag=bool(etag), bytes=len(raw),
               bytes_gzip=len(body) if encoded else len(gzip.compress(raw, 6)))
    if not etag:
        out["revalidates"] = False
        return out
    _, again, again_body, _ = get(url, {"If-None-Match": etag})
    out["revalidates"] = again == 304 and not again_body
    return out


def time_endpoints(base_url: str, spacing_s: Dict[str, float], duration_s: float,
                   stagger_s: float = 0.3) -> Dict[str, Dict[str, object]]:
    """Time every path in `spacing_s` for `duration_s`, each at its own spacing, in parallel."""
    rows: Dict[str, List[Tuple[float, object, int]]] = {p: [] for p in spacing_s}
    base = base_url.rstrip("/")

    def loop(path: str, every: float) -> None:
        end = time.monotonic() + duration_s
        while True:
            ms, status, body, _ = get(base + path)
            rows[path].append((ms, status, len(body)))
            if status == SKIPPED_STREAM:  # one look is enough; polling a feed would only open it again
                return
            if time.monotonic() + every >= end:
                return
            time.sleep(max(0.0, every - ms / 1000.0))

    threads = [threading.Thread(target=loop, args=(p, s), daemon=True) for p, s in spacing_s.items()]
    for t in threads:
        t.start()
        time.sleep(stagger_s)
    for t in threads:
        t.join()
    return {p: summarize(r, spacing_s[p]) for p, r in rows.items()}


def summarize(rows: List[Tuple[float, object, int]], spacing: float) -> Dict[str, object]:
    """Cold request apart; p50/p95/max over the rest; every status seen."""
    warm = [ms for ms, _, _ in rows[1:]]
    return {"n": len(warm), "spacing_s": spacing, "cold_ms": rows[0][0] if rows else None,
            "p50": percentile(warm, 50), "p95": percentile(warm, 95),
            "max": max(warm) if warm else None,
            "codes": sorted({str(s) for _, s, _ in rows}), "bytes": rows[-1][2] if rows else None}
