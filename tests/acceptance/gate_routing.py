"""Diff-based routing for the slow standalone suites (fleet-config#1481).

`tests/test_design_review_browser.py` takes ~34 of the gate's ~40 minutes, so the gate runs it
only when the diff touches the surface it exercises. A suite routed out reports as SKIP --
its own state, never a pass -- and anything that stops the diff being established runs the
suite: uncertainty escalates, it never narrows (same contract as `skills/_lib/e2e_route.py`).
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

from acceptance.shared import REPO

sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402

FORCE_ENV_VAR = "GATE_FULL"

# suite label (standalone_dispatch row) -> repo-relative path prefixes whose change it must re-prove.
# A path matches when it equals a prefix or sits under it.
ROUTED_SURFACES: Dict[str, Tuple[str, ...]] = {
    "design_review_browser": (
        "skills/_lib/design_review/",
        "skills/_lib/design_lint/",
        "tests/fixtures/design_review/",
        "tests/test_design_review_browser.py",
        "tests/_lib/design_review_support.py",
        "tests/acceptance/gate_routing.py",
        "design.rubric.toml",
    ),
}


def changed_paths() -> Optional[List[str]]:
    """Repo-relative paths differing from the merge-base with the default branch: committed,
    staged, unstaged and untracked. None when git cannot say."""
    base = None
    for ref in ("origin/main", "main"):
        r = run_git(["-C", str(REPO), "merge-base", "HEAD", ref])
        if r.returncode == 0 and r.stdout.strip():
            base = r.stdout.strip()
            break
    if base is None:
        return None
    tracked = run_git(["-C", str(REPO), "diff", "--name-only", base])
    untracked = run_git(["-C", str(REPO), "ls-files", "--others", "--exclude-standard"])
    if tracked.returncode != 0 or untracked.returncode != 0:
        return None
    return [p for p in (tracked.stdout + untracked.stdout).splitlines() if p.strip()]


def matches(path: str, prefixes: Tuple[str, ...]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)


def routed_out(label: str) -> Optional[str]:
    """Why the gate should not run suite `label` for this diff, or None to run it."""
    prefixes = ROUTED_SURFACES.get(label)
    if prefixes is None or os.environ.get(FORCE_ENV_VAR) == "1":
        return None
    paths = changed_paths()
    if paths is None:
        return None
    if any(matches(p, prefixes) for p in paths):
        return None
    return f"no file of its surface changed vs the default branch ({len(paths)} changed); {FORCE_ENV_VAR}=1 forces it"
