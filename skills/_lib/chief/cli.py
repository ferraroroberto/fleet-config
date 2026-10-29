"""The `chief_ops.py` argument parser: every subcommand, flag and default.

`chief_ops.py` is a live interface (the chief calls it every few minutes), so
the argv built here is a frozen contract. Subcommands are registered in the
order `--help` lists them.
"""

from __future__ import annotations

import argparse

import chief_plan
from chief import chief_sweep
from chief.steer_delivery import DEFAULT_VERIFY_POLL_INTERVAL

from . import commands, compact, launcher, mailbox


def build_parser(description: str) -> argparse.ArgumentParser:
    """The full `chief_ops` parser; `description` is `chief_ops.py`'s docstring."""
    p = argparse.ArgumentParser(prog="chief_ops", description=description)
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_read_commands(sub)
    _add_act_commands(sub)
    return p


def _add_read_commands(sub: argparse._SubParsersAction) -> None:
    """board, sessions, exchange, issues, dispatch, and the wake/context reads."""
    b = sub.add_parser("board", help="~12-line board digest")
    b.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=commands.cmd_board)

    s = sub.add_parser("sessions", help="repo occupancy")
    s.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=commands.cmd_sessions)

    e = sub.add_parser("exchange", help="last assistant text for a live session")
    e.add_argument("sid")
    e.add_argument("--tail", type=int, default=commands.DEFAULT_TAIL)
    e.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    e.set_defaults(func=commands.cmd_exchange)

    i = sub.add_parser("issues", help="multi-repo issue-state table")
    i.add_argument("refs", nargs="+", help="<repo>#<number> ...")
    i.add_argument("--owner", default=launcher.DEFAULT_OWNER)
    i.set_defaults(func=commands.cmd_issues)

    d = sub.add_parser("dispatch", help="guard-railed issue-start")
    d.add_argument("repo")
    d.add_argument("number", type=int)
    d.add_argument("--mode", choices=("start", "yolo"), default="start")
    d.add_argument("--model", default=None,
                   help="sonnet|opus|fable|luna|grok (luna = Codex, resolved from the launcher's catalog; grok = Grok Build, easy-tier lanes only)")
    d.add_argument("--brief-file", default=None,
                   help="lane brief, delivered in the launch command (#944)")
    d.add_argument("--yolo-confirmed", action="store_true")
    d.add_argument("--allow-during-fleet-job", action="store_true",
                   help="dispatch a fleet-wide brief while a fleet-wide job runs (#1078)")
    d.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    d.set_defaults(func=commands.cmd_dispatch)

    we = sub.add_parser("wait-event", help="block until a worker's turn ends (fleet-config#999)")
    we.add_argument("--timeout", type=float, default=mailbox.DEFAULT_WAIT_EVENT_TIMEOUT)
    we.add_argument("--interval", type=float, default=mailbox.DEFAULT_WAIT_EVENT_INTERVAL)
    we.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    we.set_defaults(func=commands.cmd_wait_event)

    fj = sub.add_parser("fleet-jobs", help="count running fleet-wide jobs (the Stop hook's read)")
    fj.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    fj.set_defaults(func=commands.cmd_fleet_jobs)

    ib = sub.add_parser("inbox", help="mailbox v2: claim the pending batch, or --count it (fleet-config#1455)")
    ib.add_argument("--count", action="store_true", help="print PENDING=<n> and claim nothing")
    ib.add_argument("--stale-after", type=float, default=mailbox.CLAIM_STALE_SECONDS,
                    help="seconds before an unacked claim is re-offered")
    ib.set_defaults(func=commands.cmd_inbox)

    ak = sub.add_parser("ack", help="mailbox v2: retire handled events by id (fleet-config#1455)")
    ak.add_argument("ids", nargs="+")
    ak.set_defaults(func=commands.cmd_ack)

    wk = sub.add_parser("wake", help="doorbell or waiter: read the launcher's chief_doorbell switch (fleet-config#1457)")
    wk.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    wk.set_defaults(func=commands.cmd_wake)

    ctx = sub.add_parser("context", help="a session's context-window use (default: the chief)")
    ctx.add_argument("sid", nargs="?", default=None)
    ctx.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    ctx.set_defaults(func=commands.cmd_context)


def _add_act_commands(sub: argparse._SubParsersAction) -> None:
    """self-compact, chief-sid, say, stop, escalate, verify, sweep and plan."""
    sc = sub.add_parser("self-compact", help="the chief compacts itself behind its refusals (fleet-config#1052)")
    sc.add_argument("--threshold", type=int, default=None,
                    help="override the Settings threshold (tests); default: app-launcher Settings, else 30")
    sc.add_argument("--focus", default=compact.DEFAULT_COMPACT_FOCUS)
    sc.add_argument("--fresh-within", type=float, default=compact.COMPACT_FRESH_SECONDS,
                    help="max age in seconds of the handover log and the plan")
    sc.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    sc.set_defaults(func=commands.cmd_self_compact)

    cs = sub.add_parser("chief-sid", help="find the standing chief's live session id")
    cs.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    cs.set_defaults(func=commands.cmd_chief_sid)

    say_p = sub.add_parser("say", help="send input to a live session")
    say_p.add_argument("sid")
    say_p.add_argument("--file", default=None)
    say_p.add_argument(
        "--verify", action="store_true",
        help=(
            "poll the exchange after posting and report "
            "DELIVERED/PENDING/STRANDED/UNKNOWN (fleet-config#453, #643); "
            "never auto-retries"
        ),
    )
    say_p.add_argument("--timeout", type=float, default=commands.DEFAULT_VERIFY_TIMEOUT)
    say_p.add_argument("--poll-interval", type=float, default=DEFAULT_VERIFY_POLL_INTERVAL)
    say_p.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    say_p.set_defaults(func=commands.cmd_say)

    st = sub.add_parser("stop", help="stop a live session (quit by default)")
    st.add_argument("sid")
    st.add_argument("--kill", action="store_true")
    st.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    st.set_defaults(func=commands.cmd_stop)

    esc = sub.add_parser("escalate", help="high-priority Telegram ping (fleet-config#443)")
    esc.add_argument("--file", default=None)
    esc.set_defaults(func=commands.cmd_escalate)

    v = sub.add_parser("verify", help="post-flight dirty-tree check (fleet-config#247)")
    v.add_argument("repo")
    v.add_argument("--expect", choices=("merged", "built"), required=True)
    v.add_argument("--branch", default=None)
    v.add_argument("--default-branch", default=None)
    v.set_defaults(func=commands.cmd_verify)

    sw = sub.add_parser("sweep", help="classify (and with --apply remove) finished lane worktrees (fleet-config#1496)")
    chief_sweep.add_cli(sw)
    sw.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    sw.set_defaults(func=commands.cmd_sweep)

    pl = sub.add_parser("plan", help="write the Board's structured plan (fleet-config#1034)")
    chief_plan.add_cli(pl)
