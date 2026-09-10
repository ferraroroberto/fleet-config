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

stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

STATE_DIR_ENV_VAR = "CLAUDE_HOOKS_STATE_DIR"


def state_dir() -> Path:
    """The shared hooks-state base directory, honoring `CLAUDE_HOOKS_STATE_DIR`."""
    root = os.environ.get(STATE_DIR_ENV_VAR)
    return Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
