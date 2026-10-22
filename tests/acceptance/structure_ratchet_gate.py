"""Structure ratchet stage (fleet-config#1549).

Runs the vendored `scripts/structure_ratchet.py` against this repo and fails when
a file, a function or a flat directory outgrows its limit and is new or above its
entry in the committed `.structure-ratchet-baseline.json`, or when the root
folders do not match the `shape` and `extra_roots` declared in `.fleet.toml`. The
output names the finding, the rule and where the code goes instead.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the count (git unusable, a Python file that does not parse, an
unknown `shape`). Here 3 fails as NOT CONFIRMED, as does every other non-zero
exit.

The count needs the whole tree, so the stage is never path-routed.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Tuple

from acceptance.shared import REPO, _Checker
from _lib import NO_WINDOW

SCRIPT = REPO / "scripts" / "structure_ratchet.py"

# One `ast` parse per Python file plus a `git ls-files`: a few seconds on this
# tree. The bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 180

_NOT_CONFIRMED = "NOT CONFIRMED -- the structure could not be measured, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no new or grown file, function or directory offender and the root layout matches",
        1: "a file, function or directory offender is new or grew, the root layout is off, or the baseline is missing",
        2: "bad [structure_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _structure_ratchet_check() -> Tuple[int, int]:
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
        code, output = 3, f"structure_ratchet.py did not finish in {RUN_TIMEOUT_S}s"
    except OSError as exc:
        code, output = 3, f"structure_ratchet.py could not start: {exc}"
    ok, label = classify_exit(code)
    check(f"structure_ratchet: {label}", ok, output)
    return check.failures, check.total
