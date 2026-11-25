"""Request budget ratchet stage.

Runs the vendored `scripts/request_budget_ratchet.py` against this repo and fails when a
file's count for a rule (a fixed-rate poll that reaches the server, a poll under the 2 s
floor, an unwatched server loop, a directory scan in a handler, a submit to the shared
default pool, a web app without the timing middleware) rises above the committed
`.ratchets/request-budget.json`. The output names each file, the line and the rule.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the counts (git unusable, a source file that cannot be read or parsed).
Here 3 fails as NOT CONFIRMED, as does every other non-zero exit.

Never path-routed: a loop's interval can come from a constant in another module's
change, so a path filter would miss a rise.
"""
from __future__ import annotations

from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "request_budget_ratchet.py"

# Well under a second over this tree. The bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the sources could not be read through, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no request-budget rule count above its baseline",
        1: "a request-budget rule count rose (or the baseline is missing)",
        2: "bad [request_budget_ratchet] or [request_budget] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _request_budget_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)
