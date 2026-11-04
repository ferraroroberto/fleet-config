"""Let Grok Bot talk to launcher sessions: send text in, read recent output back.

Grok Bot is the owner's phone front door (fleet-config#1741). Its local commands run on "Always allow",
so no human tap guards a call and everything below is the guard. Two verbs:

    relay.py send <session> --file <path>   deliver the file's text to a live session
    relay.py read <session> [--tail N]      print the session's last reply, credentials redacted

`<session>` is the 8-character id the board prints, or a full id. Every live PTY session is a valid
target, the `telegram:*` channel sessions and the chief included.

`send` refuses, before anything is sent and with a distinct `REFUSED reason=` line each time:
the app-launcher Grok Bot switch off (`disabled`) or unreadable (`unknown`), an empty or unreadable file, text that looks like a credential, a destructive steer (discard work,
delete or adopt a branch, tear down a worktree, force-push: those stay with the owner in the launcher
terminal), a session that is not live or a prefix that names several, a board that cannot be read, and a
call log that cannot be written. Text that passes goes in under a one-line sender tag, so the receiving
session knows it did not come from the owner's keyboard.

Every call appends one line to `grokbot-relay.log` in the hooks state directory: time, verb, the target's
board label, the outcome and a length. Never the message body, never an id.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chief import launcher, rails  # noqa: E402
from fleet_core import hooks_state  # noqa: E402
from grokbot import refusals, switch  # noqa: E402

logger = logging.getLogger("fleet_chief")

REPO = Path(__file__).resolve().parents[3]
LOG_NAME = "grokbot-relay.log"
SENDER_TAG = "[from Grok Bot: relayed from the owner's phone app, not typed at this keyboard]"
DEFAULT_TAIL_CHARS = 4000
BOARD_BUCKETS = ("claude_turn", "your_turn", "other")


def log_path() -> Path:
    return hooks_state.state_dir() / LOG_NAME


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def log_problem() -> str:
    """Why the call log cannot take a line, else an empty string. The log must live outside the code tree."""
    path = log_path()
    if _inside(path, REPO):
        return "the state directory is inside the code tree"
    try:
        # runtime-write-exempt: the call log lives in the hooks state directory, and the check above refuses one inside the code tree
        path.parent.mkdir(parents=True, exist_ok=True)
        # runtime-write-exempt: same hooks-state call log, probed for writability before any send
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        return str(exc)
    return ""


def write_log(verb: str, target: str, outcome: str, chars: int) -> None:
    safe_target = re.sub(r"[^\w:.-]", "_", target) or "unknown"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} verb={verb} target={safe_target} outcome={outcome} chars={chars}\n"
    try:
        # runtime-write-exempt: the call log in the hooks state directory, never a code folder (log_problem refuses one that is)
        with log_path().open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        logger.warning("⚠️ grokbot relay log not written: %s", exc)


def live_cards(board: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Session id -> card for every session that holds a live PTY (an `external` card cannot take input)."""
    columns = board.get("columns") or {}
    cards: Dict[str, Dict[str, Any]] = {}
    for bucket in BOARD_BUCKETS:
        for card in columns.get(bucket) or []:
            if card.get("session_id") and card.get("alive") and card.get("kind") != "external":
                cards[str(card["session_id"])] = card
    return cards


def resolve_target(base_url: str, session: str) -> Tuple[Optional[str], Dict[str, Any], str]:
    """`(full id, its board card, "")` for a live session, or `(None, {}, reason)`.

    `chief_ops.py say` lets an unmatched id through to the endpoint; the relay does not, because a typo
    from a phone must not become a POST to a session that is not there.
    """
    try:
        board = launcher._request(base_url, "/api/board")
    except (OSError, ValueError):
        return None, {}, "board-unreadable"
    cards = live_cards(board)
    resolved, ambiguous = rails.resolve_session_id(session, {"claude_turn": list(cards.values())})
    if ambiguous is not None:
        return None, {}, "ambiguous-session"
    if resolved not in cards:
        return None, {}, "unresolvable-session"
    return resolved, cards[resolved], ""


def target_name(card: Dict[str, Any]) -> str:
    return str(card.get("label") or card.get("project") or "unknown")


