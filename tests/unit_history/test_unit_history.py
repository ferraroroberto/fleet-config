"""Tests for the acceptance gate's per-unit run history (fleet-config#2120).

Covers: the ledger rows `gate_evidence run --units` appends (one per unit plus one run row, with the tree
hash, run id and per-unit spawn counts), the spawn probe counting a real child's spawn and staying inert
without its env var, `unit_report.unit` outcomes (pass / fail / skip / raised), the read side's medians, and a
command that reports nothing leaving the ledger untouched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/unit_history/test_unit_history.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
import gate_evidence as ge  # noqa: E402
from acceptance.run_history import unit_report  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from unit_history import ledger  # noqa: E402

_h = CheckHarness()
check = _h.check

PY = sys.executable
STATE = Path(tempfile.mkdtemp(prefix="unit_history_state_"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)

# A stand-in gate: reports two units (one spawning a child, one failing) the way run_acceptance does.
FAKE_GATE = f"""
import subprocess, sys
sys.path.insert(0, {str(REPO / "tests")!r})
sys.path.insert(0, {str(REPO / "skills" / "_lib")!r})
from acceptance.run_history import unit_report
with unit_report.unit("spawning_unit") as o:
    subprocess.run([sys.executable, "-c", "pass"])
    o["result"] = (0, 3, 0)
with unit_report.unit("failing_unit") as o:
    o["result"] = (1, 2, 0)
