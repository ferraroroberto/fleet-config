"""Focused parser, process, and wrapper-wiring tests for claude_progress.py."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
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


# ---- adapter-owned --stall-timeout never reaches claude (fleet-config#411) ----

for form in (["--stall-timeout", "900"], ["--stall-timeout=900"]):
    remaining, stall = cp.parse_adapter_flags(["/audit-fleet", *form, "--model", "opus"])
    check(remaining == ["/audit-fleet", "--model", "opus"],
          f"parse_adapter_flags strips {form[0]} and keeps caller flags")
    check(stall == 900.0, f"parse_adapter_flags reads the timeout from {form[0]}")

check(cp.parse_adapter_flags(["/x"]) == (["/x"], None),
      "parse_adapter_flags leaves an unflagged invocation untouched")
for bad in (["--stall-timeout"], ["--stall-timeout", "soon"]):
    try:
        cp.parse_adapter_flags(["/x", *bad])
    except ValueError:
        rejected = True
    else:
        rejected = False
    check(rejected, f"parse_adapter_flags rejects a malformed timeout: {bad}")

check(cp.resolve_stall_timeout(600.0) == 600.0, "an explicit timeout wins")
check(cp.resolve_stall_timeout(0.0) == 0.0, "an explicit 0 disables the watchdog")
check(cp.resolve_stall_timeout(None) == cp.DEFAULT_STALL_TIMEOUT_SECONDS,
      "no flag and no env falls back to the built-in default")

os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"] = "120"
check(cp.resolve_stall_timeout(None) == 120.0, "the env var sets the fleet-wide default")
os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"] = "not-a-number"
check(cp.resolve_stall_timeout(None) == cp.DEFAULT_STALL_TIMEOUT_SECONDS,
      "a malformed env override degrades to the default instead of crashing")
del os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"]


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


# ---- background-task kill signature: false success at the 600s ceiling (fleet-config#506) ----

kill_script = (
    "import json,sys; "
    "print(json.dumps({'type':'system','subtype':'init','claude_code_version':'test',"
    "'model':'fixture'}), flush=True); "
    "print('Background tasks still running after 600s; terminating', file=sys.stderr, flush=True); "
    "sys.exit(0)"
)
kill_lines: list[str] = []
kill_formatter = cp.ProgressFormatter(emit=kill_lines.append)
kill_exit = cp.run_process(
    [sys.executable, "-c", kill_script],
    formatter=kill_formatter,
)
kill_output = "\n".join(kill_lines)
check(kill_exit == cp.BACKGROUND_KILL_EXIT_CODE,
      "a background-task kill signature forces a non-zero exit even though the child exited 0")
check(kill_formatter.saw_kill_signature,
      "the formatter records that it saw the kill signature")
check("❌ failed" in kill_output and "background tasks killed after timeout" in kill_output,
      "the terminal milestone names the kill-signature cause distinctly")

# Unit-level check on the exact observed incident wording, independent of run_process.
direct_lines: list[str] = []
direct_formatter = cp.ProgressFormatter(emit=direct_lines.append)
direct_formatter.emit_stderr("Background tasks still running after 600s; terminating")
direct_formatter.finish(0)
direct_output = "\n".join(direct_lines)
check(direct_formatter.saw_kill_signature,
      "emit_stderr detects the exact observed incident wording")
check("❌ failed" in direct_output and "exit 0" in direct_output,
      "finish() reports failed even when passed exit_code=0 directly")
check("✅ completed" not in direct_output, "a killed run is never reported as completed")


# ---- unknown-record burst near shutdown is a warning, never a failure by itself ----

burst_lines: list[str] = []
burst_formatter = cp.ProgressFormatter(emit=burst_lines.append, clock=lambda: 0.0)
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    burst_formatter.handle_line(json.dumps({"type": "future_event"}))
burst_formatter.finish(0)
burst_output = "\n".join(burst_lines)
check("burst of" in burst_output and "unknown stream record" in burst_output,
      "a burst of unknown records near shutdown is flagged")
check("✅ completed" in burst_output, "the burst warning alone does not fail a clean run")

quiet_lines: list[str] = []
quiet_formatter = cp.ProgressFormatter(emit=quiet_lines.append, clock=lambda: 0.0)
for _ in range(cp.UNKNOWN_BURST_THRESHOLD - 1):
    quiet_formatter.handle_line(json.dumps({"type": "future_event"}))
quiet_formatter.finish(0)
check("burst of" not in "\n".join(quiet_lines),
      "fewer than the threshold of unknown records stays silent")


# ---- stall watchdog: a silent run is killed, not left wedged (fleet-config#411) ----

# The child leaves a grandchild holding the inherited stdout pipe and then goes
# quiet — the exact shape that wedged a scheduled run for eight hours. Killing
# only the direct child would leave that pipe open and hang the read loop here,
# so this also pins the tree-kill.
INIT_LINE = (
    "print(json.dumps({'type':'system','subtype':'init',"
    "'claude_code_version':'stall-fixture','model':'fixture'}), flush=True); "
)
stall_script = (
    "import json,subprocess,sys,time; "
    + INIT_LINE
    + "subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
    "time.sleep(300)"
)
stall_lines: list[str] = []
started = time.monotonic()
stall_exit = cp.run_process(
    [sys.executable, "-c", stall_script],
    formatter=cp.ProgressFormatter(emit=stall_lines.append),
    stall_timeout=2.0,
)
stall_elapsed = time.monotonic() - started
stall_output = "\n".join(stall_lines)
check(stall_exit == cp.STALL_EXIT_CODE,
      f"a stalled run exits {cp.STALL_EXIT_CODE}, not the killed child's code")
check(stall_elapsed < 60, f"the watchdog returns promptly (took {stall_elapsed:.1f}s)")
check("no stream activity" in stall_output, "the stall is reported with its idle time")
check("⏱ stalled" in stall_output, "the terminal milestone names the stall distinctly")

# A jammed stdout must not cost the kill (fleet-config#514). The diagnostic emit
# used to run *before* _kill_process_tree, so a blocked write parked the watchdog
# thread and the child was never killed. This emit blocks forever on the stall
# line; the run must still tear the tree down and exit STALL_EXIT_CODE — the
# grandchild in stall_script holds the inherited stdout pipe, so the read loop
# only reaches EOF if the whole tree really died.
blocked_lines: list[str] = []
blocked_release = threading.Event()


def blocking_emit(line: str) -> None:
    blocked_lines.append(line)
    if "no stream activity" in line:
        blocked_release.wait()  # never set while the run is in flight


blocked_started = time.monotonic()
blocked_exit = cp.run_process(
    [sys.executable, "-c", stall_script],
    formatter=cp.ProgressFormatter(emit=blocking_emit),
    stall_timeout=2.0,
)
blocked_elapsed = time.monotonic() - blocked_started
blocked_release.set()  # release the parked watchdog thread
check(blocked_exit == cp.STALL_EXIT_CODE,
      "a stall kill still exits 124 when the diagnostic emit blocks forever")
check(blocked_elapsed < 60,
      f"the blocked-emit stall still returns promptly (took {blocked_elapsed:.1f}s)")
check(any("⏱ stalled" in line for line in blocked_lines),
      "finish() still reports the stall after the watchdog's emit blocked")

# A child that keeps talking must never be killed, however long it runs.
chatty_script = (
    "import json,sys,time; "
    + INIT_LINE
    + "[ (print(json.dumps({'type':'system','subtype':'thinking_tokens',"
    "'estimated_tokens':1}), flush=True), time.sleep(0.2)) for _ in range(15) ]"
)
chatty_lines: list[str] = []
chatty_exit = cp.run_process(
    [sys.executable, "-c", chatty_script],
    formatter=cp.ProgressFormatter(emit=chatty_lines.append),
    stall_timeout=2.0,
)
chatty_output = "\n".join(chatty_lines)
check(chatty_exit == 0, "a continuously-producing run is left alone by the watchdog")
check("no stream activity" not in chatty_output and "⏱ stalled" not in chatty_output,
      "a busy run is never reported as stalled")

# Explicitly disabled: the watchdog must not fire at all.
disabled_lines: list[str] = []
disabled_exit = cp.run_process(
    [sys.executable, "-c", "import json; " + INIT_LINE],
    formatter=cp.ProgressFormatter(emit=disabled_lines.append),
    stall_timeout=0,
)
check(disabled_exit == 0 and "⏱ stalled" not in "\n".join(disabled_lines),
      "stall_timeout=0 disables the watchdog")


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
