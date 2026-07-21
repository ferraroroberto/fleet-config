"""Unit tests for .claude/skills/sota-watch/watchlist.py (fleet-config#393).

Exercises the pure due/fresh/delegated status logic with a fixed `today`, the
seed watchlist.toml's shape, and the mark round-trip against a temp state dir
via SOTA_WATCH_STATE_DIR (no real ~/.claude/sota-watch is touched).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_watchlist.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "sota-watch"
sys.path.insert(0, str(SKILL))
import watchlist as wl  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

TODAY = dt.date(2026, 8, 1)


# ---- area_line: due / fresh / delegated ----

research = {"verdict_date": dt.date(2026, 7, 20), "check_every_days": 45}

line = wl.area_line("x", research, {}, dt.date(2026, 9, 3))
check("|status=due|" in line, "verdict_date + 45d elapsed -> due")
check("next_due=2026-09-03" in line, "next_due = verdict_date + check_every")

line = wl.area_line("x", research, {}, dt.date(2026, 9, 2))
check("|status=fresh|" in line, "one day before the interval elapses -> fresh")

line = wl.area_line("x", research, {"x": "2026-08-30"}, dt.date(2026, 9, 3))
check("|status=fresh|" in line, "recorded state overrides verdict_date baseline")
check("last=2026-08-30" in line, "last comes from state when recorded")

delegated = {
    "verdict_date": dt.date(2026, 7, 12),
    "check_every_days": 21,
    "delegate": "ferraroroberto/local-llm-hub#272",
}
line = wl.area_line("frontier", delegated, {}, TODAY)
check("|status=delegated|" in line, "delegate field -> delegated, never due")
check("delegate=ferraroroberto/local-llm-hub#272" in line, "delegate ref printed")
check("stale_after=21" in line, "delegated line carries the staleness threshold")

# ---- guard rails ----

try:
    wl.area_line("x", {"verdict_date": dt.date(2026, 1, 1)}, {}, TODAY)
    check(False, "missing check_every_days must raise")
except SystemExit:
    check(True, "missing check_every_days raises SystemExit")

try:
    wl.last_checked("x", {}, {})
    check(False, "no verdict_date and no state must raise")
except SystemExit:
    check(True, "no verdict_date and no state raises SystemExit")

check(wl.load_state(Path(tempfile.gettempdir()) / "does-not-exist-9x" / "state.json") == {},
      "missing state file -> empty state (everything falls back to verdict_date)")


# ---- seed watchlist.toml shape ----

areas = wl.load_watchlist()
check(len(areas) >= 7, "seed watchlist has the seven seeded areas")
for name, cfg in areas.items():
    check(isinstance(cfg.get("verdict_date"), dt.date), f"{name}: verdict_date is a TOML date")
    check(int(cfg.get("check_every_days", 0)) > 0, f"{name}: positive check_every_days")
    check(bool(cfg.get("adopted")), f"{name}: has an adopted choice")
    check(bool(cfg.get("verdict")), f"{name}: has a recorded verdict")
check("delegate" in areas.get("local-model-frontier", {}),
      "local-model-frontier is delegated to the local-llm-hub ledger")
non_delegated = [n for n, c in areas.items() if not c.get("delegate")]
for name in non_delegated:
    check(bool(areas[name].get("disqualifiers")),
          f"{name}: research areas record their disqualifiers")


# ---- CLI round-trip against a temp state dir ----

tmp = tempfile.mkdtemp(prefix="sota-watch-test-")
try:
    env = {**os.environ, "SOTA_WATCH_STATE_DIR": tmp, "PYTHONUTF8": "1"}
    script = str(SKILL / "watchlist.py")

    proc = subprocess.run(
        [sys.executable, script, "due", "--today", "2026-07-21"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    check(proc.returncode == 0, "due CLI exits 0")
    check("DUE=" in proc.stdout, "due CLI prints a DUE= total")
    check(proc.stdout.count("AREA=") == len(areas), "due CLI prints one line per area")

    proc = subprocess.run(
        [sys.executable, script, "mark", "--area", "token-reduction", "--date", "2026-07-21"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    check(proc.returncode == 0, "mark CLI exits 0")
    state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
    check(state.get("token-reduction") == "2026-07-21", "mark writes the state file")

    proc = subprocess.run(
        [sys.executable, script, "mark", "--area", "nope", "--date", "2026-07-21"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    check(proc.returncode != 0, "mark on an unknown area fails loudly")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_watchlist")
