"""Evidence vocabulary and the progress formatter behind `scheduled_runner.py`.

The exit-code rungs, the stream-symptom detectors and `ProgressFormatter`,
which turns normalized provider events into readable milestones and the
evidence the runner's final verdict is read from. `scheduled_runner.py`
re-exports these names for its callers and the `claude_progress.py` facade.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Optional

from scheduled.runner_adapters import ClaudeAdapter, CodexAdapter, ProgressEvent

MAX_SUMMARY_CHARS = 180
SUMMARY_KEYS = (
    "description",
    "name",
    "repo",
    "repository",
    "path",
    "file_path",
    "subject",
    "task_id",
    "skill",
    "bucket",
)
STALL_FLAG = "--stall-timeout"

# An outer, adapter-side post-condition, checked after the child exits and
# whatever its exit code was. Every detector in this file pattern-matches a
# *symptom* of a run that delivered nothing, and each one has been a separate
# incident (a false success from a run that never ran, a sub-agent kill, a
# background-wait ceiling, a truncated stream). The fact that actually matters is not
# "was the stream clean" but "did the run deliver", and for a scheduled skill
# that is usually verifiable from outside the child — a digest comment dated
# today, a file written, a row inserted. One check on the fact catches every
# variant of this class at once, including the ones not seen yet, instead of a
# fifth pattern-matcher for the fifth variant.
DELIVERY_CHECK_FLAG = "--delivery-check"
DELIVERY_CHECK_TIMEOUT_SECONDS = 120.0
DELIVERY_NOT_CONFIRMED_EXIT_CODE = 121

# A wedged run is worse than a failed one: it holds the job slot, reports
# nothing, and is only noticed when a human looks (a stalled run once sat idle
# for eight hours). 45 minutes is far above any legitimate quiet stretch — every
# Bash/PowerShell call is itself bounded by the context-filter wrapper's own
# timeout, and observed gaps between stream events run to a few minutes at most.
DEFAULT_STALL_TIMEOUT_SECONDS = 2700.0
STALL_EXIT_CODE = 124
# Stall-watchdog poll interval. A cancellable run is polled every 0.1 s so a
# cancel request kills the tree promptly. Otherwise the poll is a tenth of the
# stall timeout, clamped: never faster than 1 s (nothing gains from sub-second
# polling of a minutes-scale limit) and never slower than 30 s (so the kill
# lands within a bounded delay after the limit even for a very long timeout).
CANCEL_POLL_SECONDS = 0.1
STALL_POLL_MIN_SECONDS = 1.0
STALL_POLL_MAX_SECONDS = 30.0

# Claude Code's own background-task ceiling (currently 600s) kills any sub-agent
# still in flight and exits 0 regardless — the same false-success shape as
# the adapter's own Bash/Monitor background calls, just one layer up in the
# child process itself. Detected by substring rather
# than an exact-wording regex so a ceiling/wording tweak upstream doesn't
# silently stop tripping this.
KILL_SIGNATURE_TERMS = ("background tasks still running", "terminat")
BACKGROUND_KILL_EXIT_CODE = 125

# Detection is not prevention: spotting the kill makes it visible, but the
# sub-agents' work is still lost. The CLI's own stderr names the cure — "Set
# CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely" — so this adapter
# sets it for every scheduled skill from the one place that owns the spawn
# Verified against the shipped CLI rather than taken on
# faith: the binary reads this exact name (`env.CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS
# ?? <default>`) and gates the sweep on `ceiling > 0 && ...`, so `0` makes
# "ceiling exceeded" permanently false — it disables the kill, it does not make
# it immediate. Safe against a genuinely wedged run: background-task events keep
# the stream alive, so the stall watchdog above remains the real upper bound on
# an unattended run.
BG_WAIT_CEILING_ENV = "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"
BG_WAIT_CEILING_UNLIMITED = "0"

# A run that ends normally having done nothing is the false success neither the
# kill detector nor the stall watchdog can see — on 2026-07-30 the tell was in
# the content (zero repos audited, no digest, no ping), not in the exit code.
# The adapter cannot judge "no work" for an arbitrary skill, so the skill
# asserts its own delivery and prints this marker when the assertion fails;
# the adapter owns turning that into a non-zero exit.
# Deliberately not 124/125/127 — those already name stall/kill/spawn-failure.
SELF_REPORTED_FAILURE_MARKER = "SCHEDULED-RUN-FAILED"
SELF_REPORTED_FAILURE_EXIT_CODE = 123

# A burst of unknown-typed stream records right at shutdown means the stream
# stopped mid-conversation: the child was cut off, and whether it delivered
# anything is unknown.
#
# It is the *secondary* symptom of the kill `KILL_SIGNATURE_TERMS` catches, but
# the wait ceiling is set to `0` for every scheduled run (`BG_WAIT_CEILING_ENV`),
# which disables the kill — and with it the kill *message*. That makes this
# detector the only remaining signal, so it must be able to fail the run:
# otherwise `/audit-fleet` can print this exact warning and then `✅
# completed · exit 0` on the very next line, having audited zero repos (the
# headless background-and-wait class of false success).
#
# It is deliberately *not* reported as a failure: a truncated stream does not
# prove the run failed, it proves delivery was never confirmed — which the
# global rule says must be its own state rather than folded into the passing
# one. The exit code is non-zero all the same, because an unattended job whose
# outcome is unknown must show red, and it is distinct from stall/kill/
# self-reported so the three stay tellable apart.
#
# The complement, and the reason this detector has to stay this narrow: an
# unrecognised record *on its own* is not this signal. `--output-format
# stream-json` gains record types routinely -- `KNOWN_IGNORED_SYSTEM_SUBTYPES`
# has grown eight entries over as many months -- so scoring every one of them
# red meant a fully delivered run went `not confirmed` the week upstream
# shipped a new subtype, with no defect anywhere. On 2026-09-09
# `fleet-health-weekly` delivered its digest, its ledger entry and its Telegram
# ping under `150 unknown stream record(s)` and would still have exited 122.
# That is the inverse of the class this file exists to
# prevent: it manufactures a red rather than hiding one, and a schedule that
# cries wolf on a timetable is how a real red gets ignored.
#
# So an unknown record is counted, named and logged, and decides nothing by
# itself. What stays verdict-bearing is evidence a fact was never *established*:
# a missing terminal result, a malformed record, a burst near shutdown, and the
# records `ProgressFormatter._mark_unverified` counts -- see its docstring for
# why those three call sites are a different thing from schema drift.
UNKNOWN_BURST_WINDOW_SECONDS = 15.0
UNKNOWN_BURST_THRESHOLD = 3
# How many *distinct* unknown-record shapes the summary line names before it
# elides the rest. A drifted schema produces a handful of repeated shapes, not a
# long tail, so a short list is the whole diagnosis; the cap only stops a
# genuinely corrupt stream from pasting itself into the log.
UNKNOWN_LABEL_SUMMARY_LIMIT = 6
TRUNCATED_STREAM_EXIT_CODE = 122

# The fifth shape, and the one none of the four above can see: a run that
# starts, answers in prose, and ends its turn normally -- never having invoked a
# single tool. On 2026-08-20 `/cleanup-fleet-all` did exactly that in 5.3s and
# reported `exit 0`; the stream was complete, nothing was
# killed, no watchdog fired, and the skill printed no failure marker, because
# from the CLI's point of view nothing went wrong. The job card was green.
#
# `SELF_REPORTED_FAILURE_MARKER` cannot cover this: it needs the skill to reach
# its own final report and assert something, and a skill that never started has
# no report to print. This detector deliberately judges nothing about *what* a
# run did -- only that a scheduled skill which invoked zero tools cannot have
# done its job, whatever prose it emitted. That is the one claim the adapter can
# make about an arbitrary skill without knowing anything about it.
#
# 121-125 is a crowded namespace by now; this one takes the last free rung
# below the four symptom detectors and the delivery post-condition, so all
# six stay tellable apart from each other and from a child's own code.
NO_TOOL_USE_EXIT_CODE = 120

# The sixth shape, and the only one so far that is nobody's fault: the upstream
# API answered `529 Overloaded` three minutes into a run, the CLI printed its own
# "usually temporary -- try again in a moment" and exited 1, and the weekly
# `/context-purge` job lost the entire week because 01:03 is not a time anyone is
# awake to try again. Re-running it by hand the same morning
# worked with nothing changed.
#
# Every detector above answers "did this run deliver?" and each is terminal by
# design. This one answers a different question -- "is this failure the run's
# fault at all?" -- and is the only one that can be *acted on* rather than merely
# reported, because the API itself named the remedy.
#
# Two deliberately separate decisions, kept apart because conflating them is how
# a retry turns into a duplicated digest:
#
#   * Classification (this exit code) applies whenever a transient upstream error
#     accompanied a failing run. It names the cause instead of a bare `1`, so the
#     Telegram ping and the job card say "upstream, not us" even for a run that
#     was never eligible to be retried.
#   * Retryability (`retryable_transient_failure`) additionally requires that the
#     run invoked *no tool at all*. A session that already edited files, pushed a
#     branch or posted a digest must never be replayed from the top; only a run
#     that died before touching anything is safe to start over.
#
# Restricted to 5xx on purpose: a 4xx (bad request, expired auth, exhausted
# quota) is a real failure that retrying only makes slower and noisier.
#
# 120-125 is full and 126/127 carry their own shell meanings (`cannot execute` /
# `not found`, and 127 is already this adapter's spawn failure), so this takes
# the next rung *below* the block rather than crowding into either.
TRANSIENT_API_EXIT_CODE = 119
TRANSIENT_API_MAX_ATTEMPTS = 3
# 60s then 180s. The failing run had burned 3m23s of a weekly window, so there is
# room to spare; the point is to outlast a blip, not to hammer a struggling API.
TRANSIENT_API_BACKOFF_SECONDS = (60.0, 180.0)

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_RE = re.compile(
    r"(?i)("
    r"(?:sk-(?:ant-)?|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
    r")"
)


# Anchored to the start of a line (bullets, blockquote marks and indentation
# tolerated) because a skill is told to print the marker as its own line. A run
# that merely *mentions* the marker in prose — quoting its own rulebook in a
# successful report — must not be turned red by the mention.
_SELF_REPORTED_FAILURE_RE = re.compile(
    rf"^[ \t>*•\-]*{re.escape(SELF_REPORTED_FAILURE_MARKER)}\b",
    re.MULTILINE,
)


# Anchored to the start of a line, like the marker above and for the same reason:
# a run that mentions an API error mid-sentence -- a skill narrating yesterday's
# job log, or this very issue -- is not a run that *hit* one. The leading class
# tolerates bullets and blockquote marks exactly as the sibling regex does, so a
# quoted `- API Error: 503` at the head of a line does still match; what filters
# those out is not this pattern but the two guards downstream, which need the
# child to have actually failed and to have invoked nothing.
_TRANSIENT_API_ERROR_RE = re.compile(
    r"^[ \t>*•\-]*API Error:\s*5\d{2}\b",
    re.MULTILINE,
)


def _is_transient_api_error(text: str) -> bool:
    """True for the CLI's own ``API Error: 5xx`` line — upstream, not this run."""
    return _TRANSIENT_API_ERROR_RE.search(text) is not None


