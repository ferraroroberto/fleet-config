"""Acceptance checks for `hooks/lane_background`: a chief-dispatched lane may not
end its turn while its own background task is still running.

Driven through the real `chief_waiter_stop` entry point: in-process for the
verdict matrix, and as a subprocess for the Claude refusal wire. Each case
writes a small transcript in the shape Claude Code records -- the launch's
tool result, then the `<task-notification>` queue operation or TaskStop result
that ends it. Nothing here talks to a live launcher.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest import mock

from acceptance.shared import HOOKS, _Checker, run

LANE_SID = "launcher-sid-lane"
TRANSCRIPT_SID = "lane-transcript-uuid"


def _iso(offset_s: float = 0) -> str:
    return datetime.fromtimestamp(time.time() + offset_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _result(text: str, offset_s: float = -60, as_list: bool = False) -> Dict[str, object]:
    content: object = [{"type": "text", "text": text}] if as_list else text
    return {"type": "user", "isSidechain": False, "timestamp": _iso(offset_s),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_x",
                                                     "content": content}]}}


def _bash(task: str, offset_s: float = -60) -> Dict[str, object]:
    return _result(f"Command running in background with ID: {task}. Output is being written to: x.output.",
                   offset_s)


def _monitor(task: str, minutes: int = 30, offset_s: float = -60) -> Dict[str, object]:
    return _result(f"Monitor started (task {task}, expires in {minutes}m unless the source ends first).", offset_s)


def _agent(task: str) -> Dict[str, object]:
    return _result(f"Async agent launched successfully. (internal)\nagentId: {task} (internal ID)", as_list=True)


def _notice(task: str, body: str) -> Dict[str, object]:
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": _iso(),
            "content": f"<task-notification>\n<task-id>{task}</task-id>\n{body}\n</task-notification>"}


def _seed(tmp: Path, records: List[Dict[str, object]], *, managed: bool = True, chief: bool = False) -> Path:
    for name in ("lane-compact-nudge", "lane-compact-queued", "gate-inflight", "session-context"):
        shutil.rmtree(tmp / name, ignore_errors=True)
    (tmp / "sessions-state.json").write_text(json.dumps(
        {TRANSCRIPT_SID: {"name": "chief" if chief else "fleet-config-1", "status": "working",
                          "launcher_session_id": LANE_SID}}), encoding="utf-8")
    (tmp / "chief-managed.json").write_text(json.dumps(
        {LANE_SID: {"repo": "fleet-config", "number": 1}} if managed else {}), encoding="utf-8")
    transcript = tmp / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return transcript


def _stop(tmp: Path, transcript: Optional[Path], payload: Optional[Dict[str, object]] = None,
          env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """`chief_waiter_stop.main()` in-process; `(exit code, stderr, fleet_hooks log)`."""
    hook = _import_hook()
    body = {"hook_event_name": "Stop", "session_id": TRANSCRIPT_SID, "cwd": str(tmp),
            "transcript_path": str(transcript) if transcript else None, "stop_hook_active": False,
            **(payload or {})}
    scrubbed = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": LANE_SID,
                "CLAUDE_CODE_ENTRYPOINT": "cli", "FLEET_SCHEDULED_RUN": "", **(env or {})}
    err, log = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(log)
    logger = logging.getLogger("fleet_hooks")
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with mock.patch.dict(os.environ, scrubbed), \
                mock.patch.object(hook, "_chief_ops", lambda *a: None), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(body))), \
                mock.patch.object(sys, "stderr", err):
            try:
                hook.main()
            except SystemExit as exc:
                return int(exc.code or 0), err.getvalue(), log.getvalue()
        return 0, err.getvalue(), log.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def _import_hook():
    sys.path.insert(0, str(HOOKS))
    try:
        return __import__("chief_waiter_stop")
    finally:
        sys.path.remove(str(HOOKS))


def _held_checks(check: _Checker, tmp: Path) -> None:
    code, err, _ = _stop(tmp, _seed(tmp, [_bash("bgate1")]))
    check("lane_background: running background command -> held (exit 2), names the task and TaskOutput",
          code == 2 and "bgate1" in err and "TaskOutput" in err, err[:300])
    code, err, _ = _stop(tmp, _seed(tmp, [_monitor("bmon1")]))
    check("lane_background: armed Monitor inside its expiry -> held", code == 2 and "Monitor bmon1" in err, err[:200])
    code, err, _ = _stop(tmp, _seed(tmp, [_agent("a1b2c3d4e5f6a7b8c")]))
    check("lane_background: background agent with no end -> held",
          code == 2 and "a1b2c3d4e5f6a7b8c" in err, err[:200])
    code, err, _ = _stop(tmp, _seed(tmp, [_bash("bdone"), _notice("bdone", "<status>completed</status>"),
                                          _bash("blive")]))
    check("lane_background: only the task with no end is named", code == 2 and "blive" in err
          and "bdone" not in err, err[:200])


def _allowed_checks(check: _Checker, tmp: Path) -> None:
    for label, records in (
        ("no background launch", [_result("plain output")]),
        ("command with a completed notification", [_bash("b1"), _notice("b1", "<status>completed</status>")]),
        ("command with a failed notification", [_bash("b1"), _notice("b1", "<status>failed</status>")]),
        ("command stopped by TaskStop", [_bash("b1"), _result('{"message":"Successfully stopped task: b1 (x)"}')]),
        ("Monitor with its expiry event",
         [_monitor("m1"), _notice("m1", "<summary>Monitor event</summary>\n<event>[Monitor expired after 30m]</event>")]),
        ("Monitor past its own expiry", [_monitor("m1", minutes=5, offset_s=-3600)]),
        ("launch older than the stale window", [_bash("b1", offset_s=-5 * 3600)]),
        ("launch phrase mid-output is not a launch", [_result("log: Command running in background with ID: b9")]),
        ("launch phrase in a sidechain record", [{**_bash("b1"), "isSidechain": True}]),
    ):
        code, err, _ = _stop(tmp, _seed(tmp, records))
        check(f"lane_background: {label} -> allowed", code == 0, err[:200])
    code, _err, _ = _stop(tmp, _seed(tmp, [_bash("b1"), _notice("b1", "<summary>Monitor event: tick</summary>")]))
    check("lane_background: a non-terminal Monitor event does not end a command -> held", code == 2)


def _scope_checks(check: _Checker, tmp: Path) -> None:
    code, _err, _ = _stop(tmp, _seed(tmp, [_bash("b1")], managed=False))
    check("lane_background: launcher session not chief-dispatched -> allowed", code == 0)
    code, _err, _ = _stop(tmp, _seed(tmp, [_bash("b1")]), env={"APP_LAUNCHER_SESSION_ID": ""})
    check("lane_background: not a launcher session -> allowed", code == 0)
    code, err, _ = _stop(tmp, _seed(tmp, [_bash("b1")], chief=True))
    check("lane_background: the chief itself is never held by this check",
          "background work" not in err and code == 0, err[:200])
    code, _err, _ = _stop(tmp, _seed(tmp, [_bash("b1")]), env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    check("lane_background: print-mode child -> allowed", code == 0)
    code, _err, _ = _stop(tmp, _seed(tmp, [_bash("b1")]), {"stop_hook_active": True})
    check("lane_background: repeat -- turn already continuing after a refusal -> allowed", code == 0)
    _seed(tmp, [])
    code, _err, log = _stop(tmp, tmp / "missing.jsonl")
    check("lane_background: unreadable transcript -> allowed, logged unknown", code == 0 and "unknown" in log, log)
    code, _err, log = _stop(tmp, None)
    check("lane_background: no transcript path -> allowed, logged unknown", code == 0 and "unknown" in log, log)


def _wire_checks(check: _Checker, tmp: Path) -> None:
    transcript = _seed(tmp, [_bash("bwire")])
    env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": LANE_SID}
    claude = {"hook_event_name": "Stop", "session_id": TRANSCRIPT_SID, "cwd": str(tmp),
              "transcript_path": str(transcript), "stop_hook_active": False}
    code, _out, err = run("chief_waiter_stop", claude, extra_env=env)
    check("lane_background e2e: Claude payload with a running task -> exit 2, instruction on stderr",
          code == 2 and "bwire" in err, err[:300])
    code, _out, err = run("chief_waiter_stop", {**claude, "stop_hook_active": True}, extra_env=env)
    check("lane_background e2e: the follow-up stop is allowed", code == 0, err[:300])


def _lane_background_unit_checks() -> Tuple[int, int]:
    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="lane_background_"))
    try:
        _held_checks(check, tmp)
        _allowed_checks(check, tmp)
        _scope_checks(check, tmp)
        _wire_checks(check, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total
