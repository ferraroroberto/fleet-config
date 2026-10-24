"""The `board` digest treats a standing `telegram:*` channel session as a non-worker.

The digest and `rails.repo_occupancy` decide "is this card a worker" through the one
`rails._is_channel_session`, so a card the digest lists as a session line is a card the
occupancy gate counts as an occupant, and the caller's own card is marked `(you, chief)`.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_chief_ops_digest_channels.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
import chief_ops as co  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _card(**over):
    base = {"session_id": "abc12345-0000", "agent": "claude", "label": "", "project": "app-launcher",
            "status": "working", "age_seconds": 120, "alive": True, "kind": "pty"}
    base.update(over)
    return base


def _channel(sid):
    return _card(project="life-os", label="telegram:main", session_id=sid)


COLUMNS = {
    "backlog": [], "other": [], "done": [],
    "claude_turn": [_channel("aaaa1111"), _channel("bbbb2222"), _card(session_id="cccc3333")],
    "your_turn": [_channel("dddd4444"),
                  _card(project="fleet-config", label="chief", session_id="eeee5555")],
}
BOARD = {
    "columns": COLUMNS,
    "github": {"available": True, "error": None},
    "live_sessions": {"available": True, "error": None},
}

lines = co.format_board_digest(BOARD, caller_sid="eeee5555").splitlines()
check(not any("life-os:" in l for l in lines),
      "digest prints no per-session line for telegram:* channel cards")
check(lines.count("channels (not workers, never block dispatch): life-os x3") == 1,
      f"digest carries one channels summary with per-repo counts (got {lines!r})")
check(any(l.startswith("  fleet-config:") and l.endswith("sid=eeee5555 (you, chief)") for l in lines),
      "digest marks the caller's own card (you, chief)")
check(not any("(you, chief)" in l
              for l in co.format_board_digest(BOARD, caller_sid="zzzz").splitlines()),
      "digest marks no card as the caller when its sid matches none")

cards = COLUMNS["claude_turn"] + COLUMNS["your_turn"]
workers = {c["project"].lower() for c in cards if not co.rails._is_channel_session(c)}
check(set(co.repo_occupancy(COLUMNS)) == workers,
      "digest and repo_occupancy agree on which cards are workers")

_h.report_and_exit("test_chief_ops_digest_channels")
