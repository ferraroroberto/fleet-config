"""Foreground blocking wait for a sentinel file.

Replaces a model-composed `Monitor` until-loop for `/audit-fleet` step 2's
sweep wait (fleet-config#609, reopened). The prior design told the
orchestrator to drive `Monitor` with a shell until-loop; in the observed
failure the run invoked `Monitor`, it returned in about a second instead of
actually blocking on the sentinel, and the model then narrated "I'll wait for
the sentinel file notification" and ended its turn -- under `claude -p`
nothing ever wakes it back up, so the CLI exited 0 having delivered no digest.
Prose already forbade ending the turn to "wait for it" (fleet-config#314,
#519) and the model quoted that rule before failing anyway, so this removes
the model's role in composing the wait rather than asking it to compose the
wait more carefully: it blocks synchronously in a real sleep loop inside one
tool call and returns only with an unambiguous, machine-checkable answer.

A single Bash tool call caps out at 600s, under the sweep's historic worst
case (1460s+), so the default timeout stays comfortably below that ceiling
and SKILL.md re-invokes this exact command on exit 2 -- the retry is the
intended use, not something to avoid.

CLI
---
  wait_for_sentinel.py --path <sentinel> [--timeout-seconds 560] [--poll-interval-seconds 5]

Exit 0, prints `SENTINEL-READY <path>`, when the file exists.
Exit 2, prints `SENTINEL-NOT-READY <path>`, when the timeout elapses first.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

NOT_READY_EXIT_CODE = 2


def wait_for_sentinel(
    path: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """Block until `path` exists or `timeout_seconds` elapses.

    Returns whether the sentinel was found. Injectable `sleep`/`clock` keep
    this unit-testable without a real wall-clock wait.
    """
    deadline = clock() + timeout_seconds
    while True:
        if path.exists():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(poll_interval_seconds, remaining))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", required=True, type=Path)
    ap.add_argument(
        "--timeout-seconds", type=float, default=560.0,
        help="stay comfortably under the Bash tool's 600s ceiling for one call",
    )
    ap.add_argument("--poll-interval-seconds", type=float, default=5.0)
    args = ap.parse_args(argv)

    found = wait_for_sentinel(args.path, args.timeout_seconds, args.poll_interval_seconds)
    if found:
        print(f"SENTINEL-READY {args.path}")
        return 0
    print(f"SENTINEL-NOT-READY {args.path}")
    return NOT_READY_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
