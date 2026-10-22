"""The chief mailbox readers (contract: docs/chief-mailbox.md): `wait-event`'s
one-shot claim, the v2 `inbox`/`ack` pair, and the doorbell-or-waiter decision
that says which of them wakes the chief.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import hooks_state

from . import digest

LOGGER = logging.getLogger(__name__)


# ---- chief inbox: worker turn-end wake events (fleet-config#999) ------------

# Written by `hooks/chief_inbox.py` (one JSON file per event); read here by
# path, not import -- `hooks/` and `skills/_lib/` stay independent trees.
CHIEF_INBOX_DIRNAME = "chief-inbox"
# Same horizon as the chief-managed registry's TTL: an event older than the
# dispatch it belongs to describes nothing the chief can still act on.
INBOX_EVENT_TTL_SECONDS = 24 * 3600
DEFAULT_WAIT_EVENT_TIMEOUT = 1800.0
DEFAULT_WAIT_EVENT_INTERVAL = 5.0
# After the first event lands, wait this long so lanes finishing together
# arrive as one wake rather than several.
WAIT_EVENT_SETTLE_SECONDS = 3.0


# Mailbox v2 (fleet-config#1455; contract: docs/chief-mailbox.md). A v2 claim
# lives under `.claimed/`, where `wait-event` never looks, so both readers
# coexist until the doorbell switch-over (#1457).
CLAIMED_DIRNAME = ".claimed"
# A claim unacked this long is re-offered: the reader that took it is assumed
# dead. Longer than any chief turn that handles a batch, so a live chief is
# not handed its own batch twice.
CLAIM_STALE_SECONDS = 900.0
EVENT_KIND_TEXT = {"turn-end": "turn ended", "session-end": "session ended", "needs-input": "needs input",
                   "job-failed": "job failed"}
# Reading a just-claimed file retries a sharing violation this many times,
# backing off from this delay and doubling (10+20+40+80 ms): the concurrent
# claimer holding it finishes its rename in well under that (fleet-config#1465).
CLAIM_READ_ATTEMPTS = 5
CLAIM_READ_BACKOFF_SECONDS = 0.01


def chief_inbox_dir() -> Path:
    return hooks_state.state_dir() / CHIEF_INBOX_DIRNAME


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _split_event_name(name: str) -> Tuple[Optional[int], str]:
    """`<ms>-<id>.json` -> `(ms, id)`; a name without that shape -> `(None, stem)`."""
    stem = name[:-len(".json")] if name.endswith(".json") else name
    prefix, dash, rest = stem.partition("-")
    if dash and prefix.isdigit() and rest:
        return int(prefix), rest
    return None, stem


def _event_files(directory: Path) -> List[Path]:
    """Published event files directly in `directory`: non-dot `*.json` regular files."""
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    return sorted(
        (Path(entry.path) for entry in entries
         if entry.name.endswith(".json") and not entry.name.startswith(".") and entry.is_file()),
        key=lambda path: path.name,
    )


def count_pending(inbox: Path, now_ms: Optional[int] = None,
                  stale_after: float = CLAIM_STALE_SECONDS) -> int:
    """Pending events by file name alone -- the reference for docs/chief-mailbox.md.

    Every root event file, plus every `.claimed/` file whose claim is at
    least `stale_after` old (or whose name carries no readable claim time).
    No file is opened or parsed. Raises `OSError` when a directory exists but
    cannot be listed: that is "unknown", never zero.
    """
    moment = _now_ms() if now_ms is None else now_ms
    pending = len(_event_files(inbox))
    for path in _event_files(inbox / CLAIMED_DIRNAME):
        claimed_at, _ = _split_event_name(path.name)
        if claimed_at is None or moment - claimed_at >= stale_after * 1000:
            pending += 1
    return pending


def reoffer_stale_claims(inbox: Path, now_ms: int, stale_after: float = CLAIM_STALE_SECONDS) -> List[str]:
    """Move each stale `.claimed/` file back to the inbox root; the re-offered ids.

    The basename is kept: `<claimed_at_ms>-<id>.json` is already a valid root
    event name. A rename lost to a concurrent reader is skipped.
    """
    reoffered = []
    for path in _event_files(inbox / CLAIMED_DIRNAME):
        claimed_at, event_id = _split_event_name(path.name)
        if claimed_at is not None and now_ms - claimed_at < stale_after * 1000:
            continue
        try:
            os.replace(path, inbox / path.name)
        except OSError:
            continue
        reoffered.append(event_id)
    return reoffered


def _read_claimed(held: Path) -> Tuple[str, Optional[Any]]:
    """Read a file this reader just renamed into a claim: `(outcome, event)`.

    Outcomes: `"read"` (event parsed), `"junk"` (read but not JSON), `"gone"`
    (another claimer holds it now) and `"locked"` (still unreadable after the
    retries). On Windows both of two racing renames can succeed: the rename
    opens the source by name and then moves it through that handle, so a
    claimer that opened it before our rename landed moves it again, out of our
    claim. Until it does, our read is a sharing violation; after, the file is
    gone from our path and that claimer reads it (fleet-config#1465). So a
    failed read is never junk, and the caller must never delete the file.
    """
    delay = CLAIM_READ_BACKOFF_SECONDS
    for attempt in range(1, CLAIM_READ_ATTEMPTS + 1):
        try:
            text = held.read_text(encoding="utf-8")
        except FileNotFoundError:
            LOGGER.info("ℹ️ claim: %s moved to a concurrent claimer (attempt %d); it delivers it",
                        held.name, attempt)
            return "gone", None
        except OSError as exc:
            if attempt == CLAIM_READ_ATTEMPTS:
                LOGGER.warning("⚠️ claim: %s still unreadable after %d attempts (%s: %s)",
                               held.name, attempt, type(exc).__name__, exc)
                return "locked", None
            LOGGER.info("ℹ️ claim: %s not readable yet (attempt %d, %s); retrying in %.0f ms",
                        held.name, attempt, type(exc).__name__, delay * 1000)
            time.sleep(delay)
            delay *= 2
            continue
        try:
            return "read", json.loads(text)
        except ValueError:
            return "junk", None
    return "locked", None  # unreachable: the last attempt always returns


def claim_mailbox(inbox: Path, now_ms: Optional[int] = None,
                  stale_after: float = CLAIM_STALE_SECONDS) -> Tuple[List[Dict[str, Any]], List[str], int]:
    """`(events, reoffered_ids, dropped)`: claim every pending event, oldest first.

    Stale claims are re-offered first, then each root event file is renamed to
    `.claimed/<now_ms>-<id>.json` before it is read -- the rename is the
    claim, so a concurrent reader (this one or `wait-event`) that loses the
    race skips the file. Nothing is deleted here except junk and events past
    the TTL; a handled event is retired by `ack_events`. Each returned event
    carries `id` from its file name, the handle `ack` takes.
    """
    moment = _now_ms() if now_ms is None else now_ms
    reoffered = reoffer_stale_claims(inbox, moment, stale_after)
    claimed = inbox / CLAIMED_DIRNAME
    now_dt = datetime.fromtimestamp(moment / 1000, tz=timezone.utc)
    events: List[Dict[str, Any]] = []
    dropped = 0
    for path in _event_files(inbox):
        _, event_id = _split_event_name(path.name)
        claimed.mkdir(exist_ok=True)
        held = claimed / f"{moment}-{event_id}.json"
        try:
            os.replace(path, held)
        except OSError:
            continue  # another reader took it first
        outcome, event = _read_claimed(held)
        if outcome in ("gone", "locked"):
            # Never delete an unread claim: a "locked" one stays in `.claimed/`
            # and is re-offered once stale; a "gone" one is another reader's.
            continue
        age = _event_age_seconds(event, now_dt) if isinstance(event, dict) else None
        if not isinstance(event, dict) or (age is not None and age > INBOX_EVENT_TTL_SECONDS):
            try:
                held.unlink()
            except OSError:
                pass
            dropped += 1
            continue
        events.append({**event, "id": event_id})
    return events, reoffered, dropped


def ack_events(inbox: Path, ids: List[str]) -> Tuple[List[str], List[str]]:
    """`(acked, missing)`: delete each id's file, claimed or already re-offered."""
    acked, missing = [], []
    for event_id in ids:
        found = False
        for path in _event_files(inbox / CLAIMED_DIRNAME) + _event_files(inbox):
            stamp, name_id = _split_event_name(path.name)
            if stamp is None or name_id != event_id:
                continue
            try:
                path.unlink()
                found = True
            except FileNotFoundError:
                pass  # a concurrent ack or reader got there first
        (acked if found else missing).append(event_id)
    return acked, missing


def _event_age_seconds(event: Dict[str, Any], now: datetime) -> Optional[float]:
    try:
        stamp = datetime.strptime(str(event.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now - stamp).total_seconds()


def claim_batch(inbox: Path, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Claim every published event in ``inbox`` exactly once, oldest first.

    Each ``*.json`` is renamed into a private claim folder before it is read:
    a concurrent claimer loses the rename race and skips the file, and an
    event published during the claim is simply not in this listing -- it lands
    in the next batch. Events past the TTL, or unparseable, are dropped. A
    claimed file that cannot be read is never deleted (`_read_claimed`): one
    that moved on belongs to the other claimer, and one still locked is put
    back in the inbox for the next batch.
    """
    if not inbox.is_dir():
        return []
    moment = now or datetime.now(timezone.utc)
    claim = inbox / f".claim-{os.getpid()}-{time.time_ns()}"
    events: List[Dict[str, Any]] = []
    try:
        for path in sorted(inbox.glob("*.json")):
            claim.mkdir(exist_ok=True)
            held = claim / path.name
            try:
                os.replace(path, held)
            except OSError:
                continue  # another claimer took it first
            outcome, event = _read_claimed(held)
            if outcome == "gone":
                continue
            if outcome == "locked":
                try:
                    os.replace(held, path)
                    LOGGER.info("ℹ️ claim: %s put back in the inbox for the next batch", path.name)
                except OSError as exc:
                    LOGGER.warning("⚠️ claim: %s left unread in %s (%s: %s)",
                                   path.name, claim, type(exc).__name__, exc)
                continue
            try:
                held.unlink()
            except OSError:
                pass
            if not isinstance(event, dict):
                LOGGER.info("ℹ️ claim: dropped %s, not a JSON event", path.name)
                continue
            age = _event_age_seconds(event, moment)
            if age is not None and age > INBOX_EVENT_TTL_SECONDS:
                continue
            events.append(event)
    finally:
        try:
            claim.rmdir()
        except OSError:
            pass
    return events


def _job_failed_lines(event: Dict[str, Any]) -> List[str]:
    """A `job-failed` event's head line and one detail line, ahead of its excerpt.

    A Job has no worker session, so the worker form (`repo#n`, sid) does not
    apply. A reaped run has no exit code and an unowned script no repo: both
    read `-`. `outcome` is shown only when it is not a plain `failed`, so a
    run whose result could not be confirmed never reads as a confirmed failure.
    """
    def field(name: str) -> str:
        value = event.get(name)
        return "-" if value in (None, "") else str(value)

    outcome = event.get("outcome")
    head = (f"- job {field('job_name')} failed exit={field('exit_code')} "
            f"run={field('run_id')} repo={field('repo')}")
    if outcome and outcome != "failed":
        head += f" outcome={outcome}"
    return [head, f"  job_id={field('job_id')} trigger={field('trigger')} script={field('script_path')}"]


def _event_line(event: Dict[str, Any], moment: datetime) -> str:
    """`- <repo>#<n>: <what> (<agent>) sid=<8> age=<age>` -- the v1 line, unchanged."""
    kind = event.get("kind")
    what = EVENT_KIND_TEXT.get(kind) if kind else None
    if what is None:
        what = "turn ended" if event.get("event") == "Stop" else "session ended" \
            if event.get("event") == "SessionEnd" else str(kind or event.get("event"))
    age = _event_age_seconds(event, moment)
    issue = f"{event.get('repo')}#{event.get('number')}"
    return (
        f"- {issue}: {what} ({event.get('agent') or '?'}) "
        f"sid={str(event.get('launcher_sid') or '')[:8]} "
        f"{'age=' + digest._fmt_age(age) if age is not None else 'age=?'}"
    )


def _excerpt_lines(event: Dict[str, Any]) -> List[str]:
    """Indented excerpt lines for a v2 event; none for a v1 event."""
    if "excerpt" not in event:
        return []
    text = event.get("excerpt")
    if not isinstance(text, str) or not text:
        return [f"  excerpt: unavailable ({event.get('excerpt_unavailable') or 'no reason recorded'})"]
    return [f"  excerpt ({event.get('excerpt_source') or '?'}, {len(text)} chars):"] + \
        [f"  | {line}" for line in text.splitlines()]


def format_inbox_digest(events: List[Dict[str, Any]], now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    lines = [f"WAIT_EVENT=events n={len(events)}"]
    for event in events:
        kind = f" kind={event['kind']}" if event.get("kind") else ""
        if event.get("kind") == "job-failed":
            head, *detail = _job_failed_lines(event)
            lines.extend([head + kind, *detail])
        else:
            lines.append(_event_line(event, moment) + kind)
        lines.extend(_excerpt_lines(event))
    lines.append("Next: `exchange <sid>` for each, `verify` any completion claim, then absorb or escalate.")
    return "\n".join(lines)


def format_mailbox_batch(events: List[Dict[str, Any]], reoffered: List[str], dropped: int,
                         now: Optional[datetime] = None, stale_after: float = CLAIM_STALE_SECONDS) -> str:
    """The `inbox` command's batch: every event with its `id`, then the `ack` line."""
    if not events:
        return f"INBOX=empty dropped={dropped}" if dropped else "INBOX=empty"
    moment = now or datetime.now(timezone.utc)
    again = set(reoffered)
    lines = [f"INBOX=events n={len(events)} reoffered={len(reoffered)} dropped={dropped}"]
    for event in events:
        tail = f" kind={event.get('kind') or '?'} id={event['id']} dispatch={event.get('dispatch_id') or '-'}"
        again_mark = " re-offered" if event["id"] in again else ""
        if event.get("kind") == "job-failed":
            head, *detail = _job_failed_lines(event)
            lines.extend([head + tail + again_mark, *detail])
        else:
            lines.append(_event_line(event, moment) + tail + again_mark)
        lines.extend(_excerpt_lines(event))
    lines.append("ACK=chief_ops.py ack " + " ".join(event["id"] for event in events))
    lines.append(f"Next: handle each (a re-offered id may already be handled), then run the ACK line; "
                 f"an unacked batch is re-offered after {int(stale_after // 60)}m.")
    return "\n".join(lines)


def wait_for_events(
    inbox: Path, timeout: float, interval: float, *,
    settle: float = WAIT_EVENT_SETTLE_SECONDS, sleep=time.sleep, clock=time.monotonic,
) -> List[Dict[str, Any]]:
    """Block until the inbox yields events (plus a settle window) or ``timeout``."""
    deadline = clock() + timeout
    while True:
        events = claim_batch(inbox)
        if events:
            sleep(settle)
            return events + claim_batch(inbox)
        remaining = deadline - clock()
        if remaining <= 0:
            return []
        sleep(min(interval, remaining))



# ---- which reader wakes the chief (fleet-config#1457) -------------------------

def resolve_wake_mode(
    config: Optional[Dict[str, Any]], unread_reason: str = "launcher unreachable",
) -> Tuple[str, bool]:
    """`(WAKE= line, known)` from the launcher's `chief_doorbell` switch (fleet-config#1457).

    Only a confirmed `true` lets the chief drop its `wait-event` waiter: an
    unread or non-boolean setting is `unknown` and still says arm the waiter,
    so a chief never ends a turn with no wake path at all.
    """
    if config is None:
        return f"WAKE=waiter doorbell=unknown reason={unread_reason}", False
    value = config.get("chief_doorbell")
    if value is True:
        return "WAKE=doorbell doorbell=on", True
    if value is False:
        return "WAKE=waiter doorbell=off", True
    return "WAKE=waiter doorbell=unknown reason=launcher config has no boolean chief_doorbell", False
