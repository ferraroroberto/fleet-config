"""Focused parser, process, and wrapper-wiring tests for claude_progress.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import claude_progress as cp  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- command construction: caller flags survive, stream contract is owned ----

arguments = [
    "/audit-fleet repo-one",
    "--model",
    "claude-sonnet-5",
    "--effort",
    "high",
    "--permission-mode",
    "bypassPermissions",
]
command = cp.build_command(arguments, executable="claude-test")
check(command[:3] == ["claude-test", "-p", "/audit-fleet repo-one"],
      "build_command adds print mode before the caller prompt")
check(command[3:-3] == arguments[1:], "build_command preserves caller flags and values")
check(command[-3:] == ["--output-format", "stream-json", "--verbose"],
      "build_command owns verbose stream-json output")
check("--include-partial-messages" not in command,
      "build_command does not enable token-delta partial messages")

for reserved in cp.RESERVED_FLAGS:
    try:
        cp.build_command(["hello", reserved], executable="claude-test")
    except ValueError:
        rejected = True
    else:
        rejected = False
    check(rejected, f"build_command rejects formatter-owned flag {reserved}")


# ---- representative native stream: useful milestones, sensitive noise dropped ----

now = [100.0]
lines: list[str] = []
formatter = cp.ProgressFormatter(emit=lines.append, clock=lambda: now[0])

formatter.handle_line(json.dumps({
    "type": "system",
    "subtype": "init",
    "claude_code_version": "2.1.212",
    "model": "claude-haiku-4-5-20251001",
    "session_id": "session-secret",
}))
now[0] += 2
formatter.handle_line(json.dumps({
    "type": "assistant",
    "message": {"content": [{
        "type": "thinking",
        "thinking": "private chain of thought",
        "signature": "SECRET_SIGNATURE",
    }]},
}))
formatter.handle_line(json.dumps({
    "type": "system",
    "subtype": "thinking_tokens",
    "estimated_tokens": 123,
}))
formatter.handle_line(json.dumps({
    "type": "assistant",
    "message": {"content": [{
        "type": "tool_use",
        "id": "tool-1",
        "name": "Bash",
        "input": {
            "description": "Inspect repo with token=xoxb-1234567890-AbCdEfGh",
            "command": "echo raw-command-must-not-appear",
        },
    }]},
}))
now[0] += 3
formatter.handle_line(json.dumps({
    "type": "user",
    "message": {"content": [{
        "type": "tool_result",
        "tool_use_id": "tool-1",
        "content": "raw tool payload must not appear",
        "is_error": False,
    }]},
}))
formatter.handle_line(json.dumps({
    "type": "assistant",
    "message": {"content": [{"type": "text", "text": "Final report ✅\n- done"}]},
}))
formatter.handle_line(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Final report ✅\n- done",
    "signature": "RESULT_SIGNATURE",
}))
formatter.handle_line("not-json SECRET_MALFORMED")
formatter.handle_line(json.dumps({"type": "future_event", "raw": "SECRET_FUTURE"}))
formatter.finish(0)

rendered = "\n".join(lines)
check("Claude Code 2.1.212" in rendered and "session started" in rendered,
      "init event emits a readable session milestone")
check("▶ Bash" in rendered and "✓ Bash completed" in rendered,
      "tool start and completion are paired by tool_use_id")
check("[redacted]" in rendered and "raw-command-must-not-appear" not in rendered,
      "tool summary is allowlisted and secret-redacted")
check("raw tool payload must not appear" not in rendered,
      "tool result payload is never echoed")
check("Final report ✅" in rendered and rendered.count("Final report ✅") == 1,
      "assistant result stays readable without result-event duplication")
check(all(secret not in rendered for secret in (
    "private chain of thought",
    "SECRET_SIGNATURE",
    "RESULT_SIGNATURE",
    "SECRET_MALFORMED",
    "SECRET_FUTURE",
)), "thinking, signatures, and raw malformed/unknown records are absent")
check("1 malformed and 1 unknown" in rendered,
      "malformed and future events are summarized without crashing")
check("✅ completed · exit 0" in rendered, "successful terminal status is explicit")


# ---- stderr redaction and exact child exit-code propagation ----

child_script = (
    "import json,sys; "
    "print(json.dumps({'type':'system','subtype':'init','claude_code_version':'test',"
    "'model':'fixture'}), flush=True); "
    "print('password=do-not-leak', file=sys.stderr, flush=True); "
    "sys.exit(7)"
)
process_lines: list[str] = []
process_formatter = cp.ProgressFormatter(emit=process_lines.append)
exit_code = cp.run_process(
    [sys.executable, "-c", child_script],
    formatter=process_formatter,
)
process_output = "\n".join(process_lines)
check(exit_code == 7, "run_process returns the child exit code unchanged")
check("password=do-not-leak" not in process_output and "[redacted]" in process_output,
      "stderr is surfaced promptly with credentials redacted")
check("❌ failed · exit 7" in process_output,
      "non-zero child exit is visible in the terminal milestone")


# ---- all checked-in scheduled wrappers use the one shared adapter ----

wrapper_expectations = {
    "audit-fleet": ('"/audit-fleet %~1"', "--model claude-sonnet-5", "--effort high"),
    "cleanup-fleet-all": ('"/cleanup-fleet-all %~1"', "--model claude-sonnet-5", "--effort high"),
    "config-map": ('"/config-map"',),
    "context-audit": ('"/context-audit"',),
    "context-purge": ('"/context-purge fleet"', "--model opus"),
    "design-sweep": ('"/design-sweep %~1"', "--model opus", "--effort high"),
    "fleet-health": ('"/fleet-health"',),
    "insights-weekly": ('"/insights-weekly"',),
    "learning-log": ('"/learning-log"', "--model claude-sonnet-4-6"),
    "sota-watch": ('"/sota-watch"',),
    "system-map": ('"/system-map"',),
}
wrappers = sorted((ROOT / ".claude" / "skills").glob("*/run-weekly.bat"))
check(len(wrappers) == len(wrapper_expectations) == 11,
      "the wiring test covers all eleven scheduled wrappers")
for wrapper in wrappers:
    text = wrapper.read_text(encoding="utf-8")
    expected = wrapper_expectations.get(wrapper.parent.name, ())
    check("claude_progress.py" in text and "claude -p" not in text.lower(),
          f"{wrapper.parent.name}: uses the shared adapter, not direct claude -p")
    check("--permission-mode bypassPermissions" in text,
          f"{wrapper.parent.name}: preserves unattended permission mode")
    check(all(fragment in text for fragment in expected),
          f"{wrapper.parent.name}: preserves prompt/model/effort arguments")


_h.report_and_exit("test_claude_progress")
