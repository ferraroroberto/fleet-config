"""Focused parser, process, and wrapper-wiring tests for claude_progress.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
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
# design-drift issues filed, Slack digest posted, final report printed — and
# still exited 122, because `stream_truncated` only counted the unknown-record
# burst and never checked whether the terminal `result` event had actually
# arrived. The burst was the normal end-of-run sub-agent task-completion
# flush, not a cut-off stream.

delivered_lines: list[str] = []
delivered_formatter = cp.ProgressFormatter(emit=delivered_lines.append, clock=lambda: 0.0)
delivered_formatter.handle_line(json.dumps({"type": "result", "result": "done"}))
for _ in range(cp.UNKNOWN_BURST_THRESHOLD):
    delivered_formatter.handle_line(json.dumps({"type": "future_event"}))
check(not delivered_formatter.stream_truncated,
      "a shutdown burst after a terminal result event is not truncation")
delivered_formatter.finish(0)
delivered_output = "\n".join(delivered_lines)
check("no terminal result event" not in delivered_output,
      "the result event was received, so finish() must not claim otherwise")
check("✅ completed · exit 0" in delivered_output,
      "a run that delivered its result event reports as completed, not unconfirmed")
check("❓ not confirmed" not in delivered_output,
      "a delivered run is never reported as unconfirmed")

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


# ---- stall watchdog: a silent run is killed, not left wedged (fleet-config#411) ----

# The child leaves a grandchild holding the inherited stdout pipe and then goes
# quiet — the exact shape that wedged a scheduled run for eight hours. Killing
# only the direct child would leave that pipe open and hang the read loop here,
# so this also pins the tree-kill.
INIT_LINE = (
    "print(json.dumps({'type':'system','subtype':'init',"
    "'claude_code_version':'stall-fixture','model':'fixture'}), flush=True); "
)
# fleet-config#689 made a clean exit with zero tool invocations red, so every
# fixture that asserts `exit 0` has to invoke something, as any real run does.
TOOL_LINE = (
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'tool_use','id':'t1','name':'Bash','input':{}}]}}), flush=True); "
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
    + TOOL_LINE
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
    [sys.executable, "-c", "import json; " + INIT_LINE + TOOL_LINE],
    formatter=cp.ProgressFormatter(emit=disabled_lines.append),
    stall_timeout=0,
)
check(disabled_exit == 0 and "⏱ stalled" not in "\n".join(disabled_lines),
      "stall_timeout=0 disables the watchdog")


# ---- the CLI background-wait ceiling is lifted for every child (fleet-config#519) ----

# #506 only made the sub-agent kill *visible*; the work was still lost. These
# probes exit with a code encoding what the child actually saw, so a missing or
# wrong value can never pass as a clean run.
CEILING = cp.BG_WAIT_CEILING_ENV
ceiling_probe = (
    "import json,os,sys; "
    + INIT_LINE
    + TOOL_LINE
    + f"sys.exit(0 if os.environ.get({CEILING!r}) == '0' else 3)"
)
ceiling_lines: list[str] = []
ceiling_exit = cp.run_process(
    [sys.executable, "-c", ceiling_probe],
    formatter=cp.ProgressFormatter(emit=ceiling_lines.append),
)
check(ceiling_exit == 0, f"run_process sets {CEILING}=0 in the spawned child's environment")

# A stale value inherited from the parent must not silently reinstate the kill.
os.environ[CEILING] = "600000"
inherited_lines: list[str] = []
try:
    inherited_exit = cp.run_process(
        [sys.executable, "-c", ceiling_probe],
        formatter=cp.ProgressFormatter(emit=inherited_lines.append),
    )
finally:
    del os.environ[CEILING]
check(inherited_exit == 0,
      "an inherited ceiling from the parent environment is overridden, never honoured")

# An explicit caller override is still the escape hatch and still wins.
override_probe = (
    "import json,os,sys; "
    + INIT_LINE
    + TOOL_LINE
    + f"sys.exit(0 if os.environ.get({CEILING!r}) == '900' else 3)"
)
override_lines: list[str] = []
override_exit = cp.run_process(
    [sys.executable, "-c", override_probe],
    formatter=cp.ProgressFormatter(emit=override_lines.append),
    env={CEILING: "900"},
)
check(override_exit == 0, "an explicit env= override still beats the adapter's default ceiling")


# ---- self-reported zero-work run: ends normally, delivers nothing (fleet-config#519) ----

# Kept ASCII on purpose: this string crosses the Windows argv boundary into a
# `python -c` child, where non-ASCII is not reliably preserved (fleet-config#523).
ZERO_REPORT = "Fleet audit - 0 repos evaluated.\nSCHEDULED-RUN-FAILED - the fleet sweep returned no repos"
zero_work_script = (
    "import json,sys; "
    + INIT_LINE
    + f"report = {ZERO_REPORT!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)
zero_lines: list[str] = []
zero_formatter = cp.ProgressFormatter(emit=zero_lines.append)
zero_exit = cp.run_process(
    [sys.executable, "-c", zero_work_script],
    formatter=zero_formatter,
)
zero_output = "\n".join(zero_lines)
check(zero_exit == cp.SELF_REPORTED_FAILURE_EXIT_CODE,
      "a self-reported zero-work run exits non-zero even though the child exited 0")
check(zero_formatter.saw_self_reported_failure,
      "the formatter records that the run reported its own delivery assertion failed")
check("❌ failed" in zero_output and "delivered no work" in zero_output,
      "the terminal milestone names the zero-work cause distinctly")
check("✅ completed" not in zero_output, "a zero-work run is never reported as completed")

# The final report arrives twice (assistant text, then the result event); the
# marker must register on the first copy, before dedup drops the second.
dup_report = "Final report\nSCHEDULED-RUN-FAILED — digest comment never attempted"
dup_lines: list[str] = []
dup_formatter = cp.ProgressFormatter(emit=dup_lines.append, clock=lambda: 0.0)
dup_formatter.handle_event(
    {"type": "assistant", "message": {"content": [{"type": "text", "text": dup_report}]}}
)
dup_formatter.handle_event(
    {"type": "result", "subtype": "success", "is_error": False, "result": dup_report}
)
dup_formatter.finish(0)
dup_output = "\n".join(dup_lines)
check(dup_formatter.saw_self_reported_failure,
      "the marker registers even when the duplicate copy is deduped away")
check(dup_output.count("digest comment never attempted") == 1,
      "the deduped second copy of the report is still not re-emitted")

# A run that delivered must stay green — the marker is opt-in, not a default.
delivered_lines: list[str] = []
delivered_formatter = cp.ProgressFormatter(emit=delivered_lines.append, clock=lambda: 0.0)
delivered_formatter.handle_event({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Fleet audit — 0 to audit, 31 unchanged; digest posted, Slack pinged.",
})
delivered_formatter.finish(0)
check(not delivered_formatter.saw_self_reported_failure
      and "✅ completed" in "\n".join(delivered_lines),
      "an all-unchanged run that still delivered a digest stays a success")

# The marker is line-anchored: a successful run that quotes its own rulebook
# ("...prints SCHEDULED-RUN-FAILED when the assertion fails") is not turned red.
check(not cp._is_self_reported_failure(
          "Delivered. The rule says to print SCHEDULED-RUN-FAILED when nothing shipped."),
      "a mid-sentence mention of the marker does not trip the detector")
for shape in (
    "SCHEDULED-RUN-FAILED - no repo evaluated",
    "report\n  SCHEDULED-RUN-FAILED - no digest",
    "report\n- SCHEDULED-RUN-FAILED - no digest",
    "report\n> SCHEDULED-RUN-FAILED - no digest",
):
    check(cp._is_self_reported_failure(shape),
          f"the marker is detected at the start of a line: {shape.splitlines()[-1].strip()[:34]!r}")

# The marker is a contract between this adapter and the skills that print it.
audit_skill = (ROOT / ".claude" / "skills" / "audit-fleet" / "SKILL.md").read_text(encoding="utf-8")
check(cp.SELF_REPORTED_FAILURE_MARKER in audit_skill,
      "audit-fleet's SKILL.md prints the exact marker string the adapter detects")
check("delivery assertion" in audit_skill.lower(),
      "audit-fleet's SKILL.md carries the zero-work delivery assertion")

# fleet-config#612: a halted cleanup-fleet-all run (residue, lanes unprocessed)
# previously exited 0 and showed green on the Jobs card because the halt path
# never printed the marker. SKILL.md now documents the contract on both the
# pre-flight stops and the halted final-summary template.
cleanup_all_skill = (
    ROOT / ".claude" / "skills" / "cleanup-fleet-all" / "SKILL.md"
).read_text(encoding="utf-8")
check(cp.SELF_REPORTED_FAILURE_MARKER in cleanup_all_skill,
      "cleanup-fleet-all's SKILL.md prints the exact marker string the adapter detects")
check("halted" in cleanup_all_skill.lower() and "612" in cleanup_all_skill,
      "cleanup-fleet-all's SKILL.md ties the marker to the halted-run case (#612)")

# Repro (not just code inspection): a final report shaped exactly like step 10's
# halted-run template -- the "HALTED at <repo>#<N>" line plus the marker line --
# must map to the same non-zero exit as any other self-reported failure. Kept
# ASCII on purpose, same reason as ZERO_REPORT above (fleet-config#523).
HALTED_REPORT = (
    "Cleanup-fleet-all HALTED at local-llm-hub#451 - see below\n"
    "  ...\n"
    "  RESIDUE (run halted): local-llm-hub - "
    "E:\\automation\\local-llm-hub-wt-451 would not delete\n"
    "     Not started because of the halt: 4 issue(s) in maintainability\n\n"
    "SCHEDULED-RUN-FAILED - halted at local-llm-hub#451: "
    "would not delete, 4 issue(s) never started"
)
halted_script = (
    "import json,sys; "
    + INIT_LINE
    + f"report = {HALTED_REPORT!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)
halted_lines: list[str] = []
halted_formatter = cp.ProgressFormatter(emit=halted_lines.append)
halted_exit = cp.run_process(
    [sys.executable, "-c", halted_script],
    formatter=halted_formatter,
)
check(halted_exit == cp.SELF_REPORTED_FAILURE_EXIT_CODE,
      "a cleanup-fleet-all halted-run report exits non-zero even though the child exited 0")
check(halted_formatter.saw_self_reported_failure,
      "the formatter records the halted run's self-reported delivery failure")

# The happy-path template (no halt) must stay green — same "opt-in, not default"
# guarantee as the audit-fleet case above, checked against this skill's own words.
COMPLETE_REPORT = (
    "Cleanup-fleet-all complete\n"
    "  documentation: 3 merged, 1 escalated\n"
    "  skipped: 0 repos, 0 issues unprocessed (0 repo state unknown)\n"
)
complete_formatter = cp.ProgressFormatter(emit=lambda *_: None, clock=lambda: 0.0)
complete_formatter.handle_event({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": COMPLETE_REPORT,
})
complete_formatter.finish(0)
check(not complete_formatter.saw_self_reported_failure,
      "a cleanup-fleet-all run that completed all lanes stays a success")

# fleet-config#642: a run that found real candidates and skipped every one of
# their repos processed nothing, so it must not read as a clean sweep. Same
# repro shape as the halted case above -- driven end-to-end through
# `run_process` against a child that exits 0, because a red exit code that
# silently stops matching the marker is worse than no marker at all.
ALL_SKIPPED_REPORT = (
    "Cleanup-fleet-all complete\n"
    "  candidates: 11 dispatched, 0 already-closed, 0 unresolved\n"
    "  skipped: 4 repos, 11 issues unprocessed (1 repo state unknown) - 0 recovered on retry\n"
    "  deferred repos (skipped at pre-flight, re-checked after the last bucket):\n"
    "    website - dirty (working tree not clean) - 7 issue(s) unprocessed\n\n"
    "SCHEDULED-RUN-FAILED - every candidate repo was skipped "
    "(4 repos, 11 issues unprocessed), no lane ran"
)
all_skipped_script = (
    "import json,sys; "
    + INIT_LINE
    + f"report = {ALL_SKIPPED_REPORT!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)
all_skipped_formatter = cp.ProgressFormatter(emit=lambda *_: None)
all_skipped_exit = cp.run_process(
    [sys.executable, "-c", all_skipped_script],
    formatter=all_skipped_formatter,
)
check(all_skipped_exit == cp.SELF_REPORTED_FAILURE_EXIT_CODE,
      "an all-candidate-repos-skipped report exits non-zero even though the child exited 0")
check(all_skipped_formatter.saw_self_reported_failure,
      "the formatter records the all-skipped run's self-reported delivery failure")

# The other side of the same rule: skipping *some* repos is normal operation,
# not a delivery failure -- the marker must stay opt-in even with a non-zero
# skipped count on the mandatory report line.
some_skipped_formatter = cp.ProgressFormatter(emit=lambda *_: None, clock=lambda: 0.0)
some_skipped_formatter.handle_event({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": (
        "Cleanup-fleet-all complete\n"
        "  skipped: 1 repos, 2 issues unprocessed (0 repo state unknown) - 1 recovered on retry\n"
        "  documentation: 3 merged, 0 escalated\n"
    ),
})
some_skipped_formatter.finish(0)
check(not some_skipped_formatter.saw_self_reported_failure,
      "a run that skipped some repos but shipped lanes stays a success")

# The marker text is a contract between this adapter and the skill that prints
# it -- assert the skill really carries the all-skipped wording, so the two
# cannot drift apart silently.
check("every candidate repo was skipped" in cleanup_all_skill,
      "cleanup-fleet-all's SKILL.md documents the all-skipped delivery-failure line (#642)")
check("642" in cleanup_all_skill and "repo_preflight.py" in cleanup_all_skill,
      "cleanup-fleet-all's SKILL.md ties the deferred-repo retry to its helper and issue")


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
    "learning-log": ('"/learning-log"', "--model claude-sonnet-5"),
    "sota-watch": ('"/sota-watch"',),
    "system-map": ('"/system-map"',),
}

# A slot claim — a weekday, a clock time, or "overnight" — restates live
# app-launcher Jobs config, which moves on every Jobs-UI edit while the comment
# stays put; three wrappers were outright fiction by the time it was noticed
# (fleet-config#520). The registry owns the schedule; a wrapper points at it.
_DAY_NAMES = (
    "mon|monday|tue|tues|tuesday|wed|weds|wednesday|thu|thur|thurs|thursday"
    "|fri|friday|sat|saturday|sun|sunday"
)
slot_claim = re.compile(
    rf"\b(?:{_DAY_NAMES})s?\b|\b\d{{1,2}}:\d{{2}}\b|\bovernight\b",
    re.IGNORECASE,
)

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
    slot = slot_claim.search(text)
    found = slot.group(0) if slot else ""
    check(slot is None,
          f"{wrapper.parent.name}: states no schedule slot (found {found!r})")



# ---- the captured 2026-08-06 run, end to end (#560) ----
#
# tests/fixtures/audit_fleet_20260806_false_success.log is the real adapter
# output from job codebase-audit-fleet run 20260806T110001, kept verbatim: it
# diagnosed the truncation in one line and declared success on the next, and
# the job recorded exit 0 while auditing zero repos. Replaying a child with the
# same shape must now flip the verdict.

_fixture = (ROOT / "tests" / "fixtures" / "audit_fleet_20260806_false_success.log").read_text(
    encoding="utf-8")
check("burst of 5 unknown stream record(s) near shutdown" in _fixture,
      "fixture: the captured run really did detect a truncated stream")
check("✅ completed · exit 0" in _fixture,
      "fixture: and reported it as a completed run on the very next line")

# 19 unknown records then a clean exit 0 — the captured run's shape, with no
# kill signature anywhere, because #519 disabled the kill that used to print it.
_replay_script = (
    "import json,sys; "
    "print(json.dumps({'type':'system','subtype':'init','claude_code_version':'test',"
    "'model':'fixture'}), flush=True); "
    "[print(json.dumps({'type':'future_event','n':i}), flush=True) for i in range(19)]; "
    "sys.exit(0)"
)
_replay_lines: list[str] = []
_replay_formatter = cp.ProgressFormatter(emit=_replay_lines.append)
_replay_exit = cp.run_process([sys.executable, "-c", _replay_script], formatter=_replay_formatter)
_replay_output = "\n".join(_replay_lines)
check(_replay_exit == cp.TRUNCATED_STREAM_EXIT_CODE,
      "replaying the captured run's shape exits non-zero with the truncated-stream code")
check("✅ completed · exit 0" not in _replay_output,
      "replaying the captured run no longer yields the false success it recorded")
check("❓ not confirmed" in _replay_output,
      "replaying the captured run reports delivery as unconfirmed")
check(not _replay_formatter.saw_kill_signature,
      "and it gets there with no kill signature at all — the signal #519 disabled")


# ---- --delivery-check: an outer post-condition on the fact (#560) ----
#
# Every other detector here pattern-matches a *symptom* of a run that delivered
# nothing, and each has been its own incident. This one asks the question that
# actually matters, from outside the child, whatever the child's exit code was.

_dc_flag, _dc_value = cp.DELIVERY_CHECK_FLAG, "E:/check.py"
for form in ([_dc_flag, _dc_value], [f"{_dc_flag}={_dc_value}"]):
    _rem, _stall, _delivery = cp.parse_adapter_flags(["/audit-fleet", *form, "--model", "opus"])
    check(_rem == ["/audit-fleet", "--model", "opus"],
          f"parse_adapter_flags strips {form[0]} — claude would reject it as unknown")
    check(_delivery == _dc_value, f"parse_adapter_flags reads the script path from {form[0]}")

_rem, _stall, _delivery = cp.parse_adapter_flags(
    ["/x", "--stall-timeout", "900", _dc_flag, _dc_value])
check(_rem == ["/x"] and _stall == 900.0 and _delivery == _dc_value,
      "both adapter flags can be given together")
try:
    cp.parse_adapter_flags(["/x", _dc_flag])
    _rejected = False
except ValueError:
    _rejected = True
check(_rejected, "a --delivery-check with no value is a usage error, not a silent skip")

_dc_tmp = Path(tempfile.mkdtemp(prefix="claude_progress_delivery_"))
try:
    _ok = _dc_tmp / "ok.py"
    _ok.write_text("print('digest comment posted 2026-08-06')\n", encoding="utf-8")
    _bad = _dc_tmp / "bad.py"
    _bad.write_text("import sys\nprint('no digest comment for today')\nsys.exit(1)\n", encoding="utf-8")

    _lines: list[str] = []
    check(cp.run_delivery_check(str(_ok), cp.ProgressFormatter(emit=_lines.append)) is True,
          "run_delivery_check: an exit-0 post-condition confirms delivery")
    check(any("delivery confirmed" in line for line in _lines),
          "run_delivery_check: a confirmed delivery says so in the log")

    _lines = []
    check(cp.run_delivery_check(str(_bad), cp.ProgressFormatter(emit=_lines.append)) is False,
          "run_delivery_check: a non-zero post-condition means delivery is NOT confirmed")
    check(any("NOT confirmed" in line and "no digest comment" in line for line in _lines),
          "run_delivery_check: the check's own output is quoted so a human knows what failed")

    _lines = []
    check(cp.run_delivery_check(str(_dc_tmp / "missing.py"),
                                cp.ProgressFormatter(emit=_lines.append)) is False,
          "run_delivery_check: a check that cannot run is unconfirmed, never confirmed")
finally:
    shutil.rmtree(_dc_tmp, ignore_errors=True)

# ---- a run that invoked nothing is not a success (fleet-config#689) ----

# The exact 2026-08-20 shape: session starts, model answers in prose, stream
# completes, child exits 0 -- and not one tool was ever invoked. None of the
# four detectors above can see it: nothing was killed, nothing stalled, the
# stream was whole, and a skill that never started printed no failure marker.
IDLE_REPLY = "Ready — what would you like to do?"
idle_script = (
    "import json,sys; "
    + INIT_LINE
    + f"reply = {IDLE_REPLY!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':reply}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':reply}), flush=True); "
    "sys.exit(0)"
)
idle_lines: list[str] = []
idle_formatter = cp.ProgressFormatter(emit=idle_lines.append)
idle_exit = cp.run_process([sys.executable, "-c", idle_script], formatter=idle_formatter)
idle_output = "\n".join(idle_lines)
check(idle_exit == cp.NO_TOOL_USE_EXIT_CODE,
      "a run that invoked no tools exits non-zero even though the child exited 0")
check(not idle_formatter.saw_tool_use,
      "the formatter records that nothing was ever invoked")
check("invoked no tools" in idle_output,
      "the terminal milestone names the no-tool cause distinctly")
check("✅ completed" not in idle_output,
      "a run that invoked nothing is never reported as completed")
check(idle_exit not in {
          cp.STALL_EXIT_CODE,
          cp.BACKGROUND_KILL_EXIT_CODE,
          cp.SELF_REPORTED_FAILURE_EXIT_CODE,
          cp.TRUNCATED_STREAM_EXIT_CODE,
          cp.DELIVERY_NOT_CONFIRMED_EXIT_CODE,
      },
      "the no-tool exit code stays distinct from all five codes already in use")

# A sub-agent or workflow is a tool invocation too, and arrives on the system
# channel rather than as an assistant `tool_use` block. An orchestrator that
# only dispatches agents must not read as having done nothing.
task_script = (
    "import json,sys; "
    + INIT_LINE
    + "print(json.dumps({'type':'system','subtype':'task_started',"
    "'description':'fan out'}), flush=True); "
    "sys.exit(0)"
)
task_formatter = cp.ProgressFormatter(emit=lambda *_: None)
task_exit = cp.run_process([sys.executable, "-c", task_script], formatter=task_formatter)
check(task_exit == 0 and task_formatter.saw_tool_use,
      "a dispatched task counts as work: task_started alone keeps the run green")

# A child that failed on its own keeps its own verdict -- the no-tool wording
# must not overwrite a cause the child already named.
own_failure_lines: list[str] = []
own_failure_exit = cp.run_process(
    [sys.executable, "-c", "import json,sys; " + INIT_LINE + "sys.exit(7)"],
    formatter=cp.ProgressFormatter(emit=own_failure_lines.append),
)
check(own_failure_exit == 7 and "invoked no tools" not in "\n".join(own_failure_lines),
      "a child that exited non-zero keeps its own exit code and wording")


# The launcher wires it up, or the whole mechanism is theoretical.
_audit_bat = ROOT / ".claude" / "skills" / "audit-fleet" / "run-weekly.bat"
_bat_text = _audit_bat.read_text(encoding="utf-8")
check(cp.DELIVERY_CHECK_FLAG in _bat_text,
      "audit-fleet's launcher passes a delivery check — the run that reported 0 repos as success")
_dc_script = ROOT / ".claude" / "skills" / "audit-fleet" / "delivery_check.py"
check(_dc_script.name in _bat_text and _dc_script.exists(),
      "audit-fleet's delivery check script exists at the path the launcher names")



# ---- transient upstream 5xx: retried, not lost (fleet-config#700) ----

# The exact wording the CLI printed at 01:03 on 2026-08-22, when a 529 three
# minutes into the weekly /context-purge cost the whole week.
INCIDENT_TEXT = (
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary "
    "-- try again in a moment. If it persists, check https://status.claude.com."
)

check(cp._is_transient_api_error(INCIDENT_TEXT),
      "the observed 529 wording is recognised as a transient upstream error")
for _code in ("500", "502", "503", "529"):
    check(cp._is_transient_api_error("API Error: " + _code + " something"),
          "a " + _code + " is treated as transient")
for _code in ("400", "401", "403", "404", "429"):
    check(not cp._is_transient_api_error("API Error: " + _code + " something"),
          "a " + _code + " is NOT transient -- retrying a client error only wastes the window")
check(not cp._is_transient_api_error("Yesterday's log said: API Error: 529 Overloaded"),
      "a quoted error mid-sentence is not mistaken for one this run hit")

# A child that dies to a 5xx having done nothing: classified, and retryable.
TRANSIENT_CHILD = (
    "import json,sys; "
    + INIT_LINE
    + "text = {0!r}; ".format(INCIDENT_TEXT)
    + "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'error','is_error':True,"
    "'result':text}), flush=True); "
    "sys.exit(1)"
)
t_lines: list[str] = []
t_formatter = cp.ProgressFormatter(emit=t_lines.append)
t_exit = cp.run_process([sys.executable, "-c", TRANSIENT_CHILD], formatter=t_formatter)
t_output = "\n".join(t_lines)
check(t_exit == cp.TRANSIENT_API_EXIT_CODE,
      "a failing run that hit a 5xx exits with the transient code, not a bare 1")
check(t_formatter.saw_transient_api_error and t_formatter.retryable_transient_failure,
      "a no-tool 5xx failure is both detected and eligible for retry")
check("transient upstream API error" in t_output and "not a fault in the skill" in t_output,
      "the terminal milestone names the upstream cause distinctly")
check("invoked no tools" not in t_output,
      "the no-tool detector does not overwrite the more specific upstream cause")

# The same error *after* the run did something: still named, never replayed.
WORKED_THEN_FAILED = (
    "import json,sys; "
    + INIT_LINE
    + TOOL_LINE
    + "text = {0!r}; ".format(INCIDENT_TEXT)
    + "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True); "
    "sys.exit(1)"
)
w_formatter = cp.ProgressFormatter(emit=lambda *_: None)
w_exit = cp.run_process([sys.executable, "-c", WORKED_THEN_FAILED], formatter=w_formatter)
check(w_exit == cp.TRANSIENT_API_EXIT_CODE,
      "a 5xx is classified as upstream even when the run had already done work")
check(w_formatter.saw_transient_api_error and not w_formatter.retryable_transient_failure,
      "a run that already invoked a tool is never eligible for retry -- replaying it "
      "would duplicate whatever it already did")

# A 5xx the run recovered from on its own must not invent a red job.
RECOVERED_CHILD = (
    "import json,sys; "
    + INIT_LINE
    + "text = {0!r}; ".format(INCIDENT_TEXT)
    + "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True); "
    + TOOL_LINE
    + "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':'done'}), flush=True); "
    "sys.exit(0)"
)
r_exit = cp.run_process([sys.executable, "-c", RECOVERED_CHILD],
                        formatter=cp.ProgressFormatter(emit=lambda *_: None))
check(r_exit == 0,
      "a run that saw a 5xx but finished cleanly stays green -- classification never "
      "creates a failure, it only renames one")


# ---- the retry policy itself, with sleep injected so it costs no wall clock ----

def _retry_run(child: str, formatter: "cp.ProgressFormatter") -> tuple[int, list[float]]:
    """Drive the retry loop against a fake child; return (exit code, backoffs slept)."""
    slept: list[float] = []
    code = cp.run_with_transient_retry(
        [sys.executable, "-c", child], formatter=formatter, sleep=slept.append,
    )
    return code, slept


retry_lines: list[str] = []
retry_exit, retry_slept = _retry_run(
    TRANSIENT_CHILD, cp.ProgressFormatter(emit=retry_lines.append))
retry_output = "\n".join(retry_lines)
_schedule = list(cp.TRANSIENT_API_BACKOFF_SECONDS[: cp.TRANSIENT_API_MAX_ATTEMPTS - 1])
check(retry_slept == _schedule,
      "a persistently-transient failure retries on the declared backoff schedule")
check(len(retry_slept) == cp.TRANSIENT_API_MAX_ATTEMPTS - 1,
      "the retry budget is bounded -- it never loops indefinitely against a struggling API")
# The attempt cap and the backoff tuple are two constants that must agree; the
# log's `n/N` is derived from the trimmed schedule so it can never promise an
# attempt the loop will not make.
check(retry_output.count("/" + str(len(_schedule)) + " in ") == len(_schedule),
      "every retry line counts against the honoured schedule, not the raw backoff tuple")
check(retry_exit == cp.TRANSIENT_API_EXIT_CODE,
      "exhausting the retries still exits with the transient code, not a bare 1")
check(retry_output.count("retry 1/") == 1 and retry_output.count("retry 2/") == 1,
      "every retry attempt is visible in the run log")
check("persisted across" in retry_output and "status.claude.com" in retry_output,
      "giving up says so, and points at the status page rather than the diff")

# Not retried: a run that already did work, and a failure of any other class.
worked_exit, worked_slept = _retry_run(
    WORKED_THEN_FAILED, cp.ProgressFormatter(emit=lambda *_: None))
check(worked_slept == [] and worked_exit == cp.TRANSIENT_API_EXIT_CODE,
      "a 5xx after tool use is reported but never retried")

plain_exit, plain_slept = _retry_run(
    "import json,sys; " + INIT_LINE + TOOL_LINE + "sys.exit(7)",
    cp.ProgressFormatter(emit=lambda *_: None))
check(plain_slept == [] and plain_exit == 7,
      "an ordinary non-transient failure is not retried and keeps its own exit code")

zero_retry_exit, zero_retry_slept = _retry_run(
    zero_work_script, cp.ProgressFormatter(emit=lambda *_: None))
check(zero_retry_slept == [] and zero_retry_exit == cp.SELF_REPORTED_FAILURE_EXIT_CODE,
      "a self-reported zero-work failure is not retried -- retrying cannot fix it")

clean_exit, clean_slept = _retry_run(
    "import json,sys; " + INIT_LINE + TOOL_LINE + "sys.exit(0)",
    cp.ProgressFormatter(emit=lambda *_: None))
check(clean_slept == [] and clean_exit == 0,
      "a clean run is run exactly once")


# The path the whole change exists for: attempt 1 dies to a 529 having done
# nothing, attempt 2 runs normally, and the job goes green instead of losing a
# week. The child fails only while a marker file is absent, so the two attempts
# genuinely differ -- exactly how the real 01:03 failure and the 07:14 hand re-run
# differed, with nothing changed but the moment.
_marker = Path(tempfile.mkdtemp()) / "attempted"
FLAKY_CHILD = (
    "import json,os,sys; "
    + INIT_LINE
    + "marker = {0!r}; ".format(str(_marker))
    + "first = not os.path.exists(marker); "
    + "open(marker,'w').close(); "
    + "text = {0!r}\n".format(INCIDENT_TEXT)
    # Attempt 1 dies to the 5xx having invoked nothing -- the only state in which
    # a retry is safe, and the state the real incident was in. Attempt 2 does the
    # work and reports it.
    + "if first:\n"
      "    print(json.dumps({'type':'assistant','message':{'content':"
      "[{'type':'text','text':text}]}}), flush=True)\n"
      "    sys.exit(1)\n"
    + TOOL_LINE
    + "\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':'digest posted'}), flush=True)\n"
    "sys.exit(0)\n"
)
flaky_lines: list[str] = []
flaky_exit, flaky_slept = _retry_run(
    FLAKY_CHILD, cp.ProgressFormatter(emit=flaky_lines.append))
flaky_output = "\n".join(flaky_lines)
check(flaky_exit == 0,
      "a transient 5xx that clears on the retry ends green -- the run is recovered, "
      "not merely relabelled")
check(flaky_slept == [cp.TRANSIENT_API_BACKOFF_SECONDS[0]],
      "recovery costs exactly one backoff -- the loop stops as soon as an attempt works")
check("retry 1/" in flaky_output and "persisted across" not in flaky_output,
      "the recovered run logs its retry and never claims it gave up")
check(flaky_output.rstrip().endswith("exit 0"),
      "the last word on a recovered run is a clean exit, not attempt 1's failure")


# Retrying is gated on the classification actually landing on 119, not merely on
# a 5xx having been seen. Both cases below are *safe* to retry (no tool use, so
# nothing to duplicate) and were retried by an earlier cut of this change; both
# are pointless, and the stall one is expensive -- each extra attempt buys
# another full 45-minute watchdog window.
_stall_then_5xx = (
    "import json,subprocess,sys,time; "
    + INIT_LINE
    + "text = {0!r}\n".format(INCIDENT_TEXT)
    + "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True)\n"
    "time.sleep(300)\n"
)
stall5_formatter = cp.ProgressFormatter(emit=lambda *_: None)
stall5_slept: list[float] = []
stall5_exit = cp.run_with_transient_retry(
    [sys.executable, "-c", _stall_then_5xx], formatter=stall5_formatter,
    stall_timeout=1.0, sleep=stall5_slept.append,
)
check(stall5_exit == cp.STALL_EXIT_CODE and stall5_slept == [],
      "a run that emits a 5xx and then stalls keeps the stall code and is NOT retried -- "
      "each retry would cost another full watchdog window")

_5xx_then_clean_exit = (
    "import json,sys; "
    + INIT_LINE
    + "text = {0!r}; ".format(INCIDENT_TEXT)
    + "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True); "
    "sys.exit(0)"
)
notool_exit, notool_slept = _retry_run(
    _5xx_then_clean_exit, cp.ProgressFormatter(emit=lambda *_: None))
check(notool_exit == cp.NO_TOOL_USE_EXIT_CODE and notool_slept == [],
      "a no-tool run that saw a 5xx but exited 0 lands on the no-tool code without "
      "burning backoff -- a retry cannot change that verdict")

# finish()'s branch order mirrors run_process's, so the status line can never
# contradict the exit code printed beside it.
_both_signals = (
    "import json,sys; "
    + INIT_LINE
    + "text = {0!r}\n".format(INCIDENT_TEXT)
    + "report = 'Final report\\nSCHEDULED-RUN-FAILED - nothing delivered'\n"
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':text}]}}), flush=True)\n"
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True)\n"
    "sys.exit(1)\n"
)
both_lines: list[str] = []
both_exit = cp.run_process([sys.executable, "-c", _both_signals],
                           formatter=cp.ProgressFormatter(emit=both_lines.append))
both_output = "\n".join(both_lines)
check(both_exit == cp.TRANSIENT_API_EXIT_CODE,
      "a run carrying both a 5xx and a self-reported failure exits with the upstream code")
check("transient upstream API error" in both_output and "delivered no work" not in both_output,
      "the verdict line agrees with the exit code beside it rather than naming a "
      "different cause")


# ---- reset_for_retry: the next attempt is judged on its own evidence ----

reset_formatter = cp.ProgressFormatter(emit=lambda *_: None)
reset_formatter.handle_event({"type": "assistant", "message": {"content": [
    {"type": "text", "text": INCIDENT_TEXT}]}})
reset_formatter.handle_event({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}})
reset_formatter.handle_event({"type": "result", "subtype": "error", "is_error": True})
check(reset_formatter.saw_transient_api_error and reset_formatter.saw_tool_use,
      "precondition: the formatter carries attempt-1 state before the reset")
reset_formatter.reset_for_retry()
check(not reset_formatter.saw_transient_api_error and not reset_formatter.saw_tool_use
      and not reset_formatter.saw_kill_signature
      and not reset_formatter.saw_self_reported_failure
      and not reset_formatter.stream_truncated,
      "reset_for_retry clears every verdict-bearing field from the failed attempt")

# The clock is deliberately NOT reset: one run, one elapsed timeline. Driven by a
# fake clock because the two facts under test -- that the backoff does not count
# as idle, and that the prefix keeps counting -- are both invisible at real speed.
_ticks = iter([0.0, 200.0])
_last = [0.0]


def _fake_clock() -> float:
    """0s at construction, then 200s forever -- as if a 200s backoff just elapsed."""
    try:
        _last[0] = next(_ticks)
    except StopIteration:
        pass
    return _last[0]


cont_lines: list[str] = []
cont_formatter = cp.ProgressFormatter(emit=cont_lines.append, clock=_fake_clock)
cont_formatter.reset_for_retry()
check(cont_formatter.seconds_since_activity() == 0.0,
      "reset_for_retry re-arms the stall watchdog -- the backoff is not counted as a "
      "silent stream by the next attempt's watchdog")
cont_formatter.emit("after retry")
check(cont_lines and cont_lines[-1].startswith("[03:20]"),
      "the elapsed prefix keeps counting from the original start across a retry, so the "
      "log reads as one run rather than two that each begin at [00:00]")

_h.report_and_exit("test_claude_progress")
