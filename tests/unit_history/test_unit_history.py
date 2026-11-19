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
    print("noise from a green unit")
    o["result"] = (0, 3, 0)
with unit_report.unit("failing_unit") as o:
    print("assertion text on stdout")
    print("crash text on stderr", file=sys.stderr)
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
    lines = ledger.read_report(report)
    check(len(lines) == 1 and lines[0]["unit"] == "u1", f"the probe counts one spawn under its unit ({lines})")
    # Threads spawning at once: a shared append-only file loses lines on Windows, so the count must still be exact.
    burst_report = tmp / "burst" / "spawns.jsonl"
    burst_report.parent.mkdir()
    burst_code = ("import subprocess, sys, threading\n"
                  "def go():\n    for _ in range(10):\n        subprocess.run([sys.executable, '-c', 'pass'])\n"
                  "ts = [threading.Thread(target=go) for _ in range(8)]\n[t.start() for t in ts]\n[t.join() for t in ts]\n")
    subprocess.run([PY, "-c", burst_code], creationflags=NO_WINDOW, timeout=300,
                   env=dict(env, **{ledger.SPAWN_REPORT_ENV: str(burst_report)}))
    burst_rows = ledger.read_report(burst_report)
    check(len(burst_rows) == 80, f"80 concurrent spawns from 8 threads are counted exactly ({len(burst_rows)})")
    env_off = dict(os.environ, PYTHONPATH=str(ledger.PROBE_DIR))
    for key in (ledger.SPAWN_REPORT_ENV, ledger.COVERAGE_REPORT_ENV, ledger.COVERAGE_ROOT_ENV):
        env_off.pop(key, None)
    proc = subprocess.run([PY, "-c", "import subprocess; print(subprocess.Popen.__init__.__name__)"],
                          env=env_off, creationflags=NO_WINDOW, timeout=120, capture_output=True, text=True)
    check(proc.returncode == 0 and proc.stdout.strip() == "__init__",
          f"without the env var the probe leaves Popen unpatched ({proc.stdout.strip()!r})")
    probe_text = (ledger.PROBE_DIR / "sitecustomize.py").read_text(encoding="utf-8")
    check(all(name in probe_text for name in (ledger.SPAWN_REPORT_ENV, ledger.CURRENT_UNIT_ENV,
                                              ledger.COVERAGE_REPORT_ENV, ledger.COVERAGE_ROOT_ENV)),
          "the probe's env names match the ledger's constants")

    # --- the probe's coverage half: per unit in one process, at exit in a child --------------------
    src = tmp / "cov_root" / "mod.py"
    src.parent.mkdir()
    src.write_text("def one():\n    return 1\n\ndef two():\n    return 2\n", encoding="utf-8")
    cov = tmp / "coverage.jsonl"
    driver = (f"import sys; sys.path[:0] = [{str(src.parent)!r}, {str(REPO / 'tests')!r}, {str(REPO / 'skills' / '_lib')!r}]\n"
              "import mod\nfrom acceptance.run_history import unit_report\n"
              "with unit_report.unit('first') as o:\n    mod.one()\n    o['result'] = (0, 1, 0)\n"
              "with unit_report.unit('second') as o:\n    mod.one(); mod.two()\n    o['result'] = (0, 1, 0)\n")
    env_cov = dict(os.environ, PYTHONPATH=str(ledger.PROBE_DIR), **{
        ledger.COVERAGE_REPORT_ENV: str(cov), ledger.COVERAGE_ROOT_ENV: str(src.parent)})
    for key in (ledger.UNIT_REPORT_ENV, ledger.SPAWN_REPORT_ENV, ledger.CURRENT_UNIT_ENV):
        env_cov.pop(key, None)
    subprocess.run([PY, "-c", driver], env=env_cov, creationflags=NO_WINDOW, timeout=120)
    subprocess.run([PY, "-c", f"import sys; sys.path.insert(0, {str(src.parent)!r}); import mod; mod.two()"],
                   env=dict(env_cov, **{ledger.CURRENT_UNIT_ENV: "child_unit"}), creationflags=NO_WINDOW, timeout=120)
    cov_rows = ledger.read_report(cov)
    by_unit = {r["unit"]: {(p, t) for p, _n, t in r["lines"]} for r in cov_rows}
    check(("mod.py", "return 1") in by_unit.get("first", set()) and ("mod.py", "return 2") not in by_unit.get("first", set()),
          f"a unit records the lines it ran and not the next unit's ({by_unit.get('first')})")
    check({("mod.py", "return 1"), ("mod.py", "return 2")} <= by_unit.get("second", set()),
          f"a line an earlier unit ran is recorded again for the next unit ({by_unit.get('second')})")
    check(("mod.py", "return 2") in by_unit.get("child_unit", set()),
          f"a child process flushes at exit under the unit it inherited ({sorted(by_unit)})")
    check(all(not p.startswith("<") and not p.startswith("..") for r in cov_rows for p, _n, _t in r["lines"]),
          "only files under the root are recorded, never `<frozen ...>` pseudo-files")
    for part in tmp.glob("coverage.jsonl*"):
        part.unlink()
    subprocess.run([PY, "-c", driver], env=dict(env_cov, **{ledger.CURRENT_UNIT_ENV: "outer"}),
                   creationflags=NO_WINDOW, timeout=120)
    nested = ledger.read_report(cov)
    check([r["unit"] for r in nested] == ["outer"]
          and {("mod.py", "return 1"), ("mod.py", "return 2")} <= {(p, t) for p, _n, t in nested[0]["lines"]},
          f"a process born in a unit keeps its lines under that unit though it reports units itself ({[r['unit'] for r in nested]})")
    proc = subprocess.run([PY, "-c", "import sys; print(sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID))"],
                          env=env_off, creationflags=NO_WINDOW, timeout=120, capture_output=True, text=True)
    check(proc.stdout.strip() == "None", f"without its env vars the coverage half claims no monitoring tool ({proc.stdout!r})")

    # --- FLEET_GATE_ONLY runs only the named units --------------------------------------------------
    os.environ[ledger.ONLY_ENV] = "kept, other"
    try:
        check(ledger.selected("kept") and ledger.selected("other") and not ledger.selected("dropped"),
              "FLEET_GATE_ONLY selects the named units only")
    finally:
        del os.environ[ledger.ONLY_ENV]
    check(ledger.selected("anything"), "with FLEET_GATE_ONLY unset every unit runs")
    tree_seen = tmp / "tree.txt"
    tree_gate = tmp / "tree_gate.py"
    tree_gate.write_text(FAKE_GATE + f"\nimport os\nopen({str(tree_seen)!r}, 'w').write(os.environ.get({ledger.TREE_ENV!r}, ''))\n",
                         encoding="utf-8")
    quiet(ge.run, repo, "tree", [PY, str(tree_gate)], units=True)
    check(tree_seen.read_text() == ge.tree_hash(repo)[0], "the gate child sees the tree hash its run started on")

    # --- a red unit keeps what it printed (both streams); a green one keeps nothing ---------------
    kept = units["failing_unit"].get("output", "")
    check("assertion text on stdout" in kept and "crash text on stderr" in kept,
          f"a failed unit's ledger row carries its stdout and stderr ({kept!r})")
    check("output" not in units["spawning_unit"], "a passing unit's row carries no output")
    big = tmp / "big_gate.py"
    big.write_text(FAKE_GATE.split("with unit_report.unit")[0]
                   + 'with unit_report.unit("loud") as o:\n    print("x" * 50000 + "END")\n    o["result"] = (1, 1, 0)\n'
                   + 'with unit_report.unit("raises") as o:\n    raise RuntimeError("boom-marker")\n', encoding="utf-8")
    quiet(ge.run, repo, "big", [PY, str(big)], units=True)
    big_rows = {r["unit"]: r for r in ledger.load(label="big") if r["kind"] == "unit"}
    loud = big_rows["loud"]["output"]
    check(len(loud) <= ledger.OUTPUT_TAIL_CHARS + 100 and loud.rstrip().endswith("END") and "cut" in loud,
          f"a loud unit keeps a bounded tail, newest last, marked as cut ({len(loud)} chars)")
    check("boom-marker" in big_rows["raises"]["output"] and big_rows["raises"]["status"] == "fail",
          "a unit that raises keeps its traceback")

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
