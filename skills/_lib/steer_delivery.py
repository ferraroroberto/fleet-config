"""Did a steer sent to a live session actually land? (fleet-config#680, lifted
out of `chief_ops.py`.)

`chief_ops say --verify` exists because the session-input endpoint reported
`{"ok": true}` on 2026-07-27 for two messages that were never submitted
(fleet-config#453). Everything needed to turn "I posted it" into an evidenced
verdict grew inside that CLI module: the exchange-marker parser, the
four-verdict classifier and its precedence rules, the output-age measurement
those rules rest on, and the operator-facing reason/verdict rendering. That is
a self-contained subsystem with its own vocabulary, and it was the single
largest thing in a module whose name promises board/issue/dispatch operations.
So it lives here; `chief_ops` keeps the CLI and calls in.

**Pure by construction** -- every function here takes already-fetched values
(`marker_available`, `target_status`, `last_output_at`, `post_reason`) and
returns a verdict or a string. No HTTP, no `gh`, no clock beyond
`last_output_age_seconds`'s injectable `now`. That is what makes the whole
delivery lattice unit-testable without a live launcher
(`tests/test_chief_ops.py`), and it is the property to preserve: if a change
here needs a network call, the call belongs in `chief_ops` and its *result*
belongs in an argument.

The four verdicts (fleet-config#643) and the precedence between them are
documented on `finalize_delivery`, which is the one place the decision is
made. Only DELIVERED exits 0 at the call site, and **no verdict ever triggers
a resend** -- a resent steer can double-execute a shipping command, so that
call stays with the operator.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# How long the `--verify` poll waits between exchange reads. Lives here rather
# than with `chief_ops`'s other CLI defaults because `recent_output_window`
# floors itself at one interval -- the value is an input to the classifier, not
# just an argparse default (`chief_ops` re-exports it for its `--poll-interval`).
DEFAULT_VERIFY_POLL_INTERVAL = 2.0
DEFAULT_RECENT_OUTPUT_WINDOW = 2 * DEFAULT_VERIFY_POLL_INTERVAL

# Input-outcome reasons the session-host reports (app-launcher#760/#763,
# `src/session_host.py:194-207`). Mirrored here as literals rather than
# imported — this file is stdlib-only and must never depend on app-launcher's
# tree. The `reason` field, not the HTTP status, is the documented contract
# (`src/session_client.py:159-163`), so a 202 is recognised by `deferred`
# alone and `_request` never has to surface status codes.
INPUT_DEFERRED = "deferred"
# The deferred watcher's terminal *failure* verdicts, landed on the session's
# `last_input` after the fact, plus the two immediate negatives. Every one of
# them is the API stating outright that nothing was submitted — authoritative
# negatives, which outrank anything inferred from board status.
INPUT_NEGATIVE_REASONS = frozenset({
    "not_ingested",     # written, never echoed back → not delivered
    "dropped",          # the write never reached the PTY at all
    "defer_timeout",    # never went quiet within the watcher's cap
    "defer_vanished",   # went quiet, but the payload had gone
    "defer_unclear",    # quiet with the payload present — and a dialog too
})



# ---- exchange marker (pure) ------------------------------------------------

def parse_exchange_timestamp(ts: Any) -> Optional[datetime]:
    """Parse an exchange `assistant.timestamp` into an aware `datetime`, or
    None if it's missing/unparseable. `board_exchange.py`'s launcher-fallback
    source reports `available: True` with `assistant.timestamp: None`
    (no per-keystroke timestamp exists in that path) — that must read as
    "can't tell", never as fresh evidence of delivery."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_exchange_marker(available: bool, timestamp: Any, send_time: datetime) -> str:
    """"delivered" if the exchange carries a parseable assistant timestamp
    newer than `send_time`; "pending" otherwise. An unreadable exchange
    (unavailable, or a source with no timestamp) must never read as
    delivered — it just means the poll keeps waiting (fleet-config#453)."""
    if not available:
        return "pending"
    parsed = parse_exchange_timestamp(timestamp)
    if parsed is None:
        return "pending"
    return "delivered" if parsed > send_time else "pending"


# ---- output age: the evidence the classifier rests on (pure) ---------------

