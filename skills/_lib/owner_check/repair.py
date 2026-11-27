"""Repair: write the missing owner-check note for a label that carries none.

A `needs-validation` or `needs-owner` label with no readable block of its own kind
renders on the Board as "Details unknown". The label can arrive without a note by
hand, from an older flow, or from a write that died between its two calls; this
builds the note from what the issue and its closing PRs already say, so the owner
sees what to try. Only unticked checklist lines are quoted, from the same issue
and its own PRs, so nothing reaches a comment that the issue did not already hold.
A repaired note never has `run`/`expect`: a command nobody recorded is not invented,
so the item stays `no-run` until a lane writes the step with `amend`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from owner_check import backfill, block, github

REPAIR_REASON = "Labelled for an owner check with no note on record"

_UNTICKED = re.compile(r"^\s*[-*] \[ \] +(\S.*?)\s*$")
_PR_URL = re.compile(r"/pull/(\d+)$")
_MAX_ITEMS = 3
_MAX_ITEM_CHARS = 160


def unticked(text: str) -> List[str]:
    """The unticked `- [ ]` checklist lines of a markdown body, in order, one line each."""
    return [m.group(1) for line in (text or "").splitlines() if (m := _UNTICKED.match(line))]


def _clip(item: str) -> str:
    return item if len(item) <= _MAX_ITEM_CHARS else item[: _MAX_ITEM_CHARS - 1].rstrip() + "…"


def build_try(kind: str, bodies: Iterable[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """`(try text, source)` for a note, or None when no honest one can be built.

    `bodies` are `(source, text)` pairs, the issue first then each closing PR; the
    first with an unticked item supplies the text. A validate-after note falls back
    to the generic "use it once" line; a before-start step must name a concrete
    owner action, so with nothing usable it has no note rather than an invented one.
    """
    for source, body in bodies:
        items = unticked(body)
        if items:
            text = "; ".join(_clip(i) for i in items[:_MAX_ITEMS])
            if kind == block.BEFORE_START and block.vague_action(text):
                continue
            return text, source
    if kind == block.VALIDATE_AFTER:
        return backfill.BACKFILL_TRY, "generic"
    return None


def repair_one(item: Dict[str, Any], apply: bool, today: str) -> str:
    """One unreadable item's outcome line: planned, repaired, unbuildable or skipped.

    A written note is copied into `item`, so the caller's list shows it at once.
    """
    repo, number, kind = item["repo"], item["number"], item["kind"]
    issue = github.fetch_issue(repo, number)
    latest = block.latest_block(issue["comments"]) or {}
    if block.LABELS[kind] not in issue["labels"] or latest.get("kind") == kind:
        return f"REPAIR skipped {item['ref']} reason=already readable or no longer labelled"
    bodies = [("issue", issue["body"])]
    for url in issue["prs"]:
        m = _PR_URL.search(url)
        if m:
            bodies.append(("pr", github.pr_body(repo, int(m.group(1)))))
    built = build_try(kind, bodies)
    if built is None:
        return f"REPAIR unbuildable {item['ref']} reason=a {kind} note needs a concrete owner action and none is on the issue"
    try_text, source = built
    if apply:
        github.note(repo, number, block.format_block(kind, REPAIR_REASON, try_text, issue["prs"], today))
        item.update({"block": True, "reason": REPAIR_REASON, "try": try_text, "proof": list(issue["prs"]),
                     "since": today, "note": github.NOTE_NO_RUN})
    return f"REPAIR {'repaired' if apply else 'planned'} {item['ref']} source={source}"


def heal(items: List[Dict[str, Any]], today: str) -> List[str]:
    """Write every missing note in `items`, in place; returns the outcome lines.

    An item that cannot be repaired keeps its own `note` state with the reason in
    `note_problem`, so a reader never shows it as merely unknown details.
    """
    states = {"unbuildable": github.NOTE_UNREPAIRABLE, "unknown": github.NOTE_UNKNOWN}
    lines = []
    for item in items:
        if item["block"]:
            continue
        try:
            line = repair_one(item, True, today)
        except github.GhError as exc:
            line = f"REPAIR unknown {item['ref']} reason={exc}"
        word = line.split()[1]
        if word in states:
            item["note"], item["note_problem"] = states[word], line.partition(" reason=")[2]
        lines.append(line)
    return lines
