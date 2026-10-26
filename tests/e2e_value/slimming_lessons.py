import tempfile
from pathlib import Path

from _suite import _log, check, v


# ---- learnings from two slimming lanes (fleet-config#1134) ---------------------------------------------

# A skipped node pays no browser time: home-automation's progress log writes `SKIPPED (setup)` and then
# `DONE`, and voice-transcriber's "30 -> 22 nodes" counted 9 skipped WebKit nodes as savings.
SKIPPY = _log("2026-10-01", "10:00:00", [
    "[10:00:00 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[10:00:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (3.0s)",
    "[10:00:03 +    3.0s] START tests/e2e/test_board.py::test_load[webkit]",
    "[10:00:03 +    3.0s] SKIPPED (setup) tests/e2e/test_board.py::test_load[webkit]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_board.py::test_load[webkit] (0.0s)",
    "[10:00:03 +    3.0s] START tests/e2e/test_board.py::test_poll[chromium]",
    "[10:00:04 +    4.0s] DONE  tests/e2e/test_board.py::test_poll[chromium] (1.0s)",
    "[10:00:04 +    4.0s] pytest session finished (exit status 0)",
])
sk = v.parse_run(SKIPPY)
check(sk["skipped"] == ["tests/e2e/test_board.py::test_load[webkit]"] and sk["complete"],
      f"a SKIPPED line marks its node skipped, the run stays complete -- {sk.get('skipped')}")
check(v.executed_e2e(sk, ["tests/e2e"]) == {"tests/e2e/test_board.py::test_load[chromium]": 3.0,
                                            "tests/e2e/test_board.py::test_poll[chromium]": 1.0},
      "executed e2e nodes leave the skipped one out")

