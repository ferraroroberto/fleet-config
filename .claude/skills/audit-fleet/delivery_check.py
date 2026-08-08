"""Post-condition for the scheduled `/audit-fleet` job (fleet-config#560).

`claude_progress.py --delivery-check` runs this after the child exits, whatever
the child's exit code was, and turns a non-zero exit here into
`DELIVERY_NOT_CONFIRMED_EXIT_CODE`. The point is to check the fact that
actually matters — did this run *deliver* — rather than another symptom of a
run that didn't. On 2026-08-06 the job recorded `success` / exit 0 while
auditing zero repos, filing zero issues and posting no digest; the newest
comment on the digest ledger was a week old, which is exactly what this asks.

The delivered artifact of an `/audit-fleet` run is one digest comment on the
`audit-fleet digest state` issue in `ferraroroberto/fleet-config` (SKILL.md
step 6). The issue number is resolved through `audit_issue.plan`, never
hardcoded — the skill's own text forbids a hardcoded id, and a check that
drifts from the skill it guards is worse than none.

Exit 0 = a digest comment landed inside the window. Non-zero = it did not, or
the question could not be answered at all; both are "not confirmed", which is
the whole reason this exists.

Usage: delivery_check.py [--max-age-hours N]   (default 12)

Its printed messages are deliberately pure ASCII: this runs with stdout on a
pipe, where Windows falls back to cp1252 and one em dash would raise
`UnicodeEncodeError` mid-check — turning the post-condition into a crash, i.e.
back into "delivery unknown" for a reason that has nothing to do with the
question being asked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("E:/automation/fleet-config/skills/_lib")))
import audit_issue  # noqa: E402

REPO = "ferraroroberto/fleet-config"
DEFAULT_MAX_AGE_HOURS = 12.0


def newest_comment_age_hours(comments: list[dict], now: dt.datetime) -> float | None:
    """Hours since the newest comment, or None if none has a readable date.

    None is a real answer -- "there is nothing here to date" -- and the caller
    reports it as unconfirmed rather than guessing in either direction.
    """
    ages = []
    for comment in comments or []:
        raw = (comment or {}).get("createdAt")
        if not raw:
            continue
        try:
            created = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        ages.append((now - created).total_seconds() / 3600.0)
    return min(ages) if ages else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Did this /audit-fleet run deliver a digest?")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    args = ap.parse_args(argv)

    try:
        issues = audit_issue._list_open(REPO)
        keep, _ = audit_issue.plan(issues, "digest")
    except SystemExit as exc:
        print(f"could not read {REPO} issues: {exc}")
        return 1
    if keep is None:
        print(f"no 'audit-fleet digest state' ledger issue in {REPO} -- nothing could have been delivered")
        return 1

    try:
        raw = audit_issue.gh(["issue", "view", str(keep), "--repo", REPO, "--json", "comments"])
        comments = json.loads(raw or "{}").get("comments", [])
    except (SystemExit, ValueError) as exc:
        print(f"could not read comments on {REPO}#{keep}: {exc}")
        return 1

    age = newest_comment_age_hours(comments, dt.datetime.now(dt.timezone.utc))
    if age is None:
        print(f"{REPO}#{keep} has no dated comments -- no digest has ever been delivered")
        return 1
    if age > args.max_age_hours:
        print(f"newest digest comment on {REPO}#{keep} is {age:.1f}h old "
              f"(> {args.max_age_hours:.0f}h) -- this run delivered no digest")
        return 1
    print(f"digest comment on {REPO}#{keep} is {age:.1f}h old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
