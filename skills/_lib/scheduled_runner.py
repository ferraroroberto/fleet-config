"""Shared scheduled lifecycle for explicit Claude/Codex process adapters.

The compatible claude_progress.py facade retains existing Claude callers.
New callers use scheduled_runner.py --harness codex PROMPT --model MODEL
with an explicit supported permission choice. Provider argv/JSONL live in
runner_adapters.py; this module owns progress, watchdog/cancellation, terminal
evidence, delivery checks and the existing pre-tool-only Claude retry policy.
Unknown events, malformed records, missing results and unfinished work never
become success merely because the child exited zero. No schedule is migrated.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402
from scheduled.process_scope import ProcessScope, PipeReader, DRAIN_TIMEOUT_SECONDS, TERMINATE_TIMEOUT_SECONDS  # noqa: E402
from rate_gate import STALL_TIMEOUT_ENV, UNATTENDED_ENV  # noqa: E402
from scheduled.runner_adapters import (  # noqa: E402
    RESERVED_FLAGS as RESERVED_FLAGS,
    ClaudeAdapter,
    CodexAdapter,
    normalize_skill_prompt as normalize_skill_prompt,
)
from scheduled.progress import (  # noqa: E402
    AUTH_UNAVAILABLE_EXIT_CODE,
    BACKGROUND_KILL_EXIT_CODE,
    BG_WAIT_CEILING_ENV as BG_WAIT_CEILING_ENV,
    CANCEL_POLL_SECONDS,
    CANCELLATION_UNCONFIRMED_EXIT_CODE,
    CANCELLED_EXIT_CODE,
    DEFAULT_STALL_TIMEOUT_SECONDS,
    DELIVERY_CHECK_FLAG,
    DELIVERY_CHECK_TIMEOUT_SECONDS,
    DELIVERY_NOT_CONFIRMED_EXIT_CODE,
    INCOMPLETE_WORK_EXIT_CODE,
    MISSING_TOOLS_EXIT_CODE,
    MODEL_UNAVAILABLE_EXIT_CODE,
    NO_TOOL_USE_EXIT_CODE,
    SELF_REPORTED_FAILURE_EXIT_CODE,
    SELF_REPORTED_FAILURE_MARKER as SELF_REPORTED_FAILURE_MARKER,
    STALL_EXIT_CODE,
    STALL_FLAG,
    STALL_POLL_MAX_SECONDS,
    STALL_POLL_MIN_SECONDS,
    TRANSIENT_API_BACKOFF_SECONDS,
    TRANSIENT_API_EXIT_CODE,
    TRANSIENT_API_MAX_ATTEMPTS,
    TRUNCATED_STREAM_EXIT_CODE,
    UNKNOWN_BURST_THRESHOLD as UNKNOWN_BURST_THRESHOLD,
    _configure_output,
    _elapsed,
    _is_self_reported_failure as _is_self_reported_failure,
    _is_transient_api_error as _is_transient_api_error,
    _one_line,
    ProgressFormatter,
)


def build_command(arguments: Sequence[str], executable: Optional[str] = None) -> list[str]:
    """Build the child command while keeping stream-owned flags canonical."""
    return ClaudeAdapter().build_command(arguments, executable)



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


def _kill_process_tree(process: subprocess.Popen) -> bool:
    """Terminate the retained scope; a dead root PID is not a tree identity."""
    scope = getattr(process, "_scheduled_scope", None)
    return scope.terminate() if scope is not None else False


def _watch_for_stall(
    process: "subprocess.Popen[str]",
    progress: ProgressFormatter,
    stall_timeout: float,
    stop_event: threading.Event,
    state: dict[str, bool],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Kill the run once its stream has been silent longer than ``stall_timeout``."""
    poll_seconds = (
        CANCEL_POLL_SECONDS if cancel_event is not None
        else max(STALL_POLL_MIN_SECONDS, min(STALL_POLL_MAX_SECONDS, stall_timeout / 10))
    )
    while not stop_event.wait(poll_seconds):
        if cancel_event is not None and cancel_event.is_set():
            state["cancelled"] = True
            state["cancel_confirmed"] = _kill_process_tree(process)
            state["stop_complete"] = True
            return
        if stall_timeout <= 0:
            continue
        idle = progress.seconds_since_activity()
        if idle < stall_timeout:
            continue
        state["stalled"] = True
        # Kill first, announce second. Announcing first put this thread inside a
        # `print()` on a backpressured stdout and the kill below never ran, so
        # the watchdog meant to un-wedge a jammed run wedged with it and the job
        # read `running` for five hours. Killing the tree also
        # stops the child writing into the shared pipe chain, which is what lets
        # the downstream backpressure drain in the first place.
        confirmed = _kill_process_tree(process)
        state["stop_complete"] = True
        progress.emit_best_effort(
            f"⏱ no stream activity for {_elapsed(idle)} "
            f"(limit {_elapsed(stall_timeout)}) — killing the stalled run · "
            f"owned termination {'confirmed' if confirmed else 'unconfirmed'}"
        )
        return


