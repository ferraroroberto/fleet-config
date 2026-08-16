"""Shared post-condition for scheduled skills that deliver a digest.

`claude_progress.py --delivery-check` runs a script like this after the child
exits, whatever the child's exit code was, and turns a non-zero exit here into
`DELIVERY_NOT_CONFIRMED_EXIT_CODE`. The point is to check the fact that
actually matters -- did this run *deliver* -- rather than another symptom of a
run that didn't. On 2026-08-06 `/audit-fleet` recorded `success` / exit 0 while
auditing zero repos, filing zero issues and posting no digest.

Two skills now ask that question (`/audit-fleet`, `/context-purge`), so the
predicate lives here once rather than in two ~100-line near-copies free to
drift (fleet-config#627). The rule this module exists to hold is narrow and
absolute:

    a delivery that cannot be ESTABLISHED is not a delivery.

An unreadable ledger, a missing issue, an undateable comment and a stamp that
never appeared are all "unknown", and unknown exits non-zero -- never folded
into the passing state. That is the same rule the fleet's health checks follow,
and it is exactly what `#560` violated.

Two strictness levels, because the callers genuinely differ:

* **lenient** (`/audit-fleet`) -- a dated comment inside the window is enough.
* **strict** (`/context-purge`, `require_complete=True`) -- the comment must
  also carry a machine-readable stamp saying the run completed and that the
  Slack post landed. `slack_notify.py` never raises and returns exit 1 on
  failure, so without consulting the stamp a successful digest comment plus a
  silently failed Slack post would read as full success.

Strictness is a **constructor argument, not a CLI flag**, on purpose:
`claude_progress.run_delivery_check` invokes the script as
`[sys.executable, script]` with no arguments, so any strictness that depended
on a flag would be silently absent in the one place it matters.

Every message here is deliberately pure ASCII: these run with stdout on a pipe,
where Windows falls back to cp1252 and one em dash would raise
`UnicodeEncodeError` mid-check -- turning the post-condition into a crash, i.e.
back into "delivery unknown" for a reason that has nothing to do with the
question being asked.

stdlib + `audit_issue.py` (which owns the `gh` shell-out) only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os  # noqa: F401 - re-exported for test fixtures
import re
import shutil  # noqa: F401 - re-exported for test fixtures
import sys
import tempfile  # noqa: F401 - re-exported for test fixtures
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_issue  # noqa: E402

DEFAULT_MAX_AGE_HOURS = 12.0

# `<!-- <prefix> run=... status=... unreached=N slack=... -->`
_STAMP_FIELD_RE = re.compile(r"([a-z_]+)=([^\s>]+)")


def _stamp_re(prefix: str) -> re.Pattern[str]:
    return re.compile(r"<!--\s*" + re.escape(prefix) + r"\s+([^>]*?)-->")


# ---- pure helpers (unit-tested without gh) ---------------------------------

def newest_comment_age_hours(comments: Sequence[dict], now: dt.datetime) -> Optional[float]:
    """Hours since the newest comment, or None if none has a readable date.

    None is a real answer -- "there is nothing here to date" -- and every
    caller reports it as unconfirmed rather than guessing in either direction.
    It must never be allowed to compare as a small number.
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


def newest_dated_comment(
    comments: Sequence[dict], now: dt.datetime
) -> tuple[Optional[float], Optional[dict]]:
    """(age_hours, comment) for the newest dated comment; (None, None) if none."""
    best: tuple[Optional[float], Optional[dict]] = (None, None)
    for comment in comments or []:
        raw = (comment or {}).get("createdAt")
        if not raw:
            continue
        try:
            created = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        age = (now - created).total_seconds() / 3600.0
        if best[0] is None or age < best[0]:
            best = (age, comment)
    return best


def parse_stamp(body: str, prefix: str = "context-purge-digest") -> dict[str, str]:
    """Fields of the machine-readable digest stamp; {} when absent.

    {} means "the run did not say", which the strict caller treats as unknown
    -- never as a pass.
    """
    match = _stamp_re(prefix).search(body or "")
    if not match:
        return {}
    return dict(_STAMP_FIELD_RE.findall(match.group(1)))


@dataclass(frozen=True)
class Verdict:
    """One outcome. ``confirmed`` is true for exactly one ``state``."""

    confirmed: bool
    state: str
    detail: str

    @property
    def exit_code(self) -> int:
        return 0 if self.confirmed else 1


