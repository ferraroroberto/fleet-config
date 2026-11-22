"""Regression tests for the three defects /fleet-health kept surviving on
hand-applied workarounds (fleet-config#812).

Three consecutive weekly runs delivered a digest only because the attending
agent re-derived three fixes from the previous run's ledger entry. Each one
below is pinned here so the scheduled run stops depending on rediscovery:

1. **UTF-8 stdout under capture** (5th occurrence). Both entry points print
   report text; Windows falls back to cp1252 when stdout is a pipe, so a
   single arrow raises UnicodeEncodeError and exits 1 -- in the scheduled run
   only, never in a terminal, which is why it shipped five times. Driven as a
   real subprocess with a pipe for stdout and `PYTHONUTF8`/`PYTHONIOENCODING`
   scrubbed
   from the child env: a test that inherits a UTF-8 environment proves nothing.
2. **Midnight crossing** (3rd occurrence, reproduced live at 00:07). The run
   date used to be recomputed from `date.today()` at every invocation, so a
   poll that crossed midnight resolved a different directory than `start`
   wrote. The date is a property of the run, resolved once and persisted.
3. **Peer addresses** (3rd occurrence). Addresses were parsed out of the hub's
   public `config/models.yaml`; they moved to a gitignored
   `machines.local.yaml` (local-llm-hub#525) and the block went empty, so
   every peer classified `no-address` until an overlay was hand-applied. The
   inventory now carries `ip` per machine, so it is the single source.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_fleet_health.py`
(also invoked by tests/run_weekly_units.py)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / ".claude" / "skills" / "fleet-health"

sys.path.insert(0, str(REPO / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(SKILL))
import capture  # noqa: E402
import storage_diff  # noqa: E402

_h = CheckHarness()
check = _h.check

# U+2192 RIGHTWARDS ARROW is the character the live run actually died on, and
# U+2248 / an emoji are the other two shapes the ledger carries. None of the
# three is representable in cp1252, so any one of them alone exits 1.
ARROW = "\u2192"
NON_CP1252 = "64.47 " + ARROW + " 106.09 GB \u2248 +41.6 \U0001fa7a"


def _captured(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run a skill entry point with stdout+stderr on a **pipe**, never a tty.

    The child env is scrubbed of `PYTHONUTF8`/`PYTHONIOENCODING` so Python
    picks the Windows ANSI code page exactly as it does under an app-launcher
    job. Output is decoded here, not by the child, so a crash stays visible as
    a traceback rather than being masked by this process's own encoding.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    proc = subprocess.run([sys.executable, *argv], cwd=str(cwd), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=120, creationflags=NO_WINDOW)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


tmp = Path(tempfile.mkdtemp(prefix="fleet_health_test_"))
try:
    # ------------------------------------------------------------- 1. UTF-8

    # ---- ledger.py previous: the step-1 crash, against a realistic entry ----
    root = tmp / "ledger-root"
    root.mkdir()
    (root / "fleet-health.md").write_text(
        "# Fleet health ledger\n\n## 2026-01-02\n\n### box \u2014 2026-01-02\n\n"
        "**Findings** \u2014 RAM " + NON_CP1252 + "\n\n## 2026-01-01\n\nolder\n",
        encoding="utf-8")

    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "previous"], REPO)
    check(code == 0,
          "ledger.py previous: exits 0 under captured stdout (got %s)" % code)
    check("UnicodeEncodeError" not in out,
          "ledger.py previous: no UnicodeEncodeError under captured stdout")
    check(ARROW in out,
          "ledger.py previous: the arrow the live run died on round-trips to stdout")
    check("PREVIOUS_RUN=2026-01-02" in out,
          "ledger.py previous: still reports the newest run heading")

    # ---- ledger.py append: a non-cp1252 entry writes, then reads back ----
    entries = tmp / "entries.md"
    entries.write_text(
        "### box \u2014 2026-01-03\n\n**Findings** \u2014 " + NON_CP1252 + "\n",
        encoding="utf-8")
    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "append",
         "--date", "2026-01-03", "--file", str(entries)], REPO)
    check(code == 0,
          "ledger.py append: exits 0 under captured stdout (got %s)" % code)
    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "previous"], REPO)
    check(code == 0 and ARROW in out and "PREVIOUS_RUN=2026-01-03" in out,
          "ledger.py append -> previous: a non-cp1252 entry round-trips through the ledger")

    # ---- capture.py collect: emit() prints hub-supplied reasons ----
    # `runs` is empty, so collect reads the skipped entries straight out of the
    # state file and makes no HTTP call -- the print path is the whole test.
    out_dir = tmp / "run-utf8"
    out_dir.mkdir()
    (out_dir / ".run-state.json").write_text(json.dumps({
        "run_date": "2026-01-02",
        "ledger_root": str(root),
        "out_dir": str(out_dir),
        "targets": {}, "runs": {},
        "skipped": [{"id": "peer", "detail": "no-hub",
                     "reason": "no hub answering " + NON_CP1252}],
    }), encoding="utf-8")
    code, out = _captured(
        [str(SKILL / "capture.py"), "--out-dir", str(out_dir), "collect"], REPO)
    check("UnicodeEncodeError" not in out,
          "capture.py collect: no UnicodeEncodeError printing a hub-supplied reason")
    check(ARROW in out,
          "capture.py collect: a non-cp1252 reason reaches stdout intact")
    check(code == 4,
          "capture.py collect: still exits 4 when nothing was captured (got %s)" % code)

    # ------------------------------------------------- 2. midnight crossing

    today = _dt.date.today().isoformat()
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    check(yesterday != today, "fixture sanity: yesterday differs from today")

    mid_root = tmp / "midnight-root"
    mid_dir = mid_root / "runs" / yesterday
    mid_dir.mkdir(parents=True)
    (mid_dir / ".run-state.json").write_text(json.dumps({
        "run_date": yesterday,
        "ledger_root": str(mid_root),
        "out_dir": str(mid_dir),
        "targets": {}, "runs": {},
        "skipped": [{"id": "peer", "detail": "dormant",
                     "reason": "machine is dormant"}],
    }), encoding="utf-8")
    capture.mark_active_run(mid_root, yesterday, mid_dir)

    class _Args:
        out_dir = None
        ledger_root = str(mid_root)
        date = None

    _root_r, out_r, date_r = capture.resolve_dirs(_Args(), follow_active=True)
    check(date_r == yesterday,
          "resolve_dirs: a poll after midnight reads the persisted run date "
          "(got %s, today is %s)" % (date_r, today))
    check(out_r == mid_dir,
          "resolve_dirs: the run directory follows the persisted date, not today's")

    # `start` must never follow a previous run's marker -- a new run gets a new date.
    _root_s, _out_s, date_s = capture.resolve_dirs(_Args(), follow_active=False)
    check(date_s == today,
          "resolve_dirs: `start` resolves today, ignoring a stale marker (got %s)" % date_s)

    # An explicit --date still wins over the marker, so a rerun stays targetable.
    class _Pinned(_Args):
        date = "2026-01-01"

    _root_p, out_p, date_p = capture.resolve_dirs(_Pinned(), follow_active=True)
    check(date_p == "2026-01-01" and out_p.name == "2026-01-01",
          "resolve_dirs: an explicit --date overrides the active-run marker")

    # End-to-end: `collect` with no --date finds yesterday's run and reports
    # yesterday's RUN_DATE, which is the date the ledger append then uses.
    code, out = _captured(
        [str(SKILL / "capture.py"), "--ledger-root", str(mid_root), "collect"], REPO)
    check("RUN_DATE=" + yesterday in out,
          "capture.py collect: reports the run's own date after midnight (expected %s), got %s"
          % (yesterday, [ln for ln in out.splitlines() if "RUN_DATE" in ln]))
    check("OUT_DIR=" + str(mid_dir) in out,
          "capture.py collect: writes into the run directory `start` created")
    check("no run state" not in out,
          "capture.py collect: finds the run state instead of looking under today's date")

    # A marker must expire. A `collect` whose `start` never ran (hub down,
    # exit 3) would otherwise follow the *previous* run's marker and republish
    # last week's capture as this week's entry -- the one failure mode SKILL.md
    # calls out as making the ledger lie.
    day = _dt.date.today()
    check(capture.is_current_run(day.isoformat(), day),
          "is_current_run: the run's own date is current")
    check(capture.is_current_run((day - _dt.timedelta(days=1)).isoformat(), day),
          "is_current_run: yesterday is current -- that is the midnight window")
    check(not capture.is_current_run((day - _dt.timedelta(days=2)).isoformat(), day),
          "is_current_run: a two-day-old marker is a previous run, not this one")
    check(not capture.is_current_run((day - _dt.timedelta(days=7)).isoformat(), day),
          "is_current_run: last week's marker is never adopted")
    check(not capture.is_current_run((day + _dt.timedelta(days=1)).isoformat(), day),
          "is_current_run: a future-dated marker is refused, not trusted")
    check(not capture.is_current_run("not-a-date", day),
          "is_current_run: an unparseable date is refused, never guessed")
    check(not capture.is_current_run("", day),
          "is_current_run: a missing date is refused")

    # End-to-end: a stale marker is ignored, so `collect` reports the honest
    # "no run state" exit 2 rather than silently collecting the older run.
    stale_root = tmp / "stale-root"
    stale_date = (day - _dt.timedelta(days=7)).isoformat()
    stale_dir = stale_root / "runs" / stale_date
    stale_dir.mkdir(parents=True)
    (stale_dir / ".run-state.json").write_text(json.dumps({
        "run_date": stale_date, "ledger_root": str(stale_root),
        "out_dir": str(stale_dir), "targets": {}, "runs": {},
        "skipped": [{"id": "peer", "detail": "dormant", "reason": "machine is dormant"}],
    }), encoding="utf-8")
    capture.mark_active_run(stale_root, stale_date, stale_dir)
    code, out = _captured(
        [str(SKILL / "capture.py"), "--ledger-root", str(stale_root), "collect"], REPO)
    check(code == 2,
          "capture.py collect: a stale marker is not adopted -- exit 2, not a "
          "silent republish of last week's run (got %s)" % code)
    check("no run state" in out,
          "capture.py collect: says it found no run state rather than reporting the old one")
    check(stale_date not in out,
          "capture.py collect: never reports the stale run's date as this run's")
    # ...and the stale marker stays targetable by hand, which is what --date is for.
    code, out = _captured(
        [str(SKILL / "capture.py"), "--ledger-root", str(stale_root),
         "--date", stale_date, "collect"], REPO)
    check("RUN_DATE=" + stale_date in out,
          "capture.py collect: an explicit --date still re-targets an older run")

    # The marker carries the run directory too, so a custom --out-dir set on
    # `start` does not have to be repeated on the later verbs either.
    cust_root = tmp / "custom-root"
    cust_dir = tmp / "elsewhere" / "run"
    cust_dir.mkdir(parents=True)
    (cust_dir / ".run-state.json").write_text(json.dumps({
        "run_date": day.isoformat(), "ledger_root": str(cust_root),
        "out_dir": str(cust_dir), "targets": {}, "runs": {},
        "skipped": [{"id": "peer", "detail": "dormant", "reason": "machine is dormant"}],
    }), encoding="utf-8")
    capture.mark_active_run(cust_root, day.isoformat(), cust_dir)
    code, out = _captured(
        [str(SKILL / "capture.py"), "--ledger-root", str(cust_root), "collect"], REPO)
    check("OUT_DIR=" + str(cust_dir) in out,
          "capture.py collect: follows the run directory `start` recorded, not just its date")

    # ------------------------------------------------- 3. peer addresses

    # The host is always loopback; a peer is dialled at the `ip` the inventory
    # returns. No config file is read, so an address moving between hub config
    # files cannot silently empty the target list again.
    check(capture.diagnostics_base({"id": "tower", "is_host": True, "ip": "10.0.0.1"})
          == capture.HUB,
          "diagnostics_base: the host runs on loopback regardless of its ip")
    check(capture.diagnostics_base({"id": "peer", "ip": "10.0.0.2"})
          == "http://10.0.0.2:%d" % capture.HUB_PORT,
          "diagnostics_base: a peer is dialled at the ip the inventory returned")
    check(capture.diagnostics_base({"id": "peer"}) is None,
          "diagnostics_base: a peer with no ip resolves to no base, never a guessed hostname")
    check(capture.diagnostics_base({"id": "peer", "ip": ""}) is None,
          "diagnostics_base: an empty ip is treated as absent, not as a valid host")

    status, reason, base = capture.classify(
        {"id": "peer", "state": "up", "reachable": True})
    check(status == "no-address" and base is None,
          "classify: a reachable peer with no ip reports no-address (got %s)" % status)
    check("no LAN address" in reason,
          "classify: the no-address reason still says why the peer could not be dialled")

    # Ordering is load-bearing: dormant and unreachable are decided before the
    # address, so a powered-off box never reports as an addressing problem.
    status, _reason, _base = capture.classify(
        {"id": "peer", "dormant": True, "ip": "10.0.0.2"})
    check(status == "dormant", "classify: dormant still wins over addressing (got %s)" % status)
    status, _reason, _base = capture.classify(
        {"id": "peer", "state": "down", "reachable": False, "ip": "10.0.0.2"})
    check(status == "unreachable",
          "classify: unreachable still wins over addressing (got %s)" % status)

    # The models.yaml reader is gone, not merely unused: a leftover parser is a
    # second source of truth waiting to be re-wired (global CLAUDE.md).
    check(not hasattr(capture, "load_addresses"),
          "capture.py: the models.yaml address parser is removed, not left dead")
    src = (SKILL / "capture.py").read_text(encoding="utf-8")
    check("MODELS_YAML" not in src and "FLEET_HEALTH_MODELS_YAML" not in src,
          "capture.py: the models.yaml constant and its env override are gone")
    # The module docstring still records *why* it stopped reading that file --
    # that history is the point. What must not survive is code that reads it.
    _body = src.split('"""', 2)[2]
    check("models.yaml" not in _body,
          "capture.py: models.yaml survives only as docstring history, never in code")
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    check("FLEET_HEALTH_MODELS_YAML" not in skill_md,
          "SKILL.md: stops telling the reader to point a removed env var at models.yaml")

    # ------------------------------------------------- 4. unknown is not done

    # A status probe that cannot answer is "unknown", never "finished": folding
    # it into False ended the poll early and let `collect` POST /stop on a
    # capture that was still running.
    _real_get, _real_post = capture._get, capture._post
    _answers: dict = {}
    _stops: list = []
    capture._get = lambda url, timeout=capture.TIMEOUT_S: _answers[url.split("/admin")[0]]
    capture._post = lambda url, payload=None, timeout=capture.TIMEOUT_S: (
        _stops.append(url) or (200, b"{}"))
    try:
        _answers["http://peer"] = (0, b"connection refused")
        check(capture.poll_once("http://peer")[0] is None,
              "poll_once: a failed probe is unknown (None), not 'not capturing'")
        _answers["http://peer"] = (503, b"")
        check(capture.poll_once("http://peer")[0] is None,
              "poll_once: a 5xx probe is unknown (None)")
        _answers["http://peer"] = (200, b'{"capturing": false}')
        check(capture.poll_once("http://peer")[0] is False,
              "poll_once: an answered 'not capturing' is False")
        _answers["http://peer"] = (200, b'{"capturing": true, "active": {"samples_written": 4}}')
        check(capture.poll_once("http://peer")[:2] == (True, 4),
              "poll_once: an answered 'capturing' is True with its sample count")

        _answers["http://peer"] = (0, b"connection refused")
        progress = capture.poll_chunk({"peer": "http://peer"}, 0.01)
        check(progress["peer"]["capturing"] is not False,
              "poll_chunk: an unanswered probe leaves the machine pending, not done")

        _answers["http://peer"] = (200, b'{"capturing": false}')
        progress = capture.poll_chunk({"peer": "http://peer"}, 5.0)
        check(progress["peer"]["capturing"] is False,
              "poll_chunk: a confirmed finish settles the machine")

        # cmd_poll / cmd_collect end to end against the stubbed hub.
        import contextlib as _cx
        import io as _io
        import time as _time

        def _state(started_at: float, duration_s: float) -> Path:
            d = tmp / ("unknown-%d" % int(started_at))
            d.mkdir(parents=True, exist_ok=True)
            (d / ".run-state.json").write_text(json.dumps({
                "run_date": "2026-01-02", "ledger_root": str(tmp), "out_dir": str(d),
                "duration_s": duration_s, "started_at": started_at,
                "targets": {"peer": "http://peer"}, "runs": {"peer": "run1"},
                "skipped": [],
            }), encoding="utf-8")
            return d

        class _PollArgs:
            ledger_root = str(tmp)
            date = "2026-01-02"
            chunk_s = 0.01
            out_dir = ""

        def _run(fn, out_dir: Path) -> str:
            _PollArgs.out_dir = str(out_dir)
            buf = _io.StringIO()
            with _cx.redirect_stdout(buf):
                fn(_PollArgs)
            return buf.getvalue()

        _answers["http://peer"] = (0, b"connection refused")
        live = _run(capture.cmd_poll, _state(_time.time(), 3600.0))
        check("DONE=no" in live and "STILL_CAPTURING=peer" in live,
              "cmd_poll: an unknown probe inside the capture window is DONE=no, not DONE=yes")
        expired = _run(capture.cmd_poll, _state(_time.time() - 100000, 3600.0))
        check("DONE=yes" in expired and "UNCONFIRMED=peer" in expired
              and "status=unconfirmed" in expired,
              "cmd_poll: past the deadline an unknown probe settles as UNCONFIRMED, its own state")

        collected = _run(capture.cmd_collect, _state(_time.time() + 1, 3600.0))
        check(not any(u.endswith("/diagnostics/stop") for u in _stops),
              "cmd_collect: an unknown probe never POSTs /diagnostics/stop")
        check("status=unconfirmed" in collected,
              "cmd_collect: an unknown probe is reported as unconfirmed")

        _answers["http://peer"] = (200, b'{"capturing": true}')
        _run(capture.cmd_collect, _state(_time.time() + 2, 3600.0))
        check(any(u.endswith("/diagnostics/stop") for u in _stops),
              "cmd_collect: a confirmed still-running capture is still stopped")
    finally:
        capture._get, capture._post = _real_get, _real_post

    # ------------------------------------------------ 4. storage and Docker flags
    # `storage_diff.py` reads the hub's `run.storage` section out of each
    # machine's export and diffs it against the newest earlier run. Synthetic
    # exports only: the real ledger never enters the repo or the test.
    G = storage_diff.GIB

    def _storage(drives=None, growers=None, folders=None, docker=None) -> dict:
        return {
            "schema": 1,
            "drives": {"status": "ok", "items": drives if drives is not None else [
                {"mount": "D:", "status": "ok", "total_bytes": 1000 * G, "free_bytes": 500 * G}]},
            "growers": {"status": "ok", "items": growers or []},
            "top_folders": {"status": "ok", "roots": [
                {"root": "R", "status": "ok", "folders": folders or []}]},
            "docker": docker if docker is not None else {
                "status": "ok", "server_version": "1",
                "containers": {"status": "ok", "items": []},
                "usage": {"status": "ok", "items": []}},
        }

    def _grower(label: str, size_gb=None, **extra) -> dict:
        item = {"label": label, "path": "P/" + label}
        if size_gb is None:
            return {**item, "status": "unknown", "reason": "walk timed out", **extra}
        return {**item, "status": "ok", "bytes": int(size_gb * G)}

    def _folder(path: str, size_gb: float) -> dict:
        return {"path": path, "status": "ok", "bytes": int(size_gb * G)}

    def _write_export(run_dir: Path, machine: str, storage) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        run = {"run_id": "r"} if storage is None else {"run_id": "r", "storage": storage}
        (run_dir / (machine + ".json")).write_text(json.dumps({"run": run}), encoding="utf-8")

    def _analyse(runs: Path, date: str) -> str:
        code, out = _captured([str(SKILL / "storage_diff.py"), "analyse",
                               "--out-dir", str(runs / date)], REPO)
        check(code == 0, "storage_diff analyse: exits 0 under captured stdout (got %s)" % code)
        return out

    runs = tmp / "storage-runs"
    prev_dir, cur_dir = runs / "2026-02-01", runs / "2026-02-08"

    # ---- second run diffs against the first and flags a planted grower ----
    _write_export(prev_dir, "box", _storage(
        growers=[_grower("onedrive-logs", 20), _grower("model-cache", 100),
                 _grower("small-grower", 1), _grower("gone-dark", 10)],
        folders=[_folder("A", 40), _folder("B", 30)]))
    _write_export(cur_dir, "box", _storage(
        growers=[_grower("onedrive-logs", 30),        # +10 GB, +50%: flagged
                 _grower("model-cache", 104),         # +4 GB: under the byte floor
                 _grower("small-grower", 4),          # +300% but only 3 GB
                 _grower("gone-dark", None)],         # unmeasured now
        folders=[_folder("A", 40.2), _folder("NEW-BIG", 12), _folder("NEW-SMALL", 1)]))
    out = _analyse(runs, "2026-02-08")
    manifest = out.split("--- MARKDOWN ---")[0]
    check("STORAGE|box|status=compared|previous=2026-02-01" in out,
          "storage_diff: the second run diffs against the first")
    check("FLAG|box|grower-growth|grower onedrive-logs +10.0 GB" in out,
          "storage_diff: a planted grower growing past both thresholds is flagged")
    check("2172" in manifest,
          "storage_diff: the OneDrive flag names the known safe cleanup")
    check("model-cache" not in out and "small-grower" not in out,
          "storage_diff: growth under the byte floor or the percentage is not flagged")
    check("FLAG|box|folder-growth|folder NEW-BIG" in out,
          "storage_diff: a folder new in the top list and over the floor is flagged")
    check("NEW-SMALL" not in out and "folder A" not in out,
          "storage_diff: a small new folder and a flat folder are not flagged")
    check("UNKNOWN|box|growth-unknown|grower gone-dark: size unknown (walk timed out)" in out
          and "FLAG|box|grower-growth|grower gone-dark" not in out,
          "storage_diff: an unmeasured grower is unknown, never diffed as zero or flagged")
    check("**Storage** — box" in out,
          "storage_diff: prints a ledger-ready markdown block")

    # ---- a lower bound can prove growth; a previous unknown cannot be diffed ----
    _write_export(prev_dir, "lb", _storage(growers=[_grower("a", 10), _grower("b", None)]))
    _write_export(cur_dir, "lb", _storage(growers=[
        _grower("a", None, lower_bound_bytes=30 * G), _grower("b", 50)]))
    out = _analyse(runs, "2026-02-08")
    check("FLAG|lb|grower-growth|grower a grew by at least 20.0 GB" in out,
          "storage_diff: a lower bound past the thresholds proves growth")
    check("UNKNOWN|lb|growth-unknown|grower b: previous size unknown" in out,
          "storage_diff: a size whose previous value was unknown is not diffed")

    # ---- Docker: unknown is its own flag, only unhealthy containers flag ----
    _write_export(cur_dir, "dk", _storage(docker={
        "status": "unknown", "reason": "docker info timed out after 8s"}))
    out = _analyse(runs, "2026-02-08")
    check("FLAG|dk|docker-unreachable|docker engine did not answer: docker info timed out"
          in out, "storage_diff: an engine that does not answer is flagged, not passed")
    check("STORAGE|dk|status=baseline" in out,
          "storage_diff: a machine with no earlier storage section is a baseline")

    _write_export(cur_dir, "nd", _storage(docker={
        "status": "unknown", "reason": "docker CLI not on PATH"}))
    _write_export(cur_dir, "ct", _storage(docker={
        "status": "ok", "server_version": "1",
        "containers": {"status": "ok", "items": [
            {"name": "sick", "state": "running", "health": "unhealthy"},
            {"name": "stopped", "state": "exited", "health": None},
            {"name": "fine", "state": "running", "health": "healthy"}]},
        "usage": {"status": "unknown", "reason": "docker system df timed out"}}))
    out = _analyse(runs, "2026-02-08")
    check("UNKNOWN|nd|docker-not-installed" in out and "FLAG|nd|" not in out,
          "storage_diff: no Docker installed is unknown, not a flagged outage")
    check("FLAG|ct|docker-unhealthy|container sick" in out,
          "storage_diff: an unhealthy container is flagged")
    check("stopped" not in out and "fine" not in out,
          "storage_diff: stopped and healthy containers are not flagged")
    check("UNKNOWN|ct|docker-unknown|docker usage totals: docker system df timed out" in out,
          "storage_diff: an unmeasured Docker usage total reports unknown")

    # ---- drives: percentage or absolute floor; unknown drive is not low ----
    _write_export(cur_dir, "dr", _storage(drives=[
        {"mount": "C:", "status": "ok", "total_bytes": 1000 * G, "free_bytes": 50 * G},
        {"mount": "E:", "status": "ok", "total_bytes": 100 * G, "free_bytes": 40 * G},
        {"mount": "F:", "status": "ok", "total_bytes": 1000 * G, "free_bytes": 400 * G},
        {"mount": "G:", "status": "unknown", "reason": "usage did not return within 3s"}]))
    out = _analyse(runs, "2026-02-08")
    check("FLAG|dr|low-free-space|drive C:" in out,
          "storage_diff: a drive under the free-space percentage is flagged")
    check("FLAG|dr|low-free-space|drive E:" in out,
          "storage_diff: a drive under the absolute free-space floor is flagged")
    check("drive F:" not in out and "UNKNOWN|dr|drive-unknown|drive G:" in out,
          "storage_diff: a roomy drive is quiet and an unreadable drive is unknown")

    # ---- no section at all, and nothing is written ----
    _write_export(cur_dir, "old", None)
    before = sorted(p.name for p in cur_dir.iterdir())
    out = _analyse(runs, "2026-02-08")
    check("STORAGE|old|status=unknown|reason=no storage section" in out,
          "storage_diff: an export with no storage section is unknown, not clean")
    check(sorted(p.name for p in cur_dir.iterdir()) == before,
          "storage_diff: the run directory is untouched (read-only)")
    check(storage_diff.fmt_bytes(1536) == "1.5 KB",
          "storage_diff: byte formatting is stable")
    code, out = _captured([str(SKILL / "storage_diff.py"), "analyse",
                           "--out-dir", str(tmp / "nowhere")], REPO)
    check(code == 2, "storage_diff: a missing run directory is a usage error (2)")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_health")