MAX_NAMED_DESCENDANTS = 8
DESCENDANT_COMMAND_CHARS = 160


def _owned_descendant_lines(members: Optional[list]) -> list[str]:
    """One breadcrumb per owned process still alive at the drain deadline.

    The count alone left #911's three processes undiagnosable from the log. The
    command line is truncated and secret-redacted like every other echoed
    value; ``None`` members (no job list, e.g. POSIX) say so rather than
    implying there was nothing to name.
    """
    if members is None:
        return ["owned descendants could not be named"]
    lines = []
    for pid, image, command in members[:MAX_NAMED_DESCENDANTS]:
        detail = _one_line(command, DESCENDANT_COMMAND_CHARS) if command else "command line unreadable"
        lines.append(f"owned descendant still running · pid {pid} · {image or 'image unreadable'} · {detail}")
    if len(members) > MAX_NAMED_DESCENDANTS:
        lines.append(f"… and {len(members) - MAX_NAMED_DESCENDANTS} more owned descendant(s)")
    return lines


def _child_environment(
    progress: ProgressFormatter, stall_timeout: float, env: Optional[dict[str, str]],
) -> dict[str, str]:
    """The child's environment: inherited, then adapter, unattended and caller overrides."""
    child_env = os.environ.copy()
    # Applied after the inherited copy so a stale ambient ceiling can never
    # silently reinstate the 600s sub-agent kill, and before the caller's own
    # `env` so an explicit override still wins.
    child_env.update(progress.adapter.environment())
    # Nobody attends a run launched here, so the rate gate must not treat a
    # missing statusline signal as "proceed".
    child_env[UNATTENDED_ENV] = "1"
    # A silent rate-gate wait emits no stream record, so the gate caps its wait
    # under this run's actual watchdog rather than a guessed one.
    child_env[STALL_TIMEOUT_ENV] = str(max(0.0, float(stall_timeout)))
    if env:
        child_env.update(env)
    for key in progress.adapter.excluded_environment:
        child_env.pop(key, None)
    return child_env


def _launch(command: Sequence[str], child_env: dict[str, str]) -> tuple[ProcessScope, "subprocess.Popen[str]"]:
    """Start the child inside an owned scope, closing the scope if the launch fails."""
    scope = ProcessScope()
    process = None
    try:
        process = scope.launch(list(command), env=child_env)
    except BaseException:
        scope.close()
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise
    assert process.stdout is not None
    assert process.stderr is not None
    return scope, process


