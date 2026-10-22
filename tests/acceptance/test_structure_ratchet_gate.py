"""Tests for tests/acceptance/structure_ratchet_gate.py (fleet-config#1549).

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined offender passes, a line appended to it or a new
     101-line function fails with the ratchet's teaching message and reverting
     passes again, a Python file that does not parse is exit 3.

The script's own measuring rules are the scaffold's tests/test_structure_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there
is no prerequisite to skip over.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_structure_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import structure_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_structure_ratchet_check", check, finding="[file size] src/a.py has 1200 lines")


# ---- 3. end to end, the real script on a throwaway git tree ----

def _ratchet(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_LONG_FN = "def long_one():\n" + "    pass\n" * 100  # 101 lines

tmp = Path(tempfile.mkdtemp(prefix="structure-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / "pkg").mkdir()
    (tmp / ".fleet.toml").write_text("[structure_ratchet]\nenabled = true\nmax_file_lines = 100\n", encoding="utf-8")
    (tmp / "pkg" / "big.py").write_text("x = 1\n" * 110, encoding="utf-8")
    (tmp / "pkg" / "small.py").write_text("def short():\n    pass\n", encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a clean tree passes against its baseline, got {code}: {out}")

    # A line appended to a baselined offender: fails by key, with the teaching message.
    big = tmp / "pkg" / "big.py"
    big.write_text("x = 1\n" * 111, encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "pkg/big.py" in out and "repo-layout.md#file-size" in out,
          f"one line appended to a baselined file fails naming the file and the rule, got {code}: {out}")
    big.write_text("x = 1\n" * 110, encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"reverting passes again, got {code}: {out}")

    # A new 101-line function fails; removing it passes.
    small = tmp / "pkg" / "small.py"
    small.write_text(small.read_text(encoding="utf-8") + _LONG_FN, encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "long_one" in out and "repo-layout.md#function-length" in out,
          f"a 101-line function fails naming it and the rule, got {code}: {out}")
    small.write_text("def short():\n    pass\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"removing the function passes again, got {code}: {out}")

    # Could not measure -> exit 3, never 0: a file that does not parse would
    # otherwise hide every function in it.
    small.write_text("def broken(:\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "pkg/small.py" in out,
          f"a Python file that does not parse exits 3 (NOT CONFIRMED) naming it, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_structure_ratchet_gate")
