"""Hook-matrix cases for the notification and SessionStart hooks, plus the py_syntax_check fixture cases."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from acceptance.hook_cases import Case
from acceptance.shared import REPO


# notify_on_idle
NOTIFY_ON_IDLE_CASES: List[Case] = [
    # fleet-config itself has no per-project telegram_chat in projects.toml, but
    # the [global] fallback IS set, so these cases really do drive the hook down
    # its posting path. What keeps them off the wire is `shared.hook_env()`: it
    # closes every token source `_resolve_token` consults *and* sets
    # FLEET_NOTIFY_BLOCK_NETWORK, so the transport refuses before opening a
    # socket. The hook still exits 0, because a blocked send raises the same
    # URLError the callers already convert to a logged False.
    #
    # This comment used to claim the network was out of reach because the env
    # var and settings.json were closed. That was false from the day
    # `_token_from_dotenv` was added — the token resolved off disk and every
    # gate run posted four real pings to Roberto's phone (fleet-config#813).
    # The claim is now enforced rather than asserted: `checks_notify` fails the
    # suite if either half stops holding, so this comment cannot drift back
    # into being a comfortable lie.
    (
        "notify_on_idle: global chat set, missing token -> allow (graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "cwd": str(REPO), "message": "needs input"},
        0,
    ),
    # idle_prompt is now a deliberate no-op (the 💤 nag was dropped). It must exit
    # 0 without attempting a post — exercises the early-return guard.
    (
        "notify_on_idle: idle_prompt -> allow (no-op, idle nag dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "idle_prompt",
         "cwd": str(REPO), "message": "Claude is waiting for your input"},
        0,
    ),
    # permission_prompt still pings — with no token it takes the graceful-fail path.
    (
        "notify_on_idle: permission_prompt -> allow (ping attempted, graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "cwd": str(REPO), "message": "needs permission"},
        0,
    ),
    # fleet-config#443: a permission_prompt that is NOT chief-managed must fall
    # straight through to the existing human-ping path without ever invoking
    # chief_live's `chief-sid` lookup. `shared.hook_env` drops
    # APP_LAUNCHER_SESSION_ID from every hook subprocess, so this runs as an
    # ordinary non-launcher session no matter how the suite itself was started
    # (fleet-config#835) — zero risk of reaching a real live chief session. The
    # payload session_id below is deliberately kept and deliberately ignored:
    # it is the id space that used to key this decision. The genuinely
    # chief-managed branch is covered by direct unit tests on
    # chief_managed_state/parse_chief_sid below and by
    # tests/test_chief_needs_input.py (lookup stubbed) — never end-to-end
    # here, since that would let chief_live query a real launcher.
    (
        "notify_on_idle: permission_prompt in a non-launcher session -> allow (falls through to human ping)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "cwd": str(REPO), "message": "needs permission",
         "session_id": "fleet-config-test-fixture-sid-not-chief-managed"},
        0,
    ),
    # agent_needs_input / agent_completed (fleet-config#274) are background
    # sub-agent lifecycle events, not the parent session asking for you — a
    # deliberate no-op, same treatment as idle_prompt. Must exit 0 without
    # attempting a post.
    (
        "notify_on_idle: agent_needs_input -> allow (no-op, sub-agent noise dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "agent_needs_input",
         "cwd": str(REPO), "message": "needs input"},
        0,
    ),
    (
        "notify_on_idle: agent_completed -> allow (no-op, sub-agent noise dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "agent_completed",
         "cwd": str(REPO), "message": "Agent completed"},
        0,
    ),
]

# session_index, chief_handover_sessionstart, index_lock_sessionstart
SESSION_START_CASES: List[Case] = [
    # ---- session_index: opt-in gating ----
    # A project not opted into capture must be a silent no-op (no indexer spawn).
    (
        "session_index: non-opted project (tempdir) -> no-op exit 0",
        "session_index",
        {"hook_event_name": "SessionStart", "cwd": tempfile.gettempdir()},
        0,
    ),

    # ---- chief_handover_sessionstart: cwd gating (fleet-config#442) ----
    # A session outside fleet-config is a silent no-op regardless of any
    # handover log's presence -- chief only ever runs cwd'd in fleet-config.
    (
        "chief_handover_sessionstart: non-fleet-config cwd -> no-op exit 0",
        "chief_handover_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": tempfile.gettempdir()},
        0,
    ),
    # fleet-config cwd but (almost certainly) no real handover log yet on this
    # machine -- still exit 0 either way; the content-bearing case is covered
    # by the dedicated unit-check function below with an isolated state dir.
    (
        "chief_handover_sessionstart: fleet-config cwd, no log -> exit 0",
        "chief_handover_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(REPO)},
        0,
    ),

    # ---- index_lock_sessionstart: never blocks a session (fleet-config#939) ----
    # A tripwire that can stop a session starting is worse than the condition
    # it reports, so both the "fleet is clean" and the "cwd isn't a repo at
    # all" paths must exit 0. The content-bearing case (a real stranded lock
    # -> a nudge naming it) is covered by the dedicated unit-check function,
    # which builds its own locked repo rather than waiting for the fleet to
    # break.
    (
        "index_lock_sessionstart: non-repo cwd -> exit 0",
        "index_lock_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": tempfile.gettempdir()},
        0,
    ),
    (
        "index_lock_sessionstart: fleet-config cwd -> exit 0",
        "index_lock_sessionstart",
        {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(REPO)},
        0,
    ),
]


def py_syntax_cases(tmp: Path) -> List[Case]:
    """py_syntax_check needs real files: a broken and a good module written under `tmp`."""
    broken = tmp / "broken.py"
    good = tmp / "good.py"
    broken.write_text("def foo(:\n    pass\n", encoding="utf-8")
    good.write_text("def foo():\n    return 1\n", encoding="utf-8")
    return [
        ("py_syntax: broken file -> block",
         "py_syntax_check",
         {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(broken)}},
         2),
        ("py_syntax: good file -> allow",
         "py_syntax_check",
         {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(good)}},
         0),
        ("py_syntax: non-py file -> allow",
         "py_syntax_check",
         {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(tmp / "x.txt")}},
         0),
    ]
