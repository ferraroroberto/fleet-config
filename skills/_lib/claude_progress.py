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
exits ``125`` instead, even when the child process itself reported ``0``.
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
from typing import Any, Optional, TextIO

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

# A burst of unknown-typed stream records right at shutdown is the secondary
# symptom of the same kill — informational only, never fails a run by itself.
UNKNOWN_BURST_WINDOW_SECONDS = 15.0
UNKNOWN_BURST_THRESHOLD = 3

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_RE = re.compile(
    r"(?i)("
    r"(?:sk-(?:ant-)?|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
    r")"
)


def _is_background_kill_signature(text: str) -> bool:
    """True for the stderr line Claude prints when it kills in-flight tasks."""
    lower = text.lower()
    return all(term in lower for term in KILL_SIGNATURE_TERMS)


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

    @property
    def saw_kill_signature(self) -> bool:
        return self._saw_kill_signature

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
        if not text or text in self._assistant_texts:
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
        now = self._clock()
        recent_unknown = sum(
            1 for seen_at in self._unknown_timestamps if now - seen_at <= UNKNOWN_BURST_WINDOW_SECONDS
        )
        if recent_unknown >= UNKNOWN_BURST_THRESHOLD:
            self.emit(
                f"⚠ burst of {recent_unknown} unknown stream record(s) near shutdown "
                "— possible truncated/killed stream"
            )
        failed = stalled or exit_code != 0 or self._result_error or self._saw_kill_signature
        if stalled:
            status = "⏱ stalled"
        elif self._saw_kill_signature:
            status = (
                "❌ failed · background tasks killed after timeout — orchestrator "
                "likely ended its turn with agents in flight"
            )
        else:
            status = "❌ failed" if failed else "✅ completed"
        result_note = " · no terminal result event" if not self._saw_result else ""
        self.emit(f"{status} · exit {exit_code}{result_note}")


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
    return [claude, "-p", *arguments, "--output-format", "stream-json", "--verbose"]


def parse_adapter_flags(arguments: Sequence[str]) -> tuple[list[str], Optional[float]]:
    """Split adapter-owned flags out of the caller's Claude arguments.

    ``--stall-timeout`` configures *this* process's watchdog and must never be
    forwarded to ``claude``, which would reject it as an unknown flag.
    """
    remaining: list[str] = []
    stall: Optional[float] = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == STALL_FLAG:
            if index + 1 >= len(arguments):
                raise ValueError(f"{STALL_FLAG} requires a value in seconds")
            value = arguments[index + 1]
            index += 2
        elif argument.startswith(STALL_FLAG + "="):
            value = argument.split("=", 1)[1]
            index += 1
        else:
            remaining.append(argument)
            index += 1
            continue
        try:
            stall = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{STALL_FLAG} expects seconds, got {value!r}") from None
    return remaining, stall


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
    progress.finish(exit_code, stalled=stall_state["stalled"])
    return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    _configure_output()
    arguments = list(sys.argv[1:] if argv is None else argv)
    progress = ProgressFormatter()
    try:
        arguments, stall_flag = parse_adapter_flags(arguments)
        command = build_command(arguments)
    except ValueError as exc:
        progress.emit(f"❌ usage error: {_one_line(exc)}")
        return 2
    try:
        return run_process(
            command, formatter=progress, stall_timeout=resolve_stall_timeout(stall_flag)
        )
    except OSError as exc:
        progress.emit(f"❌ Claude Code failed to start: {_one_line(exc)}")
        progress.finish(127)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