def classify(
    comments: Sequence[dict],
    now: dt.datetime,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    *,
    require_complete: bool = False,
    stamp_prefix: str = "context-purge-digest",
    repo: str = "",
    number: object = "",
) -> Verdict:
    """Decide whether these comments prove a digest was delivered.

    States: ``confirmed`` | ``not-delivered`` (a digest, but too old) |
    ``unestablished`` (the question could not be answered) | ``partial`` |
    ``slack-unconfirmed``. Only ``confirmed`` is truthy.
    """
    where = f"{repo}#{number}" if repo else "the ledger"
    age, comment = newest_dated_comment(comments, now)

    if age is None:
        return Verdict(False, "unestablished",
                       f"{where} has no dated comments -- no digest has ever been delivered")
    if age > max_age_hours:
        return Verdict(
            False, "not-delivered",
            f"newest digest comment on {where} is {age:.1f}h old "
            f"(> {max_age_hours:.0f}h) -- this run delivered no digest")

    if not require_complete:
        return Verdict(True, "confirmed", f"digest comment on {where} is {age:.1f}h old")

    stamp = parse_stamp((comment or {}).get("body") or "", stamp_prefix)
    if not stamp:
        return Verdict(
            False, "unestablished",
            f"digest comment on {where} is {age:.1f}h old but carries no "
            f"'{stamp_prefix}' stamp -- run status could not be established")

    status = stamp.get("status")
    if status != "complete":
        unreached = stamp.get("unreached", "?")
        return Verdict(
            False, "partial",
            f"digest on {where} is marked status={status or 'unknown'} "
            f"({unreached} repo(s) unreached) -- the run did not complete")

    slack = stamp.get("slack")
    if slack != "posted":
        return Verdict(
            False, "slack-unconfirmed",
            f"digest on {where} reports slack={slack or 'unknown'} -- the Slack "
            f"post was not confirmed, so this run did not fully deliver")

    return Verdict(True, "confirmed",
                   f"digest comment on {where} is {age:.1f}h old (status=complete, slack=posted)")


# ---- gh-backed check --------------------------------------------------------

def check_delivery(
    repo: str,
    kind: str,
    ledger_title: str,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    require_complete: bool = False,
    stamp_prefix: str = "context-purge-digest",
    now: Optional[dt.datetime] = None,
) -> Verdict:
    """Read the managed ledger issue and classify this run's delivery.

    The issue number is resolved through ``audit_issue.plan``, never hardcoded
    -- a check that drifts from the skill it guards is worse than none. Every
    read failure resolves to ``unestablished``, not to a pass.
    """
    now = now or dt.datetime.now(dt.timezone.utc)

    try:
        issues = audit_issue._list_open(repo)
        keep, _ = audit_issue.plan(issues, kind)
    except SystemExit as exc:
        return Verdict(False, "unestablished", f"could not read {repo} issues: {exc}")
    if keep is None:
        return Verdict(
            False, "unestablished",
            f"no '{ledger_title}' ledger issue in {repo} -- nothing could have been delivered")

    try:
        raw = audit_issue.gh(["issue", "view", str(keep), "--repo", repo, "--json", "comments"])
        comments = json.loads(raw or "{}").get("comments", [])
    except (SystemExit, ValueError) as exc:
        return Verdict(False, "unestablished",
                       f"could not read comments on {repo}#{keep}: {exc}")

    return classify(
        comments, now, max_age_hours,
        require_complete=require_complete, stamp_prefix=stamp_prefix,
        repo=repo, number=keep,
    )


def main_for(
    repo: str,
    kind: str,
    ledger_title: str,
    *,
    description: str,
    require_complete: bool = False,
    stamp_prefix: str = "context-purge-digest",
    default_max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    argv: Optional[list[str]] = None,
) -> int:
    """CLI body for a caller script. Exit 0 only on a confirmed delivery.

    Correct when invoked bare, because that is how the adapter invokes it.
    """
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--max-age-hours", type=float, default=default_max_age_hours)
    args = ap.parse_args(argv)

    verdict = check_delivery(
        repo, kind, ledger_title,
        max_age_hours=args.max_age_hours,
        require_complete=require_complete,
        stamp_prefix=stamp_prefix,
    )
    print(verdict.detail)
    return verdict.exit_code
