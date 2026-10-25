"""The `chief_ops.py` subcommand handlers: one `cmd_*` per subcommand.

Each handler fetches its facts through `launcher`, decides with the pure
modules (`rails`, `digest`, `mailbox`, `compact`, and `steer_delivery` for
`say --verify`) and prints the command's stdout contract. Sibling modules are
reached by module attribute, so a test that replaces `launcher._request` or
`rails.caller_session_id` is seen by every handler.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import active_issue
import chief_managed
import chief_plan
from chief import chief_sweep
import dirty_tree_check
from fleet_core import hooks_state
from no_window import NO_WINDOW
import worktree_claim
# `say --verify`'s delivery classifier is its own subsystem;
# only the names this CLI actually calls are imported.
from chief.steer_delivery import (
    INPUT_DEFERRED,
    INPUT_NEGATIVE_REASONS,
    VERDICT_REASONS,
    classify_exchange_marker,
    deferred_submit_outcome,
    finalize_delivery,
    format_output_age,
    format_verdict_line,
    last_output_age_seconds,
    pending_reason_key,
    recent_output_window,
)

from . import compact, digest, launcher, mailbox, rails

DEFAULT_TAIL = 2000

DEFAULT_VERIFY_TIMEOUT = 20.0

# `POST /api/board/issues/start` spawns the PTY before answering: 12.5/15.8/22.5/
# 41.2 s under load. A client that gives up at the 10 s
# default leaves the server spawning anyway, and a blind retry then double-spawns.
DISPATCH_TIMEOUT = 120.0

# Short names the chief types, mapped to the launcher's provider-qualified
# model; anything else is sent as typed. Grok Build ships one model, so the
# alias carries no tier.
DISPATCH_MODEL_ALIASES = {"grok": "grok:grok-4.5"}

# Codex families the chief may name, version-free: the launcher's catalog
# label (case-insensitive) picks the slug, so a new Luna release needs no edit
# here. Only Luna: Codex on a ChatGPT account rejects the other families.
CODEX_FAMILIES = ("luna",)

# Retired names, refused rather than guessed at: the Board reads an unprefixed
# value as a Claude tier and would 400 with an opaque `unknown model`.
RETIRED_DISPATCH_MODELS = {"gpt5.6": "luna"}


def resolve_dispatch_model(model: str, catalog: Optional[List[dict]], catalog_error: str = "") -> str:
    """The launcher-qualified model for a chief `--model` value, or ValueError
    naming why it can't be sent. `catalog` is only consulted for a Codex
    family; every other value is posted as typed."""
    key = model.lower()
    if key in RETIRED_DISPATCH_MODELS:
        raise ValueError(
            f"--model {model} is retired (the name goes stale with every release); "
            f"use --model {RETIRED_DISPATCH_MODELS[key]}"
        )
    if key in DISPATCH_MODEL_ALIASES:
        return DISPATCH_MODEL_ALIASES[key]
    if key not in CODEX_FAMILIES:
        return model
    if catalog is None:
        raise ValueError(f"--model {model} not resolved: launcher Codex catalog unreadable ({catalog_error})")
    offered = [entry for entry in catalog if entry.get("available")]
    for entry in offered:
        if str(entry.get("label", "")).lower() == key and entry.get("value"):
            return f"codex:{entry['value']}"
    names = ", ".join(str(e.get("label")) for e in offered) or "none"
    raise ValueError(f"--model {model} matches no available Codex model in the launcher catalog (offered: {names})")


def load_dispatch_brief(path: str) -> str:
    """A `--brief-file`'s text, or ValueError naming why it can't be one.
    Checked before any board read, so a bad brief never spawns a lane that
    would then start on the bare issue alone (fleet-config#944)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"brief file unreadable: {path} ({exc.strerror or exc})") from exc
    if not text.strip():
        raise ValueError(f"brief file is empty: {path}")
    return text


def read_brief(file_arg: Optional[str]) -> str:
    """`--file`'s content, or stdin when omitted. `say` never accepts prose
    as a bare CLI arg — it carries the brief the model already composed,
    it never assembles one."""
    if file_arg:
        return Path(file_arg).read_text(encoding="utf-8")
    return sys.stdin.read()


def cmd_board(args: argparse.Namespace) -> int:
    board = launcher._request(args.base_url, "/api/board")
    if args.json:
        print(json.dumps(board, indent=2))
    else:
        print(digest.format_board_digest(board, launcher.fetch_running_fleet_jobs(args.base_url)))
    return 0


def cmd_wait_event(args: argparse.Namespace) -> int:
    """Exit on the first batch of worker events, or on timeout with the board.

    Launched as a one-shot background task: its exit is what wakes the chief,
    so it must never loop past a batch (fleet-config#637, #999). The timeout is
    the safety net for a worker killed too hard to fire any hook.
    """
    events = mailbox.wait_for_events(mailbox.chief_inbox_dir(), args.timeout, args.interval)
    if events:
        print(mailbox.format_inbox_digest(events))
        return 0
    print(f"WAIT_EVENT=timeout after {int(args.timeout)}s (no worker events)")
    try:
        print(digest.format_board_digest(launcher._request(args.base_url, "/api/board")))
    except (ValueError, urllib.error.URLError) as exc:
        print(f"board unavailable: {exc}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    """Mailbox v2: claim the pending batch for handling, or just count it."""
    inbox = mailbox.chief_inbox_dir()
    if args.count:
        try:
            print(f"PENDING={mailbox.count_pending(inbox, stale_after=args.stale_after)}")
        except OSError as exc:
            print(f"PENDING=unknown reason={type(exc).__name__}: {exc}")
            return 1
        return 0
    events, reoffered, dropped = mailbox.claim_mailbox(inbox, stale_after=args.stale_after)
    print(mailbox.format_mailbox_batch(events, reoffered, dropped, stale_after=args.stale_after))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    acked, missing = mailbox.ack_events(mailbox.chief_inbox_dir(), args.ids)
    print(f"ACKED={len(acked)}")
    for event_id in missing:
        print(f"ACK_MISSING={event_id}")
    return 1 if missing else 0


def cmd_wake(args: argparse.Namespace) -> int:
    """How the chief wakes next: the launcher's doorbell, or its own waiter. Exit 1 when unknown."""
    config: Optional[Dict[str, Any]] = None
    reason = "launcher unreachable"
    try:
        result = launcher._request(args.base_url, "/api/config")
        config = result if isinstance(result, dict) else None
        reason = "launcher config reply was not an object"
    except urllib.error.HTTPError as exc:
        reason = f"launcher config unreadable (HTTP {exc.code})"
    except (urllib.error.URLError, ValueError):
        pass
    line, known = mailbox.resolve_wake_mode(config, reason)
    print(line)
    return 0 if known else 1


def cmd_context(args: argparse.Namespace) -> int:
    """Context-window use of `sid` (default: the live chief). Exit 1 when unknown."""
    sid = args.sid
    if sid is None:
        sid = rails.find_chief_session((launcher._request(args.base_url, "/api/board").get("columns") or {}))
        if not sid:
            print("CONTEXT sid=none percent=unknown reason=no live chief session")
            return 1
    else:
        resolved, reason = launcher.resolve_sid_via_board(args.base_url, sid)
        if reason is not None:
            print(f"UNRESOLVABLE reason={reason}")
            return 1
        sid = resolved
    line, percent = compact.format_context_line(str(sid), launcher.fetch_context(args.base_url, str(sid)))
    print(line)
    return 0 if percent is not None else 1


def cmd_self_compact(args: argparse.Namespace) -> int:
    """The chief's *automatic* compaction of itself, behind every refusal.

    Targets only the caller's own session, and only when the caller is the
    live chief. Posts `/compact <focus>` to its own input: mid-turn the submit
    is deferred until the turn ends (app-launcher#763), so the chief ends its
    turn and the compaction runs. Never involved in an operator-typed
    `/compact`, which goes straight to the session ungated.
    """
    self_sid = rails.caller_session_id()
    if not self_sid:
        print("REFUSED=not a launcher session; self-compact only compacts its own chief session")
        return 1
    chief = rails.find_chief_session((launcher._request(args.base_url, "/api/board").get("columns") or {}))
    if chief != self_sid:
        print("REFUSED=the caller is not the live chief; self-compact never compacts another session")
        return 1
    settings, unread = (None, "") if args.threshold is not None else launcher.fetch_chief_settings(args.base_url)
    threshold, note = compact.resolve_compact_threshold(settings, args.threshold, unread)
    print(note)
    if threshold is None:
        print("REFUSED=auto-compact is off")
        return 1
    line, percent = compact.format_context_line(self_sid, launcher.fetch_context(args.base_url, self_sid))
    print(line)
    state = hooks_state.state_dir()
    reason = compact.self_compact_refusal(
        percent, threshold,
        compact.file_age_seconds(state / "chief-handover.md"),
        compact.file_age_seconds(state / chief_plan.STATE_FILENAME),
        args.fresh_within,
    )
    if reason is not None:
        print(f"REFUSED={reason}")
        return 1
    delivery = launcher.post_session_input(args.base_url, self_sid, f"/compact {args.focus}".strip())
    if not delivery["ok"]:
        print(f"NOT_QUEUED reason={delivery['reason'] or delivery['error']}")
        return 2
    verdict = delivery["reason"] or "submitted"
    print(f"COMPACT_QUEUED sid={self_sid[:8]} verdict={verdict} -- end this turn; it runs when the turn ends")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    board = launcher._request(args.base_url, "/api/board")
    self_sid = rails.caller_session_id()
    occupancy = rails.repo_occupancy(board.get("columns") or {}, exclude_sid=self_sid)
    if args.json:
        print(json.dumps(occupancy, indent=2))
    else:
        print(digest.format_occupancy(occupancy))
        # Never silently: this is the pre-dispatch "is the repo busy" read, so
        # it has to answer the same question `dispatch` will, and say which
        # card it left out of the answer.
        if self_sid:
            print(f"(excluding this session: {self_sid})")
    return 0


def cmd_exchange(args: argparse.Namespace) -> int:
    resolved_sid, reason = launcher.resolve_sid_via_board(args.base_url, args.sid)
    if reason is not None:
        print(f"UNRESOLVABLE reason={reason}")
        return 1
    result = launcher._request(args.base_url, f"/api/board/sessions/{resolved_sid}/exchange")
    if not result.get("available"):
        print(f"UNAVAILABLE reason={result.get('reason')}")
        return 1
    assistant = result.get("assistant") or {}
    text = str(assistant.get("text") or "")
    print(f"SOURCE={result.get('source')}")
    print(f"TIMESTAMP={assistant.get('timestamp')}")
    print(text[-args.tail:])
    return 0


def cmd_issues(args: argparse.Namespace) -> int:
    for ref in args.refs:
        repo, number = rails.parse_issue_ref(ref)
        data = launcher.fetch_issue_state(repo, number, owner=args.owner)
        if "error" in data:
            print(f"{repo}#{number}: error: {data['error']}")
            continue
        labels = ",".join(str(lab.get("name")) for lab in (data.get("labels") or []))
        print(
            f"{repo}#{number}: {data.get('state')} "
            f"\"{data.get('title')}\" [{labels}] updated={data.get('updatedAt')}"
        )
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    brief = None
    if args.brief_file is not None:
        try:
            brief = load_dispatch_brief(args.brief_file)
        except ValueError as exc:
            print(f"REFUSED={exc}")
            return 1

    model = None
    if args.model:
        catalog, catalog_error = (launcher.fetch_codex_catalog(args.base_url)
                                  if args.model.lower() in CODEX_FAMILIES else (None, ""))
        try:
            model = resolve_dispatch_model(args.model, catalog, catalog_error)
        except ValueError as exc:
            print(f"REFUSED={exc}")
            return 1

    board = launcher._request(args.base_url, "/api/board")
    columns = board.get("columns") or {}
    settings = launcher._request(args.base_url, "/api/board/chief/settings")
    _worker_cap_raw = (settings.get("settings") or {}).get("worker_cap")
    worker_cap = int(_worker_cap_raw) if _worker_cap_raw is not None else rails.DEFAULT_WORKER_CAP

    self_sid = rails.caller_session_id()
    reason = rails.refuse_dispatch(
        args.repo,
        args.mode,
        rails.repo_occupancy(columns, exclude_sid=self_sid),
        rails.alive_worker_count(columns, exclude_sid=self_sid),
        worker_cap,
        args.yolo_confirmed,
    )
    if reason is not None:
        print(f"REFUSED={reason}")
        return 1

    running, jobs_error = launcher.fetch_running_fleet_jobs(args.base_url)
    reason = rails.fleet_job_refusal(running, jobs_error, rails.is_fleet_wide_brief(brief),
                                     args.allow_during_fleet_job)
    if reason is not None:
        print(f"REFUSED={reason}")
        return 1
    if running is None:
        print(f"WARNING=fleet-jobs-unknown reason={jobs_error}")
    for job_id in running or []:
        print(f"WARNING=fleet-job-running job={job_id}")

    body = {"repo": args.repo, "number": args.number, "mode": args.mode}
    if model:
        body["model"] = model
    if brief is not None:
        body["brief"] = brief
    try:
        result = launcher._request(args.base_url, "/api/board/issues/start", method="POST", body=body,
                                   timeout=DISPATCH_TIMEOUT)
    except (TimeoutError, urllib.error.URLError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if not isinstance(reason, TimeoutError):
            raise  # refused/unreachable: nothing was spawned, a plain failure
        # The server may still be spawning -- never a refusal, never a blind retry.
        print(
            f"DISPATCH=unconfirmed reason=timeout repo={args.repo} issue={args.number}\n"
            f"The launcher may still be spawning this lane. Poll `chief_ops.py sessions` "
            f"for up to a minute and do NOT dispatch again until it shows no {args.repo} "
            f"session; a retry before then double-spawns."
        )
        return 1
    sid = (result.get("session") or {}).get("session_id")
    # One handle per dispatch, carried by every mailbox event the worker
    # writes, so a re-dispatch of the same issue is told apart.
    dispatch_id = uuid.uuid4().hex
    if sid:
        try:
            chief_managed.mark(str(sid), args.repo, args.number, dispatch_id=dispatch_id)
        except OSError:
            pass  # best-effort -- a marking failure must never undo a real dispatch
    print(f"DISPATCHED session={sid} repo={args.repo} issue={args.number}")
    print(f"DISPATCH_ID={dispatch_id}")
    if result.get("launched"):
        print(f"LAUNCHED={result['launched']}")
    return 0


def cmd_chief_sid(args: argparse.Namespace) -> int:
    board = launcher._request(args.base_url, "/api/board")
    sid = rails.find_chief_session(board.get("columns") or {})
    print(f"CHIEF_SID={sid or 'none'}")
    return 0 if sid else 1


def cmd_say(args: argparse.Namespace) -> int:
    resolved_sid, reason = launcher.resolve_sid_via_board(args.base_url, args.sid)
    if reason is not None:
        print(f"UNRESOLVABLE reason={reason}")
        return 1
    # Resolved once, before the one POST: everything below (the POST, the
    # exchange poll, the board/card reads, and every printed line) must speak
    # about the same session, or the verdict describes a different one.
    args.sid = resolved_sid

    text = read_brief(args.file)

    send_time = datetime.now(timezone.utc)
    # The one and only POST, on both the plain and the --verify path. Nothing
    # below this line ever sends again.
    posted = launcher.post_session_input(args.base_url, args.sid, text)

    if not args.verify:
        # Plain `say` is the most-used call, and a traceback here turns a
        # steer the operator could deliberately retry into a crash they have
        # to diagnose. Report the failure instead.
        if not posted["ok"]:
            print(f"FAILED sid={args.sid} chars={len(text)} reason={posted['error']}")
            return 1
        print(f"SENT sid={args.sid} chars={len(text)}")
        return 0

    post_reason = posted.get("reason")
    # An authoritative negative needs no poll — the endpoint already said the
    # input was never submitted. Polling an exchange for movement that cannot
    # come would only spend the timeout budget before reporting the same thing.
    if not posted["ok"] and post_reason not in INPUT_NEGATIVE_REASONS:
        # The endpoint could not be reached at all — that is not evidence
        # either way, so it is `unknown`, never `stranded`.
        print(format_verdict_line(
            "unknown", args.sid, len(text), None, "unknown",
            f"send failed: {posted['error']}",
        ))
        return 1

    state, marker = "pending", {"available": False, "timestamp": None}
    if posted["ok"]:
        state, marker = _poll_delivery(args, post_reason, send_time)

    board: Dict[str, Any] = {}
    try:
        board = launcher._request(args.base_url, "/api/board")
    except (urllib.error.URLError, ValueError):
        board = {}
    target_status = rails.find_session_status(board.get("columns") or {}, args.sid)
    card = launcher.fetch_session_card(args.base_url, args.sid)
    last_input = launcher._last_input(card)
    # Measured once: the classifier and the printed line must agree about how
    # long the target has been quiet, or the verdict can contradict the
    # evidence beside it.
    output_age = last_output_age_seconds(card.get("last_output_at"))
    output_window = recent_output_window(args.poll_interval)

    state = finalize_delivery(
        state, target_status,
        marker_available=bool(marker.get("available")),
        post_reason=post_reason,
        last_input=last_input,
        last_output_age=output_age,
        output_window=output_window,
    )
    # DELIVERED is printed only after the full classification, never straight
    # out of the poll loop: exchange movement alone is not proof.
    if state == "delivered":
        print(f"DELIVERED sid={args.sid} chars={len(text)}")
        return 0

    last_input_reason = (last_input or {}).get("reason")
    reason = _verdict_reason(state, post_reason, target_status, output_age,
                             last_input_reason, posted.get("error"))
    print(format_verdict_line(
        state, args.sid, len(text), target_status,
        format_output_age(output_age),
        reason, last_input_reason,
    ))
    # Every non-delivered verdict is non-zero — including the friendly-sounding
    # PENDING. "Likely delivered" is not "delivered", and the moment it exits 0
    # a caller starts treating it as success.
    return 1


def _poll_delivery(
    args: argparse.Namespace, post_reason: Optional[str], send_time: datetime,
) -> Tuple[str, Dict[str, Any]]:
    """Poll the exchange after an accepted POST until it is delivered, the
    deferred watcher reports, or `--timeout` passes: `(state, last marker)`."""
    deadline = time.monotonic() + args.timeout
    while True:
        marker = launcher.fetch_exchange_marker(args.base_url, args.sid)
        state = classify_exchange_marker(marker["available"], marker["timestamp"], send_time)
        if post_reason == INPUT_DEFERRED:
            # The target is mid-turn, so its exchange advances whether or
            # not this paste was submitted — wait on the watcher's own
            # record instead.
            card = launcher.fetch_session_card(args.base_url, args.sid)
            if deferred_submit_outcome(post_reason, launcher._last_input(card)) != "in_flight":
                return state, marker
        elif state == "delivered":
            return state, marker
        if time.monotonic() >= deadline:
            return state, marker
        time.sleep(args.poll_interval)


def _verdict_reason(
    state: str, post_reason: Optional[str], target_status: Optional[str],
    output_age: Optional[float], last_input_reason: Optional[str], post_error: Optional[str],
) -> str:
    """The reason printed beside a non-delivered verdict."""
    if state == "pending":
        return VERDICT_REASONS[pending_reason_key(post_reason, target_status, output_age)]
    if state == "unknown":
        return VERDICT_REASONS["unknown"]
    if post_reason in INPUT_NEGATIVE_REASONS or last_input_reason in INPUT_NEGATIVE_REASONS:
        reason = VERDICT_REASONS["stranded_negative"]
        return f"{reason} ({post_error})" if post_error else reason
    return VERDICT_REASONS["stranded"]


def cmd_stop(args: argparse.Namespace) -> int:
    resolved_sid, reason = launcher.resolve_sid_via_board(args.base_url, args.sid)
    if reason is not None:
        print(f"UNRESOLVABLE reason={reason}")
        return 1
    mode = "kill" if args.kill else "quit"
    try:
        launcher._request(
            args.base_url, f"/api/claude-code/sessions/{resolved_sid}/stop",
            method="POST", body={"mode": mode},
        )
    except (TimeoutError, urllib.error.URLError) as exc:
        if not launcher.is_timeout(exc):
            raise
        return _stop_after_timeout(args.base_url, resolved_sid, mode)
    print(f"STOPPED sid={resolved_sid} mode={mode}")
    return 0


def _stop_after_timeout(base_url: str, sid: str, mode: str) -> int:
    """The stop POST went unanswered: one board read says whether it landed.
    The write alone is never taken as success, and an unreadable board is
    left as `UNCONFIRMED`."""
    try:
        board = launcher._request(base_url, "/api/board")
    except (TimeoutError, urllib.error.URLError, ValueError) as exc:
        print(f"UNCONFIRMED=timeout after {launcher.REQUEST_TIMEOUT:g}s; "
              f"follow-up board read failed ({type(exc).__name__}) sid={sid} mode={mode}")
        return 1
    if rails.session_holds_live_pty(board.get("columns") or {}, sid):
        print(f"STILL_LIVE sid={sid} mode={mode} (stop timed out; the board still shows the session alive)")
        return 1
    print(f"STOPPED sid={sid} mode={mode} (stop timed out; the board no longer shows a live session)")
    return 0


def cmd_escalate(args: argparse.Namespace) -> int:
    """Post a visibly distinct, higher-priority Telegram ping — "chief needs
    Roberto specifically", never a routine worker status (fleet-config#443).

    Shells to the existing `hooks/notify_send.py` transport (its own
    module docstring documents exactly this standalone-CLI usage) rather
    than re-implementing the transport here — same cross-tier-via-subprocess
    pattern `hooks/notify_on_idle.py` uses to reach `chief_ops.py`. Routes to
    the `attention` category chat.

    It used to force `--mention` so the ping would *sound* different from a
    routine one. Telegram has no mention machinery to force — every message to
    a chat you are in pushes — so the distinction is carried entirely by the
    🚨 banner in the message text (fleet-config#540).
    """
    text = read_brief(args.file)
    message = f"🚨 CHIEF ESCALATION 🚨\n{text}"
    repo_root = Path(__file__).resolve().parents[3]
    notify_send_path = repo_root / "hooks" / "notify_send.py"
    proc = subprocess.run(
        [sys.executable, str(notify_send_path), "--category", "attention",
         "--text", message],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        cwd=str(repo_root), check=False,
    )
    ok = proc.returncode == 0
    print(f"ESCALATED={'yes' if ok else 'no'}")
    if not ok:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        # A repo name/path that resolves to nothing is the same class of
        # non-fact as a git that failed — report it, don't traceback.
        repo_path = rails.resolve_repo_path(args.repo)
        resolved_default = args.default_branch or dirty_tree_check.detect_default_branch(repo_path)
        current_branch, porcelain_empty, commits_ahead = dirty_tree_check.gather(repo_path, resolved_default)
    except (dirty_tree_check.Unreadable, ValueError) as exc:
        # The repo could not be read, so there is no verdict about it. Exit 1
        # like DIRTY does — an unverifiable self-report must not be relayed as
        # verified — but say UNKNOWN, never DIRTY.
        return _print_verify("UNKNOWN", "", str(exc))
    result = dirty_tree_check.evaluate(
        args.expect, current_branch, resolved_default, args.branch, porcelain_empty, commits_ahead
    )
    return _print_verify(result.status, current_branch, result.reason)


def _print_verify(status: str, branch: str, reason: Optional[str]) -> int:
    print(f"STATUS={status}")
    print(f"BRANCH={branch}")
    if reason:
        print(f"REASON={reason}")
    return 0 if status == "CLEAN" else 1


def cmd_sweep(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else chief_sweep.default_root()
    cards: Optional[List[Dict[str, Any]]] = None
    try:
        cards = chief_sweep.board_cards(launcher._request(args.base_url, "/api/board"))
    except (OSError, ValueError) as exc:
        # An unreadable board is an unestablished fact, not an empty one: every
        # removable verdict degrades to `unknown`.
        print(f"NOTE: board unreadable ({exc}); removable verdicts are unknown", file=sys.stderr)
    markers = active_issue.read_rows()
    managed = active_issue.read_rows(chief_managed.state_file())
    findings = chief_sweep.scan(
        root,
        dirs=None if cards is None else chief_sweep.live_dirs(cards),
        markers=markers,
        session_ids=None if cards is None else chief_sweep.live_session_ids(cards),
        pr_lookup=chief_sweep.github_pr_lookup,
        managed=managed,
    )
    stuck = 0
    if args.apply:
        stuck = chief_sweep.apply_findings(
            findings, lambda wt: worktree_claim.remove_worktree(wt),
        )
    sessions = chief_sweep.stale_sessions(
        cards or [], managed, launcher.fetch_issue_state,
    )
    print("\n".join(chief_sweep.format_report(findings, sessions, applied=args.apply)))
    return 1 if stuck else 0
