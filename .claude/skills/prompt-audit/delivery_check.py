"""Post-condition for the scheduled `/prompt-audit` job (fleet-config#834).

`claude_progress.py --delivery-check` runs this after the child exits, whatever
the child's exit code was, and turns a non-zero exit here into
`DELIVERY_NOT_CONFIRMED_EXIT_CODE`. A `/prompt-audit` run's delivered artifact
is one digest comment on the audit-managed `prompt-audit ledger` issue
(`kind=prompt-audit`) in `ferraroroberto/fleet-config`, carrying the stamp
`audit.py digest` writes:

    <!-- prompt-audit-digest run=<date> status=... scan=... update-issue=... -->

**Strict, with no flag to forget** -- the adapter invokes this bare. Exit 0
needs all of:

  1. a digest comment inside the window         -> the run delivered at all
  2. `status=complete`                           -> every planned file judged
  3. `scan=posted` or `update-issue=#N`          -> a scan was posted, or the
                                                    rule-set update issue was
                                                    (update mode is a success)

A dry run (`scan=dry-run`) posts no comment, so it fails (1) unless an earlier
real run's digest is still inside the window. No stamp, no fields, or an
unreadable ledger are "not confirmed", never a pass.

The shared predicate lives in `skills/_lib/digest_delivery.py`; messages stay
pure ASCII (stdout is a pipe). Behaviour pinned by
`tests/test_delivery_check_contract.py`.

Usage: delivery_check.py [--max-age-hours N]   (default 12)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

# Repo-relative (fleet-config#502), matching audit.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import digest_delivery  # noqa: E402

REPO = "ferraroroberto/fleet-config"
KIND = "prompt-audit"
LEDGER_TITLE = "prompt-audit ledger"
STAMP_PREFIX = "prompt-audit-digest"

_ISSUE_REF = re.compile(r"^(#\d+|https://github\.com/\S+/issues/\d+)$")


def delivered(stamp: dict) -> Optional[str]:
    """None when the stamp proves a scan or an update issue was delivered."""
    scan = stamp.get("scan")
    update = stamp.get("update-issue")
    if scan == "posted":
        return None
    if update and _ISSUE_REF.match(update):
        return None
    return (f"reports scan={scan or 'unknown'} update-issue={update or 'unknown'} -- "
            f"neither a posted scan nor a filed rule-set update issue")


def main(argv: list[str] | None = None) -> int:
    return digest_delivery.main_for(
        REPO, KIND, LEDGER_TITLE,
        description="Did this /prompt-audit run deliver a complete digest?",
        require_complete=True,
        stamp_prefix=STAMP_PREFIX,
        delivered=delivered,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
