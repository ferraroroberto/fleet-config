"""Re-classify the open `needs-validation` list under the current rule.

A block records why an issue was put on the owner's list. This module re-runs the
rule over each such issue and keeps it only while a reason still fires. Three
kinds of recorded reason are never re-derived, so they keep the item: what the lane
said about its own verification (history cannot re-establish it), and any text a
person wrote by hand. Everything else is recomputed from the issue and its PR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from owner_check import backfill, rule

KEEP = "keep"
CLEAR = "clear"
UNKNOWN = "unknown"

# Recorded reasons the rule produces from the issue or the diff, by their opening words.
_RULE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("the issue says Owner validation", "explicit"),
    ("needs a real device or live input", "device"),
    ("changes a live-input path", "device"),
    ("involves an account only the owner holds", "own-sign-in"),
    ("involves a third-party account", "account"),
    ("adds a new user-facing surface", "surface"),
    ("adds a new page or view", "surface"),
)
# What the lane said about verifying: a fact of that run, kept as recorded.
_LANE_PREFIXES = ("the lane did not state", "the lane could not verify")

# One public-safe phrase per reason that stopped firing.
DROPPED_WORDS = {
    "own-sign-in": "an app's own sign-in is not a third-party account",
    "surface": "a vendored file, a helper script or a proofed view is not a new surface",
    "backfill": "back-fill-only entry",
    "device": "no live-input reason any more",
    "explicit": "the issue no longer says Owner validation: required",
    "account": "no third-party account involved",
}


@dataclass
class Verdict:
    state: str
    kept: List[str]
    dropped: List[str]
    why: str = ""


def _pr_number(proof: Sequence[str]) -> Optional[int]:
    for link in proof:
        m = re.search(r"/pull/(\d+)", link)
        if m:
            return int(m.group(1))
    return None


def _split(reason: str) -> List[str]:
    return [part.strip() for part in reason.split("; ") if part.strip()]


def classify_recorded(part: str) -> str:
    """`rule:<key>`, `lane`, `backfill` or `hand` for one recorded reason."""
    if part == backfill.BACKFILL_REASON:
        return "backfill"
    for prefix, key in _RULE_PREFIXES:
        if part.startswith(prefix):
            return f"rule:{key}"
    if part.startswith(_LANE_PREFIXES):
        return "lane"
    return "hand"


def reclassify(
    item: dict,
    issue: dict,
    pr_files: Callable[[int], List[Tuple[str, str]]],
    pr_body: Callable[[int], str],
) -> Verdict:
    """Keep or clear one `validate-after` item. `issue` is `github.fetch_issue`'s dict."""
    parts = _split(item.get("reason") or "")
    if not item.get("block") or not parts:
        return Verdict(UNKNOWN, [], [], "no recorded reason to re-check")
    recorded = [(part, classify_recorded(part)) for part in parts]
    kept = [part for part, kind in recorded if kind in ("lane", "hand")]
    pr = _pr_number(item.get("proof") or [])
    fired: List[str] = []
    if pr is None:
        if any(kind.startswith("rule:") for _part, kind in recorded):
            return Verdict(UNKNOWN, kept, [], "no PR link to re-read the diff")
    else:
        decision = rule.decide(issue["labels"], issue["title"], issue["body"], pr_files(pr),
                               rule.VERIFIED_NA, rule.ux_proven(pr_body(pr)))
        fired = list(decision.reasons)
    now = kept + fired
    still = {classify_recorded(r).split(":", 1)[-1] for r in fired}
    dropped = sorted({kind.split(":", 1)[-1] for _part, kind in recorded
                      if (kind == "backfill" or kind.startswith("rule:"))
                      and kind.split(":", 1)[-1] not in still})
    return Verdict(KEEP if now else CLEAR, now, dropped)
