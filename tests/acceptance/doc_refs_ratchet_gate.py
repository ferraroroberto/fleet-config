"""Doc references ratchet stage.

Runs the vendored `scripts/doc_refs_ratchet.py` against this repo and fails when
`README.md`, `CLAUDE.md` or a `docs/**/*.md` page names a repo path, `python -m`
module or relative link that does not exist and is not in the committed
`.doc-refs-ratchet-baseline.json`. The output names each doc, the line and the
reference.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the references (git unusable, a file that is not UTF-8, an unclosed
fence). Here 3 fails as NOT CONFIRMED, as does every other non-zero exit.

A code change can strand a doc reference without touching the doc, so the stage
is never path-routed.
"""
from __future__ import annotations

from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "doc_refs_ratchet.py"

# Well under a second over this tree's docs. The bound only turns a hung child
# into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the doc references could not be resolved, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no doc names a path, module or link that does not exist beyond its baseline",
        1: "a doc names a path, module or link that does not exist (or the baseline is missing)",
        2: "bad [doc_refs_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _doc_refs_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)