def _pump(
    process: "subprocess.Popen[str]",
    scope: ProcessScope,
    readers: tuple[PipeReader, PipeReader],
    progress: ProgressFormatter,
    stall_state: dict[str, bool],
    cancel_event: Optional[threading.Event],
    outcome: dict[str, Any],
) -> None:
    """Feed both pipes to the formatter until the run and its owned scope have drained.

    Writes into ``outcome`` as it goes, so an interrupt mid-loop leaves
    whatever was already established for the caller's teardown.
    """
    drain_deadline = None
    while True:
        for line in readers[0].poll():
            progress.handle_line(line)
        for line in readers[1].poll():
            progress.emit_stderr(line)
        outcome["exit_code"] = process.poll()
        stopping = stall_state["cancelled"] or stall_state["stalled"]
        if outcome["exit_code"] is not None or stopping:
            if drain_deadline is None:
                drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
                if stopping and not stall_state["stop_complete"]:
                    drain_deadline += TERMINATE_TIMEOUT_SECONDS
            active = scope.active()
            if (all(reader.eof for reader in readers) and active == 0
                    and (not stopping or stall_state["stop_complete"])
                    and not (cancel_event is not None and cancel_event.is_set()
                             and not stall_state["cancelled"])):
                return
            if time.monotonic() >= drain_deadline:
                orphaned = active is not None and active > 0 and not stopping
                outcome["orphaned"] = orphaned
                outcome["orphan_count"] = active if orphaned else 0
                if orphaned:
                    # Named now: the teardown below kills them.
                    outcome["orphan_members"] = scope.describe_active()
                # This runner stopped watching before the run was over, so
                # its outcome is a fact nobody established: verdict-bearing,
                # and it closes the pre-effect replay gate even when both
                # pipes reached EOF and the parent failed.
                if active != 0 or not all(reader.eof for reader in readers):
                    progress._mark_unverified("drain/deadline-reached")
                outcome["drain_message"] = (
                    f"owned scope drain deadline reached · active processes "
                    f"{active if active is not None else 'unknown'} · "
                    f"stdout EOF={readers[0].eof} · stderr EOF={readers[1].eof}"
                )
                return
        time.sleep(0.01)


def _verdict(
    progress: ProgressFormatter,
    outcome: dict[str, Any],
    stall_state: dict[str, bool],
    readers: tuple[PipeReader, PipeReader],
) -> int:
    """Turn the drained run's evidence into its final exit code and verdict line."""
    if outcome["drain_message"]:
        progress.emit_best_effort(outcome["drain_message"])
    if outcome["orphaned"]:
        for line in _owned_descendant_lines(outcome["orphan_members"]):
            progress.emit_best_effort(line)
    exit_code = outcome["exit_code"]
    if exit_code is None:
        exit_code = CANCELLATION_UNCONFIRMED_EXIT_CODE
    if stall_state["cancelled"]:
        confirmed = stall_state["cancel_confirmed"] and all(reader.eof for reader in readers)
        cancelled_code = CANCELLED_EXIT_CODE if confirmed else CANCELLATION_UNCONFIRMED_EXIT_CODE
        progress.finish(cancelled_code)
        return cancelled_code
    if stall_state["stalled"]:
        exit_code = STALL_EXIT_CODE
    elif outcome["orphaned"] and exit_code == 0:
        progress.emit_best_effort("unfinished owned descendants after provider exit")
        # Handed to the formatter as well as the log: the verdict line is what
        # the job card and the Telegram alert carry, and a breadcrumb printed
        # three lines above it is not in either.
        progress._mark_owned_orphans(outcome["orphan_count"])
        exit_code = INCOMPLETE_WORK_EXIT_CODE
    elif exit_code != 0 and progress.saw_transient_api_error:
        # Only ever over a child that already failed — this renames a failure,
        # it never creates one. A run that hit a 5xx, recovered on its own and
        # exited 0 succeeded, and saying otherwise would invent a red job.
        exit_code = TRANSIENT_API_EXIT_CODE
    elif progress._provider_error and (exit_code != 0 or progress._result_error):
        exit_code = {"auth": AUTH_UNAVAILABLE_EXIT_CODE, "model": MODEL_UNAVAILABLE_EXIT_CODE, "tools": MISSING_TOOLS_EXIT_CODE}[progress._provider_error]
    elif progress.saw_kill_signature and exit_code == 0:
        exit_code = BACKGROUND_KILL_EXIT_CODE
    elif progress.saw_self_reported_failure and exit_code == 0:
        exit_code = SELF_REPORTED_FAILURE_EXIT_CODE
    elif progress._result_error and exit_code == 0:
        exit_code = 1
    elif progress.unfinished_work and exit_code == 0:
        exit_code = INCOMPLETE_WORK_EXIT_CODE
    elif progress.unverified_stream and exit_code == 0:
        # Last, and only over a clean exit: the specific detectors above name
        # the cause, this one only knows the stream stopped mid-conversation.
        exit_code = TRUNCATED_STREAM_EXIT_CODE
    elif not progress.saw_tool_use and exit_code == 0:
        # Last of all, and only over a clean exit. Every detector above names a
        # cause; this one only knows the run touched nothing.
        exit_code = NO_TOOL_USE_EXIT_CODE
    progress.finish(exit_code, stalled=stall_state["stalled"])
    return exit_code


