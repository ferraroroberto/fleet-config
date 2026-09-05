"""Unit tests for the foreign-harness payload normalization in hooks/_lib.py
(fleet-config#491).

Grok Build scans `~/.claude/settings.json` for hooks by default, so every hook
in this repo already runs inside a Grok session — but Grok's stdin envelope is
camelCase with lower_snake event values, where Claude Code's is snake_case with
PascalCase events. `normalize_payload()` translates at the one entry point every
hook shares.

Two properties matter most and are asserted hardest here:

1. **Strict pass-through for Claude.** The hooks directory is junctioned live
   into `~/.claude/hooks`, so a merge is fleet-wide the instant it lands, with
   real sessions running against it. A Claude-shaped payload must come back the
   *same object* — this change cannot be allowed to alter any existing behaviour.
2. **No fabricated agent identity.** A Grok payload must be attributable to
   `grok`; silently inheriting Claude's default is the exact class of
   confident-wrong answer the capability matrix exists to prevent.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_payload_normalization.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import io
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import _lib  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. strict pass-through: Claude Code shape is untouched ----

CLAUDE_PRE_TOOL_USE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "git status"},
    "cwd": "E:/automation/fleet-config",
    "session_id": "claude-1",
}

check(
    _lib.normalize_payload(CLAUDE_PRE_TOOL_USE) is CLAUDE_PRE_TOOL_USE,
    "claude payload returns the identical object (no copy, no rewrite)",
)
check(
    _lib.payload_agent(CLAUDE_PRE_TOOL_USE) is None,
    "claude payload gains no agent hint",
)
check(
    _lib.shell_is_ambiguous(CLAUDE_PRE_TOOL_USE) is False,
    "claude payload is not marked shell-ambiguous",
)

# The Pi adapter's own envelope, and bare/edge payloads, must also pass through.
for _label, _sample in (
    ("pi extension envelope", {"event": "input", "session_id": "pi-1", "cwd": "x"}),
    ("empty payload", {}),
    ("payload with only cwd", {"cwd": "E:/automation"}),
):
    check(_lib.normalize_payload(_sample) is _sample, f"pass-through: {_label}")

check(
    _lib.normalize_payload(None) is None,  # type: ignore[arg-type]
    "non-dict input is returned unchanged",
)


# ---- 2. grok event-name translation ----

_EVENT_EXPECTATIONS = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "permission_denied": "PermissionDenied",
    "notification": "Notification",
    "stop": "Stop",
    "session_end": "SessionEnd",
}
for _grok_event, _claude_event in _EVENT_EXPECTATIONS.items():
    _out = _lib.normalize_payload({"hookEventName": _grok_event, "sessionId": "g"})
    check(
        _out.get("hook_event_name") == _claude_event,
        f"event {_grok_event} -> {_claude_event} (got {_out.get('hook_event_name')!r})",
    )

# An event grok adds later must survive rather than vanish.
check(
    _lib.normalize_payload({"hookEventName": "some_future_event"}).get("hook_event_name")
    == "some_future_event",
    "unmapped event keeps its own name (inert, not silently dropped)",
)


# ---- 3. the session-end Stop must not resurrect a deleted row ----

# Grok fires an observe-only Stop *after* SessionEnd, with reason
# channel_closed/shutdown. Mapping it to Claude's `Stop` would re-create the row
# SessionEnd just deleted, stranding a dead session on the Board as `needs-you`.
for _reason in ("channel_closed", "shutdown"):
    _out = _lib.normalize_payload(
        {"hookEventName": "stop", "reason": _reason, "sessionId": "g"}
    )
    check(
        _out.get("hook_event_name") == "StopAtSessionEnd",
        f"stop(reason={_reason}) must NOT map to Stop (got {_out.get('hook_event_name')!r})",
    )

check(
    _lib.normalize_payload(
        {"hookEventName": "stop", "reason": "end_turn", "sessionId": "g"}
    ).get("hook_event_name")
    == "Stop",
    "stop(reason=end_turn) is a real turn end -> Stop",
)
check(
    _lib.normalize_payload({"hookEventName": "stop", "sessionId": "g"}).get(
        "hook_event_name"
    )
    == "Stop",
    "stop with no reason field -> Stop (fail toward the normal meaning)",
)


# ---- 4. tool-name de-aliasing ----

_TOOL_EXPECTATIONS = {
    "run_terminal_command": "Bash",
    "run_terminal_cmd": "Bash",
    "read_file": "Read",
    # Grok collapses Edit/Write/MultiEdit into `search_replace`. It must land on
    # `Write`: `docs_dated_filename_guard` is the only hook demanding a specific
    # member of that family and it demands `Write`, while the four family-set
    # hooks accept either. Mapping to `Edit` disarms the dated-docs guard.
    "search_replace": "Write",
    "grep": "Grep",
    "list_dir": "Glob",
    "web_search": "WebSearch",
    "spawn_subagent": "Task",
}
for _grok_tool, _claude_tool in _TOOL_EXPECTATIONS.items():
    _out = _lib.normalize_payload(
        {"hookEventName": "pre_tool_use", "toolName": _grok_tool, "toolInput": {}}
    )
    check(
        _lib.tool_name(_out) == _claude_tool,
        f"tool {_grok_tool} -> {_claude_tool} (got {_lib.tool_name(_out)!r})",
    )

check(
    _lib.tool_name(
        _lib.normalize_payload(
            {"hookEventName": "pre_tool_use", "toolName": "some_future_tool"}
        )
    )
    == "some_future_tool",
    "unmapped tool keeps its own name",
)


# ---- 5. envelope key renames the hooks actually read ----

_FULL = _lib.normalize_payload(
    {
        "hookEventName": "post_tool_use",
        "sessionId": "grok-9",
        "toolName": "search_replace",
        "toolInput": {"file_path": "E:/x/y.py"},
        "toolResult": {"ok": True},
        "toolUseId": "tu-1",
        "workspaceRoot": "E:/automation/fleet-config",
        "permissionMode": "default",
        "stopHookActive": False,
        "lastAssistantMessage": "done",
        "cwd": "E:/automation/fleet-config",
    }
)
for _key in (
    "hook_event_name",
    "session_id",
    "tool_name",
    "tool_input",
    "tool_response",
    "tool_use_id",
    "workspace_root",
    "permission_mode",
    "stop_hook_active",
    "last_assistant_message",
):
    check(_key in _FULL, f"key present after rename: {_key} (keys={sorted(_FULL)})")

check(_FULL.get("cwd") == "E:/automation/fleet-config", "cwd survives unchanged")
check(
    str(_lib.file_path(_FULL) or "").replace("\\", "/") == "E:/x/y.py",
    "_lib.file_path reads the normalized tool_input",
)
check(
    not [k for k in _FULL if any(c.isupper() for c in k)],
    "no camelCase key survives normalization",
)

# A field xAI adds later should arrive under a predictable snake_case name
# rather than disappearing.
check(
    _lib.normalize_payload({"hookEventName": "stop", "someBrandNewField": 42}).get(
        "some_brand_new_field"
    )
    == 42,
    "unmapped camelCase key falls through the generic converter",
)


# ---- 6. agent attribution + shell ambiguity ----

check(
    _lib.payload_agent(
        _lib.normalize_payload(
            {"hookEventName": "user_prompt_submit", "sessionId": "g", "cwd": "E:/x"}
        )
    )
    == "grok",
    "grok payload is attributed to grok",
)

check(
    _lib.shell_is_ambiguous(
        _lib.normalize_payload(
            {
                "hookEventName": "pre_tool_use",
                "toolName": "run_terminal_command",
                "toolInput": {"command": "ls"},
            }
        )
    )
    is True,
    "grok's single shell tool is marked shell-ambiguous",
)
check(
    _lib.shell_is_ambiguous(
        _lib.normalize_payload(
            {"hookEventName": "pre_tool_use", "toolName": "read_file", "toolInput": {}}
        )
    )
    is False,
    "a non-shell grok tool is not marked shell-ambiguous",
)


# ---- 7. the refusal dialect block() speaks per harness ----

# A live grok 0.2.114 session showed exit 2 arriving at its hook runner as 1,
# which Grok treats as a hook *failure* — and hook failures fail open, so the
# guard printed its refusal and the command ran anyway. Grok's documented escape
# hatch is that a `deny` decision on stdout is honored regardless of exit code.
# Claude Code must keep seeing exactly what it saw before: exit 2, stderr only,
# stdout empty.

_GUARD = str(Path(__file__).resolve().parent.parent / "hooks" / "bash_cmdexe_syntax_guard.py")
_BLOCKED_CMD = "cmd.exe /c dir"


def _drive(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _GUARD],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


_claude_block = _drive(
    {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": _BLOCKED_CMD},
        "cwd": "E:/automation/fleet-config",
    }
)
check(_claude_block.returncode == 2, "claude block still exits 2")
check(
    _claude_block.stdout.strip() == "",
    f"claude block writes NOTHING to stdout (got {_claude_block.stdout.strip()[:80]!r})",
)
check("Blocked:" in _claude_block.stderr, "claude block puts the reason on stderr")

_grok_block = _drive(
    {
        "hookEventName": "pre_tool_use",
        "toolName": "run_terminal_command",
        "toolInput": {"command": _BLOCKED_CMD},
        "cwd": "E:/automation/fleet-config",
    }
)
check(_grok_block.returncode == 2, "grok block also exits 2 (belt and braces)")
check("Blocked:" in _grok_block.stderr, "grok block still puts the reason on stderr")
try:
    _decision = json.loads(_grok_block.stdout.strip() or "{}")
except ValueError:
    _decision = {}
check(
    _decision.get("decision") == "deny",
    f"grok block emits a deny decision on stdout (got {_grok_block.stdout.strip()[:80]!r})",
)
check(
    "Blocked:" in str(_decision.get("reason", "")),
    "the deny decision carries the human-readable reason",
)

# An allowed command must stay silent on stdout under both harnesses, or every
# ordinary tool call would carry a stray JSON blob.
for _shape, _allow_payload in (
    ("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git status"}, "cwd": "E:/x"}),
    ("grok", {"hookEventName": "pre_tool_use", "toolName": "run_terminal_command",
              "toolInput": {"command": "git status"}, "cwd": "E:/x"}),
):
    _allowed = _drive(_allow_payload)
    check(
        _allowed.returncode == 0 and _allowed.stdout.strip() == "",
        f"{_shape}: an allowed command exits 0 with empty stdout",
    )


# ---- agy (Antigravity CLI) shape: toolCall envelope -> Claude vocabulary ----
# fleet-config#546. Verified live against agy 1.1.8: PreToolUse stdin is
# {"toolCall": {"name", "args": {"CommandLine", "Cwd", ...}}, "conversationId",
# "stepIdx", ...}; the `toolCall` envelope is the detection tell.

AGY_PRE_TOOL_USE = {
    "toolCall": {
        "name": "run_command",
        "args": {"CommandLine": "git status --short", "Cwd": "E:/automation/fleet-config", "WaitMsBeforeAsync": 5000},
    },
    "conversationId": "agy-conv-9",
    "stepIdx": 3,
    "modelName": "gemini-3.6-flash-low",
}

_agy = _lib.normalize_payload(dict(AGY_PRE_TOOL_USE))
check(_agy.get("hook_event_name") == "PreToolUse", "agy: event maps to PreToolUse")
check(_agy.get("tool_name") == "Bash", "agy: run_command maps to Bash")
check(_agy.get("tool_input", {}).get("command") == "git status --short", "agy: CommandLine lands as tool_input.command")
check(_agy.get("cwd") == "E:/automation/fleet-config", "agy: Cwd lands as cwd")
check(_agy.get("session_id") == "agy-conv-9", "agy: conversationId lands as session_id")
check(_lib.payload_agent(_agy) == "antigravity", "agy: agent hint is antigravity")
check(_lib.shell_is_ambiguous(_agy) is True, "agy: run_command is shell-ambiguous")

# An unknown agy tool passes its name through rather than vanishing
_agy_other = _lib.normalize_payload({"toolCall": {"name": "read_file", "args": {}}})
check(_agy_other.get("tool_name") == "read_file", "agy: unknown tool name passes through")
check(_lib.shell_is_ambiguous(_agy_other) is False, "agy: non-shell tool is not marked ambiguous")

# ---- Copilot CLI shape: string toolArgs envelope -> Claude vocabulary ----
# fleet-config#547. Verified live against Copilot CLI 1.0.77: preToolUse stdin
# is {"sessionId", "timestamp", "cwd", "toolName", "toolArgs": "<JSON string>"},
# camelCase, no event name — the string-typed toolArgs is the tell.

COPILOT_PRE_TOOL_USE = {
    "sessionId": "cop-sess-1",
    "timestamp": 1785694280490,
    "cwd": "E:\\automation\\fleet-config",
    "toolName": "powershell",
    "toolArgs": "{\"command\":\"git status --short\",\"description\":\"d\",\"mode\":\"sync\"}",
}

_cop = _lib.normalize_payload(dict(COPILOT_PRE_TOOL_USE))
check(_cop.get("hook_event_name") == "PreToolUse", "copilot: event maps to PreToolUse")
check(_cop.get("tool_name") == "PowerShell", "copilot: powershell maps to PowerShell (tool names the real shell)")
check(_cop.get("tool_input", {}).get("command") == "git status --short", "copilot: toolArgs command lands as tool_input.command")
check(_cop.get("tool_input", {}).get("mode") == "sync", "copilot: other toolArgs keys are preserved for modifiedArgs echo")
check(_cop.get("session_id") == "cop-sess-1", "copilot: sessionId lands as session_id")
check(_lib.payload_agent(_cop) == "copilot", "copilot: agent hint is copilot")
check(_lib.shell_is_ambiguous(_cop) is False, "copilot: not shell-ambiguous (toolName is the shell)")

_cop_bad = _lib.normalize_payload({"toolName": "powershell", "toolArgs": "{not json"})
check(_cop_bad.get("tool_input") == {}, "copilot: malformed toolArgs degrades to empty tool_input")

# Claude identity property must survive the new branches
_claude_again = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "x"}}
check(
    _lib.normalize_payload(_claude_again) is _claude_again,
    "claude payload still returns the identical object after the agy + copilot branches",
)


# ---- Codex transport: invoked entry point, never inherited launcher identity ----
def _refusal(raw: str, entry: str) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", [entry]), patch.object(sys, "stdin", io.StringIO(raw)):
        with redirect_stdout(out), redirect_stderr(err):
            _lib.read_stdin_json()
            try:
                _lib.block("Fleet sentinel refused")
            except SystemExit as exc:
                return exc.code, out.getvalue(), err.getvalue()


_codex_entry = str(Path.cwd() / ".codex" / "hooks" / "guard.py")
for _event in ("PreToolUse", "PostToolUse", "FutureEvent", None):
    _payload = {"hook_event_name": _event, "tool_name": "Bash", "tool_input": {"command": "sentinel"}}
    _code, _stdout, _stderr = _refusal(json.dumps(_payload), _codex_entry)
    if _event == "PreToolUse":
        try:
            _wire = json.loads(_stdout)
        except ValueError:
            _wire = {}
        check(_code == 0 and _wire == {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Fleet sentinel refused"}}, "Codex: structured PreToolUse deny exits 0")
    else:
        check(_code == 2 and not _stdout, f"Codex: {_event} never claims a PreToolUse denial")

for _raw in ("", "not json", "[]", "null"):
    _code, _stdout, _stderr = _refusal(_raw, _codex_entry)
    check(_code == 2 and not _stdout, "malformed input cannot reuse confirmed Codex/event state")

for _entry in (str(Path.cwd() / ".claude" / "hooks" / "guard.py"),
               str(Path.cwd() / ".codex" / "elsewhere" / "guard.py")):
    with patch.dict("os.environ", {"APP_LAUNCHER_AGENT": "codex", "CODEX_THREAD_ID": "inherited"}):
        _code, _stdout, _stderr = _refusal(json.dumps(CLAUDE_PRE_TOOL_USE), _entry)
    check(_code == 2 and not _stdout and _stderr == "Fleet sentinel refused\n",
          "unknown/Claude entry: inherited Codex environment does not change stderr/exit contract")

with patch.object(sys, "argv", [_codex_entry]):
    _normalized = _lib.normalize_payload(CLAUDE_PRE_TOOL_USE)
check(_lib.payload_agent(_normalized) == "codex", "Codex invoked hook carries harness provenance")
check(_normalized.get("tool_input") is CLAUDE_PRE_TOOL_USE["tool_input"], "Codex preserves tool input identity")
check(_lib.AGENT_HINT_KEY not in CLAUDE_PRE_TOOL_USE, "Codex normalization never mutates caller input")

_h.report_and_exit("test_payload_normalization")
