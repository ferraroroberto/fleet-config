"""Post-condition for the scheduled `/audit-fleet` job (fleet-config#560).

`claude_progress.py --delivery-check` runs this after the child exits, whatever
the child's exit code was, and turns a non-zero exit here into
`DELIVERY_NOT_CONFIRMED_EXIT_CODE`. The point is to check the fact that
actually matters -- did this run *deliver* -- rather than another symptom of a
run that didn't. On 2026-08-06 the job recorded `success` / exit 0 while
auditing zero repos, filing zero issues and posting no digest; the newest
comment on the digest ledger was a week old, which is exactly what this asks.

The delivered artifact of an `/audit-fleet` run is one digest comment on the
`audit-fleet digest state` issue in `ferraroroberto/fleet-config` (SKILL.md
step 6). The issue number is resolved through `audit_issue.plan`, never
hardcoded -- the skill's own text forbids a hardcoded id, and a check that
drifts from the skill it guards is worse than none.

Exit 0 = a digest comment landed inside the window. Non-zero = it did not, or
the question could not be answered at all; both are "not confirmed", which is
the whole reason this exists.

The predicate itself now lives in `skills/_lib/digest_delivery.py`, shared with
`/context-purge`'s check (fleet-config#627) -- two ~100-line near-copies of the
rule "an unestablished delivery is not a delivery" were free to drift on
exactly the property that matters. This caller stays **lenient**: a dated
comment inside the window is enough, and the digest stamp `/context-purge`
writes is none of its business. Its observable behaviour is unchanged, pinned
by `tests/test_delivery_check_contract.py`.

Usage: delivery_check.py [--max-age-hours N]   (default 12)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo-relative (fleet-config#502), matching gate.py / check.py: the absolute
# form would load whatever lives in the primary checkout, so a run from one of
# this repo's own <repo>-wt-N worktrees would silently use main's helper rather
# than the one it is being tested against.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import digest_delivery  # noqa: E402

REPO = "ferraroroberto/fleet-config"
KIND = "digest"
LEDGER_TITLE = "audit-fleet digest state"
DEFAULT_MAX_AGE_HOURS = digest_delivery.DEFAULT_MAX_AGE_HOURS

# Re-exported so the contract test can drive this module's real entry point.
newest_comment_age_hours = digest_delivery.newest_comment_age_hours


def main(argv: list[str] | None = None) -> int:
    return digest_delivery.main_for(
        REPO, KIND, LEDGER_TITLE,
        description="Did this /audit-fleet run deliver a digest?",
        default_max_age_hours=DEFAULT_MAX_AGE_HOURS,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
