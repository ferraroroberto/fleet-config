"""Post-condition for the scheduled `/context-purge fleet` job (fleet-config#627).

`claude_progress.py --delivery-check` runs this after the child exits, whatever
the child's exit code was, and turns a non-zero exit here into
`DELIVERY_NOT_CONFIRMED_EXIT_CODE`. A `/context-purge fleet` run's delivered
artifact is one digest comment on the managed ledger issue in
`ferraroroberto/fleet-config`, carrying the machine-readable stamp
`digest.py` writes.

**Strict by default, and deliberately not behind a flag.** The adapter invokes
this as `[sys.executable, script]` with no arguments, so strictness that
depended on a CLI flag would be silently absent in the one place it matters.
Bare invocation therefore requires all three facts:

  1. a digest comment inside the window          -> the run delivered at all
  2. `status=complete`                            -> it reached every repo
  3. `slack=posted`                               -> the ping actually landed

(2) is what makes a deliberately-failed partial run exit non-zero while still
publishing a digest that names the unreached repos -- the issue's fourth
acceptance criterion, discharged by this one post-condition rather than a
second mechanism.

(3) exists because `hooks/notify_send.py` never raises and reports failure as
a return value. Without consulting the stamp, a landed digest comment plus a
silently failed Slack post would read as complete success. `slack=unknown` is
treated as not confirmed, never as a pass.

Exit 0 = a complete run delivered a digest and pinged it. Non-zero = it did
not, or the question could not be answered; both are "not confirmed", which is
the whole reason this exists.

The shared predicate lives in `skills/_lib/digest_delivery.py` (fleet-config#627),
with `/audit-fleet`'s lenient check. Behaviour pinned by
`tests/test_delivery_check_contract.py`.

Usage: delivery_check.py [--max-age-hours N]   (default 12)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo-relative (fleet-config#502), matching gate.py / check.py / digest.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import digest_delivery  # noqa: E402

REPO = "ferraroroberto/fleet-config"
KIND = "context-purge-digest"
LEDGER_TITLE = "[ledger] context-purge run digests (machine-managed, not actionable)"
STAMP_PREFIX = "context-purge-digest"


def main(argv: list[str] | None = None) -> int:
    return digest_delivery.main_for(
        REPO, KIND, LEDGER_TITLE,
        description="Did this /context-purge run deliver and ping a digest?",
        require_complete=True,
        stamp_prefix=STAMP_PREFIX,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
