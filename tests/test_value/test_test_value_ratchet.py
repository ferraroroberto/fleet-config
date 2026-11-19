"""Tests for the test-value ratchet (scripts/test_value/test_value_ratchet.py, fleet-config#2120).

Each rule is driven through the script's own `main()` against a throwaway git repo, a fake unit ledger
and fake live reports:

  spawn-budget    a unit above its entry fails, a unit at or below passes, a unit with no entry fails;
  flake-ledger    a pass and a fail on one tree is an open flake; more open flakes than the baseline fail;
                  `flake_clear_runs` passes in a row close it; one open past the age cap fails;
  new-test-value  a unit missing from the merge base's baseline is new; an exempt marker, or a red record
                  on another tree, passes it; a red record on this very tree does not; with neither, a
                  missing or stale coverage map is exit 3 with its own message, a unique covered line
                  passes and no unique line fails;
  hermetic        red in the fresh clone and green in the primary fails, a baselined one passes, no recent
                  clone run is exit 3;
  --update-baseline  shrinks entries, adds a unit only with --allow-additions.

No live report or no ledger is exit 3, never a pass. The probe's coverage half and the dispatcher's
unit selection are covered in tests/unit_history/test_unit_history.py.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_value/test_test_value_ratchet.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(REPO / "scripts" / "test_value"), str(REPO / "tests" / "_lib")]
import test_value_ratchet as tvr  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

NOW = datetime.now(timezone.utc)
CONFIG = """[test_value_ratchet]
enabled = true
base_ref = "main"
unit_runner = ["fake_runner.py"]
flake_clear_runs = 2
"""
# Stands in for the acceptance dispatcher: reports covered lines for the units FLEET_GATE_ONLY names.
FAKE_RUNNER = """import json, os
lines = {"_new_unit_check": [["src/app.py", 2, "return 2"]], "_dull_unit_check": [["src/app.py", 1, "return 1"]]}
with open(os.environ["FLEET_GATE_COVERAGE_REPORT"], "a", encoding="utf-8") as fh:
    for unit in os.environ["FLEET_GATE_ONLY"].split(","):
        fh.write(json.dumps({"unit": unit, "lines": lines.get(unit, [])}) + "\\n")
