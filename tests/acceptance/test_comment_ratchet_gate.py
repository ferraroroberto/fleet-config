"""Tests for tests/acceptance/comment_ratchet_gate.py.

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined reference passes, a new one in a comment fails naming
     the file and reverting passes again, a reference inside a string does not
     count, and a Python file that does not tokenize is exit 3.

The script's own counting rules are the scaffold's tests/test_comment_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there is
no prerequisite to skip over.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_comment_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import comment_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_comment_ratchet_check", check, finding="pkg/a.py: 2 reference(s), baseline 1")


# ---- 3. end to end, the real script on a throwaway git tree ----

def _ratchet(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


tmp = Path(tempfile.mkdtemp(prefix="comment-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / "pkg").mkdir()
    (tmp / ".fleet.toml").write_text("[comment_ratchet]\nenabled = true\n", encoding="utf-8")
    mod = tmp / "pkg" / "a.py"
    mod.write_text("x = 1  # kept for #12\n", encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a clean tree passes against its baseline, got {code}: {out}")

    # A second reference in a comment of a baselined file fails by name.
    mod.write_text("x = 1  # kept for #12\ny = 2  # added by #13\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "pkg/a.py" in out, f"a new comment reference fails naming the file, got {code}: {out}")
    mod.write_text("x = 1  # kept for #12\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"reverting passes again, got {code}: {out}")

    # A reference inside a string literal is not a comment and never counts.
    mod.write_text("x = 1  # kept for #12\nLABEL = 'issue #14'\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"a reference inside a string does not count, got {code}: {out}")

    # Could not scan -> exit 3, never 0: a file that does not tokenize would
    # otherwise hide every comment in it.
    mod.write_text("x = (\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "pkg/a.py" in out,
          f"a Python file that does not tokenize exits 3 (NOT CONFIRMED) naming it, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_comment_ratchet_gate")
