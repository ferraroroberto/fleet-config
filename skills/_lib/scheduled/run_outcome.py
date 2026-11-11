"""How a scheduled run ended: one declared state, derived in exactly one place.

`RunEvidence` is every fact the runner and the stream parser establish about a
drained run. `decide` is the only function that orders them into a `RunState`;
the exit code, the verdict line, the transient-retry gate and the
delivery-check override all read what it returns. A new detector adds a fact to
the evidence and a rung to `decide`, never a second chain elsewhere.

A fact that was never established is its own state (`STREAM_UNVERIFIED`,
`INCOMPLETE_WORK`, `CANCELLATION_UNCONFIRMED`, `DELIVERY_UNCONFIRMED`), never
folded into `COMPLETED` or `FAILED`. The exit code of each state is an
interface the launcher's job cards and the Telegram alert read.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional

SELF_REPORTED_FAILURE_MARKER = "SCHEDULED-RUN-FAILED"


class RunState(enum.Enum):
    """Every way a scheduled run ends. Value: (exit code, one-line meaning)."""

    COMPLETED = (0, "complete stream and tool work; delivery confirmed if a check was supplied")
    FAILED = (1, "the child failed by its own account; its own non-zero code is kept")
    CANCELLATION_UNCONFIRMED = (114, "cancellation requested or no exit status, descendant termination unconfirmed")
    TOOLS_UNAVAILABLE = (115, "required tools or MCP servers unavailable")
    MODEL_UNAVAILABLE = (116, "model unavailable or unsupported")
    AUTH_UNAVAILABLE = (117, "authentication unavailable")
    INCOMPLETE_WORK = (118, "unfinished tools or children, or owned descendants still running after the provider exited")
    TRANSIENT_API = (119, "upstream 5xx failure; eligible pre-tool attempts may be retried")
    NO_TOOL_USE = (120, "complete turn that invoked no tools")
    DELIVERY_UNCONFIRMED = (121, "delivery postcondition not confirmed")
    STREAM_UNVERIFIED = (122, "missing terminal event, malformed record, shutdown burst or an unverifiable surface")
    SELF_REPORTED_FAILURE = (123, "the skill asserted SCHEDULED-RUN-FAILED")
    STALLED = (124, "stalled stream, killed by the watchdog")
    BACKGROUND_KILLED = (125, "native background-kill signature on stderr")
    START_FAILED = (127, "native executable could not start")
    CANCELLED = (130, "requested cancellation of the owned process tree confirmed")

    @property
    def exit_code(self) -> int:
        return self.value[0]

    @property
    def meaning(self) -> str:
        return self.value[1]


_PROVIDER_STATES = {
    "auth": RunState.AUTH_UNAVAILABLE,
    "model": RunState.MODEL_UNAVAILABLE,
    "tools": RunState.TOOLS_UNAVAILABLE,
}


@dataclasses.dataclass(frozen=True)
class RunEvidence:
    """The facts a drained run leaves behind. Defaults describe a run with none of them."""

    child_exit: Optional[int] = None
    stalled: bool = False
    cancelled: bool = False
    cancel_confirmed: bool = False
    owned_orphans: int = 0
    transient_api_error: bool = False
    retry_before_tools: bool = False
    provider_error: str = ""
    kill_signature: bool = False
    self_reported_failure: bool = False
    result_error: bool = False
    saw_result: bool = False
    saw_tool_use: bool = False
    stream_truncated: bool = False
    burst: int = 0
    malformed: int = 0
    unknown: int = 0
    unverified: int = 0
    open_tools: int = 0
    open_children: int = 0

    @property
    def unfinished_work(self) -> bool:
        return bool(self.open_tools or self.open_children)

    @property
    def unverified_stream(self) -> bool:
        """Something about the run's outcome was never established.

        Not "something was unfamiliar": an unknown record is evidence about the
        parser, not the run, and is deliberately absent here.
        """
        return bool(not self.saw_result or self.malformed or self.unverified)


@dataclasses.dataclass(frozen=True)
class RunOutcome:
    """The decided state, the exit code to return, and the cause the verdict line names."""

    state: RunState
    exit_code: int
    cause: str = ""


_KILLED_CAUSE = (
    "background tasks killed after timeout — orchestrator likely ended its turn "
    "with agents in flight"
)
_SELF_REPORTED_CAUSE = (
    "the run reported it delivered no work "
    f"({SELF_REPORTED_FAILURE_MARKER}) — see its final report for which "
    "delivery assertion failed"
)
_NO_TOOL_CAUSE = (
    "the run invoked no tools at all — the skill never started (check that the "
    "prompt asks for it, not just names it)"
)
_TRANSIENT_CAUSE = (
    "transient upstream API error (5xx) — the run never got going; this is an "
    "API-side fault, not a fault in the skill"
)
_UNCONFIRMED_CAUSE = "owned descendants could not be verified"
_UNVERIFIED_CAUSE = "missing, malformed or unverifiable completion stream"


def incomplete_cause(evidence: RunEvidence) -> str:
    """Which half (or both) of the 118 evidence this run carries."""
    parts = []
    if evidence.unfinished_work:
        parts.append("unfinished tools or children")
    if evidence.owned_orphans:
        parts.append(
            f"{evidence.owned_orphans} owned descendant(s) still running after "
            "the provider exited"
        )
    return " and ".join(parts)


def _unverified_cause(evidence: RunEvidence) -> str:
    if evidence.stream_truncated:
        return (
            f"stream truncated near shutdown ({evidence.burst} unknown record(s)) "
            "— the run was cut off mid-flight and delivery was never verified"
        )
    return _UNVERIFIED_CAUSE


def decide(evidence: RunEvidence) -> RunOutcome:
    """Map a drained run's facts to its state. The only code that orders them.

    Rungs run top to bottom and the first that holds wins. A requested
    cancellation and a watchdog kill outrank everything; a child that already
    failed keeps its own code unless a rung below *renames* the failure (an
    upstream 5xx, an unavailable provider). Every rung after those only ever
    turns a clean exit into a non-zero state, so none of them can create a
    failure out of a run that succeeded.
    """
    if evidence.cancelled:
        if evidence.cancel_confirmed:
            return RunOutcome(RunState.CANCELLED, RunState.CANCELLED.exit_code, "owned process tree stopped")
        return RunOutcome(RunState.CANCELLATION_UNCONFIRMED, RunState.CANCELLATION_UNCONFIRMED.exit_code,
                          _UNCONFIRMED_CAUSE)
    if evidence.stalled:
        return RunOutcome(RunState.STALLED, RunState.STALLED.exit_code)
    exit_code = evidence.child_exit
    if exit_code is None:
        exit_code = RunState.CANCELLATION_UNCONFIRMED.exit_code
    clean = exit_code == 0
    if clean and evidence.owned_orphans:
        return RunOutcome(RunState.INCOMPLETE_WORK, RunState.INCOMPLETE_WORK.exit_code,
                          incomplete_cause(evidence))
    if not clean and evidence.transient_api_error:
        # Only ever over a child that already failed: a run that hit a 5xx,
        # recovered on its own and exited 0 succeeded.
        return RunOutcome(RunState.TRANSIENT_API, RunState.TRANSIENT_API.exit_code, _TRANSIENT_CAUSE)
    if evidence.provider_error and (not clean or evidence.result_error):
        state = _PROVIDER_STATES[evidence.provider_error]
        return RunOutcome(state, state.exit_code, f"{evidence.provider_error} unavailable")
    if clean:
        if evidence.kill_signature:
            return RunOutcome(RunState.BACKGROUND_KILLED, RunState.BACKGROUND_KILLED.exit_code, _KILLED_CAUSE)
        if evidence.self_reported_failure:
            return RunOutcome(RunState.SELF_REPORTED_FAILURE, RunState.SELF_REPORTED_FAILURE.exit_code,
                              _SELF_REPORTED_CAUSE)
        if evidence.result_error:
            return RunOutcome(RunState.FAILED, RunState.FAILED.exit_code)
        if evidence.unfinished_work:
            return RunOutcome(RunState.INCOMPLETE_WORK, RunState.INCOMPLETE_WORK.exit_code,
                              incomplete_cause(evidence))
        if evidence.unverified_stream:
            # After the specific detectors, which name the cause; this one only
            # knows the stream stopped mid-conversation.
            return RunOutcome(RunState.STREAM_UNVERIFIED, RunState.STREAM_UNVERIFIED.exit_code,
                              _unverified_cause(evidence))
        if not evidence.saw_tool_use:
            # Last of all: every detector above names a cause, this one only
            # knows the run touched nothing.
            return RunOutcome(RunState.NO_TOOL_USE, RunState.NO_TOOL_USE.exit_code, _NO_TOOL_CAUSE)
        return RunOutcome(RunState.COMPLETED, 0)
    if evidence.child_exit is None:
        return RunOutcome(RunState.CANCELLATION_UNCONFIRMED, exit_code, _UNCONFIRMED_CAUSE)
    # The child failed by itself and its code stands. A reserved code it chose
    # for itself keeps a cause only where this run's own evidence backs it.
    if exit_code == RunState.INCOMPLETE_WORK.exit_code and evidence.unfinished_work:
        return RunOutcome(RunState.INCOMPLETE_WORK, exit_code, incomplete_cause(evidence))
    if evidence.kill_signature:
        return RunOutcome(RunState.FAILED, exit_code, _KILLED_CAUSE)
    if evidence.self_reported_failure:
        return RunOutcome(RunState.FAILED, exit_code, _SELF_REPORTED_CAUSE)
    if exit_code == RunState.STREAM_UNVERIFIED.exit_code and evidence.unverified_stream:
        return RunOutcome(RunState.STREAM_UNVERIFIED, exit_code, _unverified_cause(evidence))
    return RunOutcome(RunState.FAILED, exit_code)


def safe_to_retry(evidence: RunEvidence) -> bool:
    """True only for a transient failure that is *safe* to start over.

    The tool-use guard is the whole safety argument: a session that already ran
    a tool may have edited files, pushed a branch or posted a digest, and
    replaying it from the top would do that twice. Everything else rules out a
    run whose failure is not established to be the upstream 5xx alone.
    """
    return (evidence.retry_before_tools and evidence.transient_api_error
            and not evidence.saw_tool_use and not evidence.unknown
            and not evidence.malformed and not evidence.unverified
            and not evidence.open_children and not evidence.kill_signature)


def apply_delivery_check(outcome: RunOutcome, delivered: bool) -> RunOutcome:
    """Fold the delivery post-condition in: it outranks a clean run only.

    A child that already failed keeps the state naming why it failed.
    """
    if delivered or outcome.state is not RunState.COMPLETED:
        return outcome
    return RunOutcome(RunState.DELIVERY_UNCONFIRMED, RunState.DELIVERY_UNCONFIRMED.exit_code,
                      "delivery not confirmed")


_HEADLINE = {
    RunState.COMPLETED: "✅ completed",
    RunState.CANCELLATION_UNCONFIRMED: "❓ cancellation not confirmed",
    RunState.INCOMPLETE_WORK: "❓ not confirmed",
    RunState.STREAM_UNVERIFIED: "❓ not confirmed",
    RunState.DELIVERY_UNCONFIRMED: "❓ final outcome",
    RunState.STALLED: "⏱ stalled",
    RunState.CANCELLED: "❌ cancelled",
}


def status_line(outcome: RunOutcome) -> str:
    """The verdict wording beside the exit code: a headline, then the cause."""
    headline = _HEADLINE.get(outcome.state, "❌ failed")
    return f"{headline} · {outcome.cause}" if outcome.cause else headline
