"""Grok's snake_case alias keys must not override its own camelCase values.

Grok 1.0.70 sends each envelope key twice — camelCase plus a snake_case alias
(user-guide `10-hooks.md`, "camelCase input with snake_case aliases") — and the
alias carries a differently-cased event value: `hookEventName: "stop"` beside
`hook_event_name: "Stop"`. If the alias wins, the session-end `Stop` filter
never sees `stop` and an observe-only session-end fire reaches the chief inbox
as a real turn end.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/payload_normalization/test_grok_snake_aliases.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import _lib  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _stop(reason: str) -> dict:
    return {"hookEventName": "stop", "hook_event_name": "Stop",
            "sessionId": "g", "session_id": "g", "reason": reason}


for _reason in ("channel_closed", "shutdown"):
    _got = _lib.normalize_payload(_stop(_reason)).get("hook_event_name")
    check(_got == "StopAtSessionEnd",
          f"both-keys stop(reason={_reason}) -> StopAtSessionEnd (got {_got!r})")

_got = _lib.normalize_payload(_stop("end_turn")).get("hook_event_name")
check(_got == "Stop", f"both-keys stop(reason=end_turn) -> Stop (got {_got!r})")

# Key order must not decide the winner: the alias may arrive first or last.
_alias_first = {"hook_event_name": "Stop", "hookEventName": "stop",
                "sessionId": "g", "reason": "channel_closed"}
_got = _lib.normalize_payload(_alias_first).get("hook_event_name")
check(_got == "StopAtSessionEnd", f"alias-first stop(channel_closed) -> StopAtSessionEnd (got {_got!r})")

_out = _lib.normalize_payload({
    "hookEventName": "pre_tool_use", "hook_event_name": "PreToolUse",
    "toolName": "run_terminal_command", "tool_name": "run_terminal_command",
    "toolInput": {"command": "git status"}, "tool_input": {"command": "git status"},
    "sessionId": "g", "session_id": "g", "cwd": "E:/x",
})
check(_out.get("hook_event_name") == "PreToolUse",
      f"both-keys pre_tool_use -> PreToolUse (got {_out.get('hook_event_name')!r})")
check(_out.get("session_id") == "g" and _out.get("cwd") == "E:/x",
      "plain snake keys (cwd) and aliased ids survive translation")
check(_lib.payload_agent(_out) == "grok", "both-keys payload is still attributed to grok")

_claude = {"hook_event_name": "Stop", "session_id": "c", "cwd": "E:/x"}
check(_lib.normalize_payload(_claude) is _claude,
      "claude payload is still returned as the identical object")

_h.report_and_exit("test_grok_snake_aliases")
