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
from perf_review import cli, http_probe, report, stamping  # noqa: E402

_h = CheckHarness()
check = _h.check

PAGE = b"""<!doctype html><html><body><h1 id="ready">hi</h1>
<script>fetch('/api/fast'); fetch('/api/big?all=1'); setInterval(function () { fetch('/api/fast'); }, 1500);</script></body></html>"""
BIG = b'{"rows": "' + b"x" * 200_000 + b'"}'
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
        if self.path.split("?")[0] == "/api/big":
            return self._send(200, BIG)
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

# ---- cold-load sampling: median of three, outlier flagged (fleet-config#1140) ----
# One cold load right after a restart read 3072 ms on an app that loads in ~370 ms; two re-runs read 368 and 372.
def cold_check(**kw) -> dict:
    got = report.verdict(probe, {"legs": {"android_cold": leg(**kw), "android_warm": leg(cache="trusted")}}, budgets)
    return {c["id"]: c for c in got["checks"]}["cold.ready_ms"]


spiky = cold_check(ready_ms=3072, ready_samples_ms=[3072, 368, 372])
check(spiky["measured"] == 372 and spiky["status"] == "pass",
      f"one cold outlier among three loads does not fail the app: the median is scored ({spiky['measured']}, {spiky['status']})")
check(spiky.get("samples") == [3072, 368, 372] and spiky.get("outliers") == [3072], "the samples and the outlier are reported")
steady = cold_check(ready_ms=380, ready_samples_ms=[380, 368, 372])
check(steady["measured"] == 372 and steady.get("outliers") == [], "three agreeing loads flag no outlier")
stalled = cold_check(ready_ms=3500, ready_samples_ms=[3500, 3600, 3400])
check(stalled["measured"] == 3500 and stalled["status"] == "fail" and stalled.get("outliers") == [],
      "a consistently slow cold load still fails: the median is over budget and nothing is an outlier")
single = cold_check(ready_ms=2000)
check(single["measured"] == 2000 and single.get("samples") == [2000], "a leg with no samples list scores its one reading")
part = cold_check(ready_ms=None, ready_samples_ms=[None, 500, 520])
check(part["measured"] == 510 and part["status"] == "pass", "a sample that never reached ready is skipped, the rest are scored")
none = cold_check(ready_ms=None, ready_samples_ms=[None, None, None])
check(none["measured"] is None and none["status"] == "unmeasured", "no sample reaching ready is unmeasured, never a pass")
spiky_body = report.merge_body("", report.verdict(probe, {"legs": {"android_cold": leg(ready_ms=3072, ready_samples_ms=[3072, 368, 372]),
                                                                  "android_warm": leg(cache="trusted")}}, budgets),
                               "20261002T000000Z", "abc1234", "2026-10-02")
check("median of 3" in spiky_body and "3072" in spiky_body and "outlier" in spiky_body,
      "the issue body says the cold number is a median and names the outlier")

# ---- ready selector: a declared one must be visible on the landing view (fleet-config#1140) ----
# home-automation declared an AC-unit card that sits on another tab: both legs went `unmeasured` silently
# and a config test that only checked the markup exists passed anyway.
def ready_checks(cold_kw: dict, warm_kw: dict, cold_status: str = "ok") -> tuple:
    got = report.verdict(probe, {"legs": {"android_cold": leg(**{"status": cold_status, **cold_kw}),
                                          "android_warm": leg(cache="trusted", **warm_kw)}}, budgets)
    return {c["id"]: c for c in got["checks"]}, got


good, _ = ready_checks({"ready_by": "selector"}, {"ready_by": "selector"})
check(good["ready.selector"]["status"] == "pass" and good["ready.selector"]["measured"] == "visible",
      "a declared selector visible on both legs passes")
hidden, hv = ready_checks({"ready_by": "selector-not-visible", "ready_ms": None}, {"ready_by": "selector-not-visible", "ready_ms": None})
check(hidden["ready.selector"]["status"] == "fail" and "cold" in hidden["ready.selector"]["measured"]
      and "warm" in hidden["ready.selector"]["measured"], "a declared selector that never shows fails, naming the legs")
check(hidden["cold.ready_ms"]["status"] == "unmeasured" and hv["summary"]["overall"] == "over-budget",
      "the unmeasured ready time is no longer silent: the selector check fails the run")
check("ready_selector" in report.fixes_section(hv) and "landing view" in report.fixes_section(hv),
      "the seeded Fixes line tells the owner to fix the selector, not the app")