"""


def stamp(days_ago: float = 0, minutes: int = 0) -> str:
    return (NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write_jsonl(path: Path, rows: list) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def unit_row(unit: str, status: str, tree: str, utc: str, label: str = "gate", spawns: int = 0) -> dict:
    return {"kind": "unit", "run_id": f"{tree}-{utc}", "label": label, "wtree": tree, "unit": unit,
            "status": status, "seconds": 1.0, "spawns": spawns, "utc": utc}


def make_repo(tmp: Path) -> Path:
    """`main` holds a baseline with `_old_unit_check`; the checked-out `feature` branch adds tests."""
    repo = init_repo()
    (repo / ".fleet.toml").write_text(CONFIG, encoding="utf-8")
    (repo / ".test-value-ratchet-baseline.json").write_text(json.dumps(
        {"units": {"_old_unit_check": 5}, "flake_count": 0, "flake_max_age_days": 14, "hermetic": []}), encoding="utf-8")
    (repo / "fake_runner.py").write_text(FAKE_RUNNER, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def a():\n    return 1\n    return 2\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "t.py").write_text("def _old_unit_check():\n    pass\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "tests" / "t.py").write_text(
        "def _old_unit_check():\n    pass\n\n"
        "# test-value-exempt: a doc scanner, it covers no source\n"
        "def _marked_unit_check():\n    pass\n\n"
        "def _new_unit_check():\n    pass\n\n"
        "def _dull_unit_check():\n    pass\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature")
    return repo


def run(repo: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = tvr.main(["--repo", str(repo), *args])
    return code, out.getvalue()


def set_baseline(repo: Path, **fields) -> None:
    data = {"units": {}, "flake_count": 0, "flake_max_age_days": 14, "hermetic": []}
    data.update(fields)
    (repo / ".test-value-ratchet-baseline.json").write_text(json.dumps(data), encoding="utf-8")


tmp = Path(tempfile.mkdtemp(prefix="test_value_"))
repo = make_repo(tmp)
try:
    ledger = write_jsonl(tmp / "ledger.jsonl", [unit_row("_old_unit_check", "pass", "t0", stamp(1))])
    units = tmp / "units.jsonl"
    spawns = tmp / "spawns.jsonl"

    # ---- spawn-budget ----
    write_jsonl(units, [{"unit": "_old_unit_check", "status": "pass"}])
    write_jsonl(spawns, [{"unit": "_old_unit_check"}] * 5)
    base = ["--rules", "spawn-budget", "--units-report", str(units), "--spawns-report", str(spawns)]
    code, out = run(repo, *base)
    check(code == 0, f"spawn-budget: a unit at its entry passes ({code}: {out})")
    write_jsonl(spawns, [{"unit": "_old_unit_check"}] * 6)
    code, out = run(repo, *base)
    check(code == 1 and "_old_unit_check: 6 spawns, above its baseline of 5" in out,
          f"spawn-budget: one spawn above the entry fails and names the unit ({code}: {out})")
    write_jsonl(units, [{"unit": "_old_unit_check"}, {"unit": "_brand_new"}])
    write_jsonl(spawns, [])
    code, out = run(repo, *base)
    check(code == 1 and "_brand_new: 0 spawns and no baseline entry" in out,
          f"spawn-budget: a unit with no entry fails until it is recorded ({code}: {out})")

    # ---- flake-ledger ----
    set_baseline(repo, units={"_old_unit_check": 5}, flake_count=0)
    flaky = [unit_row("u", "fail", "t1", stamp(2)), unit_row("u", "pass", "t1", stamp(2, 5))]
    write_jsonl(ledger, flaky)
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(ledger))
    check(code == 1 and "u: open flake since" in out and "1 open flakes, above the baseline of 0" in out,
          f"flake-ledger: a pass and a fail on one tree is a flake past a zero baseline ({code}: {out})")
    write_jsonl(ledger, flaky + [unit_row("u", "fail", "t2", stamp(2, 6), label="weekly-hermetic-gate")])
    set_baseline(repo, units={}, flake_count=1)
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(ledger))
    check(code == 0, f"flake-ledger: one open flake within a baseline of 1 passes ({code}: {out})")
    write_jsonl(ledger, flaky + [unit_row("u", "pass", "t2", stamp(1)), unit_row("u", "pass", "t3", stamp(0.5))])
    set_baseline(repo, units={}, flake_count=0)
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(ledger))
    check(code == 0 and "no open flake" in out,
          f"flake-ledger: flake_clear_runs passes in a row close the flake ({code}: {out})")
    write_jsonl(ledger, flaky + [unit_row("u", "pass", "t2", stamp(1)), unit_row("u", "fail", "t3", stamp(0.9)),
                                 unit_row("u", "pass", "t4", stamp(0.5))])
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(ledger))
    check(code == 1, f"flake-ledger: a fail between the passes restarts the count ({code}: {out})")
    write_jsonl(ledger, [unit_row("u", "fail", "t1", stamp(20)), unit_row("u", "pass", "t1", stamp(20, 5))])
    set_baseline(repo, units={}, flake_count=1, flake_max_age_days=14)
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(ledger))
    check(code == 1 and "past the 14-day cap" in out,
          f"flake-ledger: a flake open past the age cap fails though the count is within baseline ({code}: {out})")

    # ---- new-test-value ----
    set_baseline(repo, units={"_old_unit_check": 5})
    write_jsonl(ledger, [unit_row("_new_unit_check", "fail", "older-tree", stamp(1))])
    write_jsonl(units, [{"unit": u} for u in ("_old_unit_check", "_marked_unit_check", "_new_unit_check")])
    ntv = ["--rules", "new-test-value", "--ledger", str(ledger), "--units-report", str(units), "--tree", "this-tree",
           "--coverage-map", str(tmp / "map.json")]
    code, out = run(repo, *ntv)
    check(code == 0 and "_marked_unit_check: test-value-exempt marker" in out
          and "_new_unit_check: seen red before the fix" in out and "_old_unit_check" not in out,
          f"new-test-value: the marker and a red record on another tree pass, a baselined unit is not new ({code}: {out})")
    write_jsonl(ledger, [unit_row("_new_unit_check", "fail", "this-tree", stamp(1))])
    code, out = run(repo, *ntv)
    check(code == 3 and "the coverage map does not exist yet" in out,
          f"new-test-value: red on this very tree is no evidence; no map is exit 3, its own message ({code}: {out})")
    (tmp / "map.json").write_text(json.dumps({"built_utc": stamp(9), "lines": []}), encoding="utf-8")
    code, out = run(repo, *ntv)
    check(code == 3 and "the coverage map is stale" in out, f"new-test-value: a stale map is exit 3 ({code}: {out})")
    covered = tvr.line_key("src/app.py", "return 1")
    (tmp / "map.json").write_text(json.dumps({"built_utc": stamp(1), "lines": [covered]}), encoding="utf-8")
    code, out = run(repo, *ntv)
    check(code == 0 and "_new_unit_check: covers 1 line(s) no other unit covers" in out,
          f"new-test-value: a covered line the map lacks passes ({code}: {out})")
    write_jsonl(units, [{"unit": "_dull_unit_check"}])
    code, out = run(repo, *ntv)
    check(code == 1 and "_dull_unit_check: NEW unit with no red-before-fix record" in out,
          f"new-test-value: a new unit with no evidence fails ({code}: {out})")
    code, out = run(repo, "--rules", "new-test-value", "--ledger", str(ledger), "--tree", "this-tree")
    check(code == 3 and "no live unit report" in out, f"no live report is exit 3, never a pass ({code}: {out})")
    code, out = run(repo, "--rules", "flake-ledger", "--ledger", str(tmp / "absent.jsonl"))
    check(code == 3 and "the unit ledger is missing" in out, f"no ledger is exit 3 ({code}: {out})")

    # ---- hermetic ----
    rows = [unit_row("h", "pass", "t1", stamp(1)), unit_row("h", "fail", "t1", stamp(0.5), label="weekly-hermetic-gate"),
            {"kind": "run", "run_id": "t1-" + stamp(0.5), "label": "weekly-hermetic-gate", "utc": stamp(0.5)}]
    write_jsonl(ledger, rows)
    code, out = run(repo, "--rules", "hermetic", "--ledger", str(ledger))
    check(code == 1 and "h: red in a fresh clone, green in the primary checkout" in out,
          f"hermetic: red in the clone and green in the primary fails ({code}: {out})")
    set_baseline(repo, hermetic=["h"])
    code, out = run(repo, "--rules", "hermetic", "--ledger", str(ledger))
    check(code == 0 and "(baselined)" in out, f"hermetic: a baselined leak passes ({code}: {out})")
    write_jsonl(ledger, rows[:1])
    code, out = run(repo, "--rules", "hermetic", "--ledger", str(ledger))
    check(code == 3 and "no `weekly-hermetic-gate` run" in out, f"hermetic: no recent clone run is exit 3 ({code}: {out})")

    # ---- --update-baseline ----
    set_baseline(repo, units={"a": 9, "b": 2})
    write_jsonl(ledger, [unit_row("a", "pass", "t1", stamp(1), spawns=4), unit_row("b", "pass", "t1", stamp(1), spawns=7),
                         unit_row("c", "pass", "t1", stamp(1), spawns=3),
                         {"kind": "run", "run_id": "t1-" + stamp(1), "label": "gate", "utc": stamp(1)}])
    run(repo, "--update-baseline", "--ledger", str(ledger))
    got = json.loads((repo / ".test-value-ratchet-baseline.json").read_text(encoding="utf-8"))["units"]
    check(got == {"a": 4, "b": 2}, f"--update-baseline shrinks an entry, never raises one, adds nothing ({got})")
    run(repo, "--update-baseline", "--allow-additions", "--ledger", str(ledger))
    got = json.loads((repo / ".test-value-ratchet-baseline.json").read_text(encoding="utf-8"))["units"]
    check(got == {"a": 4, "b": 2, "c": 3}, f"--allow-additions records a new unit ({got})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(repo, ignore_errors=True)

_h.report_and_exit("test_test_value_ratchet")
