"""Dead-code ratchet stage.

Runs the vendored `scripts/dead_code_ratchet.py` against this repo and fails when a
function, class or method that nothing in the tracked tree refers to is not in the
committed `.dead-code-ratchet-baseline.json`. The output names each file, the line
and the definition.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the references (git unusable, a tracked source file that cannot be
read or parsed). Here 3 fails as NOT CONFIRMED, as does every other non-zero exit.

Never path-routed: deleting the last caller in one file strands a definition in
another.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Tuple

from acceptance.shared import REPO, _Checker
from _lib import NO_WINDOW

SCRIPT = REPO / "scripts" / "dead_code_ratchet.py"

# About a second over this tree. The bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the references could not be resolved, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no function, class or method nothing refers to beyond its baseline",
        1: "a function, class or method nothing refers to (or the baseline is missing)",
        2: "bad [dead_code_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _dead_code_ratchet_check() -> Tuple[int, int]:
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
        code, output = 3, f"dead_code_ratchet.py did not finish in {RUN_TIMEOUT_S}s"
    except OSError as exc:
        code, output = 3, f"dead_code_ratchet.py could not start: {exc}"
    ok, label = classify_exit(code)
    check(f"dead_code_ratchet: {label}", ok, output)
    return check.failures, check.total