# A backend-only gate run, or a narrow surface slice, finishing later must not displace the full run.
SLICE = _log("2026-10-01", "12:00:00", [
    "[12:00:00 +    0,0s] ==> phase: e2e routing: tier=surface target=tests/e2e/test_board.py",
    "[12:00:00 +    0,0s] ==> phase: pytest e2e (surface)...",
    "[12:00:00 +    0.1s] START tests/e2e/test_board.py::test_poll[chromium]",
    "[12:00:01 +    1.0s] DONE  tests/e2e/test_board.py::test_poll[chromium] (1.0s)",
    "[12:00:01 +    1.0s] pytest session finished (exit status 0)",
])
BACKEND = _log("2026-10-01", "13:00:00", [
    "[13:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[13:00:00 +    0.1s] START tests/test_api.py::test_ok",
    "[13:00:01 +    1.0s] DONE  tests/test_api.py::test_ok (1.0s)",
    "[13:00:01 +    1.0s] pytest session finished (exit status 0)",
])
tt = Path(tempfile.mkdtemp(prefix="e2e-value-tier-"))
(tt / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
(tt / "gate.log").write_text(SKIPPY + SLICE + BACKEND, encoding="utf-8")
ttm = v.timing(tt, ["tests/e2e"])
check(ttm["status"] == "ok" and ttm["run"]["started"] == "2026-10-01T10:00:00" and ttm["run"]["slice"] is None,
      f"timing takes the latest full-tier run with e2e nodes, not a later slice or backend-only run -- {ttm.get('run')}")
check(ttm["run"]["e2e_nodes"] == 2 and ttm["run"]["e2e_skipped"] == 1 and ttm["projections"]["chromium"]["nodes"] == 2
      and "webkit" not in ttm["projections"], f"executed nodes only; skipped nodes reported apart -- {ttm['run']}")
check(ttm["first_node"]["nodeid"] == "tests/e2e/test_board.py::test_load[chromium]" and ttm["first_node"]["seconds"] == 3.0,
      f"the first executed node is named: it carries the session boot -- {ttm.get('first_node')}")

# Staleness (fleet-config#1175, app-launcher#1375): the only log on disk was a 612-node run from before the suite was
# 485 nodes, and timing reported `ok` over it, doubling every paid figure. The run covers 3 e2e nodes (2 executed, 1 skipped).
check(ttm["freshness"]["status"] == "unchecked" and "not measured" in ttm["freshness"]["reason"],
      f"no suite node count given: the freshness is its own `unchecked` state, never folded into fresh -- {ttm['freshness']}")
tfresh = v.timing(tt, ["tests/e2e"], suite_nodes=3)
check(tfresh["status"] == "ok" and tfresh["freshness"] == {"status": "fresh", "run_nodes": 3, "suite_nodes": 3, "reason": None},
      f"a run covering the suite's node count is fresh, and names both counts it compared -- {tfresh['freshness']}")
tstale = v.timing(tt, ["tests/e2e"], suite_nodes=5)
check(tstale["status"] == "stale" and tstale["freshness"]["status"] == "stale" and "fresh baseline" in tstale["reason"]
      and "3" in tstale["reason"] and "5" in tstale["reason"] and tstale["run"]["e2e_nodes"] == 2,
      f"a run covering 3 nodes of a 5-node suite is stale: status `stale`, the numbers kept for reading, a fresh baseline asked for -- "
      f"{tstale['status']} {tstale.get('reason')}")
check(v.stale_nodes(100, 109)["status"] == "fresh" and v.stale_nodes(100, 112)["status"] == "stale"
      and v.stale_nodes(112, 100)["status"] == "stale" and v.stale_nodes(100, 95)["status"] == "fresh"
      and v.stale_nodes(100, 90)["status"] == "stale",
      "the tolerance is 10% of the suite's collected nodes, in either direction")
check(v.stale_nodes(612, 485)["status"] == "stale", "app-launcher's 612-node log against its 485-node suite is stale")
check(v.stale_nodes(5, 0)["status"] == "unchecked" and v.stale_nodes(5, None)["status"] == "unchecked",
      "an empty or unmeasured suite count is unchecked, never fresh")
(tt / "gate.log").write_text(SLICE + BACKEND, encoding="utf-8")
tsl = v.timing(tt, ["tests/e2e"])
check(tsl["status"] == "ok" and tsl["run"]["routed_tier"] == "surface" and "surface" in str(tsl["run"]["slice"]),
      f"with only a slice on record, timing measures it and says it is a slice, not the suite -- {tsl.get('run')}")
check(v.timing(tt, ["tests/e2e"], suite_nodes=50)["status"] == "ok" and v.timing(tt, ["tests/e2e"], suite_nodes=50)["freshness"]["status"] == "slice",
      "a slice covers fewer nodes than the suite by design: it is never called stale")

# page_loads: a module booting through a helper or a fixture paid page loads the old count missed.
BOOT_MOD = (
    "def boot_home(page):\n    page.goto('/')\n\n\n"
    "def test_a(page):\n    boot_home(page)\n\n\n"
    "def test_b(page):\n    boot_home(page)\n    page.reload()\n\n\n"
    "def test_c(authed_page):\n    assert authed_page.title()\n"
)
check(v.cost_drivers(BOOT_MOD, frozenset({"authed_page"}))["page_loads"] == 4,
      "page loads count calls to a loading helper and tests taking a loading fixture, not the helper's own goto")
check(v.loaders("import pytest\n\n@pytest.fixture\ndef authed_page(page):\n    page.goto('/login')\n    return page\n\n"
                "def _nothing():\n    return 1\n") == {"authed_page"}, "a fixture or helper holding a goto is a loader")

# shots (task-os#278): screenshot calls were a third of a suite's wall time and nothing counted them. A call
# to a helper that screenshots counts once per call site; the helper's own `screenshot` is not counted again.
SHOT_MOD = (
    "def shot(page, name):\n    page.screenshot(path=name)\n\n\n"
    "def test_a(page):\n    shot(page, 'a')\n    shot(page, 'b')\n    page.screenshot(path='c')\n"
)
check(v.cost_drivers(SHOT_MOD)["shots"] == 3, "shots count helper calls and direct screenshots, not the helper's own call")
check(v.cost_drivers("def test_x(page):\n    shot(page, 'a')\n    shot(page, 'b')\n", frozenset(), frozenset({"shot"}))["shots"] == 2,
      "a shot helper defined in a shared conftest counts at the call sites in a module")
sh = Path(tempfile.mkdtemp(prefix="e2e-value-shots-"))
(sh / "tests" / "e2e").mkdir(parents=True)
(sh / "tests" / "e2e" / "conftest.py").write_text("def shot(page, name):\n    page.screenshot(path=name)\n", encoding="utf-8")
(sh / "tests" / "e2e" / "test_a.py").write_text("def test_a(page):\n    shot(page, 'a')\n    shot(page, 'b')\n", encoding="utf-8")
mods = v.modules({"tests/e2e/test_a.py::test_a[chromium]": 4.0}, sh, ["tests/e2e"])
check(mods[0]["shots"] == 2, f"modules() reports shots through the conftest's shot helper -- {mods}")

# runtime_drift: a threshold, a history figure and an unrelated README line are not runtime claims.
claims = v.runtime_claims(
    "- Local runtime contract: 270 executions in ~3.5 min (3m30s, pytest). Investigate if a full run exceeds "
    "**~7 min** (the previous ~10 min was 2x an older measurement).\n"
    "Renew iCloud browser trust: open the Presence card, type the code (`expired` after 10 min / `failed`).\n"
    "A background telemetry sampler runs on a gentle cadence (default 5 min), so its reading gate defaults off.\n",
    "CLAUDE.md")
check([c["claimed_min"] for c in claims] == [3.5],
      f"only the measured runtime is a claim: thresholds, history and figures far from a gate word are not -- {claims}")

# One bullet carrying a headline figure, its phase figures and a history list produced five claims, each paired with the nearest
# span (fleet-config#1175, app-launcher#1375): only the headline and the figures with a span word right before them are claims.
bullet = ("- **Full-suite runtime ~12.5 min** on this dev box (re-measured at 475 executed nodes: non-e2e ~3.6 min, browser parallel pass "
          "~8.5 min, serial pass ~0.4 min; the same suite before the trims was ~13.9 min, at 620 dual-projection nodes ~22 min; `4` workers "
          "≈ 17 min)\n")
bc = v.runtime_claims(bullet, "CLAUDE.md")
check([c["claimed_min"] for c in bc] == [12.5, 3.6, 8.5],
      f"the headline and the figures a span word precedes are claims; a serial-pass, a history and a scenario figure are not -- {bc}")
check([c["claimed_min"] for c in v.runtime_claims("Typical green: **~4 min** (deps ~1 min, the non-e2e suite ~3 min).\n", "CLAUDE.md")] == [4.0, 3.0],
      "a figure with a gate word right before it stays a claim; `deps ~1 min` is not")

# waits: fixed sleeps, page timers, real poll constants and long timeouts, with what each one is paid.
wr = Path(tempfile.mkdtemp(prefix="e2e-value-waits-"))
(wr / "tests" / "e2e").mkdir(parents=True)
(wr / "tests" / "e2e" / "test_feedback.py").write_text(
    "POLL_MS = 15_000\n"
    "SLOW_INIT = '''\n"
    "window.fetch = function(u) {\n"
    "  return new Promise(function(res) { setTimeout(function() { res(orig(u)); }, 750); });\n"
    "};\n'''\n\n\n"
    "def _install(page):\n    page.add_init_script(SLOW_INIT)\n\n\n"
    "def test_loading(page):\n    _install(page)\n    page.goto('/')\n\n\n"
    "def test_settle(page):\n    page.goto('/')\n    page.wait_for_timeout(500)\n\n\n"
    "def test_retry(page):\n    page.goto('/')\n    expect(page.locator('#s')).to_have_text('Online', timeout=20_000)\n", encoding="utf-8")
(wr / "tests" / "e2e" / "conftest.py").write_text(
    "import time\n\n\ndef _wait_up(url):\n    time.sleep(0.4)\n", encoding="utf-8")
sites = v.wait_sites(wr, ["tests/e2e"])
kinds = {(s["file"].rsplit("/", 1)[-1], s["kind"], s["ms"], s["scope"]) for s in sites}
check(kinds == {("test_feedback.py", "poll-constant", 15000, "POLL_MS"), ("test_feedback.py", "page-timer", 750, "SLOW_INIT"),
                ("test_feedback.py", "sleep", 500, "test_settle"), ("test_feedback.py", "long-timeout", 20000, "test_retry"),
                ("conftest.py", "sleep", 400, "_wait_up")},
      f"every wait shape with its milliseconds and enclosing scope -- {sorted(kinds)}")
wnodes = {"tests/e2e/test_feedback.py::test_loading[chromium]": 1.9, "tests/e2e/test_feedback.py::test_loading[webkit]": 2.1,
          "tests/e2e/test_feedback.py::test_settle[chromium]": 0.9, "tests/e2e/test_feedback.py::test_retry[chromium]": 15.6,
          "tests/e2e/test_feedback.py::test_retry[webkit]": 15.8}
ranked = v.rank_waits(sites, wnodes, wr)
check(ranked[-1]["scope"] == "test_retry" and ranked[-1]["measured_s"] == 31.4 and ranked[-1]["nodes"] == 2
      and ranked[-1]["ceiling"] is True and not any(r["ceiling"] for r in ranked[:-1]),
      f"a `timeout=` is a ceiling, not a wait: it ranks after every fixed wait, flagged -- {ranked}")
check([r["scope"] for r in ranked[:3]] == ["SLOW_INIT", "test_settle", "_wait_up"],
      f"fixed sleeps and page timers rank by the seconds they are paid, not by the seconds of the test around them -- "
      f"{[(r['scope'], r['paid_s']) for r in ranked]}")
slp = next(r for r in ranked if r["scope"] == "test_settle")
tmr = next(r for r in ranked if r["scope"] == "SLOW_INIT")
check(slp["paid_s"] == 0.5 and tmr["nodes"] == 2 and tmr["measured_s"] == 4.0 and tmr["paid_s"] == 1.5,
      f"a fixed sleep or page timer is paid on every executed node that reaches it -- {slp} / {tmr}")
check(next(r for r in ranked if r["scope"] == "_wait_up")["nodes"] is None,
      "a wait in a shared conftest is not attributed to tests")

# poll loops (fleet-config#1165, facilitation-suite#165): a sleep inside a condition loop ends when the condition holds,
# so it costs its interval at most. 6 of 11 listed sleeps were that; the "paid" figure overstated the saving 1.75x.
pr_ = Path(tempfile.mkdtemp(prefix="e2e-value-pollsleep-"))
(pr_ / "tests" / "e2e").mkdir(parents=True)
(pr_ / "tests" / "e2e" / "test_poll.py").write_text(
    "import time\n\n\n"
    "def test_deadline(page):\n    deadline = time.monotonic() + 5\n    while time.monotonic() < deadline:\n"
    "        if ready():\n            break\n        time.sleep(0.2)\n\n\n"
    "def test_cond(page):\n    while not ready():\n        page.wait_for_timeout(300)\n\n\n"
    "def test_forever(page):\n    while True:\n        time.sleep(0.4)\n\n\n"
    "def test_taps(page):\n    for _ in range(5):\n        time.sleep(0.15)\n\n\n"
    "def _wait_for_calls(page, calls, n):\n    for _ in range(40):\n        if len(calls) >= n:\n            return\n"
    "        page.wait_for_timeout(250)\n\n\n"
    "def test_for_break(page):\n    for _ in range(10):\n        if ready():\n            break\n        time.sleep(0.1)\n\n\n"
    "def test_fixed(page):\n    time.sleep(0.6)\n", encoding="utf-8")
psites = {s["scope"]: s for s in v.wait_sites(pr_, ["tests/e2e"])}
check({k: psites[k]["kind"] for k in psites} == {"test_deadline": "poll-sleep", "test_cond": "poll-sleep", "test_forever": "sleep",
                                                  "test_taps": "sleep", "_wait_for_calls": "poll-sleep", "test_for_break": "poll-sleep",
                                                  "test_fixed": "sleep"},
      f"a sleep in a loop that can end on its condition is a poll, not a fixed wait; a `for` retry loop that returns or breaks "
      f"on a condition is one too (app-launcher#1375: `for _ in range(40)` priced 2.5 s for a 250 ms interval); "
      f"a for-loop over taps with no exit and an endless loop sleep still are fixed -- "
      f"{ {k: s['kind'] for k, s in psites.items()} }")
pn = {f"tests/e2e/test_poll.py::{t}[chromium]": 1.0 for t in ("test_deadline", "test_cond", "test_forever", "test_taps", "test_fixed")}
prk = {r["scope"]: r for r in v.rank_waits(list(psites.values()), pn, pr_)}
check(prk["test_deadline"]["paid_s"] is None and prk["test_deadline"]["ceiling"] is True and prk["test_fixed"]["paid_s"] == 0.6
      and {r["scope"] for r in v.rank_waits(list(psites.values()), pn, pr_)[-4:]} == {"test_deadline", "test_cond", "_wait_for_calls", "test_for_break"},
      f"a poll sleep is never priced as paid seconds and ranks after every fixed wait -- {prk['test_deadline']}")

# Unawaited timers (fleet-config#1175, app-launcher#1375): `terminal.batchQuietTimer = setTimeout(function () {}, 60000);` is a no-op
# handle the test never waits for, and the audit ranked it first at 120 s paid for a node that measured 5 s.
ut = Path(tempfile.mkdtemp(prefix="e2e-value-noop-"))
(ut / "tests" / "e2e").mkdir(parents=True)
(ut / "tests" / "e2e" / "test_probe.py").write_text(
    "def test_handle(page):\n"
    "    page.evaluate('''() => { terminal.quiet = setTimeout(function () {}, 60000); }''')\n\n\n"
    "def test_arrow(page):\n"
    "    page.evaluate('''() => { terminal.q2 = setTimeout(() => {}, 45000); }''')\n\n\n"
    "def test_real(page):\n"
    "    page.add_init_script('''setTimeout(function () { window.ready = true; }, 750);''')\n", encoding="utf-8")
usites = {s["scope"]: s for s in v.wait_sites(ut, ["tests/e2e"])}
check(usites["test_handle"]["unawaited"] is True and usites["test_arrow"]["unawaited"] is True and usites["test_real"]["unawaited"] is False,
      f"a setTimeout with an empty callback is flagged unawaited; one that does something is not -- {usites}")
unodes = {"tests/e2e/test_probe.py::test_handle[chromium]": 5.0, "tests/e2e/test_probe.py::test_arrow[chromium]": 4.0,
          "tests/e2e/test_probe.py::test_real[chromium]": 1.0}
urank = v.rank_waits(list(usites.values()), unodes, ut)
check(urank[0]["scope"] == "test_real" and urank[0]["paid_s"] == 0.8
      and all(r["paid_s"] is None and r["unawaited"] for r in urank[1:]) and [r["scope"] for r in urank[1:]][0] in ("test_handle", "test_arrow"),
      f"an unawaited timer is never priced as paid seconds and ranks after every real wait -- {[(r['scope'], r['paid_s']) for r in urank]}")

# slow_fixtures (fleet-config#1170, local-llm-hub#644): 40 s of a 76 s run was a session fixture's `httpx.get(..., timeout=_WARMUP_TIMEOUT)`
# with `_WARMUP_TIMEOUT = 90.0` (seconds). `waits` read `timeout=` as integer milliseconds, so a float-seconds timeout never matched
# and the audit ranked 4 s of sleeps first.
sf = Path(tempfile.mkdtemp(prefix="e2e-value-slowfix-"))
(sf / "tests" / "e2e").mkdir(parents=True)
(sf / "tests" / "e2e" / "test_tab.py").write_text(
    "import httpx\nimport pytest\n\n_WARMUP_TIMEOUT = 90.0\n_QUICK = 5.0\n\n\n"
    "@pytest.fixture(scope='session', autouse=True)\ndef _warm(admin_url):\n"
    "    httpx.get(admin_url, params={}, timeout=_WARMUP_TIMEOUT)\n\n\n"
    "@pytest.fixture\ndef _short(admin_url):\n    httpx.get(admin_url, timeout=_QUICK)\n\n\n"
    "def _fetch(url):\n    return httpx.get(url, timeout=45)\n\n\n"
    "def _click(page):\n    page.click('a', timeout=45000)\n    page.wait_for_selector('b', timeout=60.0 * 1000)\n\n\n"
    "def test_x(page):\n    httpx.get('u', timeout=120.0)\n    page.wait_for_selector('#x', timeout=60000)\n", encoding="utf-8")
slow = v.slow_fixtures(sf, ["tests/e2e"])
check([(r["name"], r["fixture_scope"], r["autouse"], r["timeout_s"]) for r in slow] == [("_warm", "session", True, 90.0), ("_fetch", None, False, 45.0)],
      f"a fixture or helper (not a test) with a seconds-valued timeout of 30 s or more is listed, longest first; a short one, a test, "
      f"and a Playwright millisecond timeout are not -- {slow}")
check(slow[0]["file"] == "tests/e2e/test_tab.py" and slow[0]["line"] == 9 and slow[0]["is_fixture"] is True and slow[1]["is_fixture"] is False,
      f"each entry names its file, line and whether it is a pytest fixture -- {slow[0]}")
check(v.slow_fixtures(Path(tempfile.mkdtemp(prefix="e2e-value-slowfix-none-")), ["tests/e2e"]) == [], "no test tree, no slow fixtures")

# slow_nodes + app_timers (fleet-config#1157, photo-ocr#127): a 1 s poll in app source (`poll.js`) was waited out twice
# by one test, invisible to a scan of the test tree. The junit showed it: 2.6 s against a 0.2 s median.
sn = v.slow_nodes({"tests/e2e/test_a.py::test_boot[chromium]": 7.1, "tests/e2e/test_a.py::test_fast[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_fast2[chromium]": 0.2, "tests/e2e/test_a.py::test_fast3[chromium]": 0.2, "tests/e2e/test_a.py::test_fast4[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_fast5[chromium]": 0.3, "tests/e2e/test_a.py::test_fast6[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_extract[chromium]": 2.6, "tests/e2e/test_a.py::test_mid[chromium]": 0.9})
check([r["nodeid"].rsplit("::", 1)[1] for r in sn] == ["test_extract[chromium]"],
      f"a node 10x the median and over 1 s is a find-the-wait candidate; the boot node and a sub-second one are not -- {sn}")
check(v.slow_nodes({}) == [] and v.slow_nodes({"tests/e2e/test_a.py::test_x[chromium]": 9.0}) == [],
      "no nodes, or one node (nothing to be an outlier against), yields no candidates")
ap = Path(tempfile.mkdtemp(prefix="e2e-value-apptimers-"))
(ap / "tests" / "e2e").mkdir(parents=True)
(ap / "app" / "static").mkdir(parents=True)
(ap / "app" / "static" / "_vendored").mkdir()
(ap / "node_modules" / "x").mkdir(parents=True)
(ap / "app" / "static" / "poll.js").write_text(
    "export async function poll(id) {\n  for (;;) {\n    await sleep(1000);\n    const r = await fetch(id);\n  }\n}\n"
    "setInterval(tick, 30_000);\nsetTimeout(() => hide(), 250);\nsetTimeout(function () { save(); }, 4000);\n", encoding="utf-8")
(ap / "app" / "static" / "_vendored" / "lib.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "node_modules" / "x" / "i.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "tests" / "e2e" / "helper.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "app" / "static" / "app.min.js").write_text("setTimeout(f,9000);\n", encoding="utf-8")
at = v.app_timers(ap, ["tests/e2e"])
check([(t["file"], t["line"], t["kind"], t["ms"]) for t in at] ==
      [("app/static/poll.js", 7, "interval", 30000), ("app/static/poll.js", 9, "timeout", 4000), ("app/static/poll.js", 3, "sleep", 1000)],
      f"app-source timers of 1 s or more, longest first; sub-second, vendored, node_modules, test and minified files are left out -- {at}")
check(v.app_timers(Path(tempfile.mkdtemp(prefix="e2e-value-empty-")), ["tests/e2e"]) == [], "no app JS, no timers")

# Named constants (fleet-config#1175, app-launcher#1375): the polls the slow tests waited out were `setInterval(fn, RUNNING_APPS_POLL_MS)`
# and `setTimeout(tick, LIVE_POLL_MS)` with the constant declared in the same file or exported from another module.
nt = Path(tempfile.mkdtemp(prefix="e2e-value-namedtimers-"))
(nt / "tests" / "e2e").mkdir(parents=True)
(nt / "app" / "static").mkdir(parents=True)
(nt / "app" / "static" / "state.js").write_text(
    "export const RUNNING_APPS_POLL_MS = 4000;\nexport const TUNNEL_POLL_MS = 4_000;\nexport const SHORT_MS = 250;\nexport const DUP_MS = 2000;\n",
    encoding="utf-8")
(nt / "app" / "static" / "main.js").write_text(
    "import { RUNNING_APPS_POLL_MS, SHORT_MS } from './state.js';\nsetInterval(refreshApps, RUNNING_APPS_POLL_MS);\n"
    "setInterval(blink, SHORT_MS);\nsetTimeout(later, UNKNOWN_MS);\nsetTimeout(dup, DUP_MS);\n", encoding="utf-8")
(nt / "app" / "static" / "session-transcript.js").write_text(
    "const LIVE_POLL_MS = 3000;\nconst DUP_MS = 9000;\nfunction arm() { setTimeout(tick, LIVE_POLL_MS); }\nsetTimeout(dup2, DUP_MS);\n", encoding="utf-8")
(nt / "app" / "static" / "other.js").write_text("setTimeout(dup3, DUP_MS);\n", encoding="utf-8")
ntm = [(t["file"].rsplit("/", 1)[1], t["line"], t["kind"], t["ms"], t.get("name")) for t in v.app_timers(nt, ["tests/e2e"])]
check(ntm == [("session-transcript.js", 4, "timeout", 9000, "DUP_MS"), ("main.js", 2, "interval", 4000, "RUNNING_APPS_POLL_MS"),
              ("session-transcript.js", 3, "timeout", 3000, "LIVE_POLL_MS")],
      f"a delay that is a named constant resolves through the file's own `const NAME = <ms>` or another module's export; a constant under "
      f"1 s, one declared nowhere and one declared with different values in two other files (not the file's own) are left out -- {ntm}")

# first_node.boot (fleet-config#1157, photo-ocr#127): a "session boot 6.9 s" finding was one cold run in a fresh
# worktree; warm, the same node took under 0.3 s. A boot is a finding only once a second run in the same
# checkout repeats it.
def _boot_run(day: str, start: str, first_s: float) -> str:
    h, m, sec = (int(x) for x in start.split(":"))
    lines = [f"[{start} +    0,0s] ==> phase: pytest e2e (tests/e2e)..."]
    for i, secs in enumerate([first_s, 0.5, 0.5, 0.5]):
        nid = f"tests/e2e/test_board.py::test_n{i}[chromium]"
        lines += [f"[{start} +    0.1s] START {nid}", f"[{start} +    1.0s] DONE  {nid} ({secs}s)"]
    lines.append(f"[{start} +    9.0s] pytest session finished (exit status 0)")
    return _log(day, start, lines)


bt = Path(tempfile.mkdtemp(prefix="e2e-value-boot-"))
(bt / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1), encoding="utf-8")
check(v.timing(bt, ["tests/e2e"])["first_node"]["boot"] == "single-run",
      "one run on record: the first node is a cold single run, unconfirmed")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1) + _boot_run("2026-10-01", "11:00:00", 6.0), encoding="utf-8")
