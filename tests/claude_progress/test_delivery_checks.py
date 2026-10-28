"""claude_progress delivery judgement: a self-reported zero-work run, the checked-in scheduled wrappers, the captured end-to-end run, --delivery-check and a run that invoked nothing.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_delivery_checks.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from _suite import INIT_LINE, ROOT, TOOL_LINE, _h, check, cp, zero_work_script  # noqa: E402


# ---- self-reported zero-work run: ends normally, delivers nothing (fleet-config#519) ----

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
    "result": "Fleet audit — 0 to audit, 31 unchanged; digest posted, Telegram pinged.",
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

# fleet-config#829: a scheduled run's stream includes the body of the invoked
# SKILL.md itself, injected as a "user"-kind text block when the Skill tool
# fires (not a tool_result tied to that call). cleanup-fleet-all's own SKILL.md
# carries a worked example of the marker at column 0 to teach the model the
# contract -- that example must never be scored, even though it is
# byte-identical to a genuine assertion at the block level. Proven to fail
# against pre-fix code: before the `role` field, both fixtures below reported
# SELF_REPORTED_FAILURE_EXIT_CODE.
SKILL_PREAMBLE = (
    "**If (and only if) step 7's workflow result carries a non-null "
    "`halted`**, also print the literal line, exactly as shown above:\n\n"
    "SCHEDULED-RUN-FAILED — halted at local-llm-hub#N: "
    "E:\\automation\\local-llm-hub-wt-451 would not delete, "
    "4 issue(s) never started\n\n"
    "Next: escalated issues need a human."
)
preamble_event_line = (
    "print(json.dumps({'type':'user','message':{'content':"
    f"[{{'type':'text','text':{SKILL_PREAMBLE!r}}}]}}}}), flush=True); "
)

# 1. Preamble present, run genuinely delivered: must stay green.
clean_report = "Cleanup-fleet-all complete\n  documentation: 3 merged, 1 escalated"
clean_preamble_script = (
    "import json,sys; "
    + INIT_LINE
    + preamble_event_line
    + TOOL_LINE
    + f"report = {clean_report!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)
clean_preamble_formatter = cp.ProgressFormatter(emit=lambda *_: None)
clean_preamble_exit = cp.run_process(
    [sys.executable, "-c", clean_preamble_script],
    formatter=clean_preamble_formatter,
)
check(clean_preamble_exit == 0,
      "the SKILL.md preamble's column-0 marker example does not fail a delivered run (#829)")
check(not clean_preamble_formatter.saw_self_reported_failure,
      "a skill-body preamble quoting the marker is never scored as a self-reported failure")

# 2. Same preamble, but this run's own final report genuinely asserts the
# marker: must still fail. Proves the fix narrowed *where* the adapter looks
# (skip "user"-role text), not *what* it looks for -- a real assertion right
# after the same ignored preamble still trips it.
failed_report = (
    "Cleanup-fleet-all HALTED at local-llm-hub#451\n"
    "SCHEDULED-RUN-FAILED — halted at local-llm-hub#451: would not delete, "
    "4 issue(s) never started"
)
failed_preamble_script = (
    "import json,sys; "
    + INIT_LINE
    + preamble_event_line
    + TOOL_LINE
    + f"report = {failed_report!r}; "
    "print(json.dumps({'type':'assistant','message':{'content':"
    "[{'type':'text','text':report}]}}), flush=True); "
    "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
    "'result':report}), flush=True); "
    "sys.exit(0)"
)
failed_preamble_formatter = cp.ProgressFormatter(emit=lambda *_: None)
failed_preamble_exit = cp.run_process(
    [sys.executable, "-c", failed_preamble_script],
    formatter=failed_preamble_formatter,
)
check(failed_preamble_exit == cp.SELF_REPORTED_FAILURE_EXIT_CODE,
      "a genuine marker in the run's own final report still fails, right after the same ignored preamble (#829)")
check(failed_preamble_formatter.saw_self_reported_failure,
      "the formatter still records a real self-reported delivery failure alongside a suppressed preamble")


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
    "perf-sweep": ('"/perf-sweep %~1"', "--model claude-sonnet-5"),
    "prompt-audit": ('"/prompt-audit %~1"', "--delivery-check"),
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
check(len(wrappers) == len(wrapper_expectations) == 13,
      "the wiring test covers all thirteen scheduled wrappers")
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
    "'task_id':'child-1','description':'fan out'}), flush=True); "
    "sys.exit(0)"
)
task_formatter = cp.ProgressFormatter(emit=lambda *_: None)
task_exit = cp.run_process([sys.executable, "-c", task_script], formatter=task_formatter)
check(task_exit == cp.INCOMPLETE_WORK_EXIT_CODE and task_formatter.saw_tool_use,
      "a dispatched task counts as work but must finish before success (#750)")

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

_h.report_and_exit("test_delivery_checks")
