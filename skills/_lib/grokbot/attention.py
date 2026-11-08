"""Does anything need the owner right now? One on-demand, read-only digest for Grok Bot.

    attention.py            print `ATTENTION=none` or one line per item; exit 0 when every source was read (items or none), 3 when any was not

Sources, all read with GET requests or `gh` reads: the Board's live sessions (a lane waiting on the owner),
its Job cards (a failed Job), the chief plan's open questions (`waiting_on_roberto[]`, read through
`chief_plan.load`), the owner-check labels (`needs-owner`, `needs-validation`), and the Grok Bot
status. A source that could not be read prints its own `UNKNOWN` line and the exit code is 3: unread is never
folded into "nothing needs you". Nothing is sent, dispatched, stopped or written, and nothing polls: the owner
asks, the check runs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chief_plan  # noqa: E402
from chief import launcher, rails  # noqa: E402
from owner_check import github as owner_github  # noqa: E402

from grokbot import status as grok_status  # noqa: E402
from grokbot import switch as grok_switch  # noqa: E402

logger = logging.getLogger("fleet_chief")

EXIT_OK, EXIT_UNKNOWN = 0, 3


def _board_lines(base_url: str) -> Tuple[List[str], List[str]]:
    try:
        board = launcher._request(base_url, "/api/board")
    except (OSError, ValueError) as exc:
        return [], [f"UNKNOWN board: {type(exc).__name__}"]
    items: List[str] = []
    cols = board.get("columns") or {}
    for card in cols.get("your_turn") or []:
        if card.get("alive") and not rails._is_channel_session(card):
            items.append(f"lane waiting: {card.get('project')} {card.get('status')} "
                         f"sid={str(card.get('session_id') or '')[:8]}")
    for card in cols.get("other") or []:
        if card.get("kind") == "job" and str(card.get("state")) == "failed":
            items.append(f"job failed: {card.get('job_name')}")
    return items, []


def _question_lines() -> Tuple[List[str], List[str]]:
    path = chief_plan.plan_file()
    # `load` reads a missing file as the empty plan; here a missing file is an unread source, not "no questions".
    if not path.exists():
        return [], ["UNKNOWN questions: no chief plan file"]
    try:
        doc = chief_plan.load(path)
    except (OSError, ValueError) as exc:
        return [], [f"UNKNOWN questions: {type(exc).__name__}"]
    items = []
    for row in doc["waiting_on_roberto"]:
        ref = f" {row['ref']}" if row.get("ref") else ""
        items.append(f"question {row.get('id', '?')}{ref}: {chief_plan.short_text(str(row.get('text', '')))}")
    return items, []


def _owner_lines() -> Tuple[List[str], List[str]]:
    try:
        result = owner_github.list_items(None)
    except Exception as exc:  # noqa: BLE001 - an unreadable source must report itself, not crash the digest
        return [], [f"UNKNOWN owner checks: {type(exc).__name__}"]
    items = [f"{it['kind']}: {it['ref']}" for it in result["items"]]
    unknown = [f"UNKNOWN owner checks in {m['repo']}: {m['reason']}" for m in result["unknown"]]
    return items, unknown


def _grokbot_lines() -> Tuple[List[str], List[str]]:
    # Off in the launcher is the owner's choice, not an outage; unreadable is unread, not "fine".
    switch_state, switch_reason = grok_switch.read_switch()
    if switch_state == grok_switch.OFF:
        return [], []
    if switch_state != grok_switch.ON:
        return [], [f"UNKNOWN grokbot: {switch_reason}"]
    state = grok_status.read_status()
    if state.state == grok_status.UP:
        return [], []
    if state.state == grok_status.UNKNOWN:
        return [], [f"UNKNOWN grokbot: {state.reason}"]
    return [f"grokbot {state.state}: {state.reason}"], []


def gather(base_url: str, sources: Optional[List[Callable[[], Tuple[List[str], List[str]]]]] = None
           ) -> Dict[str, Any]:
    sources = sources or [lambda: _board_lines(base_url), _question_lines, _owner_lines, _grokbot_lines]
    items: List[str] = []
    unknown: List[str] = []
    for source in sources:
        found, missed = source()
        items += found
        unknown += missed
    return {"items": items, "unknown": unknown}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
    args = parser.parse_args(argv)
    result = gather(args.base_url)
    for line in result["items"]:
        print(f"ATTENTION {line}")
    for line in result["unknown"]:
        print(line)
    if not result["items"] and not result["unknown"]:
        print("ATTENTION=none")
    logger.info("ℹ️ grokbot attention: %d items, %d unknown", len(result["items"]), len(result["unknown"]))
    return EXIT_UNKNOWN if result["unknown"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
