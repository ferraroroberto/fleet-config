"""Unit tests for `perf_review sweep` (fleet-config#1686): who is in scope, one app at a time, the digest.

Measuring and filing are stubbed (the real legs have their own suite in test_perf_review.py): the sweep owns the
population, the per-app box lock, the unmeasured states and the digest, and those are what is pinned here.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_perf_sweep.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

_STATE = tempfile.mkdtemp(prefix="perf-sweep-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = _STATE

from check_harness import CheckHarness  # noqa: E402

import box_lock  # noqa: E402
from perf_review import cli, sweep  # noqa: E402

_h = CheckHarness()
check = _h.check

toml = Path(tempfile.mkdtemp(prefix="perf-sweep-fleet-")) / "projects.toml"
toml.write_text('''
[alpha]
cwd_prefix = "E:/x/alpha"
webapp_port = 8001
[beta]
cwd_prefix = "E:/x/beta"
webapp_port = 8002
[gamma]
cwd_prefix = "E:/x/gamma"
[hidden]
cwd_prefix = "E:/x/hidden"
webapp_port = 8003
[global]
architecture_ignore = ["hidden"]
''', encoding="utf-8")

os.environ["FLEET_PROJECTS_TOML"] = str(toml)  # the measure leg's own target lookup reads the same list
apps, off = sweep.web_apps(toml)
check(apps == ["alpha", "beta"], f"only repos declaring a webapp_port are swept, sorted: {apps}")
check(off == ["hidden"], f"a repo marked dormant is named, not swept: {off}")
check(sweep.web_apps(toml, "hidden") == (["hidden"], []), "a named repo runs even when dormant")
check(sweep.web_apps(toml, "gamma") == ([], []), "a repo with no web port is not a web app, even when named")

# ---- a web app that declares its port only in its own .fleet.toml ----
cards = Path(tempfile.mkdtemp(prefix="perf-sweep-cards-"))
for repo, card in {"card-web": 'layer = "working-web"\nport = ":8449"\n',
                   "card-mirror": 'layer = "enabling"\nport = ":8000"\n',
                   "card-noport": 'layer = "working-web"\n',
                   "card-pipe": 'layer = "working-pipe"\n',
                   "card-dormant": 'layer = "working-web"\nport = ":8460"\n'}.items():
    (cards / repo).mkdir()
    (cards / repo / ".fleet.toml").write_text(card, encoding="utf-8")
(cards / "card-bare").mkdir()
card_toml = cards / "projects.toml"
card_toml.write_text("".join(f'[{n}]\ncwd_prefix = "{(cards / n).as_posix()}"\n' for n in
                             ("card-web", "card-mirror", "card-noport", "card-pipe", "card-bare", "card-dormant"))
                     + f'[declared]\ncwd_prefix = "{(cards / "card-pipe").as_posix()}"\nwebapp_port = 8010\n'
                     + '[global]\narchitecture_ignore = ["card-dormant"]\n', encoding="utf-8")
apps_c, off_c = sweep.web_apps(card_toml)
check(apps_c == ["card-web", "declared"], f"a working-web repo with only a .fleet.toml port is swept, in file order: {apps_c}")
check(off_c == ["card-dormant"] and "card-dormant" not in dict(sweep.left_out(card_toml)),
      f"a dormant card-only app is named as dormant, not swept and not double-listed as left out: {off_c}")
out_c = dict(sweep.left_out(card_toml))
check(list(out_c) == ["card-mirror", "card-noport"], f"a mirror on another layer and a port-less working-web repo are named, a pipeline repo is not: {list(out_c)}")
check(":8000" in out_c["card-mirror"] and "no port" not in out_c["card-mirror"] and "neither" in out_c["card-noport"],
      f"each left-out repo carries its own reason: {out_c}")
check(sweep.left_out(card_toml, only="card-mirror") == [], "a single named app has no left-out list")
digest_c = sweep.render_digest([], [], "2026-10-28", sweep.left_out(card_toml))
check("## Left out" in digest_c and "- card-mirror: " in digest_c and "- card-noport: " in digest_c and "card-pipe" not in digest_c,
      f"the digest names every repo it left out with its reason: {digest_c}")
check("## Left out" not in sweep.render_digest([], [], "2026-10-28"), "no left-out repos, no section")

# ---- a dead port: unmeasured with the reason, nothing filed ----
filed = []
cli.cmd_file = lambda ns: filed.append(ns.repo) or 0  # type: ignore[assignment]
args = argparse.Namespace(duration=0.2, settle=1, lock_wait=0.05, no_file=False)
orig_resolve = cli.resolve_target
cli.resolve_target = lambda repo, url_override=None: orig_resolve(repo, url_override="http://127.0.0.1:9")  # type: ignore[assignment]
with redirect_stdout(io.StringIO()):
    dead = sweep.measure_app(tempfile.mkdtemp(prefix="perf-sweep-target-"), args)
dead["repo"] = "fixture"
check(dead["state"] == "unmeasured" and "NOT_LISTENING" in dead["reason"] and dead["issue"] is None and not filed,
      f"a down port is unmeasured with its reason, and no issue is filed for it: {dead}")


# ---- a measured app: the verdict feeds the row, filing is attempted against that run ----
def fake_measure(ns: argparse.Namespace) -> int:
    run = Path(_STATE) / "run-1"
    run.mkdir(exist_ok=True)
    verdict = {"summary": {"overall": "over-budget", "unmeasured": 1},
               "checks": [{"id": "cold.bytes_kb", "status": "fail", "measured": 2287, "budget": 1000},
                          {"id": "cold.ready_ms", "status": "pass", "measured": 900, "budget": 2000}],
               "diff": {"regressed": ["warm.ready_ms"], "fixed": [], "slower": [{"path": "/", "from": 31, "to": 43, "status": "pass"}]},
               "deploy": {"state": "behind"}}
    (run / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    print(f"RUN_DIR={run}")
    return 1


cli.cmd_measure = fake_measure  # type: ignore[assignment]
runs = []
cli.cmd_file = lambda ns: runs.append((ns.repo, ns.run, ns.apply)) or print("https://example.invalid/issues/7") or 0  # type: ignore[assignment]
with redirect_stdout(io.StringIO()):
    row = sweep.measure_app("alpha", args)
check(row["state"] == "over-budget" and row["fails"][0]["id"] == "cold.bytes_kb" and row["regressed"] == ["warm.ready_ms"]
      and row["build"] == "behind" and row["issue"] == "https://example.invalid/issues/7",
      f"a measured app carries its over-budget checks, regressions, build state and issue: {row}")
check(runs == [("alpha", str(Path(_STATE) / "run-1"), True)], f"the issue is upserted from this run's directory, not the latest on disk: {runs}")
args_nofile = argparse.Namespace(duration=0.2, settle=1, lock_wait=0.05, no_file=True)
runs.clear()
with redirect_stdout(io.StringIO()):
    sweep.measure_app("alpha", args_nofile)
check(not runs, "--no-file touches no issue")


def boom(ns: argparse.Namespace) -> int:
    raise RuntimeError("playwright exploded")


cli.cmd_measure = boom  # type: ignore[assignment]
with redirect_stdout(io.StringIO()):
    err = sweep.measure_app("alpha", args)
check(err["state"] == "unmeasured" and "playwright exploded" in err["reason"], f"an app that raises is unmeasured, the sweep goes on: {err}")

# ---- the box lock: busy box -> unmeasured, never measured under load ----
marker, _ = box_lock.try_acquire(box_lock.SHARED, "some-lane-gate")
called = []
busy = sweep.run_one("alpha", args, measure=lambda r, a: called.append(r) or {})
check(busy["state"] == "unmeasured" and busy["reason"].startswith("BOX_BUSY") and not called,
      f"a gate holding the box keeps the app unmeasured and is never measured through: {busy}")
box_lock.release(marker)
free = sweep.run_one("alpha", args, measure=lambda r, a: {"repo": r, "state": "pass"})
check(free["state"] == "pass" and not box_lock.holders(), "a free box measures the app and releases the lock afterwards")

# ---- the digest ----
rows = [row, dead, {"repo": "beta", "state": "pass", "reason": "", "fails": [], "unmeasured_checks": 0, "regressed": [],
                    "slower": [], "fixed": ["cold.ready_ms"], "build": "live", "issue": "https://example.invalid/issues/9"}]
digest = sweep.render_digest(rows, ["hidden"], "2026-10-28")
check("3 web apps: 1 pass, 1 over budget, 1 unmeasured, 1 slower than the last run." in digest, f"digest counts: {digest[:300]}")
check("| alpha | over-budget | cold.bytes_kb 2287 > 1000 | regressed: warm.ready_ms; slower p95: / 31->43 ms |" in digest,
      "an over-budget check and a regression are named on the app's row")
check("| fixture |" in digest and "- fixture: " in digest and "NOT_LISTENING" in digest, "a down app is in the table and in the Unmeasured section with its reason")
check("alpha" in digest.split("older build")[1].split("\n")[0], "an app serving an older build is called out")
check("Dormant, not measured: hidden." in digest, "apps dormant are named")

# ---- the command: sequential, one digest file, exit 3 only when nothing was measured ----
sweep.web_apps = lambda projects_toml=None, only=None: (["alpha", "beta"], [])  # type: ignore[assignment]
order = []
sweep.run_one = lambda repo, a: order.append(repo) or {"repo": repo, "state": "pass", "reason": "", "fails": [],  # type: ignore[assignment]
                                                         "unmeasured_checks": 0, "regressed": [], "slower": [], "fixed": [],
                                                         "build": "live", "issue": None}
out = io.StringIO()
with redirect_stdout(out):
    rc = sweep.cmd_sweep(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True))
text = out.getvalue()
digest_file = Path(text.split("DIGEST_FILE=")[1].splitlines()[0])
check(rc == 0 and order == ["alpha", "beta"] and "SWEEP=done measured=2 unmeasured=0" in text and digest_file.is_file(),
      f"the sweep runs the apps in order, prints the digest and records it: rc={rc} {order}")
sweep.run_one = lambda repo, a: {"repo": repo, "state": "unmeasured", "reason": "NOT_LISTENING", "fails": [],  # type: ignore[assignment]
                                 "unmeasured_checks": 0, "regressed": [], "slower": [], "fixed": [], "build": None, "issue": None}
with redirect_stdout(io.StringIO()):
    rc_none = sweep.cmd_sweep(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True))
check(rc_none == 3, f"a sweep that measured nothing exits 3, never 0: {rc_none}")
sweep.web_apps = lambda projects_toml=None, only=None: ([], [])  # type: ignore[assignment]
with redirect_stdout(io.StringIO()):
    rc_empty = sweep.cmd_sweep(argparse.Namespace(only="nope", duration=1, settle=1, lock_wait=1, no_file=True))
check(rc_empty == 2, "no app in scope is refused (exit 2), not an empty pass")

# ---- detached launch + sentinel publish (fleet-config#1558) ----------------

# write_result: atomic publish, no leftover temp files.
sent = Path(tempfile.mkdtemp(prefix="perf-sweep-sentinel-")) / "result.json"
sweep.write_result(sent, {"hello": "world"})
check(sent.exists(), "write_result: sentinel file exists after publish")
check(json.loads(sent.read_text(encoding="utf-8")) == {"hello": "world"},
      "write_result: sentinel content round-trips as JSON")
check(list(sent.parent.glob("*.tmp-*")) == [], "write_result: no temp file left behind")
sweep.write_result(sent, {"hello": "again"})
check(json.loads(sent.read_text(encoding="utf-8")) == {"hello": "again"},
      "write_result: atomic overwrite replaces a stale sentinel")

check(sweep.default_out_path() != sweep.default_out_path(),
      "default_out_path: two calls never collide on the same path")
check(str(sweep.default_out_path()).endswith(".json"), "default_out_path: sentinel path is a .json file")

# run_and_publish: success path writes the sentinel with the sweep's rc and digest file.
sweep.web_apps = lambda projects_toml=None, only=None: (["alpha"], [])  # type: ignore[assignment]
sweep.run_one = lambda repo, a: {"repo": repo, "state": "pass", "reason": "", "fails": [],  # type: ignore[assignment]
                                 "unmeasured_checks": 0, "regressed": [], "slower": [], "fixed": [],
                                 "build": "live", "issue": None}
out_ok = Path(tempfile.mkdtemp(prefix="perf-sweep-sentinel-")) / "result.json"
rc_pub = sweep.run_and_publish(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True), out_ok)
pub = json.loads(out_ok.read_text(encoding="utf-8"))
check(rc_pub == 0 and pub["rc"] == 0 and pub["digest_file"] and Path(pub["digest_file"]).is_file(),
      f"run_and_publish: the sentinel carries the sweep's rc and a real digest file path: {pub}")

# run_and_publish: a cmd_sweep crash still publishes a sentinel (an error payload) instead of leaving a poller
# waiting forever on a file that will never appear.
orig_cmd_sweep = sweep.cmd_sweep
sweep.cmd_sweep = lambda a: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]
out_err = Path(tempfile.mkdtemp(prefix="perf-sweep-sentinel-")) / "result.json"
try:
    sweep.run_and_publish(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True), out_err)
    check(False, "run_and_publish: re-raises the original exception")
except RuntimeError:
    pass
sweep.cmd_sweep = orig_cmd_sweep
err_payload = json.loads(out_err.read_text(encoding="utf-8"))
check("error" in err_payload and "boom" in err_payload["error"],
      f"run_and_publish: a crash still publishes a sentinel carrying the error: {err_payload}")

# dispatch_sweep: plain call with no --out/--detach behaves exactly like cmd_sweep.
with redirect_stdout(io.StringIO()):
    rc_plain = sweep.dispatch_sweep(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True,
                                                         detach=False, out=None, log=None))
check(rc_plain == 0, "dispatch_sweep: with neither flag, runs synchronously like cmd_sweep")

# dispatch_sweep: --out alone runs synchronously but still publishes the sentinel.
out_direct = Path(tempfile.mkdtemp(prefix="perf-sweep-sentinel-")) / "result.json"
with redirect_stdout(io.StringIO()):
    rc_direct = sweep.dispatch_sweep(argparse.Namespace(only=None, duration=1, settle=1, lock_wait=1, no_file=True,
                                                          detach=False, out=out_direct, log=None))
check(rc_direct == 0 and out_direct.is_file(), "dispatch_sweep: --out alone still publishes the sentinel synchronously")

# dispatch_sweep: --detach never blocks — it only launches a child and prints LAUNCHED, never running cmd_sweep itself.
launched = {}


def _fake_launch(argv, log_path):
    launched["argv"] = argv
    launched["log_path"] = log_path
    return 4242


sweep.launch_detached = _fake_launch  # type: ignore[assignment]
out_detach = Path(tempfile.mkdtemp(prefix="perf-sweep-sentinel-")) / "detach-result.json"
with redirect_stdout(io.StringIO()) as buf_detach:
    rc_detach = sweep.dispatch_sweep(argparse.Namespace(only="alpha", duration=1, settle=1, lock_wait=1, no_file=True,
                                                          detach=True, out=out_detach, log=None))
text_detach = buf_detach.getvalue()
check(rc_detach == 0 and f"LAUNCHED pid=4242 out={out_detach}" in text_detach,
      f"dispatch_sweep: --detach returns immediately and prints the sentinel path: {text_detach!r}")
check("--only" in launched["argv"] and "alpha" in launched["argv"] and "--out" in launched["argv"],
      f"dispatch_sweep: --detach forwards the run's own flags to the child: {launched['argv']}")

check(sweep.default_out_path() != out_detach, "default_out_path: never reused across invocations")

_h.report_and_exit("test_perf_sweep")
