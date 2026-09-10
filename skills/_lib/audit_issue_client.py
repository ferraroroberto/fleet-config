"""Shared subprocess wrapper for shelling out to `audit_issue.py` (fleet-config#817).

`.claude/skills/context-purge/gate.py`, `digest.py`, and
`.claude/skills/learning-log/gather.py` each drive `audit_issue.py`'s full
`get`/`upsert` CLI as a subprocess rather than importing it in-process (unlike
`digest_delivery.py`'s direct `import audit_issue` for a few private helpers) --
the same `sys.executable <script> <args>` + `NO_WINDOW` idiom every other
cross-repo `_lib` caller uses. Before this module existed, the exact same
9-line wrapper was hand-rolled independently in `gate.py` and `digest.py`, plus
a weaker near-copy (`gather.py`'s `read_ledger_body`) that swallowed the same
failure instead of raising. One implementation now, callers keep their own
error-handling shape around it.

stdlib + `no_window.NO_WINDOW` only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

DEFAULT_TIMEOUT = 120.0


def run_audit_issue(audit_issue_path: Path, *args: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run `audit_issue.py <args>` and return stdout; raise `RuntimeError` on
    a non-zero exit. `audit_issue_path` is passed explicitly rather than
    resolved here -- each caller already computes it repo-relative to itself
    (fleet-config#502), which matters from a linked `<repo>-wt-N` worktree."""
    res = subprocess.run(
        [sys.executable, str(audit_issue_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(f"audit_issue.py {args[0]} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout
