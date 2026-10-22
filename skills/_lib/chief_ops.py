"""Deterministic operations helper for the fleet chief (fleet-config#445).

The chief's ~18h sweep (2026-07-25/26) re-hand-wrote the same handful of
board/session/issue operations in inline shell + inline Python on every
poll: ~15 board reads (each a freshly typed inline JSON parser), ~12
dispatches, ~10 session-input sends (each re-declaring the same `ssl` +
`urllib` boilerplate), ~8 exchange reads, ~6 multi-repo issue-state loops,
4 session stops. Retyping meant re-breaking: a `curl`-eats-stdin bug, a
`gh`-emoji `UnicodeEncodeError`, a bare `python` blocked by
`venv_discipline`, two recursive greps that hit the 120s timeout, and two
near-misses dispatching into an already-occupied repo.

That last class matters most: chief's safety rails were memory (prose in
`.claude/skills/chief/SKILL.md`), not mechanism. This module makes the
mechanical half real: `refuse_dispatch` is a pure, unit-tested function
that refuses an occupied-repo dispatch, an at/over-cap dispatch, or an
unconfirmed `yolo` — the CLI's `dispatch` subcommand calls it before ever
issuing the POST that would launch a session. The same principle backs
`verify`: during the same sweep a sub-agent overstepped a read-only brief
and shipped a PR on its own initiative, discovered only because the parent
worker happened to raise the alarm. `skills/_lib/dirty_tree_check.py`
already existed for exactly this (fleet-config#247) and was already wired
into four fan-out skills, but not chief — `verify` closes that gap by
wrapping it, so chief re-checks a worker's self-reported state instead of
trusting it.

Talks to the app-launcher webapp (`https://127.0.0.1:8445`, self-signed
cert — loopback bypasses all auth via `BearerTokenMiddleware`) via stdlib
`urllib.request` only — never a `curl` subprocess, so the "curl consumed
the loop's stdin" bug class cannot recur. Every request is routed through
one `_request()` that calls `assert_loopback` first: the tool refuses to
ever become a remote-control surface, even if a future caller passes a
`--base-url` override.

`issues` is one exception: it shells to `gh issue view` (no batched "view
many issues" API exists), one process per ref, `stdin=DEVNULL` +
`creationflags=NO_WINDOW`. The win there is "chief runs one command", not
"one HTTP request" — the multi-repo loop is finally something the tool
owns instead of the model re-typing it. `escalate` is the other: it shells
to `hooks/notify_send.py` (its own docstring documents exactly this
standalone-CLI usage) rather than re-implementing the transport here —
hooks/ and skills/_lib/ are two independent trees by convention, so this
crosses that boundary via subprocess, never an import.

Subcommands
-----------
  board [--base-url URL] [--json]
      The ~12-line phone-readable digest: column counts, one line per live
      session, PR/job cards, running fleet-wide jobs (`/api/jobs`,
      fleet-config#1078), the 5h rate-limit line. A count or reading that
      can't be established prints `?` with its reason, never `0`/`None`
      (fleet-config#840).

  sessions [--base-url URL] [--json]
      Repo occupancy — the "is this repo already busy" question asked
      before every dispatch. The caller's own session card is excluded (and
      named, on the human-readable path) so this answers the same question
      `dispatch` asks — see `dispatch` below.

  wait-event [--timeout 1800] [--interval 5] [--base-url URL]
      Block until a chief-managed worker's turn ends, its session exits or it
      hits a permission gate (`hooks/chief_inbox.py` publishes one event file
      per event; a gate is `kind=needs-input`, fleet-config#1456; a failed
      app-launcher Job is `kind=job-failed`, fleet-config#1539), claim every
      pending event exactly once, print them (`WAIT_EVENT=events n=N` plus one
      line each) and exit 0 -- launched as a one-shot background task, its exit
      is what wakes the chief (fleet-config#999). A burst settles for ~3s into
      one wake. On timeout: `WAIT_EVENT=timeout` plus the board digest, the
      safety net for a worker killed too hard to fire a hook. v2 events add
      ` kind=<kind>` to their line and indented excerpt lines below it; the
      header, the v1 lines and the closing `Next:` line are unchanged
      (pinned by tests/test_chief_wait_event_compat.py, fleet-config#1455).

  inbox [--count] [--stale-after SECS]
      Mailbox v2 reader (fleet-config#1455; contract: docs/chief-mailbox.md).
      Moves stale claims (unacked for `--stale-after`, default 900 s) back to
      the inbox root, then claims every pending event into
      `chief-inbox/.claimed/<claimed_at_ms>-<id>.json` and prints the batch
      (`INBOX=events n=N reoffered=R dropped=D`, one line per event with its
      `id`, the excerpt below, then `ACK=`). Nothing is deleted until `ack`,
      so a chief that dies mid-turn is re-offered the same ids: at-least-once,
      deduped by `id`. `INBOX=empty` when there is nothing. `--count` claims
      nothing and prints `PENDING=<n>` by the doc's file-name rule
      (`PENDING=unknown` + exit 1 when the directory can't be listed).

  ack <id> [<id> ...]
      Retires handled events: deletes each id's claimed (or re-offered) file.
      `ACKED=<n>`, plus `ACK_MISSING=<id>` and exit 1 for an id with no file.

  context [<sid>] [--base-url URL]
      Context-window use from the launcher (`/api/claude-code/sessions/<sid>/
      context`, the statusline's `NN%c`); default: the live chief. A null
      reading prints `percent=unknown` with its reason, never 0, and exits 1.

  self-compact [--threshold N] [--focus TEXT] [--fresh-within S] [--base-url URL]
      The chief's *automatic* compaction of itself (fleet-config#1052). The
      threshold comes from app-launcher Settings (`/api/board/chief-settings`,
      app-launcher#1298; 0 = off), else 30 with the fallback named. Refuses,
      each with its own message, unless the caller is the live chief, the
      percentage is known and at/over the threshold, and the handover log and
      plan were both written within `--fresh-within` (600s). Then posts
      `/compact <focus>` to its own input and reports the delivery verdict.
      An operator-typed `/compact` never passes through here and is never
      gated.

  exchange <sid> [--tail N] [--base-url URL]
      Last assistant text for a live session, tailed to N chars (default
      2000). `<sid>` accepts either a full session id or the 8-char prefix
      `board`/`sessions` actually print -- resolved against the board's live
      session ids before the lookup (fleet-config#613). A prefix matching
      >=2 live sessions fails as `UNRESOLVABLE reason=...`, never a silent
      pick and never `session_not_found`; a prefix/id matching no live
      session passes through unchanged and still gets the real
      `UNAVAILABLE reason=session_not_found`.

  issues <repo#n> [<repo#n> ...] [--owner OWNER]
      One state-table row per ref via `gh issue view`; a per-ref `gh`
      failure becomes an `error:` row rather than aborting the rest.

  dispatch <repo> <number> [--mode start|yolo] [--model M]
           [--brief-file PATH] [--yolo-confirmed] [--allow-during-fleet-job]
           [--base-url URL]
      Refuses (exit 1, no POST) on an occupied repo, an at/over-cap
      worker count, `yolo` without `--yolo-confirmed`, or a `--brief-file`
      that is missing, unreadable or empty. It also reads `/api/jobs` and
      prints `WARNING=fleet-job-running job=<id>` for each running
      fleet-wide job (`FLEET_JOB_PATTERNS`), or `WARNING=fleet-jobs-unknown`
      when that can't be read. A fleet-wide brief (`/propagate-vendored`,
      `/cleanup-fleet`) is refused while one runs, or while that is unknown,
      unless `--allow-during-fleet-job` (fleet-config#1078). Otherwise POSTs
      `/api/board/issues/start` and marks the new session chief-managed
      (`skills/_lib/chief_managed.py`, fleet-config#443) with a fresh
      `dispatch_id`, printed as `DISPATCH_ID=` and carried by every
      mailbox event that worker writes (fleet-config#1455), so
      `hooks/notify_on_idle.py` can route its blocked-on-input
      notifications to chief instead of the human ping.

      Neither refusal counts the *calling* session, identified by the
      launcher's own `APP_LAUNCHER_SESSION_ID` stamp (fleet-config#838).
      The standing chief runs as an ordinary PTY in `fleet-config`, so
      counting its card made that one repo — the repo the fleet's hooks,
      skills and runners live in — permanently undispatchable, and the only
      way through was to route around the guard entirely. Every *other* live
      session still occupies its repo: a second worker is refused exactly as
      before.

      `--brief-file` (fleet-config#944) carries the lane's scope, queue and
      constraints *in its launch command* — the one channel a lane trusts as
      coming from Roberto. The text rides the POST as `brief`; the launcher
      stores it in a file it owns and launches `/issue-<mode> <N> --brief
      <path>` (app-launcher#1114), printed back as `LAUNCHED=`. A later `say`
      arrives as unsigned paste and can never grant shipping.

  chief-sid [--base-url URL]
      Prints `CHIEF_SID=<sid>` (or `none`) for the live standing chief —
      the lookup `notify_on_idle.py` shells out to before pushing a
      chief-managed worker's notification into chief's own session.

  say <sid> [--file PATH] [--verify] [--timeout SECS] [--poll-interval SECS]
      [--base-url URL]
      Sends `--file`'s content (or stdin) as session input. Never accepts
      prose as a bare CLI arg — `say` is a pure pipe, it never composes
      the brief. `--verify` (fleet-config#453) polls the session's exchange
      after posting instead of trusting the endpoint's `{"ok": true}` — the
      endpoint reported success on 2026-07-27 for messages that were never
      submitted, twice, in two different ways. Four verdicts
      (fleet-config#643); only DELIVERED exits 0:

        DELIVERED  positive evidence only: the exchange advanced past a send
                   that was submitted immediately, or — for a `deferred` send
                   — the watcher's `last_input` confirms the submit. A
                   deferred send's exchange movement is never proof: the
                   target is mid-turn by definition (fleet-config#856).
        PENDING    delivery likely, unconfirmed: the target is mid-turn
                   (board says `working`, *or* it emitted output in the last
                   few seconds — fleet-config#662), its output age could not
                   be read at all, or the submit is with the deferred watcher
                   (`deferred`, app-launcher#763) which has not reported
                   within `--timeout` — no verdict reached; re-read the
                   session's `last_input` later. Exits 1.
        STRANDED   positively not delivered — either the exchange never
                   advanced on a target that is demonstrably quiet, or the
                   endpoint said so outright (`not_ingested`/`dropped`, or
                   the watcher's `defer_timeout`/`defer_vanished`/
                   `defer_unclear`/`defer_failed` on `last_input`). Never reached by
                   fallthrough: it always rests on positive evidence.
        UNKNOWN    the exchange could not be *read*. Genuinely unresolvable
                   — never merely "un-advanced", which is STRANDED.

      Every non-DELIVERED verdict carries the target's status and
      last-output age, and exits non-zero. Never auto-retries, on any
      verdict; a resent steer can double-execute a shipping command, so
      that is the caller's call. Plain `say` (no `--verify`) reports
      `FAILED` on a rejected POST rather than raising.

  stop <sid> [--kill] [--base-url URL]
      `quit` by default; `--kill` must be explicit.

  escalate [--file PATH]
      A visibly distinct, higher-priority Telegram ping ("chief needs
      Roberto specifically") — routes to the `attention` category via
      `hooks/notify_send.py`, never a routine worker status.

  verify <repo> --expect merged|built [--branch NAME]
         [--default-branch NAME]
      Post-flight dirty-tree check (wraps `skills/_lib/dirty_tree_check.py`,
      fleet-config#247) — run by chief, never by the worker being checked,
      right before a worker's self-reported "shipped ✅" is trusted onward.
      `<repo>` is a fleet-registry name (resolved via
      `skills/_lib/fleet_repo_scan.py`) or a literal path. Exits 1 on
      STATUS=DIRTY *and* on STATUS=UNKNOWN (the repo could not be read, so
      the self-report is unverified — relaying it as verified would be the
      same false pass) so a caller can gate on the exit code, not just parse
      text. UNKNOWN is never reported as DIRTY (fleet-config#570).

  sweep [--apply] [--root DIR]
      The chief's worktree sweep (fleet-config#1496): one line per
      `<repo>-wt-<N>` folder with its class and evidence -- `finished`
      (clean, nothing beyond the default branch or all of it in a merged PR),
      `in-use` (a live session's cwd or a live active-issue marker),
      `has-work` (dirty or unmerged -- the only class the chief asks about),
      `orphan-empty` (empty non-git folder) or `unknown` (not established) --
      then a SUMMARY line and one `SESSION` line per chief-managed
      `idle-finished` session with its issue state (`stoppable` when CLOSED,
      for `stop`). Read-only by default; `--apply` removes `finished` via
      `worktree_claim.remove_worktree` and `orphan-empty` via `rmdir`, and a
      folder that will not go stays and is reported (exit 1). Never kills a
      process. The logic lives in `skills/_lib/chief_sweep.py`.

  plan <show|lane|drop-lane|add|set|move|remove|wait|ask|unwait|clear> ...
      The only writer of `~/.claude/hooks/state/chief-plan.json`, the plan the
      Board's "Chief's plan" card renders (fleet-config#1034,
      app-launcher#1279). One line per update: `plan add app-launcher#1273
      "title"`, `plan set app-launcher#1273 --status gate`, `plan lane
      app-launcher gate --item "#1273"`, `plan wait "text" --ref repo#N`,
      `plan ask "question" --ref repo#N --detail ... --recommend ... --option
      "Label::desc" --recommended Label` (fleet-config#1049), `plan unwait <id>`.
      Validates the whole v1 document and replaces it atomically, or writes
      nothing and exits 2. The logic lives in `skills/_lib/chief_plan.py`, the
      contract in `docs/chief-plan.md`.

`--verify`'s four-verdict classification is **not** here: the exchange-marker
parser, the precedence rules, the output-age measurement they rest on and the
verdict/reason rendering are one self-contained subsystem, and they live in
`skills/_lib/steer_delivery.py` (fleet-config#680). This module fetches the
facts and prints the line; that one decides. Its names are re-exported here so
every existing caller and test keeps reaching them through `chief_ops`.

stdlib only, plus the `gh` CLI for `issues`.
"""

