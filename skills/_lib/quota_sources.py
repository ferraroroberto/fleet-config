"""One-shot producers for native Claude statusline and Codex account quotas."""
from __future__ import annotations

import argparse
import json
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from no_window import NO_WINDOW
from quota_snapshot import (account_key, empty_source, identifier, iso_utc, observation,
                            parse_time, publish, read_snapshot, refresh_states, utc_now, window)

LOGGER = logging.getLogger(__name__)
# App-server is experimental. A new version requires the documented native probe.
CODEX_VERSIONS = {"0.153.3"}
RPC_TIMEOUT_SECONDS = 20


def claude_source(raw: Any) -> dict[str, Any]:
    """Consume only the legacy-shaped subset from this native statusline render."""
    now = utc_now()
    source = empty_source("claude-statusline", "claude", "anthropic", "unknown",
                          "windows_absent", now=now)
    if not isinstance(raw, dict):
        return dict(source, state="error", reason="source_shape")
    observed = parse_time(raw.get("captured_at"))
    if observed is None or observed > now:
        return dict(source, state="unknown", reason="observation_time_missing")
    windows = []
    for name, minutes in (("five_hour", 300), ("seven_day", 10080)):
        entry = raw.get(name)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            return dict(source, state="error", reason="source_shape")
        windows.append(window(name, minutes, entry.get("used_percentage"), entry.get("resets_at")))
    if not windows:
        return source
    item = observation("anthropic", "claude-code", windows, now=observed)
    source.update(state=item["state"], reason="native_observation", observations=[item])
    return refresh_states(source, now)


def codex_source(raw: Any, version: Optional[str], *, now: Any = None) -> dict[str, Any]:
    """Normalize a verified app-server result, never a token_count event."""
    now = now or utc_now()
    source = empty_source("codex-app-server", "codex", "openai", "unknown",
                          "windows_absent", now=now, client_version=version)
    if version not in CODEX_VERSIONS:
        return dict(source, state="unsupported", reason="client_version_unverified")
    if not isinstance(raw, dict) or "rateLimits" not in raw:
        return dict(source, state="error", reason="source_shape")
    buckets = raw.get("rateLimitsByLimitId")
    if buckets is None:
        buckets = {}
    if not isinstance(buckets, dict):
        return dict(source, state="error", reason="source_shape")
    buckets = dict(buckets)
    fallback = raw["rateLimits"]
    if fallback is not None:
        if not isinstance(fallback, dict):
            return dict(source, state="error", reason="source_shape")
        fallback_id = fallback.get("limitId")
        if fallback_id is not None and (not isinstance(fallback_id, str) or not fallback_id):
            return dict(source, state="error", reason="source_shape")
        # Older native single-bucket responses may have no ID. Do not invent it.
        if not fallback_id and not buckets:
            return dict(source, state="unknown", reason="bucket_identity_missing")
        if fallback_id and fallback_id not in buckets:
            buckets[fallback_id] = fallback
    native_id = raw.get("accountId")
    account = account_key("openai", native_id) if isinstance(native_id, str) and native_id.strip() else None
    try:
        for bucket, entry in buckets.items():
            identifier(bucket)
            if not isinstance(entry, dict) or entry.get("limitId") != bucket:
                raise ValueError("bucket_shape")
            windows = []
            for name in ("primary", "secondary"):
                value = entry.get(name)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise ValueError("window_shape")
                windows.append(window(name, value.get("windowDurationMins"),
                                      value.get("usedPercent"), value.get("resetsAt")))
            source["observations"].append(observation("openai", bucket, windows,
                                                       now=now, account=account))
    except (TypeError, ValueError):
        return dict(source, state="error", reason="source_shape", observations=[])
    if source["observations"]:
        source.update(state="available" if any(o["state"] == "available" for o in source["observations"]) else "unknown",
                      reason="native_observation")
    return refresh_states(source, now)


class NativeReadError(Exception):
    """Safe categorical failure; upstream messages may include account data."""


def _request(process: subprocess.Popen[str], messages: queue.Queue[Any],
             request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + RPC_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeReadError("native_timeout")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty:
            raise NativeReadError("native_timeout") from None
        if message is None:
            raise NativeReadError("native_exited")
        if message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise NativeReadError("method_unsupported" if code == -32601 else "native_request_failed")
        if not isinstance(message.get("result"), dict):
            raise NativeReadError("source_shape")
        return message["result"]


def collect_codex() -> dict[str, Any]:
    """Read the installed native account method once; no thread or model turn.

    Own only this short-lived stdio child. Normal native authentication is used
    in place; credentials are neither read, copied nor included in output.
    """
    source = empty_source("codex-app-server", "codex", "openai", "unknown", "client_absent")
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        return source
    process = None
    reader = None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                encoding="utf-8", timeout=10, creationflags=NO_WINDOW)
        match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)\s*", result.stdout)
        version = match.group(1) if result.returncode == 0 and match else None
        source["source"]["client_version"] = version
        if version not in CODEX_VERSIONS:
            return dict(source, state="unsupported", reason="client_version_unverified")
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        messages: queue.Queue[Any] = queue.Queue(maxsize=100)

        def read_output() -> Optional[bool]:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        messages.put_nowait(message)
                except ValueError:
                    continue
                except queue.Full:
                    break
            try:
                messages.put_nowait(None)
            except queue.Full:
                pass
            return None

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        _request(process, messages, 1, "initialize",
                 {"clientInfo": {"name": "fleet_quota_snapshot", "version": "1"}})
        assert process.stdin is not None
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        account = _request(process, messages, 2, "account/read", {"refreshToken": False}).get("account")
        if account is None:
            return dict(source, state="unknown", reason="account_unavailable")
        if not isinstance(account, dict) or "type" not in account:
            return dict(source, state="error", reason="source_shape")
        if account["type"] != "chatgpt":
            return dict(source, state="unsupported", reason="auth_mode_unsupported")
        result = _request(process, messages, 3, "account/rateLimits/read", {})
        return codex_source(result, version)
    except NativeReadError as exc:
        reason = str(exc)
        LOGGER.info("Codex quota read: %s", reason)
        return dict(source, state="unsupported" if reason == "method_unsupported" else "error", reason=reason)
    except (OSError, subprocess.SubprocessError):
        LOGGER.info("Codex quota read: native_process_failed")
        return dict(source, state="error", reason="native_process_failed")
    finally:
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if reader is not None:
                reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()


def main(argv: Optional[list[str]] = None) -> int:
    """Publish one source, or read the shared snapshot; stdout is JSON only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("claude", "codex", "read"))
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "read":
            print(json.dumps(read_snapshot(args.state_dir), allow_nan=False))
            return 0
        if args.command == "claude":
            try:
                raw = json.load(sys.stdin)
            except ValueError:
                raw = None
            source = claude_source(raw)
        else:
            source = collect_codex()
        publish(source, args.state_dir)
        # Summary deliberately excludes percentages, account keys and raw errors.
        print(json.dumps({"producer": source["producer"], "state": source["state"],
                          "reason": source["reason"]}))
        return 1 if source["state"] == "error" else 0
    except (OSError, ValueError, TypeError, KeyError):
        LOGGER.error("Quota snapshot: publish_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
