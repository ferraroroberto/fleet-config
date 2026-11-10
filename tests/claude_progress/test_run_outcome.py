"""The scheduled-run outcome is one declared state: golden table, evidence table, doc agreement.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_run_outcome.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import re
from collections import Counter

from _suite import ROOT, _h, check, cp
from run_outcome_cases import FACTS, all_cases, drive
from scheduled import run_outcome as ro

RunState = cp.RunState
STATE_BY_CODE = {state.exit_code: state for state in RunState}

# ---- golden table: the exit code is an interface, so none may change ----
# Recorded from the pre-change `_verdict` (tests/claude_progress/record_run_outcome_golden.py):
# every combination of the eleven outcome-bearing facts for a child exiting 0 and 1,
# a child choosing a reserved code itself, cancellation, a missing exit status and reaped orphans.
golden = json.loads((ROOT / "tests" / "fixtures" / "run_outcome_golden.json").read_text(encoding="utf-8"))
check(golden["facts"] == list(FACTS), "the golden table was recorded over the facts this test enumerates")

code_diffs: list[str] = []
label_changes: Counter = Counter()
for key, facts, child_exit, options in all_cases():
    code, status = drive(facts, child_exit, **options)
    old_code, old_label_index = golden["rows"][key]
    old_label = golden["labels"][old_label_index]
    if code != old_code:
        code_diffs.append(f"{key}: {old_code} -> {code}")
    elif status != old_label:
        # The status line may only change where it contradicted the exit code beside it.
        if old_code == 1 and old_label.startswith("❓ not confirmed · stream truncated"):
            # A failure with no terminal result and a shutdown burst exited 1 under a line
            # promising 122; the line now names the exit code's own state.
            label_changes["truncated-line-beside-exit-1"] += 1
            check(status == "❌ failed", f"{key}: the exit-1 child reads plain failed, got {status!r}")
        elif key.startswith("native:"):
            # A child that chose a reserved code (119, 120, 130, ...) was handed a cause this
            # run's evidence never established; the line now keeps only evidence-backed causes.
            label_changes["cause-borrowed-from-a-native-code"] += 1
            check(status.startswith("❌ failed"), f"{key}: a native code reads failed, got {status!r}")
        else:
            code_diffs.append(f"{key}: unexpected status change {old_label!r} -> {status!r}")

check(not code_diffs, f"no exit code or unexplained status line changed: {code_diffs[:5]}")
check(sum(1 for key in golden["rows"] if key.startswith("enum:")) == 4096,
      "the enumeration covers 2 child exits x 2**11 fact combinations")
check(label_changes["truncated-line-beside-exit-1"] == 24,
      "the audit's 24 contradictory lines are exactly the ones that changed")
check(label_changes["cause-borrowed-from-a-native-code"] == 61,
      "the only other changed lines are native reserved codes that named a cause never established")


# ---- evidence table: one row per reachable combination, impossible ones included ----
def ev(**facts) -> "cp.RunEvidence":
    return cp.RunEvidence(**({"child_exit": 0, "saw_result": True, "saw_tool_use": True} | facts))


TABLE = [
    ("clean run", ev(), RunState.COMPLETED, 0),
    ("clean run carrying unknown records", ev(unknown=40, burst=40), RunState.COMPLETED, 0),
    ("a 5xx the run recovered from", ev(transient_api_error=True), RunState.COMPLETED, 0),
    ("a stale provider error over a clean result", ev(provider_error="auth"), RunState.COMPLETED, 0),
    ("child failed", ev(child_exit=1), RunState.FAILED, 1),
    ("child failed with its own code", ev(child_exit=2), RunState.FAILED, 2),
    ("failed terminal result over a clean exit", ev(result_error=True), RunState.FAILED, 1),
    # impossible: a result error needs a result event. The error still wins over the missing event.
    ("result error with no result event", ev(result_error=True, saw_result=False), RunState.FAILED, 1),
    ("watchdog stall", ev(stalled=True, child_exit=1), RunState.STALLED, 124),
    ("stall outranks a cancel that was not requested", ev(stalled=True, kill_signature=True), RunState.STALLED, 124),
    ("cancel confirmed", ev(cancelled=True, cancel_confirmed=True), RunState.CANCELLED, 130),
    ("cancel not confirmed", ev(cancelled=True), RunState.CANCELLATION_UNCONFIRMED, 114),
    ("cancel outranks a stall", ev(cancelled=True, cancel_confirmed=True, stalled=True), RunState.CANCELLED, 130),
    ("no exit status", ev(child_exit=None), RunState.CANCELLATION_UNCONFIRMED, 114),
    ("upstream 5xx over a failed child", ev(child_exit=1, transient_api_error=True), RunState.TRANSIENT_API, 119),
    ("upstream 5xx outranks an auth error", ev(child_exit=1, transient_api_error=True, provider_error="auth"),
     RunState.TRANSIENT_API, 119),
    ("auth unavailable", ev(child_exit=1, provider_error="auth"), RunState.AUTH_UNAVAILABLE, 117),
    ("model unavailable", ev(child_exit=1, provider_error="model"), RunState.MODEL_UNAVAILABLE, 116),
    ("tools unavailable", ev(child_exit=1, provider_error="tools"), RunState.TOOLS_UNAVAILABLE, 115),
    ("provider error behind a failed result", ev(provider_error="model", result_error=True),
     RunState.MODEL_UNAVAILABLE, 116),
    ("background-kill signature", ev(kill_signature=True), RunState.BACKGROUND_KILLED, 125),
    ("kill signature outranks a self-report", ev(kill_signature=True, self_reported_failure=True),
     RunState.BACKGROUND_KILLED, 125),
    ("self-reported failure", ev(self_reported_failure=True), RunState.SELF_REPORTED_FAILURE, 123),
    ("unfinished tool", ev(open_tools=1), RunState.INCOMPLETE_WORK, 118),
    ("unfinished child", ev(open_children=1), RunState.INCOMPLETE_WORK, 118),
    ("owned orphans", ev(owned_orphans=2), RunState.INCOMPLETE_WORK, 118),
    ("owned orphans outrank a self-report", ev(owned_orphans=2, self_reported_failure=True),
     RunState.INCOMPLETE_WORK, 118),
    ("orphans behind a failed child change nothing", ev(child_exit=1, owned_orphans=2), RunState.FAILED, 1),
    ("no terminal result event", ev(saw_result=False), RunState.STREAM_UNVERIFIED, 122),
    ("malformed record", ev(malformed=1), RunState.STREAM_UNVERIFIED, 122),
    ("unverifiable surface", ev(unverified=1), RunState.STREAM_UNVERIFIED, 122),
    ("truncated stream", ev(saw_result=False, stream_truncated=True, burst=4, unknown=4),
     RunState.STREAM_UNVERIFIED, 122),
    ("truncated stream behind a failed child is the child's failure", ev(child_exit=1, saw_result=False,
                                                                          stream_truncated=True, burst=4),
     RunState.FAILED, 1),
    ("unverified stream outranks zero tool use", ev(saw_result=False, saw_tool_use=False),
     RunState.STREAM_UNVERIFIED, 122),
    ("no tool use", ev(saw_tool_use=False), RunState.NO_TOOL_USE, 120),
    ("child that exits 118 on its own over unfinished work", ev(child_exit=118, open_tools=1),
     RunState.INCOMPLETE_WORK, 118),
    ("child that exits 119 on its own is only a failure", ev(child_exit=119), RunState.FAILED, 119),
    ("child that exits 130 on its own is only a failure", ev(child_exit=130), RunState.FAILED, 130),
]
for name, evidence, expected_state, expected_code in TABLE:
    outcome = cp.decide(evidence)
    check(outcome.state is expected_state and outcome.exit_code == expected_code,
          f"decide: {name} -> {expected_state.name}/{expected_code}, got {outcome.state.name}/{outcome.exit_code}")
check(len({row[2] for row in TABLE}) == len(RunState) - 2,
      "the table reaches every state decide produces (delivery_unconfirmed and start_failed are set by their callers)")

# The `not confirmed` states stay their own, never folded into completed or failed.
for state in (RunState.STREAM_UNVERIFIED, RunState.INCOMPLETE_WORK, RunState.CANCELLATION_UNCONFIRMED,
              RunState.DELIVERY_UNCONFIRMED):
    check(ro.status_line(cp.RunOutcome(state, state.exit_code)).startswith("❓"),
          f"{state.name} reads as not confirmed")


# state-machine-proof: scheduled-run-outcome
# Illegal transitions are refused: no evidence short of a clean, complete, tool-using run reaches
# `completed`, the set of states is closed, and a delivery check cannot upgrade a failure.
def test_illegal_transitions_are_refused() -> None:
    for key, facts, child_exit, options in all_cases():
        if not key.startswith("enum:"):
            continue
        evidence = cp.RunEvidence(
            child_exit=child_exit, stalled=facts["stalled"], owned_orphans=2 if facts["orphans"] else 0,
            transient_api_error=facts["transient"], provider_error="auth" if facts["provider_error"] else "",
            kill_signature=facts["kill"], self_reported_failure=facts["self_reported"],
            result_error=facts["result_error"], open_tools=1 if facts["unfinished"] else 0,
            saw_result=facts["saw_result"], saw_tool_use=facts["tool_use"],
        )
        if cp.decide(evidence).state is RunState.COMPLETED:
            clean = (child_exit == 0 and not any(facts[name] for name in
                     ("stalled", "orphans", "kill", "self_reported", "result_error", "unfinished"))
                     and facts["saw_result"] and facts["tool_use"])
            check(clean, f"{key}: reached completed without clean, complete evidence")
    try:
        RunState(999)
        closed = False
    except ValueError:
        closed = True
    check(closed, "RunState is closed: an unknown value is refused")
    check(ro.apply_delivery_check(cp.decide(ev(child_exit=1)), False).state is RunState.FAILED,
          "a failed child is not renamed by an unconfirmed delivery check")
    check(ro.apply_delivery_check(cp.decide(ev()), False).exit_code == 121,
          "a clean run with an unconfirmed delivery check is delivery_unconfirmed")
    check(ro.apply_delivery_check(cp.decide(ev()), True).state is RunState.COMPLETED,
          "a confirmed delivery check changes nothing")


test_illegal_transitions_are_refused()

# ---- the retry gate reads the same evidence ----
check(ro.safe_to_retry(ev(child_exit=1, transient_api_error=True, retry_before_tools=True, saw_tool_use=False)),
      "a 5xx before any tool is safe to retry")
for field, value in (("saw_tool_use", True), ("unknown", 1), ("malformed", 1), ("unverified", 1),
                     ("open_children", 1), ("kill_signature", True), ("retry_before_tools", False)):
    base = {"child_exit": 1, "transient_api_error": True, "retry_before_tools": True, "saw_tool_use": False}
    check(not ro.safe_to_retry(ev(**(base | {field: value}))), f"{field} closes the retry gate")

# ---- one chain, one reader: the runner holds no precedence of its own ----
runner_source = (ROOT / "skills" / "_lib" / "scheduled_runner.py").read_text(encoding="utf-8")
check(not re.search(r"\bprogress\._\w+", runner_source),
      "scheduled_runner.py reads no private formatter field")
progress_source = (ROOT / "skills" / "_lib" / "scheduled" / "progress.py").read_text(encoding="utf-8")
check("_final_status" not in progress_source, "the formatter holds no second precedence chain")

# ---- the documented exit codes are RunState ----
doc = (ROOT / "docs" / "scheduled-runners.md").read_text(encoding="utf-8")
documented = {int(m["code"]): m["state"]
              for m in re.finditer(r"^\| `(?P<code>\d+)` \| `(?P<state>\w+)` \|", doc, re.MULTILINE)}
check(documented == {state.exit_code: state.name.lower() for state in RunState},
      f"docs/scheduled-runners.md lists exactly RunState's codes and names: {documented}")
check(all(getattr(cp, name) == STATE_BY_CODE[code].exit_code for name, code in (
    ("STALL_EXIT_CODE", 124), ("BACKGROUND_KILL_EXIT_CODE", 125), ("SELF_REPORTED_FAILURE_EXIT_CODE", 123),
    ("TRUNCATED_STREAM_EXIT_CODE", 122), ("NO_TOOL_USE_EXIT_CODE", 120), ("TRANSIENT_API_EXIT_CODE", 119),
    ("INCOMPLETE_WORK_EXIT_CODE", 118), ("AUTH_UNAVAILABLE_EXIT_CODE", 117), ("MODEL_UNAVAILABLE_EXIT_CODE", 116),
    ("MISSING_TOOLS_EXIT_CODE", 115), ("CANCELLATION_UNCONFIRMED_EXIT_CODE", 114), ("CANCELLED_EXIT_CODE", 130),
    ("DELIVERY_NOT_CONFIRMED_EXIT_CODE", 121))), "the exit-code constants are RunState's codes")

_h.report_and_exit("test_run_outcome")
