"""Tests for tests/acceptance/conventions_ratchet_gate.py.

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined hit passes; a new spawn without `creationflags`, a new
     `print` in library code, a new drive-path literal and a new raw `git` spawn
     each fail naming the file and the rule, and reverting passes again; a
     tracked file that does not parse is exit 3.

The script's own detection rules are the scaffold's tests/test_conventions_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there is
no prerequisite to skip over.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_conventions_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import conventions_ratchet_gate as gate  # noqa: E402
from acceptance.shared import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_conventions_ratchet_check", check, finding="src/a.py:3: [no-window] subprocess spawn without creationflags")


# ---- 3. end to end, the real script on a throwaway git tree ----

def _ratchet(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_CONFIG = """[conventions_ratchet]
enabled = true
rules = ["no-window", "logging-not-print", "no-drive-path", "git-wrapper"]
print_paths = ["lib/"]
git_wrappers = ["lib/git_run.py"]
"""
_BASE = """import subprocess


def old():
    subprocess.run(['tool'])
"""
# One seeded violation per enabled rule, appended to _BASE.
_SEEDED = {
    "no-window": "\n\ndef new():\n    subprocess.run(['other'])\n",
    "logging-not-print": "\n\ndef new():\n    print('diagnostic')\n",
    "no-drive-path": "\n\nROOT = 'E:/data'\n",
    "git-wrapper": "\n\ndef new():\n    subprocess.run(['git', 'status'], creationflags=0)\n",
}

tmp = Path(tempfile.mkdtemp(prefix="conventions-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / ".fleet.toml").write_text(_CONFIG, encoding="utf-8")
    (tmp / "lib").mkdir()
    (tmp / "lib" / "git_run.py").write_text("", encoding="utf-8")
    mod = tmp / "lib" / "tool.py"
    mod.write_text(_BASE, encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a baselined hit passes, got {code}: {out}")

    for rule, extra in _SEEDED.items():
        mod.write_text(_BASE + extra, encoding="utf-8")
        code, out = _ratchet(tmp)
        check(code == 1 and "tool.py" in out and f"[{rule}]" in out,
              f"a new {rule} violation fails naming the file and the rule, got {code}: {out}")
        mod.write_text(_BASE, encoding="utf-8")
        code, out = _ratchet(tmp)
        check(code == 0, f"reverting the {rule} violation passes again, got {code}: {out}")

    # Could not parse -> exit 3, never 0: the file may hold a violation the scan never saw.
    mod.write_text("def broken(:\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "tool.py" in out,
          f"an unparseable tracked file exits 3 (NOT CONFIRMED) naming the file, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_conventions_ratchet_gate")
