"""Tests for the weekly burst (.claude/skills/weekly-burst/burst.py, fleet-config#2120).

Real throwaway git repos hold fake dispatchers that report units the way the acceptance gate does, so each
case drives the real `gate_evidence run --units` -> ledger -> verdict path: all green exits 0 with a summary
written under the state dir, and the hermetic gate's coverage report built into the coverage map; a red unit exits 1 and is named; the hermetic legs run in a detached clone that
lacks an ignored file the working tree has; a clone that cannot be made, a skipped leg, no `gate` history and
a green exit with no ledger row are each `not-confirmed` (exit 3), never green; old summaries are pruned;
the weekly unit table names real files, and the gate no longer lists the moved units.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/weekly_burst/test_burst.py`  (also invoked by tests/run_acceptance.py)
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
sys.path[:0] = [str(REPO / sub) for sub in (".claude/skills/weekly-burst", "tests/_lib", "tests", "skills/_lib")]
STATE = Path(tempfile.mkdtemp(prefix="weekly_burst_state_"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
import burst  # noqa: E402
from acceptance.standalone_dispatch import _STANDALONE_UNIT_CHECKS  # noqa: E402
from acceptance.weekly import units as weekly_units  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402
from unit_history import ledger  # noqa: E402

_h = CheckHarness()
check = _h.check

# A dispatcher that reports the way unit_report does: one JSON line per unit to FLEET_GATE_UNIT_REPORT.
_FAKE = """\
import json, os, sys
from pathlib import Path
marker = Path(__file__).resolve().parent.parent / 'IGNORED_MARKER'
rows = [{'unit': 'steady', 'status': 'pass', 'seconds': 0.1}]
if os.environ.get('FAKE_RED') and Path(__file__).name == 'run_acceptance.py':
    rows.append({'unit': 'broken', 'status': 'fail', 'seconds': 0.2})
if marker.exists():
    rows.append({'unit': 'sees_ignored_file', 'status': 'pass', 'seconds': 0.1})
if os.environ.get('FLEET_GATE_COVERAGE_REPORT'):
    root = os.environ['FLEET_GATE_COVERAGE_ROOT']
    Path(os.environ['FLEET_GATE_COVERAGE_REPORT']).write_text(json.dumps(
        {'unit': 'steady', 'lines': [['src.py', 1, 'VALUE = 1'], ['elsewhere.py', 1, 'x']]}) + chr(10),
        encoding='utf-8')
if not os.environ.get('FAKE_SILENT'):
    Path(os.environ['FLEET_GATE_UNIT_REPORT']).write_text(
        ''.join(json.dumps(r) + chr(10) for r in rows), encoding='utf-8')
