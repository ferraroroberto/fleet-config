"""claude_progress retries: a transient upstream 5xx is retried, the retry policy with injected sleep, and reset_for_retry judging each attempt on its own evidence.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_retry.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from _suite import INIT_LINE, RESULT_LINE, TOOL_LINE, _h, check, cp, zero_work_script  # noqa: E402


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
    "import json,sys; " + INIT_LINE + TOOL_LINE + RESULT_LINE + "sys.exit(0)",
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
check(notool_exit == cp.TRUNCATED_STREAM_EXIT_CODE and notool_slept == [],
      "a run without a terminal result remains unconfirmed despite exit 0, without "
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

_h.report_and_exit("test_retry")