# The body lives in the `chief/` package beside this file, one module per
# concern (see `chief/__init__.py`). This file stays the entry point the chief
# calls by path and the import surface its tests and `send_file.py` use; a test
# that replaces a function patches the module that owns it (`chief.launcher`,
# `chief.mailbox`, `chief.rails`), reachable here as `chief_ops.launcher` etc.

from __future__ import annotations

import logging
import sys
import urllib.error
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief_managed  # noqa: E402
from chief import cli, commands, compact, digest, launcher, mailbox, rails  # noqa: E402
from chief.commands import (  # noqa: E402
    cmd_board,
    cmd_dispatch,
    cmd_exchange,
    cmd_say,
    cmd_stop,
    cmd_verify,
)
from chief.compact import (  # noqa: E402
    format_context_line,
    resolve_compact_threshold,
    self_compact_refusal,
)
from chief.digest import _fmt_age, format_board_digest  # noqa: E402
from chief.launcher import (  # noqa: E402
    DEFAULT_BASE_URL,
    _request,
    assert_loopback,
    fetch_exchange_marker,
    fetch_issue_state,
    fetch_session_card,
    post_session_input,
)
from chief.mailbox import (  # noqa: E402
    CLAIM_READ_ATTEMPTS,
    CLAIM_READ_BACKOFF_SECONDS,
    CLAIM_STALE_SECONDS,
    CLAIMED_DIRNAME,
    INBOX_EVENT_TTL_SECONDS,
    LOGGER,
    WAIT_EVENT_SETTLE_SECONDS,
    _now_ms,
    ack_events,
    chief_inbox_dir,
    claim_batch,
    claim_mailbox,
    count_pending,
    format_inbox_digest,
    format_mailbox_batch,
    reoffer_stale_claims,
    resolve_wake_mode,
    wait_for_events,
)
from chief.rails import (  # noqa: E402
    LAUNCHER_SESSION_ID_ENV_VAR,
    alive_worker_count,
    caller_session_id,
    find_chief_session,
    find_session_status,
    is_fleet_wide_brief,
    parse_issue_ref,
    refuse_dispatch,
    repo_occupancy,
    resolve_repo_path,
    resolve_session_id,
    running_fleet_jobs,
)
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

