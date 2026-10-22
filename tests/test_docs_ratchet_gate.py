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

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_docs_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import docs_ratchet_gate as gate  # noqa: E402
from acceptance.shared import SKIP_EXIT  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping ----

check(gate.classify_exit(0)[0], "exit 0 passes")
for _code in (1, 2, 3, 4, -1):
    check(not gate.classify_exit(_code)[0], f"exit {_code} fails")
check("NOT CONFIRMED" in gate.classify_exit(3)[1], "exit 3 is labelled NOT CONFIRMED")
check("SKIP" not in gate.classify_exit(3)[1].upper(), "exit 3 is never labelled a skip")
# The collision this module exists for: the gate's own SKIP code is the
# ratchet's NOT CONFIRMED code, so only a dedicated mapping keeps it a failure.
check(SKIP_EXIT == 3, "shared.SKIP_EXIT is the ratchet's exit 3 (why the check is dedicated)")
check(not gate.classify_exit(SKIP_EXIT)[0], "shared.SKIP_EXIT fails through the dedicated mapping")


# ---- 2. the check function, script run stubbed ----

def _run_check_with(result: object) -> tuple[tuple[int, int], str]:
    """`_docs_ratchet_check()` with `subprocess.run` replaced by `result` -- a
    CompletedProcess to return, or an exception to raise."""
    real = gate.subprocess.run

    def fake(*_a: object, **_k: object) -> object:
        if isinstance(result, BaseException):
            raise result
        return result

    buf = io.StringIO()
    gate.subprocess.run = fake  # type: ignore[assignment]
    try:
        with contextlib.redirect_stdout(buf):
            counts = gate._docs_ratchet_check()
    finally:
        gate.subprocess.run = real  # type: ignore[assignment]
    return counts, buf.getvalue()


def _done(code: int, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr="")


counts, out = _run_check_with(_done(0))
check(counts == (0, 1) and out.startswith("OK"), f"exit 0 -> one passing check, got {counts} {out!r}")

counts, out = _run_check_with(_done(1, "docs/a.md: 2 markers, baseline 1"))
check(counts == (1, 1) and out.startswith("FAIL") and "docs/a.md" in out,
      f"exit 1 -> failing check that echoes the script's finding, got {counts} {out!r}")

counts, out = _run_check_with(_done(3, "docs/a.md: fenced block opened on line 4 never closes"))
check(counts == (1, 1), f"exit 3 counts as a failure, not a skip, got {counts}")
check("NOT CONFIRMED" in out and "SKIP" not in out and "never closes" in out,
      f"exit 3 prints NOT CONFIRMED plus the script's reason, got {out!r}")

counts, out = _run_check_with(subprocess.TimeoutExpired(cmd="x", timeout=1))
check(counts == (1, 1) and "NOT CONFIRMED" in out, f"a timed-out run is NOT CONFIRMED, got {counts} {out!r}")

counts, out = _run_check_with(OSError("no interpreter"))
check(counts == (1, 1) and "NOT CONFIRMED" in out, f"an unstartable run is NOT CONFIRMED, got {counts} {out!r}")


# ---- 3. end to end, the real script on a throwaway git tree ----

def _ratchet(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


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