warm_only, _ = ready_checks({"ready_by": "selector"}, {"ready_by": "selector-not-visible", "ready_ms": None})
check(warm_only["ready.selector"]["status"] == "fail" and warm_only["ready.selector"]["measured"] == "not visible on warm",
      "a selector visible on the cold load but not the warm one still fails")
fcp, fv = ready_checks({"ready_by": "fcp"}, {"ready_by": "fcp"})
check("ready.selector" not in fcp, "no declared selector -> no selector check (ready falls back to first contentful paint)")
check("first contentful paint" in report.render_body(fv, "r", "b") and "ready_selector" in report.render_body(fv, "r", "b"),
      "the body warns that FCP can be a painted shell and asks for a declared selector")
errored, _ = ready_checks({"ready_by": "selector"}, {"ready_by": "selector"}, cold_status="error")
check("ready.selector" not in errored or errored["ready.selector"]["status"] != "pass",
      "a failed cold leg never lets the selector check pass")
no_leg = report.verdict(probe, {"legs": {}}, budgets)
check(not any(c["id"] == "ready.selector" for c in no_leg["checks"]), "no load leg -> no selector check, nothing invented")

# ---- a failing transfer check names the largest responses (fleet-config#1151) ----
# task-os's "6.6 MB cold load" was one API response (5.56 MB, 4.35 MB of it a field nothing rendered); the check
# said only "over budget" and the playbook pointed at assets, so it was found by hand.
TOP = [{"path": "/api/tasks/tree", "bytes": 5_830_000, "encoding": "identity", "kind": "Fetch"},
       {"path": "/static/app.js", "bytes": 120_000, "encoding": "gzip", "kind": "Script"}]


def bytes_checks(cold_kw: dict, warm_kw: dict | None = None) -> tuple:
    got = report.verdict(probe, {"legs": {"android_cold": leg(**cold_kw),
                                          "android_warm": leg(cache="trusted", **(warm_kw or {}))}}, budgets)
    return {c["id"]: c for c in got["checks"]}, got


heavy, hv = bytes_checks({"bytes": 6_000_000, "top_responses": TOP})
check(heavy["cold.bytes_kb"]["status"] == "fail" and [r["path"] for r in heavy["cold.bytes_kb"]["top_responses"]] == ["/api/tasks/tree", "/static/app.js"],
      "a failing cold transfer check carries the largest responses, biggest first")
check(heavy["cold.bytes_kb"]["top_responses"][0]["share"] == 97 and heavy["cold.bytes_kb"]["top_responses"][0]["encoding"] == "identity",
      "each response keeps its encoding and its share of the transfer (5.83 of 6.00 MB = 97%)")
check("top_responses" not in heavy["warm.bytes_kb"], "a passing transfer check lists no responses")
fine, _ = bytes_checks({"bytes": 100 * 1024, "top_responses": TOP})
check("top_responses" not in fine["cold.bytes_kb"], "a cold transfer within budget lists no responses")
bare, _ = bytes_checks({"bytes": 6_000_000})
check(bare["cold.bytes_kb"]["status"] == "fail" and bare["cold.bytes_kb"]["top_responses"] == [],
      "a failing check on a leg that recorded no responses reports an empty list, never an invented one")
heavy_body = report.render_body(hv, "r", "b")
check("`/api/tasks/tree`" in heavy_body and "5.56 MB" in heavy_body and "97% of the transfer" in heavy_body
      and not re.search(r"://|127\.0\.0\.1|\.ts\.net|localhost", heavy_body),
      "the issue body names the response, its wire size and share, as a path only (no scheme, host or IP)")
check("P12" in report.fixes_section(hv) and "TOP" in report.fixes_section(hv),
      "the seeded Fixes line for a failing transfer points at the TOP lines and P12, not only compression")
check("Largest responses" not in report.render_body(report.verdict(probe, load, budgets), "r", "b"),
      "a run with no failing transfer check adds no largest-responses section")

# ---- a failing warm transfer says whether it is data or cache (fleet-config#1151) ----
# task-os's relaunch ended at 256 KB against 100, nearly all `/api/` data: a cache fix would be the wrong advice.
api_heavy, ah = bytes_checks({}, {"bytes": 256 * 1024, "api_bytes": 240 * 1024, "asset_bytes": 16 * 1024})
check(api_heavy["warm.bytes_kb"]["status"] == "fail" and api_heavy["warm.bytes_kb"]["split"] == {"api_kb": 240, "asset_kb": 16},
      "a failing warm transfer carries the split between /api/ data and other responses")
check("a data problem, not a cache problem" in report.render_body(ah, "r", "b") and "240 KB" in report.render_body(ah, "r", "b"),
      "the body calls a mostly-/api/ warm transfer a data problem")