def check_text(text: str) -> Optional[str]:
    """The `REFUSED` line for text the relay will not carry, else None."""
    if not text.strip():
        return "REFUSED reason=empty"
    family = refusals.secret_family(text)
    if family:
        return f"REFUSED reason=secret kind={family!r} -- nothing sent; the text looks like a credential"
    family = refusals.destructive_family(text)
    if family:
        return (f"REFUSED reason=destructive kind={family!r} -- nothing sent; this steer needs the owner "
                "in the launcher terminal")
    return None


def tagged(text: str) -> str:
    return f"{SENDER_TAG}\n{text}"


def refuse(verb: str, target: str, line: str, chars: int = 0) -> int:
    reason = re.search(r"reason=([\w-]+)", line)
    write_log(verb, target, f"refused-{reason.group(1) if reason else 'unknown'}", chars)
    print(line)
    return 1


def cmd_send(args: argparse.Namespace) -> int:
    state, _reason = switch.read_switch(args.base_url)
    if state != switch.ON:
        reason = "disabled" if state == switch.OFF else "unknown"
        return refuse("send", "unresolved",
                      f"REFUSED reason={reason} -- nothing sent; the Grok Bot switch is not confirmed on")
    problem = log_problem()
    if problem:
        print(f"REFUSED reason=log-unwritable -- nothing sent ({problem})")
        return 1
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return refuse("send", "unresolved", f"REFUSED reason=unreadable-file -- {type(exc).__name__}")
    refusal = check_text(text)
    if refusal:
        return refuse("send", "unresolved", refusal, len(text))
    sid, card, why = resolve_target(args.base_url, args.session)
    if sid is None:
        return refuse("send", "unresolved", f"REFUSED reason={why} -- nothing sent", len(text))
    name = target_name(card)
    # The one and only POST: a resent steer can double-execute whatever it asked for.
    posted = launcher.post_session_input(args.base_url, sid, tagged(text))
    if posted["ok"]:
        note = f" reason={posted['reason']}" if posted.get("reason") else ""
        write_log("send", name, "sent", len(text))
        logger.info("ℹ️ grokbot relay sent %d chars to %s", len(text), name)
        print(f"SENT target={name} chars={len(text)}{note}")
        return 0
    if posted.get("http_status") is None:
        write_log("send", name, "unconfirmed", len(text))
        print(f"UNCONFIRMED target={name} chars={len(text)} reason={posted['error']} -- it may have landed")
        return 2
    write_log("send", name, "failed", len(text))
    print(f"FAILED target={name} chars={len(text)} reason={posted['error']}")
    return 1


def cmd_read(args: argparse.Namespace) -> int:
    sid, card, why = resolve_target(args.base_url, args.session)
    if sid is None:
        return refuse("read", "unresolved", f"REFUSED reason={why}")
    name = target_name(card)
    try:
        result = launcher._request(args.base_url, f"/api/board/sessions/{sid}/exchange")
    except (OSError, ValueError) as exc:
        write_log("read", name, "unreachable", 0)
        print(f"UNREACHABLE target={name} reason={type(exc).__name__}")
        return 2
    if not result.get("available"):
        write_log("read", name, "unavailable", 0)
        print(f"UNAVAILABLE target={name} reason={result.get('reason')}")
        return 1
    assistant = result.get("assistant") or {}
    text, redacted = refusals.redact_secrets(str(assistant.get("text") or "")[-args.tail:])
    write_log("read", name, "read", len(text))
    print(f"TARGET={name}")
    print(f"TIMESTAMP={assistant.get('timestamp')}")
    print(f"REDACTED={redacted}")
    print(text)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="command", required=True)
    send = sub.add_parser("send", help="deliver a file's text to a live session")
    send.add_argument("session")
    send.add_argument("--file", required=True)
    read = sub.add_parser("read", help="print a session's last reply")
    read.add_argument("session")
    read.add_argument("--tail", type=int, default=DEFAULT_TAIL_CHARS)
    args = parser.parse_args(argv)
    return cmd_send(args) if args.command == "send" else cmd_read(args)


if __name__ == "__main__":
    sys.exit(main())
