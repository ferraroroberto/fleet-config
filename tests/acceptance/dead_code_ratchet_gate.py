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

from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

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
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)
