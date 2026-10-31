"""Acceptance checks for `chief_waiter_stop.py` and the waiter heartbeat.

The hook's verdicts that reach the launcher (`wake`, `fleet-jobs`) run
in-process with `_chief_ops` stubbed, so the suite never talks to a live
launcher and its verdicts do not depend on the box it runs on. The cases that
must never reach a launcher at all -- a non-chief session, a headless
`claude -p` child -- run as real hook subprocesses, which is the proof that
they exit before any launcher read.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest import mock

from acceptance.shared import HOOKS, REPO, _Checker, run

CHIEF_LAUNCHER_SID = "launcher-sid-chief"
LANE_LAUNCHER_SID = "launcher-sid-lane"
CHIEF_PAYLOAD = {"hook_event_name": "Stop", "session_id": "chief-transcript-uuid"}


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_state(tmp: Path, *, lane_status: Optional[str], chief_name: str = "chief") -> None:
    """A state dir: the chief's own row, plus one chief-managed lane in `lane_status`."""
    now = _stamp(datetime.now(timezone.utc))
    rows = {"chief-transcript-uuid": {"name": chief_name, "status": "working",
                                      "launcher_session_id": CHIEF_LAUNCHER_SID, "updated_at": now}}
    if lane_status is not None:
        rows["lane-transcript-uuid"] = {"name": "fleet-config-1", "status": lane_status,
                                        "launcher_session_id": LANE_LAUNCHER_SID, "updated_at": now}
    (tmp / "sessions-state.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp / "chief-managed.json").write_text(json.dumps(
        {LANE_LAUNCHER_SID: {"repo": "fleet-config", "number": 1, "dispatched_at": now}}), encoding="utf-8")


def _write_heartbeat(tmp: Path, name: str, *, end_in_s: float, pid: Optional[int] = None) -> None:
    now = datetime.now(timezone.utc)
    directory = tmp / "chief-waiters"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(
        {"pid": pid, "started": _stamp(now), "expected_end": _stamp(now + timedelta(seconds=end_in_s))}),
        encoding="utf-8")


def _drive(tmp: Path, payload: Dict[str, object], chief_ops: Callable[..., Optional[str]],
           env: Optional[Dict[str, str]] = None) -> Tuple[int, List[Tuple[str, ...]]]:
    """Run the hook's `main()` in-process; returns `(exit code, chief_ops calls)`."""
    sys.path.insert(0, str(HOOKS))
    try:
        import chief_waiter_stop  # noqa: PLC0415
    finally:
        sys.path.remove(str(HOOKS))
    calls: List[Tuple[str, ...]] = []

    def stub(*args: str) -> Optional[str]:
        calls.append(args)
        return chief_ops(*args)

    scrubbed = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": CHIEF_LAUNCHER_SID,
                "CLAUDE_CODE_ENTRYPOINT": "cli", "FLEET_SCHEDULED_RUN": "", **(env or {})}
    with mock.patch.dict(os.environ, scrubbed), \
            mock.patch.object(chief_waiter_stop, "_chief_ops", stub), \
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
            mock.patch.object(sys, "stderr", io.StringIO()):
        try:
            chief_waiter_stop.main()
        except SystemExit as exc:
            return int(exc.code or 0), calls
    return 0, calls


NOT_DUE = "COMPACT_NOT_DUE percent=12 threshold=30"


def _launcher(wake: str = "WAKE=waiter doorbell=off", jobs: str = "FLEET_JOBS=0",
              compact: str = NOT_DUE) -> Callable[..., Optional[str]]:
    answers = {"fleet-jobs": jobs, "compact-due": compact}
    return lambda *args: answers.get(args[0], wake)


