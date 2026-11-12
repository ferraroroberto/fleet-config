"""The shared check-or-regenerate tail (skills/_lib/fleet_core/check_or_write.py).

Beside the unit cases, one structural check: no renderer re-implements the
STALE-to-stderr tail, so a fifth copy fails here instead of drifting.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_check_or_write.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from fleet_core.check_or_write import check_or_write  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_h = CheckHarness()
check = _h.check


def run(path: Path, rendered: str, check_mode: bool, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = check_or_write(path, rendered, "lbl", Path("regen_script.py"), check_mode, **kw)
    return rc, out.getvalue(), err.getvalue()


root = throwaway_dir("check-or-write-")
target = root / "out.md"

rc, out, err = run(target, "a\n", True)
check(rc == 1 and "lbl: STALE" in err and "regen_script.py" in err and out == "", f"check: a missing file is stale on stderr ({rc}, {err!r})")
check(not target.exists(), "check: never writes")

rc, out, err = run(target, "a\n", False, newline="\n")
check(rc == 0 and target.read_bytes() == b"a\n" and "regenerated out.md" in out, f"write: a missing file is created ({out!r})")

rc, out, err = run(target, "a\n", False)
check(rc == 0 and "already up to date" in out, f"write: identical content is a no-op ({out!r})")

rc, out, err = run(target, "a\n", True)
check(rc == 0 and out == "lbl: up to date.\n" and err == "", f"check: current content passes on stdout ({out!r})")

rc, out, err = run(target, "b\n", True, current="a\n")
check(rc == 1 and target.read_text(encoding="utf-8") == "a\n", "check: a differing render is stale and leaves the file")

rc, out, err = run(target, "b\n", False, current="b\n")
check(rc == 0 and "already up to date" in out and target.read_text(encoding="utf-8") == "a\n", "a caller-supplied `current` is trusted, not re-read")

# ---- no second copy of the tail ----
_home = Path("skills/_lib/fleet_core/check_or_write.py")
copies = sorted(str(p.relative_to(REPO)).replace("\\", "/")
                for root_dir in (REPO / "skills", REPO / ".claude" / "skills", REPO / "hooks", REPO / "scripts")
                for p in root_dir.rglob("*.py")
                if "STALE — run" in p.read_text(encoding="utf-8") and p.relative_to(REPO).as_posix() != _home.as_posix())
check(copies == [], f"only check_or_write prints the STALE tail; found copies in {copies}")

_h.report_and_exit("test_check_or_write")