sys.exit(1 if any(r['status'] == 'fail' for r in rows) else 0)
"""


def make_repo() -> Path:
    repo = init_repo()
    (repo / "tests").mkdir()
    for name in ("run_weekly_units.py", "run_acceptance.py"):
        (repo / "tests" / name).write_text(_FAKE, encoding="utf-8")
    (repo / ".gitignore").write_text("IGNORED_MARKER\n", encoding="utf-8")
    (repo / "IGNORED_MARKER").write_text("only the working tree has this\n", encoding="utf-8")
    (repo / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".fleet.toml").write_text("[test_value_ratchet]\nenabled = true\n", encoding="utf-8")
    (repo / ".test-value-ratchet-baseline.json").write_text('{"units": {}}\n', encoding="utf-8")
    for cmd in (["add", "-A"], ["commit", "-q", "-m", "fake dispatchers"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    return repo


def seed_gate_history() -> None:
    rows = [{"kind": "unit", "run_id": "g1", "label": "gate", "unit": "steady", "status": "pass", "seconds": 2.0},
            {"kind": "run", "run_id": "g1", "label": "gate", "exit": 0, "seconds": 2.0, "spawns": 1, "units": 1}]
    path = ledger.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def run_main(repo: Path, *args: str, env: dict | None = None) -> tuple[int, str]:
    saved = burst.REPO
    burst.REPO = repo
    os.environ.update(env or {})
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = burst.main(list(args))
    finally:
        burst.REPO = saved
        for key in env or {}:
            os.environ.pop(key, None)
    return code, out.getvalue()


def latest_summary() -> dict:
    newest = sorted((STATE / "weekly-burst").glob("*.json"))[-1]
    return json.loads(newest.read_text(encoding="utf-8"))


made: list[Path] = []
try:
    repo = make_repo()
    made.append(repo)

    # ---- every leg green: exit 0, a summary under the state dir, medians from the gate history ----
    seed_gate_history()
    code, text = run_main(repo)
    summary = latest_summary()
    check(code == 0 and summary["verdict"] == "green", f"all legs green exit 0 ({code}) -- {text[-300:]}")
    check([leg["leg"] for leg in summary["legs"]]
          == ["units", "hermetic-units", "hermetic-gate", "coverage-map", "hermetic-rule"],
          f"every leg ran ({[leg['leg'] for leg in summary['legs']]})")
    cov_map =json.loads(ledger.coverage_map_path().read_text(encoding="utf-8"))
    check(len(cov_map["lines"]) == 1 and cov_map["lines"][0].startswith("src.py:") and cov_map["units"] == {"steady": 1},
          f"the hermetic gate's coverage became the map, tracked source lines only ({cov_map})")
    check(summary["medians"]["state"] == "green" and summary["medians"]["top"][0]["unit"] == "steady",
          f"medians come from the gate history ({summary['medians']})")
    check(all((STATE / "weekly-burst" / leg["log"]).is_file() for leg in summary["legs"]),
          "each leg's log is kept in the state dir")
    check(not [p for p in repo.glob("*.json") if p.name != ".test-value-ratchet-baseline.json"] and not (repo / "weekly-burst").exists(), "nothing is written next to the code")

    # ---- the hermetic legs ran in a detached clone: the ignored file is not there ----
    in_tree = {r["unit"] for r in ledger.load(label="weekly-units") if r.get("kind") == "unit"}
    in_clone = {r["unit"] for r in ledger.load(label="weekly-hermetic-gate") if r.get("kind") == "unit"}
    check("sees_ignored_file" in in_tree and "sees_ignored_file" not in in_clone,
          f"the working tree sees the ignored file, the fresh clone does not ({in_tree} / {in_clone})")

    # ---- a red unit in the hermetic gate: exit 1, named ----
    code, text = run_main(repo, env={"FAKE_RED": "1"})
    summary = latest_summary()
    red = [leg for leg in summary["legs"] if leg["state"] == "red"]
    check(code == 1 and summary["verdict"] == "red" and red and red[0]["red_units"] == ["broken"],
          f"a red unit exits 1 and is named ({code}, {red})")
    check("broken" in text, "the red unit is printed")

    # ---- a green exit with no ledger row is not a pass ----
    code, _text = run_main(repo, "--skip-hermetic", env={"FAKE_SILENT": "1"})
    summary = latest_summary()
    check(code == 3 and summary["legs"][0]["state"] == "not-confirmed",
          f"exit 0 with no ledger row for the run is not-confirmed, exit 3 ({code}, {summary['legs'][0]})")

    # ---- a skipped hermetic leg is its own state, never green ----
    code, _text = run_main(repo, "--skip-hermetic")
    summary = latest_summary()
    check(code == 3 and summary["verdict"] == "not-confirmed" and summary["legs"][1]["state"] == "not-confirmed",
          f"--skip-hermetic does not read as green ({code}, {summary['verdict']})")

    # ---- no gate history: medians are not confirmed ----
    ledger.ledger_path().unlink()
    code, _text = run_main(repo)
    summary = latest_summary()
    check(code == 3 and summary["medians"]["state"] == "not-confirmed" and summary["verdict"] == "not-confirmed",
          f"no `gate` run in the ledger is not-confirmed ({code}, {summary['medians']})")

    # ---- a clone that cannot be made: not-confirmed ----
    plain = Path(tempfile.mkdtemp(prefix="weekly_burst_nogit_"))
    made.append(plain)
    (plain / "tests").mkdir()
    shutil.copy(repo / "tests" / "run_weekly_units.py", plain / "tests" / "run_weekly_units.py")
    seed_gate_history()
    code, _text = run_main(plain)
    summary = latest_summary()
    clone_leg = next((leg for leg in summary["legs"] if leg["leg"] == "hermetic"), None)
    check(code == 3 and clone_leg == {"leg": "hermetic", "state": "not-confirmed", "reason": "clone or detach failed"},
          f"an unmakeable clone is not-confirmed ({code}, {clone_leg})")

    # ---- only the newest KEEP_RUNS summaries survive, with their logs ----
    folder = STATE / "weekly-burst"
    for old in list(folder.iterdir()):
        old.unlink()
    for n in range(burst.KEEP_RUNS + 3):
        (folder / f"2000010{n:02d}T000000Z.json").write_text("{}", encoding="utf-8")
        (folder / f"2000010{n:02d}T000000Z-units.log").write_text("", encoding="utf-8")
    burst._prune(folder)
    check(len(list(folder.glob("*.json"))) == burst.KEEP_RUNS, "old summaries are pruned to the newest few")
    check(len(list(folder.glob("*-units.log"))) == burst.KEEP_RUNS, "a pruned run's log goes with it")
finally:
    for path in made:
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(STATE, ignore_errors=True)

# ---- the table: real files, and the gate no longer lists them ----
gate_labels = {label for label, _file, _why in _STANDALONE_UNIT_CHECKS}
for label, test_file, _why in weekly_units.WEEKLY_SUITES:
    check((REPO / "tests" / test_file).is_file(), f"weekly suite {label} names a real file")
    check(label not in gate_labels, f"{label} is not also a per-change gate row")
check([fn.__name__ for fn in weekly_units.WEEKLY_FUNCTION_UNITS] == ["_host_power_codex_unit_checks"],
      "the function unit is the host-power Codex check")
check("_host_power_codex_unit_checks" not in (REPO / "tests" / "run_acceptance.py").read_text(encoding="utf-8"),
      "the per-change gate does not run the host-power Codex check")

_h.report_and_exit("test_burst")
