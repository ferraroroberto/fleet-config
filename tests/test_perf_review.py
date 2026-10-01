"""Unit tests + the probe self-test for skills/_lib/perf_review (fleet-config#1121).

The probe self-test drives `http_probe` against a local stdlib fixture server:
a slow and a fast endpoint at short spacing (cold request kept apart, the slow
one over budget, the fast one within, only GETs ever received), and two entry
documents — plain, and gzip + ETag + 304 — read as such. Then the verdict
(`unmeasured` never folded into a pass, an untrusted warm cache never scored),
the issue body (numbers and paths only — no scheme, host or IP; the
hand-curated Fixes section kept; one run-log line per run), the ledger diff,
the endpoint schedule, the `measure` CLI on a dead port (refused, never
started) and on the fixture (`--no-load`, over budget -> exit 1), the `file`
dry run, and the guard that the browser leg never installs a `route()` —
which silently turns the HTTP cache off and makes every warm load a cold one.

When a fleet `.venv` with Playwright exists, the browser leg also runs once
against the fixture page; otherwise that one check is a recorded skip.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_perf_review.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

_STATE = tempfile.mkdtemp(prefix="perf-review-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = _STATE

from check_harness import CheckHarness  # noqa: E402
from acceptance.shared import SKIP_EXIT  # noqa: E402

import audit_issue  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from perf_review import cli, http_probe, report  # noqa: E402

_h = CheckHarness()
check = _h.check

PAGE = b"""<!doctype html><html><body><h1 id="ready">hi</h1>
<script>fetch('/api/fast'); setInterval(function () { fetch('/api/fast'); }, 1500);</script></body></html>"""
ETAG = '"fixture-1"'


class Fixture(BaseHTTPRequestHandler):
    methods: list = []
    modern = False  # gzip + ETag + 304 on `/`

    def log_message(self, *args) -> None:  # keep the gate output clean
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).methods.append("GET")
        if self.path == "/":
            if not self.modern:
                return self._send(200, PAGE, "text/html")
            if self.headers.get("If-None-Match") == ETAG:
                return self._send(304, b"", "text/html", {"ETag": ETAG})
            body = gzip.compress(PAGE) if "gzip" in (self.headers.get("Accept-Encoding") or "") else PAGE
            enc = {"Content-Encoding": "gzip"} if body is not PAGE else {}
            return self._send(200, body, "text/html", {"ETag": ETAG, **enc})
        if self.path == "/api/slow":
            time.sleep(0.12)
            return self._send(200, b'{"ok": true}')
        if self.path == "/api/fast":
            return self._send(200, b'{"ok": true}')
        if self.path == "/api/broken":
            return self._send(500, b'{"ok": false}')
        return self._send(404, b"{}")

    def do_POST(self) -> None:
        type(self).methods.append("POST")
        self._send(405, b"{}")


def serve(modern: bool) -> tuple[ThreadingHTTPServer, str]:
    handler = type("H", (Fixture,), {"modern": modern, "methods": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---- percentile -------------------------------------------------------------
vals = [float(i) for i in range(1, 21)]
check(http_probe.percentile(vals, 95) == 19.0, "nearest-rank p95 of 1..20 is 19")
check(http_probe.percentile(vals, 50) == 10.0, "nearest-rank p50 of 1..20 is 10")
check(http_probe.percentile([7.0], 95) == 7.0, "a single sample is its own p95")
check(http_probe.percentile([], 95) is None, "no samples -> None, never 0")

# ---- probe self-test --------------------------------------------------------
old_srv, old_url = serve(modern=False)
new_srv, new_url = serve(modern=True)

timed = http_probe.time_endpoints(old_url, {"/api/slow": 0.05, "/api/fast": 0.05, "/api/broken": 0.05}, 1.0, stagger_s=0)
slow, fast = timed["/api/slow"], timed["/api/fast"]
check(slow["n"] >= 3 and fast["n"] >= 3, f"each endpoint sampled repeatedly (slow n={slow['n']}, fast n={fast['n']})")
check(slow["cold_ms"] is not None and slow["p95"] is not None and slow["p95"] >= 100, f"slow endpoint p95 >= 100 ms ({slow['p95']})")
check(fast["p95"] is not None and fast["p95"] < 100, f"fast endpoint p95 < 100 ms ({fast['p95']})")
check(slow["codes"] == ["200"] and timed["/api/broken"]["codes"] == ["500"], "every status seen is reported")
check(set(old_srv.RequestHandlerClass.methods) == {"GET"},
      "the probe only ever sent GET")

plain, modern = http_probe.index_checks(old_url), http_probe.index_checks(new_url)
check(plain["compressed"] is False and plain["etag"] is False and plain["revalidates"] is False,
      f"plain entry document reads as uncompressed, no ETag, no 304 ({plain})")
check(modern["compressed"] is True and modern["etag"] is True and modern["revalidates"] is True,
      f"modern entry document reads as compressed, ETag, 304 ({modern})")
check(modern["bytes"] == len(PAGE), "bytes are the decoded size of the entry document")
dead = http_probe.index_checks("http://127.0.0.1:9")
check(dead["compressed"] is None and dead["revalidates"] is None, "an unreachable / is unknown, never a pass or a fail")

# ---- verdict ----------------------------------------------------------------
budgets = report.load_budgets()
check(report.load_budgets({"warm": {"data_ms": 2000}})["warm"] == {**budgets["warm"], "data_ms": 2000},
      "a target override replaces one key and keeps the rest")
leg = lambda **kw: {"status": "ok", "ready_ms": 500, "data_ms": 900, "bytes": 50 * 1024, **kw}  # noqa: E731
probe = {"index": modern, "endpoints": {"/": {"n": 5, "p95": 12.0, "codes": ["200"]},
                                        "/api/slow": {"n": 5, "p95": 400.0, "codes": ["200"]},
                                        "/api/fast": {"n": 5, "p95": 20.0, "codes": ["200"]},
                                        "/api/broken": {"n": 5, "p95": 5.0, "codes": ["500"]}}}
load = {"legs": {"android_cold": leg(ready_ms=2000, bytes=400 * 1024), "android_warm": leg(cache="trusted"),
                 "iphone_cold": leg(ready_ms=99999, requests=7)}}
v = report.verdict(probe, load, budgets)
by = {c["id"]: c["status"] for c in v["checks"]}
eps = {e["path"]: e["status"] for e in v["endpoints"]}
check(by["warm.data_ms"] == "pass" and by["cold.bytes_kb"] == "pass", "within-budget legs pass")
check(eps["/api/slow"] == "fail" and eps["/api/fast"] == "pass" and by["endpoints.api_p95_ms"] == "fail",
      "an endpoint over 250 ms p95 fails the API check")
check(eps["/api/broken"] == "unmeasured", "an endpoint answering 500 is unmeasured, not a fast pass")
check(v["summary"]["overall"] == "over-budget", "any failing check -> over-budget")
check(v["iphone_cold"]["ready_ms"] == 99999 and not any(c["measured"] == 99999 for c in v["checks"]),
      "the unthrottled iPhone leg is reported, never scored")
untrusted = report.verdict(probe, {"legs": {"android_cold": leg(), "android_warm": leg(cache="untrusted", data_ms=99999)}}, budgets)
uby = {c["id"]: c["status"] for c in untrusted["checks"]}
check(uby["warm.data_ms"] == "unmeasured" and uby["warm.bytes_kb"] == "unmeasured",
      "a warm leg without a trusted cache is unmeasured, never scored")
empty = report.verdict({"index": dead, "endpoints": {}}, {"legs": {}}, budgets)
check(empty["summary"]["fail"] == 0 and empty["summary"]["overall"] == "unmeasured", "nothing measured -> unmeasured, not pass")

# ---- issue body -------------------------------------------------------------
body = report.merge_body("", v, "20261001T000000Z", "abc1234", "2026-10-01")
check(not re.search(r"://|127\.0\.0\.1|\.ts\.net|localhost", body), "the issue body carries no scheme, host or IP")
check("## Fixes" in body and "P4 serve hot reads from memory" in body, "a first body seeds Fixes from the failing checks")
curated = body.replace("## Fixes\n", "## Fixes\n\n- [x] curated by hand #12\n")
again = report.merge_body(curated, v, "20261002T000000Z", "def5678", "2026-10-02")
check("- [x] curated by hand #12" in again, "the hand-curated Fixes section survives a re-run")
check(again.count("\n- 2026-10-0") == 2, "each run appends exactly one run-log line")
check(report.merge_body(again, v, "20261002T000000Z", "def5678", "2026-10-02") == again, "an identical rerun changes nothing")

# ---- ledger -----------------------------------------------------------------
report.record("fixture-app", "r1", v, "c1", "b1")
better = report.verdict({**probe, "endpoints": {**probe["endpoints"], "/api/slow": {"n": 5, "p95": 30.0, "codes": ["200"]}}}, load, budgets)
e2 = report.record("fixture-app", "r2", better, "c2", "b2")
d = report.diff(e2, report.previous_entry("fixture-app", "r2"))
check(d["previous_run"] == "r1" and d["fixed"] == ["/api/slow"] and not d["regressed"],
      f"the ledger diff names exactly what was fixed; the API aggregate, still unmeasured via /api/broken, is not ({d})")
check(json.dumps(report.load_ledger("fixture-app")).count("://") == 0, "the ledger stores no URL")

# ---- schedule ---------------------------------------------------------------
sched = cli.schedule({"api": {"/api/a": {"interval_s": 15.0, "query": False}, "/api/b": {"interval_s": None, "query": False},
                              "/api/geo": {"interval_s": 2.0, "query": True}, "/api/skip/x": {"interval_s": 1.0, "query": False},
                              "/api/c": {"interval_s": 1.0, "query": False}}},
                     {"exclude": ["/api/skip"], "cadence": {"/api/a": 60}}, 5.0)
check("/api/geo" not in sched and "/api/skip/x" not in sched, "query-carrying and excluded paths are never timed")
check(sched["/api/a"] == 60 and sched["/api/b"] == cli.DEFAULT_SPACING_S and sched["/api/c"] == 5.0,
      f"declared cadence wins, unseen interval defaults, the floor holds ({sched})")

# ---- the browser leg never routes (the cache trap) ----------------------------
load_src = (REPO / "skills" / "_lib" / "perf_review" / "load.py").read_text(encoding="utf-8")
check(not re.search(r"\.route\(|route_from_har|\.unroute\(", load_src),
      "load.py installs no route(): Playwright disables the HTTP cache under routing")

# ---- CLI: dead port, fixture, file dry-run ------------------------------------
root = Path(tempfile.mkdtemp(prefix="perf-review-target-"))
out = io.StringIO()
with redirect_stdout(out):
    rc_dead = cli.main(["measure", str(root), "--url", "http://127.0.0.1:9", "--no-load", "--duration", "0.2"])
check(rc_dead == 3 and "PERF=unmeasured" in out.getvalue() and "never started" in out.getvalue(),
      "a dead port is refused as unmeasured (exit 3), nothing started")
(root / ".fleet.toml").write_text('[perf.review]\nendpoints = ["/api/slow"]\n[perf.review.cadence]\n"/" = 0.1\n"/api/slow" = 0.1\n',
                                  encoding="utf-8")
out = io.StringIO()
with redirect_stdout(out):
    rc_fix = cli.main(["measure", str(root), "--url", old_url, "--no-load", "--duration", "1", "--min-spacing", "0.05"])
text = out.getvalue()
check(rc_fix == 1 and "PERF=over-budget" in text and "index.compressed" in text,
      f"measure on the fixture: uncompressed / is over budget (exit {rc_fix})")
run_dir = Path(re.search(r"RUN_DIR=(.+)", text).group(1).strip())
check(all((run_dir / n).is_file() for n in ("probe.json", "verdict.json", "issue-body.md")), "the run dir holds probe, verdict and body")
audit_issue._list_open = lambda repo: []  # the dry run's one gh read, faked
audit_issue.get_managed = lambda repo, kind: {"number": None, "body": "", "duplicates": []}
out = io.StringIO()
with redirect_stdout(out):
    rc_file = cli.main(["file", str(root), "--url", old_url, "--slug", "owner/fixture"])
check(rc_file == 0 and "DRY_RUN=1" in out.getvalue() and "ACTION=create" in out.getvalue()
      and "<!-- audit-managed: kind=perf-review -->" in out.getvalue(), "file is a dry run by default and stamps the marker")

# ---- the browser leg, when a Playwright interpreter exists --------------------
def _playwright_python() -> str | None:
    for cand in [os.environ.get("PERF_REVIEW_PLAYWRIGHT", "")] + [str(p) for p in Path("E:/automation").glob("*/.venv/Scripts/python.exe")]:
        if cand and Path(cand).is_file():
            probe_cmd = subprocess.run([cand, "-c", "import playwright"], capture_output=True, creationflags=NO_WINDOW)
            if probe_cmd.returncode == 0:
                return cand
    return None


py = _playwright_python()
if py is None:
    _h.skip("browser leg: no fleet .venv with Playwright on this host")
else:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([py, str(REPO / "skills" / "_lib" / "perf_review" / "load.py"), "--url", old_url, "--out", tmp,
                               "--ready-selector", "#ready", "--settle-s", "4", "--boot-window-ms", "3000", "--skip-webkit"],
                              capture_output=True, text=True, timeout=180, creationflags=NO_WINDOW)
        doc = json.loads((Path(tmp) / "load.json").read_text(encoding="utf-8")) if (Path(tmp) / "load.json").is_file() else {}
    legs = doc.get("legs", {})
    cold, warm = legs.get("android_cold", {}), legs.get("android_warm", {})
    check(cold.get("status") == "ok" and cold.get("ready_by") == "selector" and cold.get("ready_ms") is not None,
          f"browser leg: cold ready via the declared selector ({cold or proc.stderr[-300:]})")
    check(warm.get("cache") == "trusted" and warm.get("from_cache", 0) >= 0, "browser leg: plain-http warm cache is trusted")
    api = doc.get("api", {}).get("/api/fast", {})
    check(api.get("interval_s") is not None and 1.0 <= api["interval_s"] <= 2.5,
          f"browser leg: the 1.5 s poll interval is learned ({api})")
    check(not cold.get("non_get") and not warm.get("non_get"), "browser leg: no non-GET request seen")

old_srv.shutdown()
new_srv.shutdown()
_h.report_and_exit("test_perf_review", skip_code=SKIP_EXIT)