def run_process(
    command: Sequence[str],
    *,
    formatter: Optional[ProgressFormatter] = None,
    env: Optional[dict[str, str]] = None,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT_SECONDS,
    cancel_event: Optional[threading.Event] = None,
) -> int:
    """Run one JSONL-producing child and return its exit code unchanged.

    Returns ``STALL_EXIT_CODE`` instead if the watchdog had to kill the child for
    going silent — a wedged unattended run must surface as a failed job rather
    than hold its slot indefinitely (fleet-config#411).
    """
    progress = formatter or ProgressFormatter()
    scope, process = _launch(command, _child_environment(progress, stall_timeout, env))
    readers = (PipeReader(process.stdout), PipeReader(process.stderr))

    stall_state = {"stalled": False, "cancelled": False, "cancel_confirmed": False,
                   "stop_complete": False}
    watchdog_stop = threading.Event()
    watchdog: Optional[threading.Thread] = None
    if stall_timeout > 0 or cancel_event is not None:
        watchdog = threading.Thread(
            target=_watch_for_stall,
            args=(process, progress, stall_timeout, watchdog_stop, stall_state, cancel_event),
            name="scheduled-progress-watchdog",
            daemon=True,
        )
        watchdog.start()

    outcome: dict[str, Any] = {"exit_code": None, "orphaned": False, "orphan_count": 0,
                               "orphan_members": None, "drain_message": None}
    try:
        _pump(process, scope, readers, progress, stall_state, cancel_event, outcome)
    except KeyboardInterrupt:
        stall_state["cancelled"] = True
        stall_state["cancel_confirmed"] = _kill_process_tree(process)
        stall_state["stop_complete"] = True
        deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        while not all(reader.eof for reader in readers) and time.monotonic() < deadline:
            for reader in readers:
                reader.poll()
            time.sleep(0.01)
    finally:
        watchdog_stop.set()
        if watchdog is not None:
            watchdog.join(timeout=TERMINATE_TIMEOUT_SECONDS + 1)
        # Closing the Windows job is a final ownership-scoped safety net. It
        # cannot upgrade an unconfirmed cancellation into confirmed success.
        if scope.active() != 0:
            scope.terminate()
        scope.close()
        if process.poll() is None:
            try:
                process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                progress._mark_unverified("process/exit-wait-timeout")
        if outcome["exit_code"] is None:
            outcome["exit_code"] = process.poll()
        process.stdout.close()
        process.stderr.close()
    return _verdict(progress, outcome, stall_state, readers)


