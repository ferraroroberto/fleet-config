"""Instruction ratchet stage.

Runs the vendored `scripts/instruction_ratchet.py` against this repo and fails when
a prompt-audit lint rule's hit count in an instruction file (`CLAUDE.md`, a
`SKILL.md`, the docs a skill links to, `global-CLAUDE.md`) rises above its entry in
the committed `.instruction-ratchet-baseline.json`, or a file passes its size cap.
The output names each file, the rule and every hit with its line.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the count (git unusable, a file it cannot read, an unclosed fence,
a skill whose description cannot be read). Here 3 fails as NOT CONFIRMED, as does
every other non-zero exit.

The count reads one file in light of another (`AGENTS.md` decides who reads a
`CLAUDE.md`, a skill's reference docs live outside its folder), so the stage is
never path-routed.
"""
from __future__ import annotations

from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "instruction_ratchet.py"

# About a second over this tree's instruction files. The bound only turns a hung
# child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the instruction files could not be measured, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no instruction file has more rule hits than its baseline or passes its size cap",
        1: "an instruction file gained a rule hit or passed its size cap (or the baseline is missing)",
        2: "bad [instruction_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _instruction_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)
