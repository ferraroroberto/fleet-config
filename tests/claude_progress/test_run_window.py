"""Unit tests for skills/_lib/scheduled/run_window.py.

`window_state` is exercised with a fixed clock, and the `arm`/`check` CLIs run
against a temp `--state-dir` with an injected `now`, so no real clock, no real
`~/.claude/hooks/state` file and no real run is involved.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_run_window.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib" / "scheduled"))
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import run_window as rw  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

START = dt.datetime(2026, 11, 12, 17, 24, 0, tzinfo=dt.timezone.utc)


def _run(argv, now):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rw.main(argv, now=now)
    return dict(line.split("=", 1) for line in out.getvalue().splitlines())


# ---- window_state: the pure decision ----
ends = (START + dt.timedelta(hours=5)).isoformat()
state = {"ends_at": ends}
check(rw.window_state(None, START) == ("unarmed", None), "no state file -> unarmed")
check(rw.window_state(state, START)[0] == "open", "at arm time the window is open")
check(rw.window_state(state, START + dt.timedelta(hours=4, minutes=59))[0] == "open", "a minute before the end -> open")
check(rw.window_state(state, START + dt.timedelta(hours=5))[0] == "closed", "exactly at the end -> closed")
check(rw.window_state(state, START + dt.timedelta(hours=30))[0] == "closed",
      "a 30 h run (the longest on record) is long past a 5 h window -> closed")
check(rw.window_state({"ends_at": ends}, START)[1] == 5 * 3600, "remaining seconds are reported")
check(rw.window_state({}, START)[0] == "unknown", "a state with no deadline -> unknown, never open")
check(rw.window_state({"ends_at": "not a date"}, START)[0] == "unknown", "an unparseable deadline -> unknown")
check(rw.window_state({"ends_at": 12345}, START)[0] == "unknown", "a non-text deadline -> unknown")
naive = {"ends_at": "2026-11-12T22:24:00"}
check(rw.window_state(naive, START)[0] == "open", "a deadline without a zone is read as UTC, not a crash")

# ---- CLIs against a temp state dir ----
tmp = Path(tempfile.mkdtemp(prefix="run-window-"))
try:
    check(_run(["check", "--state-dir", str(tmp)], START)["WINDOW"] == "unarmed", "check before any arm -> unarmed")

    attended = _run(["arm", "--state-dir", str(tmp)], START)
    check(attended["WINDOW_ARMED"] == "no" and not (tmp / rw.STATE_FILE).exists(),
          "an attended arm bounds nothing and writes nothing")

    armed = _run(["arm", "--state-dir", str(tmp), "--unattended"], START)
    check(armed["WINDOW_ARMED"] == "yes" and float(armed["WINDOW_SECONDS"]) == rw.DEFAULT_WINDOW_HOURS * 3600,
          "an unattended arm opens the default window")
    check(_run(["check", "--state-dir", str(tmp)], START + dt.timedelta(hours=1))["WINDOW"] == "open",
          "one hour in -> open")
    late = _run(["check", "--state-dir", str(tmp)], START + dt.timedelta(hours=6))
    check(late["WINDOW"] == "closed" and float(late["REMAINING_SECONDS"]) < 0, "six hours in -> closed")

    _run(["arm", "--state-dir", str(tmp), "--unattended", "--hours", "1"], START)
    check(_run(["check", "--state-dir", str(tmp)], START + dt.timedelta(hours=2))["WINDOW"] == "closed",
          "--hours narrows the window")

    _run(["arm", "--state-dir", str(tmp)], START)
    check(_run(["check", "--state-dir", str(tmp)], START + dt.timedelta(hours=9))["WINDOW"] == "unarmed",
          "an attended arm clears a stale deadline an earlier run left behind")

    (tmp / rw.STATE_FILE).write_text("{not json", encoding="utf-8")
    check(_run(["check", "--state-dir", str(tmp)], START)["WINDOW"] == "unknown",
          "a corrupt state file -> unknown, never open")
    (tmp / rw.STATE_FILE).write_text(json.dumps(["list"]), encoding="utf-8")
    check(_run(["check", "--state-dir", str(tmp)], START)["WINDOW"] == "unknown",
          "a state file that is not an object -> unknown")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_run_window")
