"""`/config-map` step 1 runs only the config-map check, and a stale snapshot still fails it (fleet-config#2018).

  1. The skill's step 1 names `check.py` and never `tests/run_acceptance.py`: the full run takes
     many minutes and outlived the scheduled job's watchdog.
  2. `check.py` exits 1 on a stale `config.data.js` (the check reports it as a SKIP, which this
     skill must treat as a failure) and 0 on a snapshot `build_data.py` just produced.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_config_map_check.py
Exit 0 = all pass.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))
from acceptance.shared import gate_projects_toml  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_h = CheckHarness()
check = _h.check

SKILL_DIR = REPO / ".claude" / "skills" / "config-map"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"config_map_{name}", SKILL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _run_check(cm, snapshot: Path) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return cm.main(["--data-js", str(snapshot)])


# ---- 1. the skill's step 1 ----

skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
step1 = re.search(r"^### 1\..*?(?=^### 2\.)", skill, re.MULTILINE | re.DOTALL)
check(step1 is not None, "SKILL.md has a step 1 section")
body = step1.group(0) if step1 else ""
check("run_acceptance.py" not in body, "step 1 does not run the full tests/run_acceptance.py")
check(".claude/skills/config-map/check.py" in body, "step 1 runs the config-map-only check.py")
check(".claude/skills/config-map/build_data.py" in body, "step 1 still regenerates the data first")

# ---- 2. check.py ----

cm = _load("check")
bd = _load("build_data")
with tempfile.TemporaryDirectory() as tmp:
    stale = Path(tmp) / "stale.js"
    stale.write_text("window.CONFIG = {};\n", encoding="utf-8")
    check(_run_check(cm, stale) == 1, "a stale config.data.js fails the check (exit 1)")

    fresh = Path(tmp) / "fresh.js"
    fresh.write_text(bd.regenerate(gate_projects_toml()), encoding="utf-8")
    check(_run_check(cm, fresh) == 0, "a snapshot build_data.py just produced passes (exit 0)")

_h.report_and_exit("test_config_map_check")
