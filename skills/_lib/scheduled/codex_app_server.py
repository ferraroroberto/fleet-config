"""One Codex `app-server --stdio` JSON-RPC session (fleet-config#1062).

`quota_sources` (account/rate-limit read) and `discovery_probe` (skills/list)
both spawn the installed Codex client's app server, run the
initialize/initialized handshake and read one or two responses. This module
owns the spawn, the reader thread feeding a queue, the deadline loop and the
reap; each caller keeps only its method calls and its own error vocabulary.

stdlib only. The session never inspects a message beyond its `id`, so callers
decide what an `error` member or an odd result shape means.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any, Mapping, Optional

from no_window import NO_WINDOW


class AppServerTimeout(Exception):
    """No response with the wanted id arrived before the deadline."""


class AppServerExited(Exception):
    """The child closed stdout before the wanted response arrived."""


class AppServerSession:
    """A running app server plus the queue its reader thread fills.

    Construct with `spawn`; the constructor takes a ready `process` and
    `messages` queue so a test can drive `request` with fakes.
    """

    def __init__(self, process: Any, messages: "queue.Queue[Optional[dict[str, Any]]]",
                 reader: Optional[threading.Thread] = None) -> None:
        self._process = process
        self._messages = messages
        self._reader = reader

    @classmethod
    def spawn(cls, executable: str, *, cwd: Optional[Any] = None,
              env: Optional[Mapping[str, str]] = None, queue_max: int = 0) -> "AppServerSession":
        """Start `<executable> app-server --stdio` and its reader thread.
        `queue_max` bounds the queue (0 = unbounded); a full queue stops the
        reader, which the waiting side sees as `AppServerExited`."""
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        messages: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue(maxsize=queue_max)

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    messages.put_nowait(message)
                except queue.Full:
                    break
            try:
                messages.put_nowait(None)  # end-of-output sentinel
            except queue.Full:
                pass

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        return cls(process, messages, reader)

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(payload) + "\n")
        self._process.stdin.flush()

    def request(self, request_id: int, method: str, params: dict[str, Any],
                timeout: float) -> dict[str, Any]:
        """Send one request and return the whole response message with that
        id (an `error` member included), skipping unrelated notifications."""
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerTimeout(method)
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty:
                raise AppServerTimeout(method) from None
            if message is None:
                raise AppServerExited(method)
            if message.get("id") == request_id:
                return message

    def initialize(self, client_name: str, timeout: float) -> dict[str, Any]:
        """Run the initialize request (id 1) and, unless it answered with an
        `error`, send the `initialized` notification. Returns the response."""
        response = self.request(1, "initialize",
                                {"clientInfo": {"name": client_name, "version": "1"}}, timeout)
        if "error" not in response:
            self._send({"method": "initialized"})
        return response

    def close(self, wait_seconds: float) -> None:
        """Close stdin, then reap the child (terminate, then kill, after
        `wait_seconds` each) and release the reader and stdout."""
        process = self._process
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=wait_seconds)
        if self._reader is not None:
            self._reader.join(timeout=1)
        if process.stdout:
            process.stdout.close()
