"""The phone-load leg of /perf-review: a Playwright child (fleet-config#1121).

Spawned by `cli.py` under the **target repo's** `.venv` (where Playwright
lives — this repo's venv is stdlib-only), the same split `/design-review`
uses. Self-contained on purpose: it imports nothing from fleet-config.

Three legs, written to `<out>/load.json`:

  iphone_cold   WebKit, "iPhone 15 Pro Max", fresh context. WebKit cannot be
                throttled and cannot map a hostname, so it reports cold only.
  android_cold  Chromium, "Pixel 7", fresh context, CDP-throttled to the
                phone profile; stays on the page `settle_s` to learn how
                often the app polls each `/api/` path.
  android_warm  a new page in the same context — a PWA relaunch, with the
                HTTP cache and localStorage the cold leg left behind.

`--cold-samples N` (default 3) adds N-1 more fresh-context cold loads after
the warm leg and lists every cold "ready" in `android_cold.ready_samples_ms`;
`/perf-review` scores their median, because one cold load right after an
app restart can read ~3 s on an app that loads in ~370 ms (fleet-config#1140).

Read-only by construction: navigate and wait, nothing else — no click, no
fill. Two traps it must not fall into (both measured, #1121):

  * **No request interception.** Playwright turns the HTTP cache off for a
    page or context with any `route()` installed, so a non-GET guard built
    on routing silently makes every warm load a cold one. Non-GET requests
    are observed and reported instead; the load issues none of its own.
  * **No certificate error on the warm leg.** Chromium never stores a
    response served under a certificate error. With `--tls-name` (the name
    the app's cert is issued for) the browser maps that name to loopback so
    the cert verifies, as it does on the phone; without it, or when it does
    not verify, the warm leg is marked `cache: "untrusted"` and its numbers
    are not compared to a budget.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

PAINT_JS = """() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime;
  return {fcp: fcp || null, load: nav.loadEventEnd || null};
}"""


# Chromium inherits Windows "Automatically detect settings", and an HTTPS hostname is no implicit proxy
# bypass (a bare loopback address is), so a cold autoproxy cache stalls the first request ~2.7 s on WPAD
# (fleet-config#1139). A phone has no such step. Only `direct://` takes effect: in a net log
# `--no-proxy-server` and `--proxy-bypass-list` still report "auto detect, from system".
CHROMIUM_ARGS = ["--proxy-server=direct://"]
SAMPLE_SETTLE_MS = 2000  # an extra cold sample only needs its paint metrics, not the poll-interval window
TOP_RESPONSES = 5  # how many of the largest responses a leg keeps for the transfer checks


def _path(url: str, base: str) -> str:
    parts = urlsplit(url)
    return parts.path if url.startswith(base) else "(external)"


def _ready(page, selector: str, timeout_ms: int, t0: float):
    """`(ready_ms, ready_by)` — the declared selector visible, else first contentful paint."""
    if selector:
        try:
            page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
            return round((time.perf_counter() - t0) * 1000), "selector"
        except PwError:
            return None, "selector-not-visible"
    return None, "fcp"


def _finish(page, ready_ms, ready_by, settle_ms: int) -> dict:
    page.wait_for_timeout(settle_ms)
    paint = page.evaluate(PAINT_JS)
    fcp = round(paint["fcp"]) if paint.get("fcp") else None
    if ready_by == "fcp":
        ready_ms = fcp
    return {"ready_ms": ready_ms, "ready_by": ready_by, "fcp_ms": fcp,
            "load_ms": round(paint["load"]) if paint.get("load") else None}


def webkit_cold(pw, url: str, base: str, a) -> dict:
    browser = pw.webkit.launch()
    try:
        ctx = browser.new_context(ignore_https_errors=True, **pw.devices["iPhone 15 Pro Max"])
        page = ctx.new_page()
        seen, non_get = [], []
        page.on("request", lambda r: non_get.append(r.method + " " + _path(r.url, base)) if r.method != "GET" else None)
        page.on("requestfinished", lambda r: seen.append(r))
        t0 = time.perf_counter()
        page.goto(url, wait_until="commit")
        leg = _finish(page, *_ready(page, a.ready_selector, a.boot_window_ms, t0), settle_ms=a.boot_window_ms)
        origin = page.evaluate("performance.timeOrigin")
        api_done, total = {}, 0
        for r in seen:
            total += r.sizes().get("responseBodySize", 0)
            p = _path(r.url, base)
            done = r.timing["startTime"] + r.timing["responseEnd"] - origin
            if p.startswith("/api/") and done <= a.boot_window_ms:
                api_done.setdefault(p, done)
        leg.update(status="ok", requests=len(seen), bytes=total,
                   data_ms=round(max(api_done.values())) if api_done else None, non_get=non_get)
        return leg
    finally:
        browser.close()


class CdpLeg:
    """Per-request facts from Chrome's network domain: start, finish, bytes on the wire, cache."""

    def __init__(self, ctx, page, base: str, a) -> None:
        self.base, self.rows = base, {}
        self.cdp = ctx.new_cdp_session(page)
        self.cdp.send("Network.enable")
        self.cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": a.rtt_ms,
            "downloadThroughput": a.down_mbps * 1_000_000 / 8, "uploadThroughput": a.up_mbps * 1_000_000 / 8})
        self.cdp.on("Network.requestWillBeSent", self._sent)
        self.cdp.on("Network.requestServedFromCache", lambda ev: self._row(ev).update(cached=True))
        self.cdp.on("Network.responseReceived", self._received)
        self.cdp.on("Network.loadingFinished", self._done)

    def _row(self, ev) -> dict:
        return self.rows.setdefault(ev["requestId"], {"cached": False, "bytes": 0})

    def _sent(self, ev) -> None:
        self._row(ev).update(path=_path(ev["request"]["url"], self.base), method=ev["request"]["method"],
                             query=bool(urlsplit(ev["request"]["url"]).query), start=ev["timestamp"])

    def _received(self, ev) -> None:
        row = self._row(ev)
        if ev["response"].get("fromDiskCache"):
            row["cached"] = True
        headers = {k.lower(): v for k, v in (ev["response"].get("headers") or {}).items()}
        row.update(encoding=headers.get("content-encoding", "identity"), kind=ev.get("type"))

    def _done(self, ev) -> None:
        self._row(ev).update(end=ev["timestamp"], bytes=ev.get("encodedDataLength", 0))

    def summary(self, boot_window_ms: int) -> dict:
        rows = [r for r in self.rows.values() if "start" in r]
        t0 = min((r["start"] for r in rows), default=0)
        api_done = {}
        for r in sorted(rows, key=lambda r: r["start"]):
            if r["path"].startswith("/api/") and "end" in r and (r["end"] - t0) * 1000 <= boot_window_ms:
                api_done.setdefault(r["path"], (r["end"] - t0) * 1000)
        net = [r for r in rows if not r["cached"]]
        return {"requests": len(rows), "from_cache": len(rows) - len(net),
                "bytes": sum(r["bytes"] for r in net),
                "data_ms": round(max(api_done.values())) if api_done else None,
                "top_responses": self.top_responses(net),
                "non_get": [r["method"] + " " + r["path"] for r in rows if r["method"] != "GET"]}

    @staticmethod
    def top_responses(net: list, keep: int = TOP_RESPONSES) -> list:
        """The `keep` biggest network responses: path without query, bytes on the wire, content-encoding, resource type.

        A transfer budget that fails says nothing about *which* bytes; one API payload was 5.56 MB of a "6.6 MB"
        cold load in task-os (#280) while the playbook pointed at assets. Responses with no size are left out.
        """
        ranked = sorted((r for r in net if r["bytes"] > 0), key=lambda r: r["bytes"], reverse=True)[:keep]
        return [{"path": r["path"], "bytes": r["bytes"], "encoding": r.get("encoding", "identity"),
                 "kind": r.get("kind")} for r in ranked]

    def api_paths(self) -> dict:
        """Every `/api/` path seen: its poll interval (median gap between requests) and whether it carried a query."""
        starts, query = {}, {}
        for r in self.rows.values():
            if r.get("path", "").startswith("/api/") and r.get("method") == "GET":
                starts.setdefault(r["path"], []).append(r["start"])
                query[r["path"]] = query.get(r["path"], False) or r["query"]
        out = {}
        for p, ts in starts.items():
            ts.sort()
            gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a > 1.0]
            out[p] = {"interval_s": round(statistics.median(gaps), 1) if gaps else None, "query": query[p]}
        return out


