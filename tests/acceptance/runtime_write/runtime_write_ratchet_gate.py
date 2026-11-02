"""Runtime write ratchet stage.

Runs the vendored `scripts/runtime_write_ratchet.py` against this repo in its two
halves:

- the static check (`_runtime_write_ratchet_check`) fails when a tracked Python
  file gains a write target whose path resolves under a code folder (`hooks`,
  `skills`, `scripts`, `.claude`), or a new one it cannot resolve, beyond the
  committed `.runtime-write-ratchet-baseline.json`;
- the runtime bracket: `_runtime_write_snapshot_check` records the untracked and
  ignored files under the code folders before the first acceptance check, and
  `_runtime_write_probe_check`, after the last, fails on any file the run added
  there.

Deliberately its own checks, not rows in `standalone_dispatch`, for the reason
`static_ratchet_gate` gives: `shared._subprocess_unit_check` reads exit
`shared.SKIP_EXIT` (3) as SKIP, and 3 is what the ratchet exits when it could not
establish a target or list the tree. Here 3 fails as NOT CONFIRMED, as does every
other non-zero exit. A probe without its snapshot is exit 3 too, so a snapshot
that failed can never let the probe pass.

Never path-routed: the bracket has to span the whole run to see what it wrote.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple

from acceptance.shared import REPO, run_ratchet_script

SCRIPT = REPO / "scripts" / "runtime_write_ratchet.py"

# The snapshot sits outside the tree so it is never itself a runtime file in a
# code folder; the pid keeps two concurrent runs from sharing one.
SNAPSHOT = Path(tempfile.gettempdir()) / f"fleet-config-runtime-write-snapshot-{os.getpid()}.json"

# A few seconds over this tree. The bound only turns a hung child into NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_NOT_CONFIRMED = "NOT CONFIRMED -- the facts could not be established, so nothing was checked"


def classify_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for one static-check exit code. Only 0 passes; 3 is its own
    failing state and never reads as a pass or a skip."""
    labels = {
        0: "no write target in a code folder above its baseline",
        1: "a write target in a code folder rose above its baseline (or the baseline is missing)",
        2: "bad [runtime_write_ratchet] table, baseline or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def classify_snapshot_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for the pre-run `--snapshot`."""
    labels = {
        0: "runtime files under the code folders recorded before the run",
        2: "bad [runtime_write_ratchet] table or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def classify_probe_exit(code: int) -> Tuple[bool, str]:
    """`(ok, label)` for the post-run `--probe`."""
    labels = {
        0: "the run left no runtime file under a code folder",
        1: "the run left a runtime file under a code folder",
        2: "bad [runtime_write_ratchet] table or arguments",
        3: _NOT_CONFIRMED,
    }
    return code == 0, labels.get(code, f"unexpected exit {code}")


def _runtime_write_ratchet_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_exit)


def _runtime_write_snapshot_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_snapshot_exit, ("--snapshot", str(SNAPSHOT)))


def _runtime_write_probe_check() -> Tuple[int, int]:
    return run_ratchet_script(SCRIPT, RUN_TIMEOUT_S, classify_probe_exit, ("--probe", str(SNAPSHOT)))