asset_heavy, sh = bytes_checks({}, {"bytes": 300 * 1024, "api_bytes": 10 * 1024, "asset_bytes": 290 * 1024})
check("a cache problem, not a data problem" in report.render_body(sh, "r", "b"), "the body calls a mostly-asset warm transfer a cache problem")
no_split, _ = bytes_checks({}, {"bytes": 300 * 1024})
check(no_split["warm.bytes_kb"]["status"] == "fail" and no_split["warm.bytes_kb"]["split"] is None,
      "a warm leg that recorded no split reports none, never a made-up 0/0")
check("split" not in bytes_checks({}, {"bytes": 50 * 1024, "api_bytes": 50 * 1024, "asset_bytes": 0})[0]["warm.bytes_kb"],
      "a warm transfer within budget carries no split")
check("split" not in heavy["cold.bytes_kb"], "the split belongs to the warm check only; a failing cold check carries none")
warm_fix = report.REMEDY["warm.bytes_kb"]
check("SPLIT" in warm_fix and "P12" in warm_fix and "P3" in warm_fix and "will not help" in warm_fix,
      "the warm remedy names both branches: payload (P12, P8) and cache (P3, P11)")
skill_flat = (REPO / "skills" / "perf-review" / "SKILL.md").read_text(encoding="utf-8").replace("\n  ", " ")
check("SPLIT" in skill_flat and "never propose a cache fix" in skill_flat, "SKILL.md: no cache fix for a data-dominated warm transfer")

# ---- cache-busting stamps must cover the import graph (fleet-config#1140) ----
# voice-transcriber stamped each module from its own bytes only, so editing a nested module left a cached
# importer on the old import URLs (its #220/#221). One fleet hash over every asset, or a transitive
# import-graph hash, is safe; a per-file hash of the file's own bytes is not.
def stamp_root(source: str | None) -> Path:
    root_dir = Path(tempfile.mkdtemp(prefix="perf-review-stamp-"))
    if source is not None:
        (root_dir / "src").mkdir()
        (root_dir / "src" / "static_versioning.py").write_text(source, encoding="utf-8")
    return root_dir


FLEET = "def compute_asset_hashes(static_dir):\n    return {}\n\n\ndef fleet_hash_of(hashes):\n    return 'x'\n"
GRAPH = "def asset_hash(path):\n    return 'x'\n\n\ndef _graph_hash(name, content_hashes, static_dir):\n    return 'y'\n"
PER_FILE = "def asset_hash(path):\n    return 'x'\n\n\nclass BuildInfo:\n    pass\n"
check(stamping.classify(stamp_root(FLEET))["strategy"] == "fleet-hash", "one fleet hash over every asset is recognised")
check(stamping.classify(stamp_root(GRAPH))["strategy"] == "graph-hash", "a transitive import-graph hash is recognised")
check(stamping.classify(stamp_root(PER_FILE))["strategy"] == "per-file", "a per-file hash with no graph walk is recognised")
check(stamping.classify(stamp_root("# per-file transitive fleet_hash_of graph\nx = 1\n"))["strategy"] == "unknown",
      "a stamping module whose shape is not recognised is unknown; words in comments decide nothing")
check(stamping.classify(stamp_root(None))["strategy"] == "none", "a repo with no static_versioning module has nothing to check")
check(stamping.classify(Path(tempfile.gettempdir()) / "perf-review-no-such-dir")["strategy"] == "none", "a missing root is none, not a crash")


def stamp_check(strategy: str) -> dict:
    got = report.verdict({**probe, "stamping": {"strategy": strategy}}, load, budgets)
    return {c["id"]: c for c in got["checks"]}


check(stamp_check("fleet-hash")["cache.stamping"]["status"] == "pass" and stamp_check("graph-hash")["cache.stamping"]["status"] == "pass",
      "fleet-hash and graph-hash stamping pass")
bad = stamp_check("per-file")["cache.stamping"]
check(bad["status"] == "fail" and bad["measured"] == "per-file", "per-file stamping without a graph hash fails")
check(stamp_check("unknown")["cache.stamping"]["status"] == "unmeasured", "an unrecognised stamping module is unmeasured, never a pass")
check("cache.stamping" not in stamp_check("none"), "no stamping module -> no check")
check("cache.stamping" not in {c["id"]: c for c in report.verdict(probe, load, budgets)["checks"]}, "a probe without stamping adds no check")
check("P11" in report.fixes_section(report.verdict({**probe, "stamping": {"strategy": "per-file"}}, load, budgets)),
      "the seeded Fixes line names playbook P11")