def _is_background_kill_signature(text: str) -> bool:
    """True for the stderr line Claude prints when it kills in-flight tasks."""
    lower = text.lower()
    return all(term in lower for term in KILL_SIGNATURE_TERMS)


def _is_self_reported_failure(text: str) -> bool:
    """True when a run declared, on its own line, that it delivered nothing."""
    return _SELF_REPORTED_FAILURE_RE.search(text) is not None


def _configure_output() -> None:
    """Keep captured Windows output UTF-8-safe and immediately visible."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def _redact(value: str) -> str:
    return _SECRET_RE.sub("[redacted]", value)


def _one_line(value: object, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = _ANSI_RE.sub("", str(value)).replace("\r", " ").replace("\n", " ")
    text = _redact(" ".join(text.split()))
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _tool_summary(tool_input: object) -> str:
    """Return only allowlisted metadata; never echo commands or prompts."""
    if not isinstance(tool_input, dict):
        return ""
    for key in SUMMARY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return _one_line(value)
    return ""


# Two independent detectors reach this code, and the decision to let them share
# it is deliberate rather than inherited. The stream-level half fires when the
# parser still holds an open tool or child at exit; the OS-level half fires when
# the owned Windows job still has live descendants after the provider process
# returned. Both answer the same question — *was this run's work finished when
# it exited?* — and neither answers a question the other cannot, which is the
# only bar this file mints a new rung for (see `saw_kill_signature`, where a
# failed child was denied one for exactly that reason). So no rung is minted;
# what has to be tellable apart is carried by the verdict line, which names
# which half fired.
#
# The shape is `❓ not confirmed`, not `❌ failed`, and that is the second half
# of the decision. On 2026-09-06 `weekly-recap-draft` swept, rewrote and
# validated its ledger, posted to Telegram and left a clean tree — and reported
# `❌ failed · exit 118` because three owned descendants outlived the provider.
# "Delivered its work" and "left three processes behind" are two different
# facts; folding the second into `❌ failed` asserts the first did not happen,
# which the adapter has no evidence for. It has no evidence of success either —
# a descendant still running may have been mid-effect — so the honest state is
# the one this file already uses everywhere a fact was never established, per
# the global rule that such a state is its own and never folded into a
# neighbouring one. The exit code stays non-zero so an unattended job still
# shows something, and nothing about the detection changes: the drain deadline,
# the orphan count and the pre-effect retry gate this same evidence closes are
# all exactly as they were.
INCOMPLETE_WORK_EXIT_CODE = 118
AUTH_UNAVAILABLE_EXIT_CODE = 117
MODEL_UNAVAILABLE_EXIT_CODE = 116
MISSING_TOOLS_EXIT_CODE = 115
CANCELLATION_UNCONFIRMED_EXIT_CODE = 114
CANCELLED_EXIT_CODE = 130


class ProgressFormatter:
    """Shared evidence state and readable milestones from normalized events."""

    def __init__(
        self,
        emit: Optional[Callable[[str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        adapter: Optional[ClaudeAdapter | CodexAdapter] = None,
    ) -> None:
        self.adapter = adapter or ClaudeAdapter()
        self._children: set[str] = set()
        self._provider_error = ""
        self._clock = clock
        self._started_at = clock()
        self._last_activity = self._started_at
        # logging-not-print-exempt: the scheduled run's milestone stream is this adapter's stdout by design
        self._emit_raw = emit or (lambda line: print(line, flush=True))
        self._emit_lock = threading.Lock()
        self._tools: dict[str, str] = {}
        self._assistant_texts: set[str] = set()
        self._malformed = 0
        self._unknown = 0
        self._unknown_labels: collections.Counter[str] = collections.Counter()
        self._unknown_timestamps: list[float] = []
        self._unverified = 0
        self._unverified_labels: collections.Counter[str] = collections.Counter()
        self._result_error = False
        self._saw_result = False
        self._saw_kill_signature = False
        self._child_failures = 0
        self._child_cancellations = 0
        self._saw_self_reported_failure = False
        self._saw_tool_use = False
        self._saw_transient_api_error = False
        self._burst_count: Optional[int] = None
        self._owned_orphans = 0

    @property
    def saw_kill_signature(self) -> bool:
        """True only for the stderr line Claude prints when it kills tasks.

        This used to also return `self._saw_child_failure`, which made **any**
        background task that ended `failed` exit an otherwise-successful run as
        `BACKGROUND_KILL_EXIT_CODE`. On 2026-09-09 `fleet-health` delivered its
        full digest, ledger and Telegram ping and was reported `❌ failed ·
        exit 125` because it had cancelled one mis-targeted disk scan and had
        one backgrounded PowerShell exit non-zero after a 90s `docker system
        df` timeout — both recovered from, neither a fault (fleet-config#808).

        Two things were wrong and both are fixed here. A failed child *created*
        a failure, which is precisely what the sibling comment on
        `TRANSIENT_API_EXIT_CODE` forbids ("this renames a failure, it never
        creates one"); and it borrowed 125's name, which means one specific
        thing — the CLI's own background-task ceiling killed sub-agents that
        were still in flight (#506) — destroying the tellability the rest of
        the constant block exists to preserve.

        No new rung was minted for a failed child, and that is a decision
        rather than an omission. Every question a failed child could raise is
        "did this run deliver?", and each detector in this file already answers
        that from the run's *own* outcome: a failed `result` (1), the delivery
        post-condition (121), the self-reported marker (123), children still
        unfinished at exit (118), stream integrity (122), zero tool use (120),
        and the genuine kill above (125). A child that failed and was recovered
        from adds no independent evidence about delivery — recovering from one
        is the orchestrator's whole job — so it is surfaced in the log by
        `finish()` and left out of the verdict.
        """
        return self._saw_kill_signature

    @property
    def saw_tool_use(self) -> bool:
        """True once the child invoked anything at all.

        Counts both shapes the stream reports: an assistant ``tool_use`` block,
        and a ``task_started`` system event for a sub-agent or workflow the
        parent dispatched. A run showing neither did nothing.
        """
        return self._saw_tool_use

    @property
    def saw_self_reported_failure(self) -> bool:
        return self._saw_self_reported_failure

    @property
    def saw_transient_api_error(self) -> bool:
        """True once the CLI reported an upstream 5xx — the run's own fault, no."""
        return self._saw_transient_api_error

    @property
    def retryable_transient_failure(self) -> bool:
        """True only for a transient failure that is *safe* to start over.

        The tool-use guard is the whole safety argument for retrying at all: a
        session that already ran a tool may have edited files, pushed a branch
        or posted a digest, and replaying it from the top would do that twice.
        A run that died before touching anything has, by definition, nothing to
        duplicate. `saw_tool_use` already tracks both shapes the stream reports
        (an assistant `tool_use` block and a `task_started` sub-agent event), so
        it is exactly the right question to ask here.
        """
        return (self.adapter.retry_before_tools and self._saw_transient_api_error
                and not self._saw_tool_use and not self._unknown and not self._malformed
                and not self._unverified and not self._children
                and not self._saw_kill_signature)

    def reset_for_retry(self) -> None:
        """Clear per-attempt state, keeping the run-level clock.

        Only the *attempt* starts over; the elapsed-time prefix keeps counting
        from the original start so the log reads as one continuous run rather
        than three that each begin at `[00:00]`. Every verdict-bearing field is
        cleared, because a fresh attempt must be judged on its own evidence —
        leaving `_result_error` or the unknown-record timestamps behind would
        let attempt 1's failure decide attempt 2's exit code.
        """
        self._tools.clear()
        self._children.clear()
        self._provider_error = ""
        self._assistant_texts.clear()
        self._unknown_timestamps.clear()
        self._unknown_labels.clear()
        self._unverified_labels.clear()
        self._malformed = 0
        self._unknown = 0
        self._unverified = 0
        self._result_error = False
        self._saw_result = False
        self._saw_kill_signature = False
        self._child_failures = 0
        self._child_cancellations = 0
        self._saw_self_reported_failure = False
        self._saw_tool_use = False
        self._saw_transient_api_error = False
        self._burst_count = None
        self._touch()

    def truncated_stream_burst(self) -> int:
        """Unknown records inside the shutdown window — computed once, then cached.

        `run()` needs this *before* it calls `finish()`, to pick the exit code,
        and `finish()` needs the same number for its verdict line. Recomputing
        would read a later clock and could disagree with itself across the two
        call sites, so the first caller fixes the value.
        """
        if self._burst_count is None:
            now = self._clock()
            self._burst_count = sum(
                1 for seen_at in self._unknown_timestamps
                if now - seen_at <= UNKNOWN_BURST_WINDOW_SECONDS
            )
        return self._burst_count

    @property
    def stream_truncated(self) -> bool:
        """True when the stream stopped mid-conversation — delivery unconfirmed.

        A burst of unknown-typed records near shutdown is only evidence of a
        cut-off stream when the terminal `result` event never arrived. A run
        that delivered its result event proved the opposite — the burst is
        just the normal end-of-run sub-agent flush — so `_saw_result` overrides
        the burst count rather than being folded into it (fleet-config#608).
        """
        if self._saw_result:
            return False
        return self.truncated_stream_burst() >= UNKNOWN_BURST_THRESHOLD

    def _prefix(self) -> str:
        return f"[{_elapsed(self._clock() - self._started_at)}]"

    def _touch(self) -> None:
        """Record that the child produced output just now.

        Deliberately driven by *received* child output rather than by ``emit``,
        so the watchdog's own stall message can never reset its own deadline.
        """
        with self._emit_lock:
            self._last_activity = self._clock()

    def seconds_since_activity(self) -> float:
        """Seconds since the child last produced any output."""
        with self._emit_lock:
            return max(0.0, self._clock() - self._last_activity)

    def emit(self, message: str) -> None:
        with self._emit_lock:
            self._emit_raw(f"{self._prefix()} {message}")

    def emit_best_effort(self, message: str) -> None:
        """Write one diagnostic line that must never block or raise.

        Used only *after* the stall watchdog has killed the child, where the
        thing most likely to be jammed is this adapter's own stdout — a
        backpressured downstream consumer is what wedged the 2026-07-30
        scheduled run (fleet-config#514). Taking ``_emit_lock`` here would park
        it inside a blocked write, and the main thread's own ``finish()`` emit
        would then deadlock on it, so this deliberately writes unlocked and
        swallows whatever the write does. Interleaving with a concurrent
        ``emit`` is an acceptable price for a single last-gasp line on a run
        that is already being torn down.
        """
        try:
            self._emit_raw(f"{self._prefix()} {message}")
        except Exception:  # noqa: BLE001 — best-effort by contract
            pass

    def _mark_unknown(self, label: str = "") -> None:
        """Count one unrecognised record, and remember what shape it was.

        The count alone is what made fleet-config#841 invisible for weeks: a
        healthy run and a truncated one both read "N unknown stream record(s)",
        so the number stopped being actionable and started being wallpaper. The
        label is a shape descriptor only (see `runner_adapters.describe_record`)
        -- never payload.

        Counted, named, logged -- and, since fleet-config#810, not a verdict.
        A record this parser has never seen is evidence about the parser, not
        about the run; `_mark_unverified` is where a record that genuinely
        leaves the outcome unestablished goes.
        """
        self._unknown += 1
        self._unknown_labels[label or "unlabelled"] += 1
        self._unknown_timestamps.append(self._clock())

    def _mark_unverified(self, label: str = "") -> None:
        """Count one fact this run left unestablished. Verdict-bearing.

        The other half of the split fleet-config#810 drew. Three call sites
        reach here, and none of them is schema drift:

        * a native delegated child whose completion this parser cannot read,
          which must never disappear into a green turn (`CodexAdapter`);
        * a drain deadline reached with a pipe still open or a descendant still
          running -- this runner stopped watching before the run was over;
        * a child that never exited inside the terminate timeout.

        Each says the same thing: the outcome is a fact nobody established.
        The global rule is that such a state is its own, never folded into the
        passing one, so these keep `TRUNCATED_STREAM_EXIT_CODE`. Deliberately
        outside the burst window and the `_unknown` count -- these are not
        stream records, and `truncated_stream_burst` is a statement about the
        stream.
        """
        self._unverified += 1
        self._unverified_labels[label or "unlabelled"] += 1

    @staticmethod
    def _summarize(labels: "collections.Counter[str]") -> str:
        """`shape ×N` breakdown, most-seen first, capped as one log line."""
        if not labels:
            return ""
        ranked = labels.most_common()
        named = ", ".join(f"{label} ×{count}"
                          for label, count in ranked[:UNKNOWN_LABEL_SUMMARY_LIMIT])
        hidden = len(ranked) - UNKNOWN_LABEL_SUMMARY_LIMIT
        return named + (f", +{hidden} more shape(s)" if hidden > 0 else "")

    def unknown_summary(self) -> str:
        """`type/subtype ×N` breakdown of the unknown records, most-seen first."""
        return self._summarize(self._unknown_labels)

    def unverified_summary(self) -> str:
        """`shape ×N` breakdown of the records that left the outcome unproven."""
        return self._summarize(self._unverified_labels)

    def emit_stderr(self, line: str) -> None:
        self._touch()
        clean = _one_line(line)
        if not clean:
            return
        if _is_background_kill_signature(clean):
            self._saw_kill_signature = True
        self._classify_error(clean)
        self.emit(f"⚠ {self.adapter.label} stderr: {clean}")

    def handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        self._touch()
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            self._malformed += 1
            return
        if not isinstance(event, dict):
            self._malformed += 1
            return
        self.handle_event(event)

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            normalized_events = self.adapter.events(event)
        except (TypeError, ValueError, AttributeError, KeyError):
            self._malformed += 1
            return
        for normalized in normalized_events:
            self.handle_progress(normalized)

    def _classify_error(self, text: str) -> None:
        category = self.adapter.error_category(text)
        if category:
            self._provider_error = category

    def handle_progress(self, event: ProgressEvent) -> None:
        """Consume only the shared contract; provider fields stay at the edge."""
        if event.kind == "unknown":
            self._mark_unknown(event.name)
        elif event.kind == "unverified":
            self._mark_unverified(event.name)
        elif event.kind == "malformed":
            self._malformed += 1
        elif event.kind == "start":
            self.emit(f"▶ {self.adapter.label} {_one_line(event.text)} · session started")
        elif event.kind == "text":
            self._emit_assistant_text(event.text, from_model=event.role == "assistant")
        elif event.kind == "error":
            self._classify_error(event.text)
            self.emit(f"⚠ {_one_line(event.text)}")
        elif event.kind in {"tool_start", "child_start", "child_progress"}:
            self._saw_tool_use = True
            name = _one_line(event.name or "tool")
            if event.kind == "tool_start":
                self._tools[event.id] = name
            else:
                self._children.add(event.id)
            self.emit(f"▶ {name}" + (f" · {_tool_summary(event.metadata)}" if event.metadata else ""))
        elif event.kind in {"tool_end", "child_end"}:
            self._saw_tool_use = True
            if event.kind == "child_end":
                self._children.discard(event.id)
                name = "task"
                # Counted, never a verdict — see `saw_kill_signature` for why a
                # failed or cancelled child does not decide the exit code, and
                # `finish()` for where the counts do become visible.
                if event.cancelled:
                    self._child_cancellations += 1
                elif event.failed:
                    self._child_failures += 1
            else:
                name = self._tools.pop(event.id, event.name or "tool")
            mark, verb = (("⊘", "stopped") if event.cancelled
                          else ("✗", "failed") if event.failed
                          else ("✓", "completed"))
            self.emit(f"{mark} {_one_line(name)} {verb}")
        elif event.kind == "result":
            self._saw_result = True
            self._result_error = event.failed
            self._classify_error(event.text)
            self._emit_assistant_text(event.text)

    @property
    def unverified_stream(self) -> bool:
        """True when something about this run's outcome was never established.

        Not "something was unfamiliar" -- that was the fleet-config#810 bug.
        A missing terminal result means delivery was never confirmed; a
        malformed record means the adapter could not parse what it was sent;
        `_unverified` counts the surfaces that named themselves unprovable.
        An unknown record is none of those and is deliberately absent here.
        """
        return bool(not self._saw_result or self._malformed or self._unverified)

    @property
    def unfinished_work(self) -> bool:
        return bool(self._tools or self._children)

    @property
    def owned_orphans(self) -> int:
        """Owned descendants still running when the drain deadline was reached.

        The OS-level half of `INCOMPLETE_WORK_EXIT_CODE`. `run_process` knows
        this and `finish()` did not, which is the whole reason an orphaned run
        used to fall through the status chain to a bare `❌ failed · exit 118`
        with no cause named at all (fleet-config#811).
        """
        return self._owned_orphans

    def _mark_owned_orphans(self, count: int) -> None:
        """Record that the owned job outlived the provider, and by how many."""
        self._owned_orphans = max(0, int(count))

    def incomplete_work_cause(self) -> str:
        """Which half (or both) of the 118 evidence this run actually carries."""
        parts = []
        if self.unfinished_work:
            parts.append("unfinished tools or children")
        if self.owned_orphans:
            parts.append(
                f"{self.owned_orphans} owned descendant(s) still running after "
                "the provider exited"
            )
        return " and ".join(parts)

    def _emit_assistant_text(self, value: object, from_model: bool = True) -> None:
        if not isinstance(value, str):
            return
        text = value.replace("\r\n", "\n").strip()
        if not text:
            return
        # `from_model` is False only for Claude's "user"-kind text blocks — a
        # skill invocation's own SKILL.md body, injected back into the
        # conversation verbatim rather than as a tool_result. That body is
        # allowed (expected, even) to carry a worked example of the marker at
        # column 0 to teach the model the contract; it is never something the
        # run itself asserted, so it must never be scored.
        # Still emitted below for log visibility, same as any other text.
        if from_model:
            # Scanned before the dedup guard: the same final report arrives twice
            # (assistant text block, then the terminal result event), and the marker
            # must register whichever copy is seen first.
            if _is_self_reported_failure(text):
                self._saw_self_reported_failure = True
            # Same reason as the marker above: the CLI's error text arrives as an
            # assistant block and again as the terminal result, and the dedup guard
            # below drops whichever copy lands second.
            if self.adapter.retry_before_tools and _is_transient_api_error(text):
                self._saw_transient_api_error = True
        if text in self._assistant_texts:
            return
        self._assistant_texts.add(text)
        self.emit(f"{self.adapter.label}:\n{_redact(text)}")

    def finish(self, exit_code: int, stalled: bool = False) -> None:
        recent_unknown = self._emit_record_warnings()
        status = self._final_status(exit_code, stalled, recent_unknown)
        result_note = " · no terminal result event" if not self._saw_result else ""
        self.emit(f"{status} · exit {exit_code}{result_note}")

    def _emit_record_warnings(self) -> int:
        """One line per kind of record that did not count; returns the unknown burst size."""
        if self._malformed or self._unknown:
            breakdown = self.unknown_summary()
            self.emit(
                "⚠ ignored "
                f"{self._malformed} malformed and {self._unknown} unknown stream record(s)"
                + (f" · unknown: {breakdown}" if breakdown else "")
            )
        if self._unverified:
            # Its own line, never folded into "ignored": these records decided the
            # verdict, and a reader who sees "not confirmed" needs to know which.
            self.emit(
                f"⚠ {self._unverified} record(s) left this run unverified "
                f"· {self.unverified_summary()}"
            )
        if self._child_failures or self._child_cancellations:
            # Not a verdict (see `saw_kill_signature`) but never silent: its own
            # line tells "happened and was worked around" from "never happened".
            # Worded as a record, not a judgement — the adapter knows the child
            # ended badly, not whether the run coped.
            counts = ", ".join(
                part for part in (
                    f"{self._child_failures} failed" if self._child_failures else "",
                    f"{self._child_cancellations} stopped" if self._child_cancellations else "",
                ) if part
            )
            self.emit(
                f"⚠ background task(s): {counts} — recorded, not counted toward "
                "this run's outcome"
            )
        recent_unknown = self.truncated_stream_burst()
        if self.stream_truncated:
            self.emit(
                f"⚠ burst of {recent_unknown} unknown stream record(s) near shutdown "
                "— possible truncated/killed stream"
            )
        return recent_unknown

    def _final_status(self, exit_code: int, stalled: bool, recent_unknown: int) -> str:
        failed = (stalled or exit_code != 0 or self._result_error
                  or self._saw_kill_signature or self._saw_self_reported_failure)
        if stalled:
            status = "⏱ stalled"
        elif exit_code == TRANSIENT_API_EXIT_CODE:
            # Placed here so this chain mirrors `run_process`'s precedence
            # exactly. Those branches test formatter flags while the code was
            # already decided, so any disagreement in ordering prints a verdict
            # that contradicts the exit code beside it — a run that both
            # self-reported and hit a 5xx would exit 119 under a line reading
            # "delivered no work". Reading the code first keeps the two honest.
            status = (
                "❌ failed · transient upstream API error (5xx) — the run never got "
                "going; this is an API-side fault, not a fault in the skill"
            )
        elif exit_code == CANCELLATION_UNCONFIRMED_EXIT_CODE:
            status = "❓ cancellation not confirmed · owned descendants could not be verified"
        elif exit_code == CANCELLED_EXIT_CODE:
            status = "❌ cancelled · owned process tree stopped"
        elif ((self.unfinished_work or self.owned_orphans)
              and exit_code in {0, INCOMPLETE_WORK_EXIT_CODE}):
            # Both halves of 118 land here and the cause names which fired; see
            # the constant's own comment for why they share a rung and why the
            # shape is `❓` rather than `❌`. Guarded on the evidence rather
            # than on the code alone so a child that exits 118 natively — which
            # neither detector produced — is never handed a cause this adapter
            # did not establish.
            status = f"❓ not confirmed · {self.incomplete_work_cause()}"
        elif exit_code in {AUTH_UNAVAILABLE_EXIT_CODE, MODEL_UNAVAILABLE_EXIT_CODE, MISSING_TOOLS_EXIT_CODE}:
            status = f"❌ failed · {self._provider_error} unavailable"
        elif self._saw_kill_signature:
            status = (
                "❌ failed · background tasks killed after timeout — orchestrator "
                "likely ended its turn with agents in flight"
            )
        elif self._saw_self_reported_failure:
            status = (
                "❌ failed · the run reported it delivered no work "
                f"({SELF_REPORTED_FAILURE_MARKER}) — see its final report for which "
                "delivery assertion failed"
            )
        elif self.stream_truncated:
            # Not "failed" — unconfirmed. The stream stopped mid-conversation,
            # so whether the run delivered anything is a fact nobody
            # established, and folding that into ✅ is what let a zero-repo
            # audit report success.
            status = (
                f"❓ not confirmed · stream truncated near shutdown ({recent_unknown} "
                "unknown record(s)) — the run was cut off mid-flight and delivery "
                "was never verified"
            )
        elif exit_code == NO_TOOL_USE_EXIT_CODE:
            # Its own state rather than folded into the passing one: a scheduled
            # skill that invoked no tool did not run, and saying so beats green.
            status = (
                "❌ failed · the run invoked no tools at all — the skill never "
                "started (check that the prompt asks for it, not just names it)"
            )
        elif self.unverified_stream and exit_code in {0, TRUNCATED_STREAM_EXIT_CODE}:
            status = "❓ not confirmed · missing, malformed or unverifiable completion stream"
        else:
            status = "❌ failed" if failed else "✅ completed"
        return status