def _waiter_calls(calls: List[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    """The launcher reads other than the context gate's own `compact-due`."""
    return [c for c in calls if c[0] != "compact-due"]


def _compact_gate_checks(check: _Checker, tmp: Path) -> None:
    """The context-threshold hold: over / under / unknown / debounce / queued / non-chief."""
    over = "COMPACT_DUE percent=61 threshold=30"
    nudge, queued = tmp / "chief-compact-nudge", tmp / "chief-compact-queued"
    _seed_state(tmp, lane_status=None)  # no lane working: only the context gate can hold the turn

    def _asked(calls: List[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
        return [c for c in calls if c[0] == "compact-due"]

    def drive(compact: str, payload: Dict[str, object] = CHIEF_PAYLOAD, env: Optional[Dict[str, str]] = None):
        return _drive(tmp, payload, _launcher(compact=compact), env)

    code, _ = drive(NOT_DUE)
    check("chief_compact_gate: under the threshold -> turn end allowed, no marker", code == 0 and not nudge.exists())
    code, _ = drive("COMPACT_OFF")
    check("chief_compact_gate: auto-compact off in Settings -> allowed", code == 0 and not nudge.exists())
    code, _ = drive("COMPACT_DUE=unknown reason=percent not reported")
    check("chief_compact_gate: unknown percent -> allowed (fails open) and writes no marker",
          code == 0 and not nudge.exists())
    code, _ = drive("")
    check("chief_compact_gate: no answer from chief_ops -> allowed", code == 0 and not nudge.exists())

    code, _ = drive(over)
    check("chief_compact_gate: over the threshold -> turn end held (exit 2), nudge marker written",
          code == 2 and nudge.exists())
    code, calls = drive(over)
    check("chief_compact_gate: debounced -- a second turn end inside the window is allowed without asking",
          code == 0 and not _asked(calls), str(calls))
    old = time.time() - 3600
    os.utime(nudge, (old, old))
    code, _ = drive(over)
    check("chief_compact_gate: a nudge older than the window holds again", code == 2)

    os.utime(nudge, (old, old))
    queued.write_text("1", encoding="utf-8")
    code, calls = drive(over)
    check("chief_compact_gate: a queued self-compact silences the hold without asking",
          code == 0 and not _asked(calls), str(calls))
    queued.unlink()
    nudge.unlink()

    code, calls = drive(over, {**CHIEF_PAYLOAD, "stop_hook_active": True})
    check("chief_compact_gate: turn already continuing after a refusal -> never held again",
          code == 0 and not calls and not nudge.exists())
    code, calls = drive(over, env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    check("chief_compact_gate: headless child of the chief is never held", code == 0 and not calls)
    code, calls = drive(over, {**CHIEF_PAYLOAD, "session_id": "worker-transcript-uuid"},
                        {"APP_LAUNCHER_SESSION_ID": ""})
    check("chief_compact_gate: non-chief session is untouched (exit 0, no launcher read, no marker)",
          code == 0 and not calls and not nudge.exists(), str(calls))
    _seed_state(tmp, lane_status=None, chief_name="telegram:general")
    code, calls = drive(over)
    check("chief_compact_gate: a telegram:* session is untouched", code == 0 and not calls and not nudge.exists())
    _seed_state(tmp, lane_status="working")


def _chief_waiter_stop_unit_checks() -> Tuple[int, int]:
    """`chief_waiter_stop.py` + `waiter_heartbeat.py`: the four issue payloads,
    the expiry/dead-pid rule, and the never-refuse-headless guarantee."""
    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="chief_waiter_stop_"))
    try:
        _seed_state(tmp, lane_status="working")
        code, calls = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: lane working + no heartbeat -> refused (exit 2)", code == 2)

        _write_heartbeat(tmp, "live", end_in_s=300)
        code, calls = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: live heartbeat -> allowed before any launcher read",
              code == 0 and not _waiter_calls(calls), str(calls))

        shutil.rmtree(tmp / "chief-waiters")
        _write_heartbeat(tmp, "expired", end_in_s=-120)
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: expired heartbeat counts as no waiter -> refused", code == 2)

        shutil.rmtree(tmp / "chief-waiters")
        _write_heartbeat(tmp, "dead", end_in_s=300, pid=2 ** 22 - 7)
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: dead pid counts as no waiter -> refused", code == 2)

        shutil.rmtree(tmp / "chief-waiters")
        _write_heartbeat(tmp, "mine", end_in_s=300, pid=os.getpid())
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: heartbeat with a live pid -> allowed", code == 0)
        shutil.rmtree(tmp / "chief-waiters")

        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher(wake="WAKE=doorbell doorbell=on"))
        check("chief_waiter_stop: doorbell on needs no waiter -> allowed", code == 0)

        _seed_state(tmp, lane_status="needs-you")
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
        check("chief_waiter_stop: no lane working, no fleet job -> allowed", code == 0)
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher(jobs="FLEET_JOBS=1"))
        check("chief_waiter_stop: fleet job running, no heartbeat -> refused", code == 2)
        code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher(jobs="FLEET_JOBS=unknown reason=down"))
        check("chief_waiter_stop: fleet jobs unknown -> fails open", code == 0)

        _seed_state(tmp, lane_status="working")
        code, _ = _drive(tmp, {**CHIEF_PAYLOAD, "stop_hook_active": True}, _launcher())
        check("chief_waiter_stop: turn already continuing after a refusal -> allowed", code == 0)

        _compact_gate_checks(check, tmp)

        # Subprocess cases: these must exit before any launcher read, so no stub is possible.
        env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": CHIEF_LAUNCHER_SID}
        code, _out, _err = run("chief_waiter_stop", CHIEF_PAYLOAD,
                               extra_env={**env, "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
        check("chief_waiter_stop: headless `claude -p` child of the chief is never refused", code == 0)
        code, _out, _err = run("chief_waiter_stop", CHIEF_PAYLOAD,
                               extra_env={**env, "FLEET_SCHEDULED_RUN": "1"})
        check("chief_waiter_stop: scheduled run is never refused", code == 0)
        code, _out, _err = run("chief_waiter_stop", {**CHIEF_PAYLOAD, "session_id": "other-uuid"},
                               extra_env={"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": ""})
        check("chief_waiter_stop: non-chief session -> no-op (exit 0, no launcher read)", code == 0)
        (tmp / "sessions-state.json").unlink()
        code, _out, _err = run("chief_waiter_stop", CHIEF_PAYLOAD, extra_env=env)
        check("chief_waiter_stop: no session state at all -> no-op", code == 0)

        # The heartbeat writer is the other half of the contract the hook reads.
        sys.path.insert(0, str(REPO / "skills" / "_lib"))
        try:
            from chief import waiter_heartbeat  # noqa: PLC0415
        finally:
            sys.path.remove(str(REPO / "skills" / "_lib"))
        _seed_state(tmp, lane_status="working")
        with mock.patch.dict(os.environ, {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}):
            with waiter_heartbeat.armed(60, pid=os.getpid()):
                code, calls = _drive(tmp, CHIEF_PAYLOAD, _launcher())
                check("waiter_heartbeat: armed() heartbeat satisfies the hook", code == 0 and not _waiter_calls(calls))
            code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
            check("waiter_heartbeat: heartbeat is gone once the waiter exits -> refused", code == 2)
            waiter_id = waiter_heartbeat.start(60)
            code, _ = _drive(tmp, CHIEF_PAYLOAD, _launcher())
            check("waiter_heartbeat: start() without a pid is live until stop()", code == 0)
            waiter_heartbeat.stop(waiter_id)
            check("waiter_heartbeat: stop() removes the file",
                  not list((tmp / "chief-waiters").glob("*.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total
