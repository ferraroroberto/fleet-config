"""Shared preamble of the browser leg of the design-review suite: import paths, the harness, the throwaway hooks
state and the sibling interpreter the fixture walks run through.

Imported first by every part and by the entry (the script's own folder is on `sys.path`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

from design_review import capture, plan  # noqa: E402

sys.path.insert(0, str(REPO / "skills" / "_lib" / "design_review"))

_h = CheckHarness()
check = _h.check

# Every hooks-state write in this file lands in a throwaway dir, never in the live one.
STATE = throwaway_dir("design-review-test-state-")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)

# ---- browser leg: the static fixture page through a sibling venv --------------

FLEET = REPO.parent


def _walk_failure(screen: object) -> str | None:
    """Why a fixture walk's screen cannot be read, or None when it carries metrics (fleet-config#1447).

    A walk that failed or timed out hands back no screen, an `{"error": ...}` stub, or a screen whose
    `metrics` is None. A leg that indexed `screen["metrics"]` raised TypeError there; it names the walk's
    error in one FAIL line instead, and never reads a missing walk as a pass."""
    if not isinstance(screen, dict) or not screen:
        return "no screen came back from the walk"
    if "error" in screen and "metrics" not in screen:
        return f"walk error: {screen['error']}"
    if not isinstance(screen.get("metrics"), dict):
        return f"no metrics (status={screen.get('status')} reason={screen.get('reason')} error={screen.get('error')})"
    return None


def _walk_run(argv: list, **kw) -> "subprocess.CompletedProcess":
    """`subprocess.run` for a fixture walk, where a timeout is a failed walk (rc 124 + stderr), not a raised TimeoutExpired (fleet-config#1447)."""
    try:
        return subprocess.run(argv, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, stdout="", stderr=f"walk timed out after {kw.get('timeout')}s")


interp = None
for sibling in ("project-scaffolding", "app-launcher"):
    cand = capture.resolve_interpreter(plan.Target(name=sibling, root=FLEET / sibling, base_url="file:///"))
    if cand["status"] == "ok":
        interp = cand["python"]
        break
scaffold = FLEET / "project-scaffolding"


