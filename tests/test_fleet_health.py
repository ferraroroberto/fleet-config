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

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_health.py`
(also invoked by tests/run_acceptance.py)
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

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "fleet-health"

sys.path.insert(0, str(REPO / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(SKILL))
import capture  # noqa: E402

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

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_health")
