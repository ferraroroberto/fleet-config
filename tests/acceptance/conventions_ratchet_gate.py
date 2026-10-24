"""Conventions ratchet stage.

Runs the vendored `scripts/conventions_ratchet.py` against this repo and fails when
a file's hit count for an enabled rule (`no-window`, `logging-not-print`,
`no-drive-path`, `git-wrapper`) rises above the committed
`.conventions-ratchet-baseline.json`. The output names each file, the line and the
rule.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not scan the tree (git unusable, a tracked source file that cannot be read or
parsed). Here 3 fails as NOT CONFIRMED, as does every other non-zero exit.

Never path-routed: it is cheap, and a rule added to `rules` must see the whole tree.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Tuple

from acceptance.shared import REPO, _Checker
from _lib import NO_WINDOW

SCRIPT = REPO / "scripts" / "conventions_ratchet.py"

# About a second over this tree. The bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the tree could not be scanned, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no convention rule has a file count above its baseline",
        1: "a convention rule count rose above its baseline (or the baseline is missing)",
        2: "bad [conventions_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _conventions_ratchet_check() -> Tuple[int, int]:
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
        code, output = 3, f"conventions_ratchet.py did not finish in {RUN_TIMEOUT_S}s"
    except OSError as exc:
        code, output = 3, f"conventions_ratchet.py could not start: {exc}"
    ok, label = classify_exit(code)
    check(f"conventions_ratchet: {label}", ok, output)
    return check.failures, check.total
