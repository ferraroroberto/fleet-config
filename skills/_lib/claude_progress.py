"""Stream readable progress from a headless Claude Code print-mode run.

Scheduled skills call this helper instead of invoking ``claude -p`` directly.
The caller supplies the prompt and ordinary Claude flags; this process owns
print mode plus the verbose JSONL stream, filters it into concise milestones,
and returns Claude's exit code unchanged.

Usage::

    python claude_progress.py "/audit-fleet" --model sonnet \
        --permission-mode bypassPermissions

The parser deliberately consumes only a small, stable subset of the evolving
stream schema. Unknown and malformed records are counted, never echoed raw,
and never allowed to terminate a long unattended run.

A stall watchdog kills the run and exits ``124`` if the stream goes silent for
``--stall-timeout`` seconds (default 45 minutes; ``0`` disables, and
``CLAUDE_PROGRESS_STALL_TIMEOUT`` sets the fleet-wide default). The flag is
consumed here and never forwarded to ``claude``.

Claude Code's own ``--print`` mode kills any sub-agents still in flight when a
background-task ceiling elapses (currently 600s) and exits ``0`` anyway — a
false success that looks identical to a clean run unless something reads
stderr (fleet-config#506). This adapter watches for that stderr signature and
exits ``125`` instead, even when the child process itself reported ``0``. It
also *prevents* the kill in the first place by handing the child
``CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`` — the CLI's own documented "wait
indefinitely" setting (fleet-config#519).

The third false-success shape is a run that ends perfectly normally having done
no work at all: nothing was killed, nothing stalled, so the child exits ``0``.
Only the skill itself knows what "no work" means for it, so a scheduled skill
self-reports by printing ``SCHEDULED-RUN-FAILED`` in its final report and this
adapter turns that into exit ``123``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

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
RESERVED_FLAGS = ("-p", "--print", "--output-format", "--include-partial-messages")
STALL_FLAG = "--stall-timeout"

# An outer, adapter-side post-condition, checked after the child exits and
# whatever its exit code was. Every detector in this file pattern-matches a
# *symptom* of a run that delivered nothing, and each one has been a separate
# incident (#314, #506, #519, #560). The fact that actually matters is not
# "was the stream clean" but "did the run deliver", and for a scheduled skill
# that is usually verifiable from outside the child — a digest comment dated
# today, a file written, a row inserted. One check on the fact catches every
# variant of this class at once, including the ones not seen yet, instead of a
# fifth pattern-matcher for the fifth variant.
DELIVERY_CHECK_FLAG = "--delivery-check"
DELIVERY_CHECK_TIMEOUT_SECONDS = 120.0
DELIVERY_NOT_CONFIRMED_EXIT_CODE = 121

# A wedged run is worse than a failed one: it holds the job slot, reports
# nothing, and is only noticed when a human looks (fleet-config#411 sat idle for
# eight hours). 45 minutes is far above any legitimate quiet stretch — every
# Bash/PowerShell call is itself bounded by the context-filter wrapper's own
# timeout, and observed gaps between stream events run to a few minutes at most.
DEFAULT_STALL_TIMEOUT_SECONDS = 2700.0
STALL_EXIT_CODE = 124

# Claude Code's own background-task ceiling (currently 600s) kills any sub-agent
# still in flight and exits 0 regardless — the exact false-success shape #314
# already fixed for this adapter's own Bash/Monitor calls, just one layer up in
# the child process itself (fleet-config#506). Detected by substring rather
# than an exact-wording regex so a ceiling/wording tweak upstream doesn't
# silently stop tripping this.
KILL_SIGNATURE_TERMS = ("background tasks still running", "terminat")
BACKGROUND_KILL_EXIT_CODE = 125

# Detection is not prevention: #506 made the kill visible, but the sub-agents'
# work was still lost. The CLI's own stderr names the cure — "Set
# CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely" — so this adapter
# sets it for every scheduled skill from the one place that owns the spawn
# (fleet-config#519). Verified against the shipped CLI rather than taken on
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
# the adapter owns turning that into a non-zero exit (fleet-config#519).
# Deliberately not 124/125/127 — those already name stall/kill/spawn-failure.
SELF_REPORTED_FAILURE_MARKER = "SCHEDULED-RUN-FAILED"
SELF_REPORTED_FAILURE_EXIT_CODE = 123

# A burst of unknown-typed stream records right at shutdown means the stream
# stopped mid-conversation: the child was cut off, and whether it delivered
# anything is unknown.
#
# This was wired informational-only because it was the *secondary* symptom of
# the kill `KILL_SIGNATURE_TERMS` already caught. #519 then set the wait
# ceiling to `0` for every scheduled run, which disables the kill — and with it
# the kill *message*. That promoted this detector from secondary symptom to the
# only remaining signal while it was still hard-wired never to fail anything,
# so on 2026-08-06 `/audit-fleet` printed this exact warning and then `✅
# completed · exit 0` on the very next line, having audited zero repos
# (fleet-config#560, the fourth variant of the headless background-and-wait
# class after #314/#506/#519).
#
# It is deliberately *not* reported as a failure: a truncated stream does not
# prove the run failed, it proves delivery was never confirmed — which the
# global rule says must be its own state rather than folded into the passing
# one. The exit code is non-zero all the same, because an unattended job whose
# outcome is unknown must show red, and it is distinct from stall/kill/
# self-reported so the three stay tellable apart.
UNKNOWN_BURST_WINDOW_SECONDS = 15.0
UNKNOWN_BURST_THRESHOLD = 3
TRUNCATED_STREAM_EXIT_CODE = 122

# The fifth shape, and the one none of the four above can see: a run that
# starts, answers in prose, and ends its turn normally -- never having invoked a
# single tool. On 2026-08-20 `/cleanup-fleet-all` did exactly that in 5.3s and
# reported `exit 0` (fleet-config#689); the stream was complete, nothing was
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


class ProgressFormatter:
    """Stateful minimal parser for Claude Code's verbose JSONL stream."""

    def __init__(
        self,
        emit: Optional[Callable[[str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._started_at = clock()
        self._last_activity = self._started_at
        self._emit_raw = emit or (lambda line: print(line, flush=True))
        self._emit_lock = threading.Lock()
        self._tools: dict[str, str] = {}
        self._assistant_texts: set[str] = set()
        self._malformed = 0
        self._unknown = 0
        self._unknown_timestamps: list[float] = []
        self._result_error = False
        self._saw_result = False
        self._saw_kill_signature = False
        self._saw_self_reported_failure = False
        self._saw_tool_use = False
        self._burst_count: Optional[int] = None

    @property
    def saw_kill_signature(self) -> bool:
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

    def _mark_unknown(self) -> None:
        self._unknown += 1
        self._unknown_timestamps.append(self._clock())

    def emit_stderr(self, line: str) -> None:
        self._touch()
        clean = _one_line(line)
        if not clean:
            return
        if _is_background_kill_signature(clean):
            self._saw_kill_signature = True
        self.emit(f"⚠ Claude stderr: {clean}")

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
        event_type = event.get("type")
        if event_type == "system":
            self._handle_system(event)
        elif event_type == "assistant":
            self._handle_assistant(event)
        elif event_type == "user":
            self._handle_user(event)
        elif event_type == "result":
            self._handle_result(event)
        elif event_type in {"rate_limit_event", "prompt_suggestion"}:
            return
        else:
            self._mark_unknown()

    def _handle_system(self, event: dict[str, Any]) -> None:
        subtype = event.get("subtype")
        if subtype == "init":
            version = _one_line(event.get("claude_code_version") or "unknown version")
            model = _one_line(event.get("model") or "unknown model")
            self.emit(f"▶ Claude Code {version} · {model} · session started")
            return
        if subtype == "thinking_tokens":
            return
        if subtype in {"task_started", "task_progress", "task_notification"}:
            self._saw_tool_use = True
            label = {
                "task_started": "▶ task",
                "task_progress": "… task",
                "task_notification": "✓ task",
            }[subtype]
            summary = _tool_summary(event)
            self.emit(f"{label}{f' · {summary}' if summary else ''}")
            return
        self._mark_unknown()

    def _handle_assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            self._mark_unknown()
            return
        for block in content:
            if not isinstance(block, dict):
                self._malformed += 1
                continue
            block_type = block.get("type")
            if block_type == "thinking":
                continue
            if block_type == "text":
                self._emit_assistant_text(block.get("text"))
                continue
            if block_type == "tool_use":
                self._start_tool(block)
                continue
            self._mark_unknown()

    def _start_tool(self, block: dict[str, Any]) -> None:
        self._saw_tool_use = True
        tool_id = block.get("id")
        name = _one_line(block.get("name") or "tool")
        summary = _tool_summary(block.get("input"))
        if isinstance(tool_id, str) and tool_id:
            self._tools[tool_id] = name
        self.emit(f"▶ {name}{f' · {summary}' if summary else ''}")

    def _handle_user(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            self._mark_unknown()
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                self._mark_unknown()
                continue
            tool_id = block.get("tool_use_id")
            name = self._tools.pop(tool_id, "tool") if isinstance(tool_id, str) else "tool"
            if block.get("is_error") is True:
                self.emit(f"✗ {name} failed")
            else:
                self.emit(f"✓ {name} completed")

    def _emit_assistant_text(self, value: object) -> None:
        if not isinstance(value, str):
            return
        text = value.replace("\r\n", "\n").strip()
        if not text:
            return
        # Scanned before the dedup guard: the same final report arrives twice
        # (assistant text block, then the terminal result event), and the marker
        # must register whichever copy is seen first.
        if _is_self_reported_failure(text):
            self._saw_self_reported_failure = True
        if text in self._assistant_texts:
            return
        self._assistant_texts.add(text)
        self.emit(f"Claude:\n{text}")

    def _handle_result(self, event: dict[str, Any]) -> None:
        self._saw_result = True
        self._result_error = event.get("is_error") is True or event.get("subtype") == "error"
        self._emit_assistant_text(event.get("result"))

    def finish(self, exit_code: int, stalled: bool = False) -> None:
        if self._malformed or self._unknown:
            self.emit(
                "⚠ ignored "
                f"{self._malformed} malformed and {self._unknown} unknown stream record(s)"
            )
        recent_unknown = self.truncated_stream_burst()
        if self.stream_truncated:
            self.emit(
                f"⚠ burst of {recent_unknown} unknown stream record(s) near shutdown "
                "— possible truncated/killed stream"
            )
        failed = (
            stalled
            or exit_code != 0
            or self._result_error
            or self._saw_kill_signature
            or self._saw_self_reported_failure
        )
        if stalled:
            status = "⏱ stalled"
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
            # audit report success (fleet-config#560).
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
        else:
            status = "❌ failed" if failed else "✅ completed"
        result_note = " · no terminal result event" if not self._saw_result else ""
        self.emit(f"{status} · exit {exit_code}{result_note}")


# Claude Code 2.1.237 changed how a bare `/<skill>` prompt is framed in headless
# `-p` mode. The skill body now arrives as its own message flagged
# `"isMeta": true, "turnCompanion": true` -- passive context -- while the user
# turn carries only `<command-name>/<skill></command-name>`; the run that
# exposed this recorded `input_tokens: 2` against 52,603 cached ones. A skill
# whose text opens with an imperative still gets executed. One that opens with
# descriptive prose reads as reference material, and the model answers "Ready --
# what would you like to do?" and stops (fleet-config#689).
#
# So the adapter stops depending on slash expansion and asks for the skill by
# name. Appending the instruction *after* the slash command is not an option:
# trailing text lands in `<command-args>`, where the skill parses it as its own
# arguments.
_SLASH_COMMAND_RE = re.compile(r"^/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<rest>\s[\s\S]*)?$")

SKILL_PROMPT_TEMPLATE = (
    "Run the {name} skill now via the Skill tool, end to end and fully unattended, "
    "following its SKILL.md steps exactly. Skill arguments: {arguments}. "
    "Nobody is attending this run: never ask a question, never end your turn "
    "waiting to be resumed, and poll every background call to completion inside "
    "your own turn."
)


def normalize_skill_prompt(prompt: str) -> str:
    """Rewrite a bare ``/<skill>`` prompt into an explicit instruction.

    Anything that is not a slash command comes back untouched -- a caller that
    already phrases its own instruction keeps it verbatim. Trailing text after
    the command name is forwarded as the skill's arguments, which is what slash
    expansion would have done with it anyway.
    """
    match = _SLASH_COMMAND_RE.match(prompt.strip())
    if match is None:
        return prompt
    arguments = (match.group("rest") or "").strip()
    return SKILL_PROMPT_TEMPLATE.format(
        name=match.group("name"),
        arguments=arguments or "none",
    )


def build_command(arguments: Sequence[str], executable: Optional[str] = None) -> list[str]:
    """Build the child command while keeping stream-owned flags canonical."""
    if not arguments:
        raise ValueError("a Claude prompt is required")
    for argument in arguments:
        if argument in RESERVED_FLAGS or any(
            argument.startswith(flag + "=") for flag in RESERVED_FLAGS if flag.startswith("--")
        ):
            raise ValueError(f"{argument} is owned by claude_progress.py")
    claude = executable or shutil.which("claude") or "claude"
    # Prompt-first is this adapter's contract (the empty check above rests on the
    # same assumption), so only the first argument is a candidate for rewriting;
    # the Claude flags that follow are passed through untouched.
    prompt = normalize_skill_prompt(arguments[0])
    return [
        claude, "-p", prompt, *arguments[1:], "--output-format", "stream-json", "--verbose",
    ]


class AdapterFlags(NamedTuple):
    arguments: list[str]
    stall_timeout: Optional[float]
    delivery_check: Optional[str]


def parse_adapter_flags(arguments: Sequence[str]) -> AdapterFlags:
    """Split adapter-owned flags out of the caller's Claude arguments.

    ``--stall-timeout`` configures *this* process's watchdog and
    ``--delivery-check`` its post-condition; neither may be forwarded to
    ``claude``, which would reject them as unknown flags.
    """
    remaining: list[str] = []
    stall: Optional[float] = None
    delivery: Optional[str] = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        for flag in (STALL_FLAG, DELIVERY_CHECK_FLAG):
            if argument == flag:
                if index + 1 >= len(arguments):
                    raise ValueError(f"{flag} requires a value")
                matched, value, index = flag, arguments[index + 1], index + 2
                break
            if argument.startswith(flag + "="):
                matched, value, index = flag, argument.split("=", 1)[1], index + 1
                break
        else:
            remaining.append(argument)
            index += 1
            continue
        if matched == DELIVERY_CHECK_FLAG:
            delivery = value
            continue
        try:
            stall = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{STALL_FLAG} expects seconds, got {value!r}") from None
    return AdapterFlags(remaining, stall, delivery)


def run_delivery_check(script: str, formatter: "ProgressFormatter") -> bool:
    """Run the post-condition script; True only when it proves delivery.

    Invoked with *this* interpreter and no shell, so a Windows path needs no
    quoting gymnastics through a `.bat`. A script that exits non-zero, cannot
    be run, or hangs past its timeout all mean the same thing here: delivery
    was not confirmed. That is the point — this check exists precisely because
    the child's own exit code cannot be trusted to reflect whether the run did
    anything (fleet-config#560), so an inconclusive post-condition may not
    resolve to "delivered" either.
    """
    try:
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW, timeout=DELIVERY_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        formatter.emit(f"❓ delivery check could not run: {_one_line(exc)}")
        return False
    detail = _one_line((proc.stdout or "").strip() or (proc.stderr or "").strip())
    if proc.returncode == 0:
        formatter.emit(f"✓ delivery confirmed{f' · {detail}' if detail else ''}")
        return True
    formatter.emit(
        f"❓ delivery NOT confirmed (check exit {proc.returncode})"
        + (f" · {detail}" if detail else "")
    )
    return False


def resolve_stall_timeout(explicit: Optional[float] = None) -> float:
    """Flag beats env beats built-in default; ``<= 0`` disables the watchdog."""
    if explicit is not None:
        return max(0.0, explicit)
    raw = os.environ.get("CLAUDE_PROGRESS_STALL_TIMEOUT", "")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_STALL_TIMEOUT_SECONDS


def _kill_process_tree(process: "subprocess.Popen[str]") -> None:
    """Kill the Claude child *and every descendant*.

    ``Popen.kill()`` alone leaves the sub-processes Claude spawned running, and
    they hold the stdout pipe open — so the read loop here would never see EOF
    and this adapter would wedge alongside the run it just tried to end.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                timeout=30,
                creationflags=NO_WINDOW,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the direct-child kill below
    try:
        process.kill()
    except OSError:
        pass


def _watch_for_stall(
    process: "subprocess.Popen[str]",
    progress: ProgressFormatter,
    stall_timeout: float,
    stop_event: threading.Event,
    state: dict[str, bool],
) -> None:
    """Kill the run once its stream has been silent longer than ``stall_timeout``."""
    poll_seconds = max(1.0, min(30.0, stall_timeout / 10))
    while not stop_event.wait(poll_seconds):
        idle = progress.seconds_since_activity()
        if idle < stall_timeout:
            continue
        state["stalled"] = True
        # Kill first, announce second. Announcing first put this thread inside a
        # `print()` on a backpressured stdout and the kill below never ran, so
        # the watchdog meant to un-wedge a jammed run wedged with it and the job
        # read `running` for five hours (fleet-config#514). Killing the tree also
        # stops the child writing into the shared pipe chain, which is what lets
        # the downstream backpressure drain in the first place.
        _kill_process_tree(process)
        progress.emit_best_effort(
            f"⏱ no stream activity for {_elapsed(idle)} "
            f"(limit {_elapsed(stall_timeout)}) — killing the stalled run"
        )
        return


def run_process(
    command: Sequence[str],
    *,
    formatter: Optional[ProgressFormatter] = None,
    env: Optional[dict[str, str]] = None,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT_SECONDS,
) -> int:
    """Run one JSONL-producing child and return its exit code unchanged.

    Returns ``STALL_EXIT_CODE`` instead if the watchdog had to kill the child for
    going silent — a wedged unattended run must surface as a failed job rather
    than hold its slot indefinitely (fleet-config#411).
    """
    progress = formatter or ProgressFormatter()
    child_env = os.environ.copy()
    # Applied after the inherited copy so a stale ambient ceiling can never
    # silently reinstate the 600s sub-agent kill, and before the caller's own
    # `env` so an explicit override still wins (fleet-config#519).
    child_env[BG_WAIT_CEILING_ENV] = BG_WAIT_CEILING_UNLIMITED
    if env:
        child_env.update(env)
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=child_env,
        # This adapter *is* the scheduled-job parent the convention names: every
        # `run-weekly.bat` calls it from an app-launcher job with no console, so
        # an unsuppressed `claude -p` here flashes a window (fleet-config#412).
        # Plain NO_WINDOW, not a new process group — the stall watchdog kills the
        # tree with `taskkill /T`, so no CTRL_BREAK_EVENT signalling is needed.
        creationflags=NO_WINDOW,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    def drain_stderr(stream: TextIO) -> None:
        for stderr_line in stream:
            progress.emit_stderr(stderr_line)

    stderr_thread = threading.Thread(
        target=drain_stderr,
        args=(process.stderr,),
        name="claude-progress-stderr",
        daemon=True,
    )
    stderr_thread.start()

    stall_state = {"stalled": False}
    watchdog_stop = threading.Event()
    watchdog: Optional[threading.Thread] = None
    if stall_timeout > 0:
        watchdog = threading.Thread(
            target=_watch_for_stall,
            args=(process, progress, stall_timeout, watchdog_stop, stall_state),
            name="claude-progress-watchdog",
            daemon=True,
        )
        watchdog.start()

    for stdout_line in process.stdout:
        progress.handle_line(stdout_line)
    exit_code = process.wait()
    watchdog_stop.set()
    stderr_thread.join(timeout=5)
    if watchdog is not None:
        watchdog.join(timeout=5)
    if stall_state["stalled"]:
        exit_code = STALL_EXIT_CODE
    elif progress.saw_kill_signature and exit_code == 0:
        exit_code = BACKGROUND_KILL_EXIT_CODE
    elif progress.saw_self_reported_failure and exit_code == 0:
        exit_code = SELF_REPORTED_FAILURE_EXIT_CODE
    elif progress.stream_truncated and exit_code == 0:
        # Last, and only over a clean exit: the specific detectors above name
        # the cause, this one only knows the stream stopped mid-conversation.
        exit_code = TRUNCATED_STREAM_EXIT_CODE
    elif not progress.saw_tool_use and exit_code == 0:
        # Last of all, and only over a clean exit. Every detector above names a
        # cause; this one only knows the run touched nothing.
        exit_code = NO_TOOL_USE_EXIT_CODE
    progress.finish(exit_code, stalled=stall_state["stalled"])
    return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    _configure_output()
    arguments = list(sys.argv[1:] if argv is None else argv)
    progress = ProgressFormatter()
    try:
        flags = parse_adapter_flags(arguments)
        command = build_command(flags.arguments)
    except ValueError as exc:
        progress.emit(f"❌ usage error: {_one_line(exc)}")
        return 2
    try:
        exit_code = run_process(
            command, formatter=progress, stall_timeout=resolve_stall_timeout(flags.stall_timeout)
        )
        if flags.delivery_check and not run_delivery_check(flags.delivery_check, progress):
            # Runs whatever the child reported, and outranks a clean exit only:
            # a child that already failed keeps the code naming *why* it failed.
            return exit_code if exit_code != 0 else DELIVERY_NOT_CONFIRMED_EXIT_CODE
        return exit_code
    except OSError as exc:
        progress.emit(f"❌ Claude Code failed to start: {_one_line(exc)}")
        progress.finish(127)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