def last_output_age_seconds(last_output_at: Any, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the target last emitted output, or None when
    `last_output_at` is missing/unparseable — an age that cannot be read is
    its own state, never a large number standing in for "long ago"
    (fleet-config#662). This is a classifier input, not just a display value:
    `finalize_delivery` uses it as evidence the target is mid-turn."""
    try:
        stamp = float(last_output_at)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return max(0.0, (time.time() if now is None else now) - stamp)


def recent_output_window(poll_interval: float) -> float:
    """How fresh output has to be to count as "the target is still talking",
    derived from the poll budget rather than picked by feel (fleet-config#662).

    Two poll intervals: the classifier runs after the poll loop ends, and the
    age is read from a session card fetched *after* that — one more board call
    and one more sessions call later. One interval covers the last poll cycle,
    the second covers those two round trips, so latency alone can never make a
    still-talking target look quiet. Floored at one default interval so an
    operator passing `--poll-interval 0.1` doesn't shrink the window below the
    time those calls take. Still far short of the 20s verify budget, so a
    target that fell silent early in the wait is correctly stranded.
    """
    return max(2 * poll_interval, DEFAULT_VERIFY_POLL_INTERVAL)


def format_output_age(age: Optional[float]) -> str:
    """An age from `last_output_age_seconds` rendered as `"4s ago"`, or the
    literal `"unknown"` when it could not be read — never a fabricated number,
    and never silently omitted.

    Takes the already-measured age rather than the raw stamp so the verdict
    line and the classifier read the same single measurement (fleet-config#662)
    instead of each taking its own `time.time()`."""
    if age is None:
        return "unknown"
    if age < 90:
        return f"{int(age)}s ago"
    if age < 5400:
        return f"{int(age // 60)}m ago"
    return f"{int(age // 3600)}h ago"



# ---- the verdict (pure) ----------------------------------------------------

def finalize_delivery(
    last_marker_state: str,
    target_status: Optional[str],
    *,
    marker_available: bool = True,
    post_reason: Optional[str] = None,
    last_input: Optional[Dict[str, Any]] = None,
    last_output_age: Optional[float] = None,
    output_window: float = DEFAULT_RECENT_OUTPUT_WINDOW,
) -> str:
    """The caller-facing verdict once the poll budget is exhausted: one of
    "delivered", "pending", "stranded", "unknown" (fleet-config#643).

    `UNKNOWN` used to carry two unrelated meanings at once — "the worker is
    mid-turn so the exchange hasn't moved yet" and "the exchange could not be
    read at all". A verifier that cries wolf gets ignored, and this is the
    only mechanism that has ever caught a genuinely stranded steer, so the
    two are split here. Precedence, strongest evidence first:

    1. The exchange advanced past the send — "delivered". Positive proof.
    2. An **authoritative negative**: the API itself said nothing was
       submitted, either immediately (`not_ingested`/`dropped`) or via the
       deferred watcher's terminal verdict on `last_input`
       (`defer_timeout`/`defer_vanished`/`defer_unclear`). This outranks a
       busy board status: a `working` session whose watcher reported
       `defer_timeout` is *not* pending, it is stranded.
    3. `deferred` (app-launcher#763): the payload is in the composer and its
       submitting CR is with a background watcher that has not reported yet.
       Genuinely in flight — neither delivered nor stranded → "pending".
    4. A busy target: mid-turn, so non-movement is not proof of loss →
       "pending". Busy is read from **two** independent signals, either one
       sufficient — the board's `status == "working"`, *or* output emitted
       within `output_window` seconds. The second was added by
       fleet-config#662: the board's status field is demonstrably unreliable,
       reading `awaiting-input` for sessions the exchange showed mid-turn, and
       trusting it alone produced a confident `STRANDED` for a steer that had
       been delivered and acted upon. A target that emitted output a second
       ago is not idle, whatever the label says — and #643 was already
       collecting that figure, printing it on the same line as the verdict it
       refuted, purely for display.
    5. An exchange that could not be **read** (transport error, or the
       launcher reporting it unavailable) → "unknown". This is the narrow,
       genuinely-unresolvable case, per the fleet rule that a check which
       cannot establish a fact reports that as its own state.
    6. A readable exchange that never advanced on a target whose output age
       could **not** be established → "pending". No positive grounds:
       un-advanced plus an unreliable status label is not evidence of loss.
    7. Otherwise — a readable exchange that never advanced on a target that is
       demonstrably quiet (output older than `output_window`) → "stranded".
       Non-movement is a real signal here.

    Note 5 vs 7: "un-advanced" and "unreadable" are different facts, and only
    the second is `unknown`. A readable-but-un-advanced exchange must never
    land in `unknown`, or the narrowing exists only in this docstring.

    `stranded` is now reached only on **positive** grounds — an authoritative
    negative, or measured silence — never by fallthrough (fleet-config#662).
    The asymmetry justifies it: a false `pending` costs a second look, while a
    false `stranded` invites a resend, and a resent steer can double-execute a
    shipping command.

    Every non-delivered verdict is non-zero at the call site and none of them
    ever triggers a resend — a resent steer can double-execute a shipping
    command, so the decision stays with the operator.
    """
    if last_marker_state == "delivered":
        return "delivered"
    watcher_reason = (last_input or {}).get("reason")
    if post_reason in INPUT_NEGATIVE_REASONS or watcher_reason in INPUT_NEGATIVE_REASONS:
        return "stranded"
    if post_reason == INPUT_DEFERRED:
        return "pending"
    if target_status == "working":
        return "pending"
    if not marker_available:
        return "unknown"
    if last_output_age is None or last_output_age <= output_window:
        return "pending"
    return "stranded"


# ---- operator-facing rendering (pure) --------------------------------------

def format_verdict_line(
    verdict: str,
    sid: str,
    chars: int,
    target_status: Optional[str],
    last_output_age: str,
    reason: str,
    last_input_reason: Optional[str] = None,
) -> str:
    """One self-contained line per verdict. Every non-`DELIVERED` result
    carries the target's status and last-output age so the operator can judge
    without a second round of calls (fleet-config#643)."""
    parts = [
        verdict.upper(), f"sid={sid}", f"chars={chars}",
        f"status={target_status or 'unknown'}",
        f"last_output={last_output_age}",
    ]
    if last_input_reason:
        parts.append(f"last_input={last_input_reason}")
    parts.append(f"reason={reason}")
    return " ".join(parts)


VERDICT_REASONS = {
    "pending_deferred": (
        "submit accepted and handed to the watcher (deferred); "
        "exchange not advanced yet, delivery likely — not resent"
    ),
    "pending_busy": (
        "target still mid-turn, exchange not advanced yet; "
        "delivery likely, unconfirmed — not resent"
    ),
    "pending_talking": (
        "target emitting output despite a non-working board status, so it is "
        "mid-turn; exchange not advanced yet, delivery likely — not resent"
    ),
    "pending_unmeasured": (
        "exchange not advanced, but the target's output age could not be read "
        "— no positive grounds to call it stranded; not resent"
    ),
    "unknown": (
        "exchange could not be read, delivery neither confirmed nor "
        "disproved; not resent, operator decides"
    ),
    "stranded_negative": (
        "the endpoint reported the input was never submitted; "
        "not resent, operator decides"
    ),
    "stranded": (
        "exchange never advanced past send and the target has emitted nothing "
        "since; not resent, operator decides"
    ),
}


def pending_reason_key(
    post_reason: Optional[str],
    target_status: Optional[str],
    last_output_age: Optional[float],
) -> str:
    """Which `VERDICT_REASONS` entry explains *this* PENDING. The four
    situations are operationally different — in flight with the watcher, board
    says working, board says otherwise but the target is still talking, or the
    output age could not be read at all — and the verdict line exists so the
    operator can judge without a second round of calls.

    No window needed: `finalize_delivery` only returns "pending" on a measured
    age when that age is already inside the window, so a non-None age reaching
    here *is* recent output."""
    if post_reason == INPUT_DEFERRED:
        return "pending_deferred"
    if target_status == "working":
        return "pending_busy"
    if last_output_age is None:
        return "pending_unmeasured"
    return "pending_talking"