__all__ = [
    "chief_managed", "cli", "commands", "compact", "digest", "launcher", "mailbox", "rails",
    "cmd_board", "cmd_dispatch", "cmd_exchange", "cmd_say", "cmd_stop", "cmd_verify",
    "format_context_line", "resolve_compact_threshold", "self_compact_refusal",
    "_fmt_age", "format_board_digest",
    "DEFAULT_BASE_URL", "_request", "assert_loopback", "fetch_exchange_marker",
    "fetch_issue_state", "fetch_session_card", "post_session_input",
    "CLAIM_READ_ATTEMPTS", "CLAIM_READ_BACKOFF_SECONDS", "CLAIM_STALE_SECONDS",
    "CLAIMED_DIRNAME", "INBOX_EVENT_TTL_SECONDS", "LOGGER", "WAIT_EVENT_SETTLE_SECONDS",
    "_now_ms", "ack_events", "chief_inbox_dir", "claim_batch", "claim_mailbox",
    "count_pending", "format_inbox_digest", "format_mailbox_batch", "reoffer_stale_claims",
    "resolve_wake_mode", "wait_for_events",
    "LAUNCHER_SESSION_ID_ENV_VAR", "alive_worker_count", "caller_session_id",
    "find_chief_session", "find_session_status", "is_fleet_wide_brief", "parse_issue_ref",
    "refuse_dispatch", "repo_occupancy", "resolve_repo_path", "resolve_session_id",
    "running_fleet_jobs", "main",
]


def main(argv: Optional[List[str]] = None) -> int:
    args = cli.build_parser(__doc__).parse_args(argv)
    # Breadcrumbs go to stderr, so every command's stdout format is unchanged.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        return args.func(args)
    except (ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
