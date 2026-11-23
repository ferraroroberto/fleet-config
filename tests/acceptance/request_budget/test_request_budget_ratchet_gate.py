"""Tests for tests/acceptance/request_budget_ratchet_gate.py.

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined loop passes, a new short-sleep loop fails naming the
     file and the rule and reverting passes again, a declared loop passes, and a
     tracked Python file that does not parse is exit 3.

The script's own detection rules are the scaffold's tests/ratchets/test_request_budget_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there is
no prerequisite to skip over.

Lives under tests/acceptance/request_budget/ because the flat tests/acceptance/ directory is at its
module limit and a new module goes in a subject subfolder.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/request_budget/test_request_budget_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests"))
from acceptance.request_budget import request_budget_ratchet_gate as gate  # noqa: E402

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


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_request_budget_ratchet_check", check, finding="src/a.py:3: unwatched-loop")


# ---- 3. end to end, the real script on a throwaway git tree ----

_ratchet = partial(run_ratchet, gate)

_TABLE = '[request_budget_ratchet]\nenabled = true\nserver_paths = ["*.py"]\n'
_LOOP = "import time\n\n\ndef watch():\n    while True:\n        time.sleep(1)\n"

tmp = Path(tempfile.mkdtemp(prefix="request-budget-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    fleet = tmp / ".fleet.toml"
    fleet.write_text(_TABLE, encoding="utf-8")
    mod = tmp / "watcher.py"
    mod.write_text("def idle():\n    return 1\n", encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")
    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a clean tree passes against its baseline, got {code}: {out}")

    # A new short-sleep loop fails by file and rule.
    mod.write_text(_LOOP, encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "watcher.py" in out and "unwatched-loop" in out,
          f"a new 1 s loop fails naming the file and the rule, got {code}: {out}")
    mod.write_text("def idle():\n    return 1\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 0, f"reverting passes again, got {code}: {out}")

    # A loop declared in .fleet.toml is a reviewed exception, not a hit.
    mod.write_text(_LOOP, encoding="utf-8")
    fleet.write_text(
        _TABLE + '\n[[request_budget.loops]]\npath = "watcher.py"\nfunction = "watch"\n'
        'interval_s = 1\nwhy = "reads hardware state while nobody is viewing"\n',
        encoding="utf-8",
    )
    code, out = _ratchet(tmp)
    check(code == 0, f"a declared loop passes, got {code}: {out}")
    fleet.write_text(_TABLE, encoding="utf-8")
    mod.write_text("def idle():\n    return 1\n", encoding="utf-8")

    # Could not parse -> exit 3, never 0: the file's count is unknown, not zero.
    mod.write_text("def broken(:\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "watcher.py" in out,
          f"an unparseable tracked file exits 3 (NOT CONFIRMED) naming the file, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_request_budget_ratchet_gate")
