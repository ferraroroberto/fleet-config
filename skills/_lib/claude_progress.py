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
from typing import Any, Optional, TextIO

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

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_RE = re.compile(
    r"(?i)("
    r"(?:sk-(?:ant-)?|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
    r")"
)


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
        self._emit_raw = emit or (lambda line: print(line, flush=True))
        self._emit_lock = threading.Lock()
        self._tools: dict[str, str] = {}
        self._assistant_texts: set[str] = set()
        self._malformed = 0
        self._unknown = 0
        self._result_error = False
        self._saw_result = False

    def _prefix(self) -> str:
        return f"[{_elapsed(self._clock() - self._started_at)}]"

    def emit(self, message: str) -> None:
        with self._emit_lock:
            self._emit_raw(f"{self._prefix()} {message}")

    def emit_stderr(self, line: str) -> None:
        clean = _one_line(line)
        if not clean:
            return
        self.emit(f"⚠ Claude stderr: {clean}")

    def handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
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
            self._unknown += 1

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
        self._unknown += 1

    def _handle_assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            self._unknown += 1
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
            self._unknown += 1

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
            self._unknown += 1
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                self._unknown += 1
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

    def finish(self, exit_code: int) -> None:
        if self._malformed or self._unknown:
            self.emit(
                "⚠ ignored "
                f"{self._malformed} malformed and {self._unknown} unknown stream record(s)"
            )
        failed = exit_code != 0 or self._result_error
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


def run_process(
    command: Sequence[str],
    *,
    formatter: Optional[ProgressFormatter] = None,
    env: Optional[dict[str, str]] = None,
) -> int:
    """Run one JSONL-producing child and return its exit code unchanged."""
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
    for stdout_line in process.stdout:
        progress.handle_line(stdout_line)
    exit_code = process.wait()
    stderr_thread.join(timeout=5)
    progress.finish(exit_code)
    return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    _configure_output()
    arguments = list(sys.argv[1:] if argv is None else argv)
    progress = ProgressFormatter()
    try:
        command = build_command(arguments)
    except ValueError as exc:
        progress.emit(f"❌ usage error: {_one_line(exc)}")
        return 2
    try:
        return run_process(command, formatter=progress)
    except OSError as exc:
        progress.emit(f"❌ Claude Code failed to start: {_one_line(exc)}")
        progress.finish(127)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
