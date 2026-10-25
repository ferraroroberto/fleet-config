"""Shared preamble of the worktree_claim suites: state isolation, import paths and the harness.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`). The
state-dir redirect runs before `worktree_claim` is imported, so no part can write port
reservations into live fleet state.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# The port registry `copy_runtime_config` now reserves into lives under
# `hooks_state.state_dir()` (fleet-config#937), and `run_acceptance.py` runs
# this file as a bare subprocess with the ambient environment -- `hook_env`'s
# isolation only covers hook payload checks. Redirect it before the import, or
# a gate run writes reservations into live fleet state.
os.environ["CLAUDE_HOOKS_STATE_DIR"] = tempfile.mkdtemp(prefix="wc-state-")
# The gate points fleet-list readers at the example; this suite declares its own
# temp registry trees, which the default read must reach.
os.environ.pop("FLEET_PROJECTS_TOML", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "_lib"))
from worktree import service_probe as sp  # noqa: E402
import worktree_claim as wc  # noqa: E402
from worktree import claim as wclaim, gitops as wgit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


__all__ = ['_h', 'check', 'sp', 'wc', 'wclaim', 'wgit']
