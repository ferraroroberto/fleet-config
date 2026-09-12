"""Shared resolution for the hooks-state base directory (fleet-config#817).

Every `skills/_lib` module that persists advisory state under
`~/.claude/hooks/state/` re-derived this exact `CLAUDE_HOOKS_STATE_DIR`
override lookup independently (`active_issue.py`, `chief_managed.py`,
`quota_snapshot.py`, `rate_gate.py` -- plus four more hook-tree copies fixed
alongside this one, `hooks/_lib.py`'s own `state_dir()`). This is a
correctness contract, not a style point: hermetic acceptance runs depend on
the env var being honoured everywhere, so a ninth caller that re-derives it
by hand risks getting it wrong and writing into live fleet state during a
test run.

Also owns the atomic-write temp naming + age sweep for writers into that
directory (fleet-config#864), mirroring `hooks/_lib.py`'s copy -- the tree
boundary forbids either tier importing the other's.

stdlib only.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

STATE_DIR_ENV_VAR = "CLAUDE_HOOKS_STATE_DIR"

# An abrupt death between mkstemp and os.replace runs no Python handler, so a
# writer's own except-branch unlink never fires and the temp is stranded
# (fleet-config#816). Nothing inside the dead process can clean up, so temps
# are named after their target and expired by age by the next writer instead.
# An hour is orders of magnitude beyond a real write's duration, so the sweep
# cannot race a live writer's temp.
ATOMIC_TMP_SWEEP_AFTER_SECONDS = 3600.0


def state_dir() -> Path:
    """The shared hooks-state base directory, honoring `CLAUDE_HOOKS_STATE_DIR`."""
    root = os.environ.get(STATE_DIR_ENV_VAR)
    return Path(root) if root else Path.home() / ".claude" / "hooks" / "state"


def atomic_tmp_prefix(path: Path) -> str:
    """Writer-identifying `mkstemp` prefix for `path`'s write temporaries.

    An orphan is then `.<target>.<random>.tmp` -- it names the file whose
    writer stranded it, instead of the anonymous `tmp<8>.tmp` that left six
    accumulated temps in `hooks/state/` unattributable (fleet-config#816).
    Pair with `suffix=".tmp"`.
    """
    return f".{path.name}."


def sweep_stale_atomic_temps(path: Path) -> None:
    """Unlink `path`'s abandoned write temporaries, by age.

    The cleanup a hard-killed writer could never do itself: see
    `ATOMIC_TMP_SWEEP_AFTER_SECONDS`. `hooks/state/` is shared by several
    writers, so a name only matches when it is exactly this target's prefix
    plus one `mkstemp` random segment (no dots) plus `.tmp` -- a sibling
    target whose name merely *starts* with this one is never swept. Advisory:
    any failure leaves the orphan for the next writer rather than disturbing
    the write this sweep precedes.
    """
    pattern = re.compile(re.escape(atomic_tmp_prefix(path)) + r"[a-z0-9_]+\.tmp")
    cutoff = time.time() - ATOMIC_TMP_SWEEP_AFTER_SECONDS
    try:
        candidates = [entry for entry in path.parent.iterdir() if pattern.fullmatch(entry.name)]
    except OSError:
        return
    for stale in candidates:
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue
