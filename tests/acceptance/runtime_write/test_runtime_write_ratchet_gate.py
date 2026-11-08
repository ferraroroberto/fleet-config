"""Tests for tests/acceptance/runtime_write/runtime_write_ratchet_gate.py.

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`. Held for the static check, the snapshot and the probe.
  2. the three check functions themselves, with the script's run stubbed -- the
     reported line and the (failures, total) each hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined write target passes; a new write resolving under a code
     folder fails naming the file and reverting passes; a probe fails naming a file
     the run left under a code folder and passes when it left none; a probe with no
     snapshot is exit 3.

The script's own resolution rules are the scaffold's tests/test_runtime_write_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there is
no prerequisite to skip over.

Lives in the tests/acceptance/runtime_write/ subpackage beside its stage, because the
flat tests/acceptance/ directory is at its 40-module limit.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/runtime_write/test_runtime_write_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests"))
from acceptance.runtime_write import runtime_write_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import (  # noqa: E402
    assert_check_function,
    assert_exit_mapping,
    run_ratchet,
)

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check functions with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_exit_mapping(SimpleNamespace(classify_exit=gate.classify_snapshot_exit), check)
assert_exit_mapping(SimpleNamespace(classify_exit=gate.classify_probe_exit), check)
assert_check_function(gate, "_runtime_write_ratchet_check", check,
                      finding="lib/tool.py:4: write target resolves under code folder lib")
assert_check_function(gate, "_runtime_write_snapshot_check", check, finding="git could not list lib")
assert_check_function(gate, "_runtime_write_probe_check", check, finding="lib/run.log")
check(not gate.SNAPSHOT.resolve().is_relative_to(REPO.resolve()),
      f"the snapshot file sits outside the tree, got {gate.SNAPSHOT}")


# ---- 3. end to end, the real script on a throwaway git tree ----

_CONFIG = """[runtime_write_ratchet]
enabled = true
code_folders = ["lib"]
"""
_BASE = """from pathlib import Path

HERE = Path(__file__).resolve().parent


def old():
    (HERE / "old.log").write_text("x")
"""
_NEW_WRITE = '\n\ndef new():\n    (HERE / "new.log").write_text("y")\n'

tmp = Path(tempfile.mkdtemp(prefix="runtime-write-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / ".fleet.toml").write_text(_CONFIG, encoding="utf-8")
    (tmp / "lib").mkdir()
    mod = tmp / "lib" / "tool.py"
    mod.write_text(_BASE, encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = run_ratchet(gate, tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = run_ratchet(gate, tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = run_ratchet(gate, tmp)
    check(code == 0, f"a baselined write target passes, got {code}: {out}")

    mod.write_text(_BASE + _NEW_WRITE, encoding="utf-8")
    code, out = run_ratchet(gate, tmp)
    check(code == 1 and "tool.py" in out,
          f"a new write under a code folder fails naming the file, got {code}: {out}")
    mod.write_text(_BASE, encoding="utf-8")
    code, out = run_ratchet(gate, tmp)
    check(code == 0, f"reverting the new write passes again, got {code}: {out}")

    snap = tmp.parent / f"{tmp.name}-snapshot.json"
    code, out = run_ratchet(gate, tmp, "--snapshot", str(snap))
    check(code == 0, f"--snapshot records the code folders, got {code}: {out}")
    (tmp / "lib" / "run.log").write_text("left behind", encoding="utf-8")
    code, out = run_ratchet(gate, tmp, "--probe", str(snap))
    check(code == 1 and "run.log" in out,
          f"a file the run left under a code folder fails the probe naming it, got {code}: {out}")
    (tmp / "lib" / "run.log").unlink()
    code, out = run_ratchet(gate, tmp, "--probe", str(snap))
    check(code == 0, f"a run that left nothing passes the probe, got {code}: {out}")

    # The passing probe deleted its snapshot: probing again cannot be confirmed.
    code, out = run_ratchet(gate, tmp, "--probe", str(snap))
    check(code == 3, f"a probe with no snapshot exits 3 (NOT CONFIRMED), got {code}: {out}")
    snap.unlink(missing_ok=True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_runtime_write_ratchet_gate")