def run_with_transient_retry(
    command: Sequence[str],
    *,
    formatter: ProgressFormatter,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the child, restarting it while a *safe* transient upstream error is the cause.

    The retry is bounded by `TRANSIENT_API_BACKOFF_SECONDS` and gated on
    `retryable_transient_failure`, so it fires only for a run that hit a 5xx
    having invoked nothing — never for a stall, a self-reported failure, a
    truncated stream, a non-transient error, or a run that already did work.

    Synchronous by construction. Every caller of this module is an unattended
    scheduled `.bat`; a headless run has no wake-up mechanism, so waiting out
    the backoff has to happen inside this process or not at all.

    `sleep` is injected so the tests can prove the retry policy without
    actually parking for four minutes.
    """
    # One source of truth for "how many retries": the attempt cap trims the
    # backoff schedule, and everything else — the loop, the `n/N` in the log,
    # the give-up tally — is derived from the trimmed list. Reading the cap in
    # one place and the raw tuple's length in another is how a log line starts
    # promising a third attempt that the loop never makes.
    schedule = TRANSIENT_API_BACKOFF_SECONDS[: max(0, TRANSIENT_API_MAX_ATTEMPTS - 1)]
    attempts = 1
    exit_code = run_process(command, formatter=formatter, stall_timeout=stall_timeout)
    for retry_number, delay in enumerate(schedule, start=1):
        # Gated on the *classification landing*, not merely on a 5xx having been
        # seen. A run can emit a 5xx and then stall, or emit one and still exit
        # 0 having touched nothing: both are safe to retry, and both are
        # pointless to retry — the stall would cost another 45-minute watchdog
        # window per attempt, and the exit-0 run is already destined for 120
        # whatever a retry does. Requiring the code to actually be
        # TRANSIENT_API_EXIT_CODE means the retry fires only where run_process
        # concluded the upstream error is what failed the run.
        if exit_code != TRANSIENT_API_EXIT_CODE or not formatter.retryable_transient_failure:
            return exit_code
        formatter.emit(
            f"↻ transient API error — retry {retry_number}/{len(schedule)} "
            f"in {_elapsed(delay)}"
        )
        sleep(delay)
        # Judge the new attempt on its own evidence, not the failed one's.
        formatter.reset_for_retry()
        attempts += 1
        exit_code = run_process(command, formatter=formatter, stall_timeout=stall_timeout)
    if exit_code == TRANSIENT_API_EXIT_CODE and formatter.retryable_transient_failure:
        formatter.emit(
            f"↻ transient API error persisted across {attempts} attempt(s) — giving up; "
            "check https://status.claude.com"
        )
    return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    _configure_output()
    arguments = list(sys.argv[1:] if argv is None else argv)
    progress = ProgressFormatter()
    try:
        harness = "claude"
        if arguments[:1] == ["--harness"]:
            if len(arguments) < 3:
                raise ValueError("--harness requires claude or codex and a prompt")
            harness, arguments = arguments[1], arguments[2:]
        if harness not in {"claude", "codex"}:
            raise ValueError(f"unsupported scheduled harness: {harness}")
        progress = ProgressFormatter(adapter=ClaudeAdapter() if harness == "claude" else CodexAdapter())
        flags = parse_adapter_flags(arguments)
        command = progress.adapter.build_command(flags.arguments)
    except ValueError as exc:
        progress.emit(f"❌ usage error: {_one_line(exc)}")
        return 2
    try:
        # The delivery check below stays outside the retry loop deliberately: it
        # is a post-condition on the run as a whole, so it runs once, against
        # whatever the final attempt left behind.
        exit_code = run_with_transient_retry(
            command, formatter=progress, stall_timeout=resolve_stall_timeout(flags.stall_timeout)
        )
        if flags.delivery_check and not run_delivery_check(flags.delivery_check, progress):
            # Runs whatever the child reported, and outranks a clean exit only:
            # a child that already failed keeps the code naming *why* it failed.
            final_code = exit_code if exit_code != 0 else DELIVERY_NOT_CONFIRMED_EXIT_CODE
            progress.emit(f"❓ final outcome · delivery not confirmed · exit {final_code}")
            return final_code
        return exit_code
    except OSError as exc:
        progress.emit(f"❌ {progress.adapter.label} failed to start: {_one_line(exc)}")
        progress.finish(127)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
