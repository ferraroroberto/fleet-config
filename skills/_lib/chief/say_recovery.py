"""`say --verify`'s recovery from a transient transport failure, without double-sending.

A steer is an unsigned paste: a resent `/issue-finish` can run a merge twice. So a resend is
allowed only when the session transcript shows the exact steer text is not there yet, and the
transcript is read before every further POST. Plain `say` never comes through here.
"""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict, Optional

from chief.steer_delivery import format_verdict_line

from . import launcher

# What the transcript says about a steer: a copy of it is there (`PRESENT`), the transcript
# was read and holds none (`ABSENT`), or it could not be read (`UNREADABLE`).
PRESENT = "present"
ABSENT = "absent"
UNREADABLE = "unreadable"

# A transcript entry keeps only the head of a long user turn, so a steer longer than this is
# matched by its head and a shorter one by its whole text.
MATCH_HEAD_CHARS = 80

# Why a verdict line says a steer is `pending` or `unknown` after every POST failed on transport.
SEND_FAILED_REASONS = {
    "pending": (
        "every send attempt failed on transport and the transcript shows no copy "
        "of the steer; delivery is neither confirmed nor disproved; not resent "
        "further, operator decides"
    ),
    "unknown": (
        "every send attempt failed on transport and the transcript could not be "
        "read, so a copy of the steer may have landed; not resent further, "
        "operator decides"
    ),
}


def _normalise(text: Any) -> str:
    return " ".join(str(text or "").split())


def user_turn_count(transcript: Dict[str, Any], message: str) -> int:
    """How many `user` entries of the transcript are `message` (or its truncated head)."""
    wanted = _normalise(message)
    if not wanted:
        return 0
    count = 0
    for entry in transcript.get("entries") or []:
        if entry.get("kind") != "user":
            continue
        seen = _normalise(entry.get("text"))
        if len(wanted) <= MATCH_HEAD_CHARS:
            matches = seen == wanted
        else:
            matches = seen.startswith(wanted[:MATCH_HEAD_CHARS])
        count += matches
    return count


def transcript_copies(base_url: str, sid: str, text: str) -> Optional[int]:
    """How many user turns carry `text`, or None when the transcript cannot be read.
    Only an answer that says `available: true` counts as a read: anything else says nothing
    about the session, so it is unreadable here, never an empty transcript."""
    try:
        transcript = launcher._request(base_url, f"/api/claude-code/sessions/{sid}/transcript")
    except (OSError, ValueError):
        return None
    if not isinstance(transcript, dict) or transcript.get("available") is not True:
        return None
    return user_turn_count(transcript, text)


def steer_evidence(base_url: str, sid: str, text: str, baseline: int) -> str:
    """`PRESENT` only when more copies of `text` are in the transcript than before the first
    send, so an identical earlier steer is never taken for this one."""
    copies = transcript_copies(base_url, sid, text)
    if copies is None:
        return UNREADABLE
    return PRESENT if copies > baseline else ABSENT


class SendOutcome:
    """What `post_with_recovery` did: the last POST's result, how many POSTs it made, and
    the transcript's last word on the steer (None when no transcript read was needed)."""

    def __init__(self, posted: Dict[str, Any], attempts: int, evidence: Optional[str]) -> None:
        self.posted = posted
        self.attempts = attempts
        self.evidence = evidence


def post_with_recovery(args: argparse.Namespace, text: str, baseline: int) -> SendOutcome:
    """POST `text`, and after a transient transport failure look at the transcript before
    each further POST, at most `launcher.RECOVERY_ATTEMPTS` in all. The exact-text check
    gates every resend, so a steer that landed is never sent twice:

    - the transcript holds the steer -> stop, `evidence == PRESENT`;
    - it is readable and holds no copy -> resend;
    - it is unreadable -> resend only while every failure so far proves the request was never
      handled (a refused connection); after a timeout or a reset a write may have landed,
      so it stops.

    An HTTP answer (a 502/409/400/403 negative, or any other status) is never retried.
    """
    maybe_landed = False
    for attempt, delay in enumerate(launcher.RECOVERY_BACKOFF, start=1):
        posted = launcher.post_session_input(args.base_url, args.sid, text)
        if posted["ok"] or posted.get("transport") is None:
            return SendOutcome(posted, attempt, None)
        maybe_landed = maybe_landed or posted["transport"] == launcher.MAYBE_SENT
        time.sleep(delay)
        evidence = steer_evidence(args.base_url, args.sid, text, baseline)
        if evidence == PRESENT or attempt == launcher.RECOVERY_ATTEMPTS:
            return SendOutcome(posted, attempt, evidence)
        if evidence == UNREADABLE and maybe_landed:
            return SendOutcome(posted, attempt, evidence)
    raise AssertionError("unreachable: the last attempt returns")


def unsent_verdict(sid: str, chars: int, outcome: SendOutcome) -> str:
    """The verdict line for a steer no POST was accepted for. Never `stranded`: no HTTP answer
    said the input was refused, so a transcript that was read and holds no copy is `pending`
    and one that could not be read is `unknown`."""
    error = outcome.posted["error"]
    if outcome.evidence is None:
        # An HTTP answer that is neither a negative nor transient (a 404, a 500): nothing to read.
        return format_verdict_line("unknown", sid, chars, None, "unknown", f"send failed: {error}")
    key = "pending" if outcome.evidence == ABSENT else "unknown"
    detail = f"{SEND_FAILED_REASONS[key]} (attempts={outcome.attempts}; last error: {error})"
    return format_verdict_line(key, sid, chars, None, "unknown", detail)