playbook_text = (REPO / "skills" / "perf-review" / "playbook.md").read_text(encoding="utf-8")
remedy_ids = set(re.findall(r"\bP\d+\b", " ".join(report.REMEDY.values())))
check(remedy_ids and all(re.search(rf"^## {pid} ", playbook_text, re.M) for pid in remedy_ids),
      f"every playbook id the REMEDY map names exists as a heading in playbook.md ({sorted(remedy_ids)})")

# ---- the playbook keeps the two fix lanes' corrections (fleet-config#1140) ----
# These are prose, so the test pins only the load-bearing terms: drop one and the next review proposes the old, wrong fix.
skill_text = (REPO / "skills" / "perf-review" / "SKILL.md").read_text(encoding="utf-8")
p2 = re.search(r"^## P2 .*?(?=^## P)", playbook_text, re.S | re.M).group(0)
p3 = re.search(r"^## P3 .*?(?=^## P)", playbook_text, re.S | re.M).group(0)
check("BaseHTTPMiddleware" in p2 and "minimum_size" in p2 and "/healthz" in p2,
      "P2: gzip registered first, inside any BaseHTTPMiddleware, with the /healthz test")
check("text/event-stream" in p2 and "Accept-Encoding" in p2, "P2: how to verify a stream survives compression, which the helper cannot")
check("fingerprint" in p3 and "git sha" in p3 and "W/" in p3 and "sha256(body)" not in p3,
      "P3: the ETag carries a build fingerprint and is weak; the body-only recipe is gone")
check("transitive" in p3 and "index.html" in p3, "P3: the invalidation test names a transitive module and an edited entry document")
p12 = re.search(r"^## P12 .*?(?=^## )", playbook_text, re.S | re.M).group(0)
check("TOP" in p12 and "descriptions=false" in p12 and "task-os#291" in p12, "P12: slim the oversized response, keyed to the TOP lines and its evidence")
check("TOP" in skill_text and "P12" in skill_text, "SKILL.md: read the TOP lines before ranking a transfer fix")
check("--duration" in skill_text and "never `pass`" in skill_text, "SKILL.md: a short run cannot produce a clean pass")
check("hypothesis" in skill_text and "net log" in skill_text, "SKILL.md: a cold-paint finding is a hypothesis until a trace confirms it")

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

# ---- declaring a ready selector moves the baseline; the diff says so (fleet-config#1151) ----
# task-os's cold ready went 516 -> 1967 ms with no code change: first contentful paint of a painted shell became
# "the first card with data is visible". Read as a regression, it would send a fixer after nothing.
def ran(run_id: str, ready_by: str | None, cold_ms: int) -> tuple:
    got = report.verdict(probe, {"legs": {"android_cold": leg(ready_ms=cold_ms, ready_by=ready_by),
                                          "android_warm": leg(cache="trusted", ready_by=ready_by)}}, budgets)
    return got, report.record("baseline-app", run_id, got, "c", "b")


_, first = ran("b1", "fcp", 516)
second_v, second = ran("b2", "selector", 1967)
check(first.get("ready_by") == "fcp" and second.get("ready_by") == "selector", "the ledger entry records what 'ready' meant for the run")
moved_diff = report.diff(second, report.previous_entry("baseline-app", "b2"))
check(moved_diff["ready_baseline_changed"] == {"from": "fcp", "to": "selector"},
      f"fcp -> selector is flagged as a baseline change ({moved_diff['ready_baseline_changed']})")
moved_body = report.render_body({**second_v, "diff": moved_diff}, "r", "b")
check("do not compare" in moved_body and "first contentful paint" in moved_body and "not a regression" in moved_body,
      "the issue body says the ready times do not compare and that the rise is not a regression")
check("do not compare" not in report.render_body(second_v, "r", "b"), "a body without a baseline change says nothing about one")
_, third = ran("b3", "selector", 1900)
check(report.diff(third, report.previous_entry("baseline-app", "b3"))["ready_baseline_changed"] is None,
      "selector -> selector is not a baseline change")
_, hidden_run = ran("b4", "selector-not-visible", 1900)
check(report.diff(hidden_run, report.previous_entry("baseline-app", "b4"))["ready_baseline_changed"] is None,
      "a selector that did not show is still the selector baseline: nothing to flag against a selector run")
check(report.diff({"ready_by": "selector", "checks": {}, "endpoints": {}}, {"run_id": "old", "checks": {}, "endpoints": {}})["ready_baseline_changed"] is None,
      "a previous run that never recorded ready_by leaves the change unknown, never claimed")
