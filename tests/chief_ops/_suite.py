"""Shared preamble of the chief_ops suites: import paths, the harness and the board-card fixture.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`). No
network, no `gh`, no live launcher: every part drives synthetic board payloads.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_ops as co  # noqa: E402
import steer_delivery as sd  # noqa: E402

sys.path.insert(0, str(REPO / "hooks"))
import notify_on_idle  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


def _session_card(**over):
    base = {
        "session_id": "abc12345-0000",
        "agent": "claude",
        "label": "",
        "project": "app-launcher",
        "status": "working",
        "age_seconds": 120,
        "alive": True,
        "kind": "pty",
    }
    base.update(over)
    return base


_CHIEF_SID = "7abf17b1b8134042b628557617a5414e"


_WORKER_SID = "27c7aace60ce4d2ba40e2e0f19dd8ca5"


_exchange_columns = {
    "claude_turn": [
        _session_card(project="life-os", session_id="d235f290aaaaaaaaaaaaaaaaaaaaaaaa"),
        _session_card(project="minecraft-bedrock-bot", session_id="d235f299bbbbbbbbbbbbbbbbbbbbbbbb"),
    ],
    "your_turn": [_session_card(project="local-llm-hub", session_id="ad3e8bbbcccccccccccccccccccccccc")],
    "other": [],
}


__all__ = ['REPO', '_CHIEF_SID', '_WORKER_SID', '_exchange_columns', '_h', '_session_card', 'check', 'co', 'make_upstream_and_clone', 'notify_on_idle', 'run_git', 'sd']
