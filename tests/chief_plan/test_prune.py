"""chief_plan prune: `prune_items`, the locked `prune_plan_file` entry the app calls, and the `chief_ops.py plan prune` verb.

Every part runs against a throwaway plan file; the real chief-plan.json is never touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_plan/test_prune.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_plan as cp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _doc() -> dict:
    doc = cp.empty_plan()
    doc["updated_at"] = "2026-10-25T12:00:00Z"
    cp.add_item(doc, "app-launcher#1", "done one", status="merged")
    cp.add_item(doc, "app-launcher#2", "in flight", status="building")
    cp.add_item(doc, "life-os#3", "done two", status="merged")
    cp.add_item(doc, "life-os#4", "parked one", status="parked")
    cp.set_lane(doc, "life-os", "gate", item="#3", session="sid-1")
    cp.add_waiting(doc, "Remember the last tab?", ref="app-launcher#2")
    return doc


# ---- prune_items --------------------------------------------------------------

doc = _doc()
lanes, waiting = json.dumps(doc["lanes"]), json.dumps(doc["waiting_on_roberto"])
check(cp.prune_items(doc) == 2, "default prune drops the two merged rows")
check([row["status"] for row in doc["queue"]] == ["building", "parked"], "default prune keeps every other status, in order")
check(json.dumps(doc["lanes"]) == lanes and json.dumps(doc["waiting_on_roberto"]) == waiting,
      "prune leaves lanes and waiting items untouched, even a lane pointing at a pruned row")
check(cp.validate(doc) == [], "the pruned plan still validates")

doc = _doc()
check(cp.prune_items(doc, ("merged", "parked")) == 3 and [r["status"] for r in doc["queue"]] == ["building"],
      "a wider status set drops every listed status")

doc = _doc()
check(cp.prune_items(doc, ("gate",)) == 0 and len(doc["queue"]) == 4, "no match prunes nothing")

try:
    cp.prune_items(_doc(), ("done",))
    check(False, "an unknown status is refused")
except ValueError as exc:
    check("unknown status 'done'" in str(exc), "an unknown status is refused")

# ---- prune_plan_file and the CLI verb -------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="chief-plan-prune-"))
try:
    path = tmp / "chief-plan.json"
    check(cp.prune_plan_file(path) == 0 and cp.load(path)["queue"] == [],
          "a missing plan file prunes to 0 and stays the empty plan")

    cp.update(path, lambda d: d.update(_doc()))
    check(cp.prune_plan_file(path) == 2 and len(cp.load(path)["queue"]) == 2,
          "prune_plan_file removes the merged rows through the locked writer")
    check(cp.prune_plan_file(path) == 0, "a second prune finds nothing left")

    path.write_text("{not json", encoding="utf-8")
    before = path.read_bytes()
    try:
        cp.prune_plan_file(path)
        check(False, "a corrupt plan file is refused")
    except ValueError:
        check(path.read_bytes() == before, "a corrupt plan file is refused and left byte-identical")

    plan_file = tmp / "cli.json"
    env = {**os.environ, "PYTHONUTF8": "1"}

    def plan(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"), "plan",
             "--plan-file", str(plan_file), *argv],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    cp.update(plan_file, lambda d: d.update(_doc()))
    r = plan("prune")
    check(r.returncode == 0 and r.stdout.startswith("PRUNED=2 PLAN=written") and "queue=2" in r.stdout,
          f"CLI prune defaults to merged and prints PRUNED=2 ({r.stdout.strip()[:60]!r}, {r.stderr.strip()[:80]})")
    r = plan("prune")
    check(r.returncode == 0 and r.stdout.startswith("PRUNED=0 "), "CLI prune with nothing to drop prints PRUNED=0")
    r = plan("prune", "--status", "parked", "--status", "building")
    check(r.returncode == 0 and r.stdout.startswith("PRUNED=2 ") and "queue=0" in r.stdout,
          "CLI prune --status is repeatable and widens the set")
    r = plan("prune", "--status", "bogus")
    check(r.returncode == 2 and "invalid choice" in r.stderr, "CLI prune refuses an unknown --status")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_prune")
