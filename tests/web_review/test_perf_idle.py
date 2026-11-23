"""Unit tests for the `/perf-sweep` idle sample: CPU s/min and processes started/min from a fake process table.

The clock and the process table are frozen fakes, so nothing real is read, slept on or signalled.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_perf_idle.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

_STATE = tempfile.mkdtemp(prefix="perf-idle-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = _STATE

from check_harness import CheckHarness  # noqa: E402
from perf_review import cli, sweep  # noqa: E402
from perf_review import idle_sample as ids  # noqa: E402

_h = CheckHarness()
check = _h.check

T0 = 1_000_000.0
OLD = T0 - 5000
P = ids.ProcRow


class Fake:
    """A clock and a table that advance together: each `sleep` moves time on and serves the next scripted table."""

    def __init__(self, tables):
        self.tables, self.i, self.now = tables, 0, T0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.i = min(self.i + 1, len(self.tables) - 1)

    def table(self):
        t = self.tables[self.i]
        return t() if callable(t) else t


def run(tables, roots=frozenset({100}), window=60.0, interval=20.0):
    f = Fake(tables)
    return ids.sample(8000, window, interval, table_fn=f.table, pids_fn=lambda port: set(roots), sleep=f.sleep, clock=f.clock)


def root(cpu):
    return P(100, 1, OLD, cpu)


def neighbour(cpu):
    return P(900, 1, OLD, cpu)


def child(pid, born, cpu):
    return P(pid, 100, born, cpu)


# ---- a quiet app: 3 CPU s over 60 s, no children, a busy neighbour left out ----
quiet = run([[root(10.0), neighbour(0)], [root(11.0), neighbour(50)], [root(12.0), neighbour(99)], [root(13.0), neighbour(150)]])
check(quiet["state"] == "measured" and quiet["cpu_s_min"] == 3.0 and quiet["procs_min"] == 0.0,
      f"idle CPU is the tree's growth per minute, a neighbour excluded: {quiet}")

# ---- children: one started per pass, with its own CPU from birth ----
kids = run([[root(10.0)],
            [root(10.5), child(201, T0 + 5, 1.0)],
            [root(11.0), child(201, T0 + 5, 2.0), child(202, T0 + 25, 0.5)],
            [root(11.5), child(203, T0 + 45, 0.25)]])
# root 1.5 + child 201 (0 -> 2.0) + child 202 0.5 + child 203 0.25 = 4.25 CPU s in 60 s; 3 processes started
check(kids["state"] == "measured" and kids["procs_min"] == 3.0 and kids["cpu_s_min"] == 4.25,
      f"children count from birth, in the window only: {kids}")
grand = run([[root(1.0)], [root(1.0), child(201, T0 + 5, 0.0), P(300, 201, T0 + 6, 0.0)], [root(1.0)], [root(1.0)]])
check(grand["procs_min"] == 2.0, f"a grandchild is in the tree: {grand}")
reused = run([[root(1.0)], [root(1.0), P(300, 100, OLD - 10, 0.0)], [root(1.0)], [root(1.0)]])
check(reused["procs_min"] == 0.0, f"a child older than its parent is a reused PID, not a descendant: {reused}")
before = run([[root(1.0), child(201, OLD, 5.0)], [root(1.0), child(201, OLD, 5.0)], [root(1.0), child(201, OLD, 5.0)], [root(1.0), child(201, OLD, 5.0)]])
check(before["procs_min"] == 0.0 and before["cpu_s_min"] == 0.0, f"a child already running before the window is not a start: {before}")

# ---- unknown is never zero ----
missing = run([[]])
check(missing["state"] == "unknown" and "not in the process table" in missing["reason"], f"a listener missing from the table is unknown: {missing}")
unreadable = run([None])
check(unreadable["state"] == "unknown" and "unreadable" in unreadable["reason"], f"an unreadable table is unknown: {unreadable}")
check(run([[root(1)], lambda: None, [root(1)], [root(1)]])["state"] == "unknown", "a table that fails mid-window is unknown")
restarted = run([[root(1)], [P(100, 1, T0 + 10, 0.1)], [root(1)], [root(1)]])
check(restarted["reason"].startswith("app restarted"), f"a root replaced mid-window (same PID, new creation time) is unknown: {restarted}")
check(ids.sample(8000, 60, pids_fn=lambda p: set())["reason"] == "no listener on the port", "no listener is unknown")
check(ids.sample(8000, 60, pids_fn=lambda p: None)["reason"] == "listener table unreadable", "an unreadable listener table is unknown")
check(ids.sample(None, 60)["state"] == "unknown" and ids.sample(8000, 0)["state"] == "unknown", "no port, or a disabled window, is unknown")

# ---- the rise flag ----
base = {"state": "measured", "cpu_s_min": 1.0, "procs_min": 0.0}
check(ids.rise(base, {"state": "measured", "cpu_s_min": 3.0, "procs_min": 0.0}) == ["cpu"], "CPU 1.0 -> 3.0 s/min is a rise")
check(ids.rise(base, {"state": "measured", "cpu_s_min": 1.4, "procs_min": 0.0}) == [], "a wobble under the threshold is quiet")
check(ids.rise(base, {"state": "measured", "cpu_s_min": 1.0, "procs_min": 4.0}) == ["procs"], "processes 0 -> 4 a minute is a rise")
zero = {"state": "measured", "cpu_s_min": 0.0, "procs_min": 0.0}
check(ids.rise(zero, {"state": "measured", "cpu_s_min": 0.5, "procs_min": 0.5}) == [], "0 -> 0.5 is under the absolute floor")
check(ids.rise(None, base) == [] and ids.rise(base, {"state": "unknown"}) == [] and ids.rise({"state": "unknown"}, base) == [],
      "an unknown side claims no rise")

# ---- the ledger keeps measured samples only; the previous one is the latest measured ----
led = Path(_STATE) / "idle-test.jsonl"
ids.record("alpha", {"state": "unknown", "reason": "x"}, "t1", led)
check(ids.previous_sample("alpha", led) is None, "an unknown sample is never stored as a baseline")
ids.record("alpha", {"state": "measured", "cpu_s_min": 2.0, "procs_min": 1.0}, "t2", led)
ids.record("beta", {"state": "measured", "cpu_s_min": 9.0, "procs_min": 9.0}, "t3", led)
ids.record("alpha", {"state": "measured", "cpu_s_min": 3.0, "procs_min": 1.0}, "t4", led)
led.write_text(led.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
check((ids.previous_sample("alpha", led) or {}).get("cpu_s_min") == 3.0, "the previous sample is the latest measured one for the app")


# ---- the digest ----
def row(repo, idle):
    return {"repo": repo, "state": "pass", "reason": "", "fails": [], "unmeasured_checks": 0, "regressed": [], "slower": [],
            "fixed": [], "build": "live", "issue": None, "idle": idle}


digest = sweep.render_digest([
    row("alpha", {"state": "measured", "cpu_s_min": 3.0, "procs_min": 0.5, "previous": {"cpu_s_min": 1.0, "procs_min": 0.5, "ts": "t"}, "rise": ["cpu"]}),
    row("beta", {"state": "measured", "cpu_s_min": 0.2, "procs_min": 0.0, "previous": None, "rise": []}),
    row("gamma", {"state": "unknown", "reason": "no listener on the port"}),
    row("delta", None)], [], "2026-11-24")
check("| 3.00 | 0.50 | CPU +2.00, procs +0.00 RISE (cpu) |" in digest, f"digest shows the figures, the change and the rise: {digest}")
check("| 0.20 | 0.00 | first sample |" in digest, "a first sample has no change to claim")
check("| gamma | pass |" in digest and "unknown | unknown | unknown" in digest, "an unknown app reads unknown, not 0")
check("Idle load rose since the last run: alpha (cpu)." in digest, "the digest names the apps whose idle load rose")
check("- gamma: no listener on the port" in digest and "- delta: not sampled" in digest, "unknown reasons are listed; a row never sampled says so")

# ---- measure_app carries the idle sample on its row ----
sweep.idle_for = lambda repo, args: {"state": "unknown", "reason": "stub"}  # type: ignore[assignment]
cli.cmd_measure = lambda ns: print("PERF=unmeasured reason=NOT_LISTENING") or 3  # type: ignore[assignment]
with redirect_stdout(io.StringIO()):
    r = sweep.measure_app("alpha", argparse.Namespace(duration=1, settle=1, no_file=True, idle_window=60))
check(r["idle"] == {"state": "unknown", "reason": "stub"} and r["state"] == "unmeasured", f"the row carries the idle result: {r}")

_h.report_and_exit("test_perf_idle")