check(v.timing(bt, ["tests/e2e"])["first_node"]["boot"] == "repeated",
      "the two newest runs of one log both carry the boot: a repeated cost")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1) + _boot_run("2026-10-01", "11:00:00", 0.6), encoding="utf-8")
fn = v.timing(bt, ["tests/e2e"])["first_node"]
check(fn["boot"] == "not-repeated" and fn["seconds"] == 0.6,
      f"a warm re-run without the boot: the cold run was a one-off, not a finding -- {fn}")

# checkout + skip reasons (fleet-config#1157, photo-ocr#127): the baseline ran from a linked worktree with no
# certificates, so the cert test skipped as "not HTTPS" and the suite booted over plain HTTP. A run in a linked
# worktree can differ from the primary in skips and boot; say so, and say why each node skipped.
ck = Path(tempfile.mkdtemp(prefix="e2e-value-checkout-"))
(ck / "primary" / ".git").mkdir(parents=True)
(ck / "linked").mkdir()
(ck / "linked" / ".git").write_text("gitdir: ../primary/.git/worktrees/linked", encoding="utf-8")
(ck / "primary" / "webapp").mkdir()
(ck / "primary" / "webapp" / "junit.xml").write_text("<x/>", encoding="utf-8")
(ck / "bare").mkdir()
check(v.checkout_of(ck / "primary" / "webapp" / "junit.xml")["linked_worktree"] is False
      and v.checkout_of(ck / "primary" / "webapp" / "junit.xml")["root"] == str((ck / "primary").resolve()),
      "a file inside a normal checkout: the root is the checkout, not a linked worktree")