"""


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def rows() -> list:
    return ledger.load()


repo = init_repo(empty_commit=False)
tmp = Path(tempfile.mkdtemp(prefix="unit_history_"))
try:
    # --- a gate that reports units: one row per unit plus a run row -------------------------------
    gate = tmp / "fake_gate.py"
    gate.write_text(FAKE_GATE, encoding="utf-8")
    code, line = quiet(ge.run, repo, "gate", [PY, str(gate)], units=True)
    check(code == 0, f"the stand-in gate exits 0 ({code})")
    got = rows()
    units = {r["unit"]: r for r in got if r["kind"] == "unit"}
    runs = [r for r in got if r["kind"] == "run"]
    check(set(units) == {"spawning_unit", "failing_unit"} and len(runs) == 1, f"two unit rows and one run row ({len(got)})")
    check(units["spawning_unit"]["status"] == "pass" and units["failing_unit"]["status"] == "fail",
          "a unit with failures is fail, one without is pass")
    check(units["spawning_unit"]["spawns"] == 1 and units["failing_unit"]["spawns"] == 0,
          f"the child's spawn is counted under the unit that made it ({units['spawning_unit']['spawns']})")
    check(runs[0]["spawns"] >= 1 and runs[0]["units"] == 2, "the run row totals the spawns and units")
    wtree = ge.tree_hash(repo)[0]
    check(all(r["run_id"] == runs[0]["run_id"] and r["label"] == "gate" for r in got), "every row carries the run id and label")
    check(all(isinstance(r["utc"], str) and r["utc"].endswith("Z") for r in got), "every row carries a UTC timestamp")
    check(runs[0]["wtree"] == wtree, "the row carries the tree hash the run started on")
    check(isinstance(units["spawning_unit"]["seconds"], (int, float)) and runs[0]["seconds"] >= 0, "seconds are numbers")

    # --- a second run appends, never replaces -----------------------------------------------------
    quiet(ge.run, repo, "gate", [PY, str(gate)], units=True)
    check(len(rows()) == 2 * len(got), "a second run appends its own rows")

    # --- a command that reports nothing writes nothing --------------------------------------------
    before = len(rows())
    quiet(ge.run, repo, "other", [PY, "-c", "pass"])
    check(len(rows()) == before, "a gate without --units and not run_acceptance.py leaves the ledger alone")
    quiet(ge.run, repo, "other", [PY, "-c", "pass"], units=True)
    check(len(rows()) == before, "a --units run that reports no unit writes no rows (nothing to fold)")
    check(ge._wants_units(["py", "tests/run_acceptance.py"]) and not ge._wants_units(["py", "other.py"]),
          "run_acceptance.py in the argv turns the history on by itself")

    # --- the read side ----------------------------------------------------------------------------
    summary = ledger.summarize(rows())
    check(summary["runs"] == 2 and summary["units"] == 2, f"summary counts runs and units ({summary['runs']}, {summary['units']})")
    check(summary["failed_units"] == ["failing_unit"], "summary names the failing unit")
    medians = ledger.summarize([
        {"kind": "unit", "unit": "a", "seconds": 1.0}, {"kind": "unit", "unit": "a", "seconds": 5.0},
        {"kind": "unit", "unit": "a", "seconds": 3.0}, {"kind": "unit", "unit": "b", "seconds": 2.0},
    ], top=1)
    check(medians["top"] == [{"unit": "a", "median_seconds": 3.0}] and medians["top_share"] == 0.6,
          f"per-unit median and top-N share ({medians['top']}, {medians['top_share']})")
    check(ledger.summarize([])["run_seconds_median"] is None, "no runs is None, never 0")

    # --- the spawn probe ----------------------------------------------------------------------------
    report = tmp / "spawns.jsonl"
    env = dict(os.environ, PYTHONPATH=str(ledger.PROBE_DIR), FLEET_GATE_UNIT="u1", **{ledger.SPAWN_REPORT_ENV: str(report)})
    subprocess.run([PY, "-c", "import subprocess,sys; subprocess.run([sys.executable,'-c','pass'])"],
                   env=env, creationflags=NO_WINDOW, timeout=120)
    lines = [json.loads(x) for x in report.read_text(encoding="utf-8").splitlines()]
    check(len(lines) == 1 and lines[0]["unit"] == "u1", f"the probe counts one spawn under its unit ({lines})")
    env_off = dict(os.environ, PYTHONPATH=str(ledger.PROBE_DIR))
    env_off.pop(ledger.SPAWN_REPORT_ENV, None)
    proc = subprocess.run([PY, "-c", "import subprocess; print(subprocess.Popen.__init__.__name__)"],
                          env=env_off, creationflags=NO_WINDOW, timeout=120, capture_output=True, text=True)
    check(proc.returncode == 0 and proc.stdout.strip() == "__init__",
          f"without the env var the probe leaves Popen unpatched ({proc.stdout.strip()!r})")
    probe_text = (ledger.PROBE_DIR / "sitecustomize.py").read_text(encoding="utf-8")
    check(all(name in probe_text for name in (ledger.SPAWN_REPORT_ENV, ledger.CURRENT_UNIT_ENV)),
          "the probe's env names match the ledger's constants")

    # --- unit_report outcomes ----------------------------------------------------------------------
    report = tmp / "units.jsonl"
    os.environ[ledger.UNIT_REPORT_ENV] = str(report)
    try:
        with unit_report.unit("ok") as o:
            o["result"] = (0, 4, 0)
        with unit_report.unit("skipped") as o:
            o["result"] = (0, 0, 1)
        with unit_report.unit("red") as o:
            o["result"] = (2, 4, 0)
        try:
            with unit_report.unit("raised"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    finally:
        del os.environ[ledger.UNIT_REPORT_ENV]
    got_status = {r["unit"]: r["status"] for r in (json.loads(x) for x in report.read_text(encoding="utf-8").splitlines())}
    check(got_status == {"ok": "pass", "skipped": "skip", "red": "fail", "raised": "fail"},
          f"pass / skip / fail / raised-is-fail ({got_status})")
    check(ledger.CURRENT_UNIT_ENV not in os.environ, "the current-unit tag is cleared after a unit")
    with unit_report.unit("quiet") as o:
        o["result"] = (0, 1, 0)
    check(True, "with no report env var a unit reports nothing and does not raise")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(STATE, ignore_errors=True)

_h.report_and_exit("test_unit_history")
