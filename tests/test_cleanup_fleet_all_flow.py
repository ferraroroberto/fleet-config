"""Control-flow gate for `.claude/workflows/cleanup-fleet-all.js` (fleet-config#518).

The workflow script is JavaScript, so its assertions live in the sibling
`cleanup_fleet_all_flow.mjs`, which loads the real script with stubbed
`agent`/`log`/`phase` and asserts the three properties the 2026-07-30 fleet
collapse violated -- strict lane seriality, teardown on every terminal path,
and halt-on-residue. This file is the thin Python shim that lets
`tests/run_acceptance.py`'s standalone dispatch table own it like any other
suite.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_cleanup_fleet_all_flow.py`
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "cleanup_fleet_all_flow.mjs"


def main() -> int:
    node = shutil.which("node")
    if not node:
        # Honest skip, not a silent pass: the fact could not be established.
        print("test_cleanup_fleet_all_flow: SKIPPED (node not on PATH) -- "
              "the cleanup-fleet-all control-flow properties were NOT verified")
        return 0

    res = subprocess.run(
        [node, str(HARNESS)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stdout.write(res.stderr)
        print("test_cleanup_fleet_all_flow: FAILED")
        return 1
    print("test_cleanup_fleet_all_flow: all checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