check(v.checkout_of(ck / "linked" / "x.xml")["linked_worktree"] is True, "a `.git` file marks a linked worktree")
check(v.checkout_of(ck / "bare" / "x.xml") == {"root": None, "linked_worktree": None},
      "outside any checkout the answer is unknown, never a guess")
sj = Path(tempfile.mkdtemp(prefix="e2e-value-skipreasons-"))
(sj / ".git").write_text("gitdir: ../elsewhere/.git/worktrees/sj", encoding="utf-8")
(sj / "junit.xml").write_text(
    '<testsuite><testcase classname="tests.e2e.test_cert" name="test_cert_lifetime[chromium]" time="0.0"><skipped message="not HTTPS" type="pytest.skip">x</skipped></testcase>'
    '<testcase classname="tests.e2e.test_cert" name="test_cert_lifetime[webkit]" time="0.0"><skipped message="not HTTPS" type="pytest.skip">x</skipped></testcase>'
    '<testcase classname="tests.e2e.test_board" name="test_load[chromium]" time="1.0"/></testsuite>', encoding="utf-8")
sjt = v.timing(sj, ["tests/e2e"], Path("junit.xml"))
check(sjt["run"]["skip_reasons"] == [{"reason": "not HTTPS", "nodes": 2}] and sjt["run"]["checkout"]["linked_worktree"] is True,
      f"a JUnit run in a linked worktree names the checkout and why each node skipped -- {sjt['run']}")
