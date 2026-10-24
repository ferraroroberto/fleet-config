"""Tests for tests/acceptance/instruction_ratchet_gate.py.

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined hit passes, a new rule hit in `CLAUDE.md` fails naming
     the file and the rule and reverting passes again, a hit inside a code fence
     does not count, and an unclosed fence is exit 3.

The script's own rule set is the scaffold's tests/test_instruction_ratchet.py and
is not repeated here. The script needs only git and the stdlib, so there is no
prerequisite to skip over.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_instruction_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import instruction_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_instruction_ratchet_check", check, finding="CLAUDE.md: R-03 2 hit(s), baseline 1")


# ---- 3. end to end, the real script on a throwaway git tree ----

def _ratchet(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


tmp = Path(tempfile.mkdtemp(prefix="instruction-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / ".fleet.toml").write_text("[instruction_ratchet]\nenabled = true\n", encoding="utf-8")
    doc = tmp / "CLAUDE.md"
    doc.write_text("# Rules\n\nDouble-check your answer before replying.\n", encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a clean tree passes against its baseline, got {code}: {out}")

    # A second hit of the same rule in a baselined file fails by file and rule.
    doc.write_text("# Rules\n\nDouble-check your answer before replying.\n\nRe-verify your work afterwards.\n",
                   encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "CLAUDE.md" in out and "R-03" in out,
          f"a new rule hit fails naming the file and the rule, got {code}: {out}")
    doc.write_text("# Rules\n\nDouble-check your answer before replying.\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"reverting passes again, got {code}: {out}")

    # A phrase inside a fenced code block is an example, not an instruction.
    doc.write_text("# Rules\n\nDouble-check your answer before replying.\n\n```\nRe-verify your work.\n```\n",
                   encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"a hit inside a code fence does not count, got {code}: {out}")

    # Could not measure -> exit 3, never 0: an unclosed fence would hide every
    # line below it.
    doc.write_text("# Rules\n\n```\nDouble-check your answer before replying.\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "CLAUDE.md" in out,
          f"an unclosed fence exits 3 (NOT CONFIRMED) naming the file, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_instruction_ratchet_gate")
