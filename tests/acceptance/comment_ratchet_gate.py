"""Comment ratchet stage.

Runs the vendored `scripts/comment_ratchet.py` against this repo and fails when a
file's count of `#NNN` issue references inside comments rises above its entry in
the committed `.comment-ratchet-baseline.json`, or a file not in the baseline
carries any. The output names each file with every reference and its line.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the count (git unusable, a file it cannot scan). Here 3 fails as
NOT CONFIRMED, as does every other non-zero exit.

The count needs the whole tree, so the stage is never path-routed.
"""
from __future__ import annotations

from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "comment_ratchet.py"

# One tokenizer pass per source file: about a second on this tree. The bound only
# turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the comments could not be scanned, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no file carries more issue references in comments than its baseline",
        1: "a file gained an issue reference in a comment (or the baseline is missing)",
        2: "bad [comment_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _comment_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)