check(report.diff({"ready_by": None, "checks": {}, "endpoints": {}}, {"run_id": "p", "ready_by": "fcp", "checks": {}, "endpoints": {}})["ready_baseline_changed"] is None,
      "a current run with no known ready basis (failed load leg) flags nothing")
check(report.diff(second, None)["ready_baseline_changed"] is None, "a first run has no baseline to change")
cli_src = (REPO / "skills" / "_lib" / "perf_review" / "cli.py").read_text(encoding="utf-8")
check("ready_baseline_changed" in cli_src and "baselines do not compare" in cli_src, "the measure DIFF line carries the baseline change")
skill_ready = (REPO / "skills" / "perf-review" / "SKILL.md").read_text(encoding="utf-8").replace("\n  ", " ")
check("coarse-pointer phone" in skill_ready and "moves the baseline" in skill_ready and "baselines do not compare" in skill_ready,
      "SKILL.md: the selector must match the phone's landing view, and declaring one moves the baseline")

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
check("--cold-samples" in load_src and re.search(r'"--cold-samples".*default=3', load_src), "load.py takes 3 cold samples by default")

# ---- the Chromium leg bypasses Windows proxy auto-detect (fleet-config#1139) ----
# The harness addresses the app by an HTTPS hostname, which is no implicit proxy bypass, so with
# "Automatically detect settings" on, a cold autoproxy cache stalls the first request ~2.7 s (WPAD).
# `--no-proxy-server` and `--proxy-bypass-list` did not take effect in a net log; `direct://` did.
load_code = "\n".join(ln for ln in load_src.split('"""', 2)[2].splitlines() if not ln.lstrip().startswith("#"))
check(re.search(r"^CHROMIUM_ARGS\s*=\s*\[[^\]]*--proxy-server=direct://", load_code, re.M) is not None,
      "load.py declares --proxy-server=direct:// in CHROMIUM_ARGS")
check(not re.search(r"--no-proxy-server|--proxy-bypass-list", load_code),
      "load.py uses neither --no-proxy-server nor --proxy-bypass-list (measured: they leave auto-detect on)")
check(re.search(r"chromium\.launch\(args=\[\*CHROMIUM_ARGS\b", load_code) is not None,
      "the proxy flag is unconditional: Chromium launches with CHROMIUM_ARGS with or without --tls-name")

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
    check(len(cold.get("ready_samples_ms", [])) == 3 and all(isinstance(x, int) for x in cold.get("ready_samples_ms", [])),
          f"browser leg: three cold loads, each reached the declared selector ({cold.get('ready_samples_ms')})")
    check(warm.get("cache") == "trusted" and warm.get("from_cache", 0) >= 0, "browser leg: plain-http warm cache is trusted")
    api = doc.get("api", {}).get("/api/fast", {})
    check(api.get("interval_s") is not None and 1.0 <= api["interval_s"] <= 2.5,
          f"browser leg: the 1.5 s poll interval is learned ({api})")
    check(not cold.get("non_get") and not warm.get("non_get"), "browser leg: no non-GET request seen")
    top = cold.get("top_responses", [])
    check(0 < len(top) <= 5 and top[0]["path"] == "/api/big" and top[0]["bytes"] >= len(BIG)
          and top[0]["encoding"] == "identity" and top[0]["kind"] == "Fetch" and "?" not in top[0]["path"]
          and [r["bytes"] for r in top] == sorted((r["bytes"] for r in top), reverse=True),
          f"browser leg: the cold leg lists its largest responses, biggest first, query stripped ({top})")
    # the fixture sends no cache headers, so the warm relaunch re-fetches /api/big: it must land in api_bytes
    check(all(isinstance(leg_.get(k), int) for leg_ in (cold, warm) for k in ("api_bytes", "asset_bytes"))
          and warm["api_bytes"] + warm["asset_bytes"] == warm["bytes"] and cold["api_bytes"] + cold["asset_bytes"] == cold["bytes"],
          f"browser leg: transferred bytes split into api_bytes + asset_bytes that sum to the total ({warm})")
    check(cold["api_bytes"] >= len(BIG) and cold["asset_bytes"] > 0 and cold["asset_bytes"] < len(BIG),
          f"browser leg: the 200 KB /api/ payload is counted as API, the entry document as other ({cold.get('api_bytes')}, {cold.get('asset_bytes')})")
old_srv.shutdown()
new_srv.shutdown()
_h.report_and_exit("test_perf_review", skip_code=SKIP_EXIT)
