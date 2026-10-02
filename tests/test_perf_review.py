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

old_srv.shutdown()
new_srv.shutdown()
_h.report_and_exit("test_perf_review", skip_code=SKIP_EXIT)
