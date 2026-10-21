"""Static-analysis ratchet stage (fleet-config#1514).

Runs the vendored `scripts/static_ratchet.py` against this repo and fails on a
duplicate block or an unused import/variable that is not in the committed
`.static-ratchet-baseline.json`. Baselined hits never fail; a new one is named
by key in the output.

Deliberately its own check, not a row in `standalone_dispatch`: that table runs
a file through `shared._subprocess_unit_check`, which reads exit
`shared.SKIP_EXIT` (3) as SKIP -- and 3 is exactly what the ratchet exits when a
detector could not run (no `npx`, no ruff, a timeout). Routed there, "nothing
was checked" would land in the Skipped column and read as fine. Here 3 fails,
reported as NOT CONFIRMED, and so does every other non-zero exit.

The interpreter is `sys.executable`, the venv python that launched the run: it
is the one that has ruff (requirements.txt), which `shared.PYTHON` need not be.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Tuple

from acceptance.shared import REPO, _Checker
from _lib import NO_WINDOW

SCRIPT = REPO / "scripts" / "static_ratchet.py"

EXIT_PASS, EXIT_NEW, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# The script bounds each detector at 600 s itself; this only has to outlast two
# of them so a hung child is reported as the script's own NOT CONFIRMED first.
RUN_TIMEOUT_S = 1300

_FAIL_LABELS = {
    EXIT_NEW: "NEW finding or missing baseline",
    EXIT_CONFIG: "bad [static_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a detector could not run, so nothing was checked",
}


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    if code == EXIT_PASS:
        return True, "no new duplicate block or unused import"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def _static_ratchet_check() -> Tuple[int, int]:
    check = _Checker()
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=RUN_TIMEOUT_S, check=False,
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = EXIT_NOT_CONFIRMED, f"static_ratchet.py did not finish in {RUN_TIMEOUT_S}s"
    except OSError as exc:
        code, output = EXIT_NOT_CONFIRMED, f"static_ratchet.py could not start: {exc}"
    ok, label = classify_exit(code)
    check(f"static_ratchet: {label}", ok, output)
    return check.failures, check.total
