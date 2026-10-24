"""Tests for tests/acceptance/static_ratchet_gate.py (fleet-config#1514).

Three layers:
  1. exit-code mapping, pure -- 0 passes; 1, 2 and 3 all fail; 3 is NOT CONFIRMED
     and never a SKIP, which is what `shared.SKIP_EXIT == 3` would make of it
     under `_subprocess_unit_check`.
  2. the check function itself, with the script's run stubbed -- the reported
     line and the (failures, total) it hands the gate.
  3. the acceptance criteria end to end on a throwaway tree with the real ruff
     and jscpd -- baselined hits pass, a new unused import or duplicate block
     fails by key, a missing prerequisite is a hard failure.

A missing prerequisite (no ruff in any known venv, no `npx`) fails layer 3; it is
never a skip. The detectors' own key and mode behaviour is the scaffold's
tests/test_static_ratchet.py and is not repeated here.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_static_ratchet_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance import static_ratchet_gate as gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from ratchet_gate_fixtures import assert_check_function, assert_exit_mapping  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. exit-code mapping, 2. the check function with the script's run stubbed ----

assert_exit_mapping(gate, check)
assert_check_function(gate, "_static_ratchet_check", check, finding="new: unused:F401:x.py:os")


# ---- 3. end to end, real ruff + jscpd on a throwaway tree ----

def _has_ruff(python: str) -> bool:
    try:
        proc = subprocess.run([python, "-m", "ruff", "--version"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _ratchet_python() -> Optional[str]:
    """The interpreter that has ruff. The suite runs this file under
    `shared.PYTHON`, which need not be the repo venv -- so look in the venv too."""
    candidates = [sys.executable, str(REPO / ".venv" / "Scripts" / "python.exe"), str(REPO / ".venv" / "bin" / "python")]
    return next((c for c in candidates if Path(c).exists() and _has_ruff(c)), None)


def _ratchet(python: str, root: Path, *args: str, env: Optional[dict[str, str]] = None) -> tuple[int, str]:
    proc = subprocess.run(
        [python, str(gate.SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, env=env, timeout=600,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _clone_block() -> str:
    # 15 lines, ~150 tokens: over jscpd's min_lines=10 / min_tokens=100 defaults.
    return "".join(f"value_{i} = compute({i}, 'label_{i}') + offset_{i}\n" for i in range(15))


_python = _ratchet_python()
_npx = shutil.which("npx")
if _python is None or _npx is None:
    missing = [name for name, found in (("ruff in the repo venv", _python), ("npx (Node)", _npx)) if not found]
    check(False, f"prerequisite missing -- {', '.join(missing)}: the end-to-end layer cannot run (a failure, never a skip)")
else:
    tmp = Path(tempfile.mkdtemp(prefix="static-ratchet-gate-"))
    try:
        (tmp / ".fleet.toml").write_text("[static_ratchet]\nenabled = true\n", encoding="utf-8")
        (tmp / "clean.py").write_text("import os\n\nprint(os.name)\n", encoding="utf-8")
        (tmp / "legacy.py").write_text("import json\n\nVALUE = 1\n", encoding="utf-8")

        code, out = _ratchet(_python, tmp)
        check(code == 1, f"enabled but no baseline file fails, got {code}: {out}")

        code, out = _ratchet(_python, tmp, "--init")
        check(code == 0, f"--init baselines the tree, got {code}: {out}")
        code, out = _ratchet(_python, tmp)
        check(code == 0, f"a baselined unused import does not fail, got {code}: {out}")

        (tmp / "fresh.py").write_text("import re\n\nVALUE = 2\n", encoding="utf-8")
        code, out = _ratchet(_python, tmp)
        check(code == 1 and "[unused] fresh.py" in out and "`re`" in out,
              f"a new unused import fails naming the file and symbol, got {code}: {out}")
        check("legacy.py" not in out, f"the baselined unused import is not re-reported, got: {out}")
        (tmp / "fresh.py").unlink()

        (tmp / "dup_a.py").write_text(_clone_block(), encoding="utf-8")
        (tmp / "dup_b.py").write_text(_clone_block(), encoding="utf-8")
        code, out = _ratchet(_python, tmp)
        check(code == 1 and "[duplicate block] dup_a.py <-> dup_b.py" in out,
              f"a new duplicate block fails naming the file pair, got {code}: {out}")

        code, out = _ratchet(_python, tmp, "--update-baseline", "--allow-additions")
        check(code == 0, f"--allow-additions baselines the new duplicate, got {code}: {out}")
        code, out = _ratchet(_python, tmp)
        check(code == 0, f"the baselined duplicate no longer fails, got {code}: {out}")

        # A prerequisite that cannot run is exit 3 -- here, no npx on PATH.
        bare = tmp / "empty-path"
        bare.mkdir()
        env = {**os.environ, "PATH": str(bare)}
        code, out = _ratchet(_python, tmp, env=env)
        check(code == 3 and "npx" in out,
              f"a missing npx exits 3 (NOT CONFIRMED), never 0, got {code}: {out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_static_ratchet_gate")
