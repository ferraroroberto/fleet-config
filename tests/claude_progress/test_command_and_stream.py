"""claude_progress command construction and stream reading: caller flags, the adapter-owned stall timeout, milestone formatting, stderr redaction, exit-code propagation, the background-task kill signature and shutdown-burst truncation.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_command_and_stream.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import sys

from _suite import _h, check, cp  # noqa: E402


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
check(command[:2] == ["claude-test", "-p"],
      "build_command adds print mode before the caller prompt")
check(command[3:-3] == arguments[1:], "build_command preserves caller flags and values")

# fleet-config#689: the prompt reaching `claude` is an instruction, not a bare
# slash command -- 2.1.237 delivers a `/<skill>` body as passive context, and the
# model answers "Ready -- what would you like to do?" instead of running it.
check("/audit-fleet" not in command[2],
      "build_command does not hand `claude` a bare slash command")
check(command[2].startswith("Run the audit-fleet skill now"),
      "build_command asks for the skill by name")
check("Skill arguments: repo-one" in command[2],
      "build_command forwards the slash command's trailing text as skill arguments")

check("Skill arguments: none" in cp.normalize_skill_prompt("/cleanup-fleet-all "),
      "normalize_skill_prompt: an argument-less command says so rather than trailing off")
_plain = "Run the cleanup-fleet-all skill and report."
check(cp.normalize_skill_prompt(_plain) == _plain,
      "normalize_skill_prompt: a caller's own instruction is passed through untouched")
check(cp.normalize_skill_prompt("not/a/command") == "not/a/command",
      "normalize_skill_prompt: only a leading slash counts as a command")
for _phrase in ("never ask a question", "never end your turn waiting to be resumed"):
    check(_phrase in cp.normalize_skill_prompt("/fleet-health"),
          "normalize_skill_prompt states the unattended contract: " + _phrase)
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
    remaining, stall, _delivery = cp.parse_adapter_flags(["/audit-fleet", *form, "--model", "opus"])
    check(remaining == ["/audit-fleet", "--model", "opus"],
          f"parse_adapter_flags strips {form[0]} and keeps caller flags")
    check(stall == 900.0, f"parse_adapter_flags reads the timeout from {form[0]}")

check(cp.parse_adapter_flags(["/x"]) == (["/x"], None, None),
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
check("❓ not confirmed" in rendered, "malformed or unknown records remain unverified even after a result")


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


# ---- unknown-record burst near shutdown means delivery is UNCONFIRMED ----
#
# This used to be informational-only, on the stated assumption that it was the
# secondary symptom of a kill `KILL_SIGNATURE_TERMS` would catch first. #519
# disabled that kill, and with it the kill message, leaving this as the only
# remaining signal while it was still wired never to fail anything — so on
# 2026-08-06 /audit-fleet printed the burst warning and `✅ completed · exit 0`
# on the very next line, having audited zero repos (fleet-config#560).

burst_lines: list[str] = []
burst_formatter = cp.ProgressFormatter(emit=burst_lines.append, clock=lambda: 0.0)
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    burst_formatter.handle_line(json.dumps({"type": "future_event"}))
check(burst_formatter.stream_truncated, "a shutdown burst is visible before finish() is called")
check(burst_formatter.truncated_stream_burst() == cp.UNKNOWN_BURST_THRESHOLD,
      "the burst count is the number of unknown records inside the window")
burst_formatter.finish(cp.TRUNCATED_STREAM_EXIT_CODE)
burst_output = "\n".join(burst_lines)
check("burst of" in burst_output and "unknown stream record" in burst_output,
      "a burst of unknown records near shutdown is flagged")
check("✅ completed" not in burst_output,
      "a truncated stream never reports as a completed run")
check("❓ not confirmed" in burst_output and "delivery was never verified" in burst_output,
      "a truncated stream is reported as unconfirmed — its own state, not a pass and not a failure")
check("❌ failed" not in burst_output,
      "a truncated stream is not claimed to be a proven failure either")
check(cp.TRUNCATED_STREAM_EXIT_CODE not in (
    0, cp.STALL_EXIT_CODE, cp.BACKGROUND_KILL_EXIT_CODE, cp.SELF_REPORTED_FAILURE_EXIT_CODE,
    cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE),
    "a truncated stream has its own exit code, distinct from stall/kill/self-reported/delivery")

# The burst count is fixed by whoever asks first, so run()'s exit-code decision
# and finish()'s verdict line can never disagree about it.
_drift_clock = iter([0.0] * 6 + [999.0] * 6)
drift_formatter = cp.ProgressFormatter(emit=[].append, clock=lambda: next(_drift_clock))
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    drift_formatter.handle_line(json.dumps({"type": "future_event"}))
_first = drift_formatter.truncated_stream_burst()
check(_first == drift_formatter.truncated_stream_burst(),
      "the burst count is cached, so a later clock cannot change the verdict mid-shutdown")

quiet_lines: list[str] = []
quiet_formatter = cp.ProgressFormatter(emit=quiet_lines.append, clock=lambda: 0.0)
for _ in range(cp.UNKNOWN_BURST_THRESHOLD - 1):
    quiet_formatter.handle_line(json.dumps({"type": "future_event"}))
quiet_formatter.finish(0)
check("burst of" not in "\n".join(quiet_lines),
      "fewer than the threshold of unknown records stays silent")


# ---- a shutdown burst is not truncation when the result event arrived (#608) ----
#
# design-sweep-fleet run 20260813T100001 completed in full — 10 apps swept, 4
# design-drift issues filed, Telegram digest posted, final report printed — and
# still exited 122, because `stream_truncated` only counted the unknown-record
# burst and never checked whether the terminal `result` event had actually
# arrived. The burst was the normal end-of-run sub-agent task-completion
# flush, not a cut-off stream.

delivered_lines: list[str] = []
delivered_formatter = cp.ProgressFormatter(emit=delivered_lines.append, clock=lambda: 0.0)
delivered_formatter.handle_line(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"}))
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    delivered_formatter.handle_line(json.dumps({"type": "future_event"}))
check(not delivered_formatter.stream_truncated,
      "a shutdown burst after a terminal result event is not truncation")
delivered_formatter.finish(0)
delivered_output = "\n".join(delivered_lines)
check("no terminal result event" not in delivered_output,
      "the result event was received, so finish() must not claim otherwise")
check("✅ completed" in delivered_output,
      "a delivered run is not red merely for carrying unrecognised records (#810)")
check("unknown stream record" in delivered_output,
      "and the records it carried are still counted and named in its log (#841)")

# The #560 case this must not weaken: no result event ever arrives, so the
# same burst still means the stream was genuinely cut off mid-flight.
undelivered_lines: list[str] = []
undelivered_formatter = cp.ProgressFormatter(emit=undelivered_lines.append, clock=lambda: 0.0)
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    undelivered_formatter.handle_line(json.dumps({"type": "future_event"}))
check(undelivered_formatter.stream_truncated,
      "a shutdown burst with no result event is still reported as truncated (#560)")
undelivered_formatter.finish(cp.TRUNCATED_STREAM_EXIT_CODE)
undelivered_output = "\n".join(undelivered_lines)
check("❓ not confirmed" in undelivered_output,
      "the no-result-event case still reports delivery as unconfirmed")
check("no terminal result event" in undelivered_output,
      "and still names the missing result event")

_h.report_and_exit("test_command_and_stream")