check(v.timing(tt, ["tests/e2e"])["run"]["skip_reasons"] is None,
      "a progress log carries no skip reason: unknown, not an empty list")

# routing: the rule that wins once the first one stops matching, and the command that checks it.
class _R:  # noqa: E302
    def __init__(self, tier, label, prefix=None, path=None, extensions=None):
        self.tier, self.label, self.prefix, self.path, self.extensions = tier, label, prefix, path, extensions

    def matches(self, path, ext):
        if self.path is not None:
            return path == self.path
        if self.prefix is not None and not path.startswith(self.prefix):
            return False
        if self.extensions is not None and ext not in self.extensions:
            return False
        return self.prefix is not None or self.extensions is not None


HA_RULES = [_R(1, "vendored-static", prefix="app/webapp/static/_vendored/", extensions=("html", "md")),
            _R(2, "webapp", prefix="app/webapp/"), _R(0, "docs", extensions=("md",))]
she = v.shadow_entry("app/webapp/static/_vendored/nav/README.md", HA_RULES)
check(she is not None and she["shadowed"] == ["docs"] and she["next_rule"] == "webapp" and she["next_tier"] == 2
      and she["drop_safe"] is False,
      f"home-automation#778: dropping md from the static rule hands the README to the full webapp rule, not docs -- {she}")
safe = v.shadow_entry("static/_vendored/nav/README.md", [_R(2, "static-code", prefix="static/"), _R(0, "docs", extensions=("md",))])
check(safe is not None and safe["next_rule"] == "docs" and safe["drop_safe"] is True, f"no rule in between: dropping is safe -- {safe}")
check(v.shadow_entry("tests/e2e/test_a.py", [_R(2, "e2e", prefix="tests/e2e/"), _R(0, "tests", prefix="tests/")]) is None,
      "a general rule after a specific one is the intended order")
check(v.classify_cmd(["tray.bat", ".gitignore"]) == "python scripts/classify_e2e.py tray.bat .gitignore",
      "the exact classifier command that checks a proposed rule's paths")
