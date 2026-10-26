"""Tests for tests/acceptance/docs_ratchet_gate.py (fleet-config#1542).

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT` would make of it under
     `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway git tree with the real
     script -- a baselined marker passes, an added issue reference fails by
     name, README growth fails, an unclosed fence is exit 3.

The script's own counting rules are the scaffold's tests/test_docs_ratchet.py
and are not repeated here. The script needs only git and the stdlib, so there
is no prerequisite to skip over.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_docs_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import docs_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping, run_ratchet  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_docs_ratchet_check", check, finding="docs/a.md: 2 markers, baseline 1")


# ---- 3. end to end, the real script on a throwaway git tree ----

_ratchet = partial(run_ratchet, gate)


tmp = Path(tempfile.mkdtemp(prefix="docs-ratchet-gate-"))
try:
    run_git(tmp, "init", "-q", check=check)
    (tmp / "docs").mkdir()
    (tmp / ".fleet.toml").write_text("[docs_ratchet]\nenabled = true\n", encoding="utf-8")
    (tmp / "README.md").write_text("# Tool\n\nWhat it does, in the present tense.\n", encoding="utf-8")
    (tmp / "docs" / "legacy.md").write_text("# Legacy\n\nAdded in #12.\n", encoding="utf-8")
    (tmp / "docs" / "clean.md").write_text("# Clean\n\nNo history here.\n", encoding="utf-8")
    run_git(tmp, "add", "-A", check=check)

    code, out = _ratchet(tmp)
    check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")

    code, out = _ratchet(tmp, "--init")
    check(code == 0, f"--init baselines the tree, got {code}: {out}")
    code, out = _ratchet(tmp)
    check(code == 0, f"a baselined marker does not fail, got {code}: {out}")

    (tmp / "docs" / "clean.md").write_text("# Clean\n\nFixed in #34.\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "docs/clean.md" in out and "#34" in out,
          f"an added issue reference fails naming the file and the reference, got {code}: {out}")
    check("legacy.md" not in out, f"the baselined marker is not re-reported, got: {out}")
    (tmp / "docs" / "clean.md").write_text("# Clean\n\nNo history here.\n", encoding="utf-8")

    (tmp / "README.md").write_text("# Tool\n\n## Changelog\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 1 and "README.md" in out, f"a new changelog heading fails by name, got {code}: {out}")
    (tmp / "README.md").write_text("# Tool\n\nWhat it does, in the present tense.\n", encoding="utf-8")

    # Could not count -> exit 3, never 0: a fence that never closes would
    # otherwise hide every reference below it.
    (tmp / "docs" / "clean.md").write_text("# Clean\n\n```\nopen forever\n", encoding="utf-8")
    code, out = _ratchet(tmp)
    check(code == 3 and "docs/clean.md" in out,
          f"an unclosed fence exits 3 (NOT CONFIRMED) naming the file, got {code}: {out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_docs_ratchet_gate")