def _cold_sample(browser, pw, ctx_url: str, ctx_base: str, a, trusted: bool) -> Optional[int]:
    """One more cold load in a fresh context (its own empty HTTP cache); its ready_ms, or None."""
    ctx = browser.new_context(ignore_https_errors=not trusted, **pw.devices["Pixel 7"])
    try:
        page = ctx.new_page()
        CdpLeg(ctx, page, ctx_base, a)
        t0 = time.perf_counter()
        page.goto(ctx_url, wait_until="commit")
        return _finish(page, *_ready(page, a.ready_selector, a.boot_window_ms, t0), settle_ms=SAMPLE_SETTLE_MS)["ready_ms"]
    except PwError:
        return None
    finally:
        ctx.close()


def chromium_legs(pw, url: str, base: str, a) -> dict:
    trusted = bool(a.tls_name)
    args = [f"--host-resolver-rules=MAP {a.tls_name} 127.0.0.1"] if trusted else []
    browser = pw.chromium.launch(args=[*CHROMIUM_ARGS, *args])
    try:
        ctx_url, ctx_base = url, base
        if trusted:
            parts = urlsplit(url)
            ctx_url = urlunsplit(parts._replace(netloc=f"{a.tls_name}:{parts.port}"))
            ctx_base = urlunsplit(parts._replace(netloc=f"{a.tls_name}:{parts.port}", path="", query=""))
        ctx = browser.new_context(ignore_https_errors=not trusted, **pw.devices["Pixel 7"])
        page = ctx.new_page()
        cold = CdpLeg(ctx, page, ctx_base, a)
        t0 = time.perf_counter()
        try:
            page.goto(ctx_url, wait_until="commit")
        except PwError:
            if not trusted:
                raise
            browser.close()  # the mapped name did not verify: fall back, warm cache untrusted
            a.tls_name = ""
            return chromium_legs(pw, url, base, a)
        legs = {"android_cold": _finish(page, *_ready(page, a.ready_selector, a.boot_window_ms, t0),
                                        settle_ms=a.settle_s * 1000)}
        legs["android_cold"].update(status="ok", **cold.summary(a.boot_window_ms))
        api = cold.api_paths()
        warm_page = ctx.new_page()
        warm = CdpLeg(ctx, warm_page, ctx_base, a)
        t0 = time.perf_counter()
        warm_page.goto(ctx_url, wait_until="commit")
        legs["android_warm"] = _finish(warm_page, *_ready(warm_page, a.ready_selector, a.boot_window_ms, t0),
                                       settle_ms=a.boot_window_ms)
        legs["android_warm"].update(status="ok", cache="trusted" if trusted or url.startswith("http:") else "untrusted",
                                    **warm.summary(a.boot_window_ms))
        legs["android_cold"]["ready_samples_ms"] = [legs["android_cold"]["ready_ms"]] + [
            _cold_sample(browser, pw, ctx_url, ctx_base, a, trusted) for _ in range(max(a.cold_samples, 1) - 1)]
        return {"legs": legs, "api": api}
    finally:
        browser.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tls-name", default="")
    ap.add_argument("--ready-selector", default="")
    ap.add_argument("--rtt-ms", type=float, default=80)
    ap.add_argument("--down-mbps", type=float, default=10)
    ap.add_argument("--up-mbps", type=float, default=5)
    ap.add_argument("--settle-s", type=int, default=40)
    ap.add_argument("--boot-window-ms", type=int, default=8000)
    ap.add_argument("--cold-samples", type=int, default=3)
    ap.add_argument("--skip-webkit", action="store_true")
    a = ap.parse_args(argv)
    url = a.url.rstrip("/") + "/"
    base = a.url.rstrip("/")
    doc = {"legs": {}, "api": {}}
    with sync_playwright() as pw:
        for name, fn in (("iphone_cold", None if a.skip_webkit else webkit_cold), ("android", chromium_legs)):
            if fn is None:
                continue
            try:
                got = fn(pw, url, base, a)
            except Exception as exc:  # noqa: BLE001 — a failed leg is a fact in the output, not a crash
                doc["legs"][name] = {"status": "error", "error": str(exc).splitlines()[0][:300]}
                continue
            if name == "iphone_cold":
                doc["legs"][name] = got
            else:
                doc["legs"].update(got["legs"])
                doc["api"] = got["api"]
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "load.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
