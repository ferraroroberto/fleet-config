"""Docs ratchet stage (fleet-config#1542).

Runs the vendored `scripts/docs_ratchet.py` against this repo and fails when
`README.md` outgrows 40 KiB or its baselined size, or when `README.md` or
`docs/**/*.md` gains an issue reference, a dated heading or a changelog heading
beyond the committed `.docs-ratchet-baseline.json`. A new marker is named by
file, kind and line in the output.

Deliberately its own check, not a row in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could
not establish the count (git unusable, a non-UTF-8 file, an unclosed fence).
Here 3 fails as NOT CONFIRMED, as does every other non-zero exit.

The count needs the whole tree, so the stage is never path-routed.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Tuple

from acceptance.shared import REPO, _Checker
from _lib import NO_WINDOW

SCRIPT = REPO / "scripts" / "docs_ratchet.py"

# The script is a line scan over a few dozen Markdown files and finishes in well
# under a second; the bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the markers could not be counted, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no README growth and no new history marker in the docs",
        1: "README grew or a doc gained a history marker (or the baseline is missing)",
        2: "bad [docs_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _docs_ratchet_check() -> Tuple[int, int]:
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
        code, output = 3, f"docs_ratchet.py did not finish in {RUN_TIMEOUT_S}s"
    except OSError as exc:
        code, output = 3, f"docs_ratchet.py could not start: {exc}"
    ok, label = classify_exit(code)
    check(f"docs_ratchet: {label}", ok, output)
    return check.failures, check.total
