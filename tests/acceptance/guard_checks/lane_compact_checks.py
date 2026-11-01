"""Acceptance checks for `hooks/lane_compact`: a chief-dispatched lane's
handoff-then-compact at its per-model context threshold.

The Stop half rides `chief_waiter_stop` and the SessionStart half rides
`chief_handover_sessionstart`, so both are driven through those real hook
entry points: in-process for the verdict matrix, and as subprocesses for the
real wire (a Claude refusal on stderr, a Codex-shaped payload, the
SessionStart envelope). The lane path makes no launcher call, so nothing here
talks to a live launcher. The statusline writer, the gate marker writer
(`gate_evidence.inflight_marker`) and the `lane-compact` refusals are the
other halves of the contract and are checked against the same state dir.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from unittest import mock

from acceptance.shared import HOOKS, REPO, _Checker, run

LANE_SID = "launcher-sid-lane"
TRANSCRIPT_SID = "lane-transcript-uuid"
POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def _import(name: str, root: Path):
    sys.path.insert(0, str(root))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(root))


def _seed(tmp: Path, *, percent: Optional[float] = 65, window: Optional[int] = 1000000,
          model: str = "claude-opus-5-5", managed: bool = True) -> None:
    """A lane that the chief dispatched, with a fresh statusline snapshot."""
    for name in ("lane-compact-nudge", "lane-compact-queued", "gate-inflight"):
        shutil.rmtree(tmp / name, ignore_errors=True)
    (tmp / "sessions-state.json").write_text(json.dumps(
        {TRANSCRIPT_SID: {"name": "fleet-config-1", "status": "working", "launcher_session_id": LANE_SID}}),
        encoding="utf-8")
    (tmp / "chief-managed.json").write_text(json.dumps(
        {LANE_SID: {"repo": "fleet-config", "number": 1}} if managed else {}), encoding="utf-8")
    snapshots = tmp / "session-context"
    snapshots.mkdir(exist_ok=True)
    (snapshots / f"{TRANSCRIPT_SID}.json").write_text(json.dumps(
        {"session_id": TRANSCRIPT_SID, "used_percentage": percent, "context_window_size": window,
         "model_id": model}), encoding="utf-8")


def _gate_marker(tmp: Path, repo: Path, pid: int, name: str = "gate") -> None:
    directory = tmp / "gate-inflight"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({"pid": pid, "repo": str(repo)}), encoding="utf-8")


def _stop(tmp: Path, work: Path, payload: Optional[Dict[str, object]] = None,
          env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """`chief_waiter_stop.main()` in-process for a lane; `(exit code, stderr)`."""
    hook = _import("chief_waiter_stop", HOOKS)
    body = {"hook_event_name": "Stop", "session_id": TRANSCRIPT_SID, "cwd": str(work), **(payload or {})}
    scrubbed = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": LANE_SID,
                "CLAUDE_CODE_ENTRYPOINT": "cli", "FLEET_SCHEDULED_RUN": "", **(env or {})}
    err = io.StringIO()
    with mock.patch.dict(os.environ, scrubbed), \
            mock.patch.object(hook, "_chief_ops", lambda *a: None), \
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(body))), \
            mock.patch.object(sys, "stderr", err):
        try:
            hook.main()
        except SystemExit as exc:
            return int(exc.code or 0), err.getvalue()
    return 0, err.getvalue()


def _threshold_checks(check: _Checker, lane) -> None:
    config = {"default": 70, "windows": {"1000000": 60, "200000": 75},
              "models": {"claude-haiku": 80, "claude-haiku-5-5": 85, "bad": 0}}
    check("lane_compact threshold: exact model id wins", lane.threshold_for(config, "claude-haiku-5-5", 200000) == 85)
    check("lane_compact threshold: longest model prefix next",
          lane.threshold_for(config, "claude-haiku-6", 200000) == 80)
    check("lane_compact threshold: window size when no model entry",
          lane.threshold_for(config, "claude-opus-5-5", 1000000) == 60
          and lane.threshold_for(config, "claude-opus-5-5", 200000) == 75)
    check("lane_compact threshold: default for an unknown window",
          lane.threshold_for(config, "x", 128000) == 70 and lane.threshold_for(config, "", None) == 70)
    check("lane_compact threshold: an out-of-range value is skipped", lane.threshold_for(config, "bad", None) == 70)
    shipped = lane.load_config()
    check("lane_compact threshold: the tracked thresholds.toml loads with the 1M/200k entries",
          shipped is not None and lane.threshold_for(shipped, "claude-opus-5-5", 1000000) == 60
          and lane.threshold_for(shipped, "claude-sonnet-5-5", 200000) == 75)


def _stop_checks(check: _Checker, tmp: Path, work: Path, _lane) -> None:
    """The verdict matrix: over / under / per window / debounce / queued / not managed."""
    _seed(tmp)
    code, err = _stop(tmp, work)
    check("lane_compact stop: 1M lane at 65% (threshold 60) -> held (exit 2) with handoff path and lane-compact",
          code == 2 and f"lane-handoffs/{LANE_SID}.md" in err and "lane-compact" in err
          and (tmp / "lane-compact-nudge" / LANE_SID).exists(), err[:300])
    code, _ = _stop(tmp, work)
    check("lane_compact stop: debounced -- a second turn end inside the window is allowed", code == 0)
    old = time.time() - 3600
    os.utime(tmp / "lane-compact-nudge" / LANE_SID, (old, old))
    code, _ = _stop(tmp, work)
    check("lane_compact stop: a nudge older than the window holds again", code == 2)

    _seed(tmp)
    (tmp / "lane-compact-queued").mkdir()
    (tmp / "lane-compact-queued" / LANE_SID).write_text("1", encoding="utf-8")
    code, _ = _stop(tmp, work)
    check("lane_compact stop: a queued lane-compact silences the hold", code == 0)

    for label, seed, expect in (
        ("200k lane at 65% (threshold 75) -> allowed", {"window": 200000}, 0),
        ("1M lane at 50% -> allowed", {"percent": 50}, 0),
        ("percent null (early / just compacted) -> allowed", {"percent": None}, 0),
        ("not chief-dispatched -> allowed", {"managed": False}, 0),
    ):
        _seed(tmp, **seed)
        code, _ = _stop(tmp, work)
        check(f"lane_compact stop: {label}", code == expect and not (tmp / "lane-compact-nudge").exists())



def _stop_edge_checks(check: _Checker, tmp: Path, work: Path, lane) -> None:
    """Missing or stale readings, gates, early exits, config faults and the real wire."""
    old = time.time() - 3600
    _seed(tmp)
    (tmp / "session-context" / f"{TRANSCRIPT_SID}.json").unlink()
    code, _ = _stop(tmp, work)
    check("lane_compact stop: no statusline snapshot -> allowed (fails open)", code == 0)
    _seed(tmp)
    snap = tmp / "session-context" / f"{TRANSCRIPT_SID}.json"
    os.utime(snap, (old, old))
    code, _ = _stop(tmp, work)
    check("lane_compact stop: stale snapshot -> allowed", code == 0)

    _seed(tmp)
    _gate_marker(tmp, work, os.getpid())
    code, _ = _stop(tmp, work / "sub")
    check("lane_compact stop: a live gate in this tree (cwd in a subfolder) -> never held", code == 0)
    _seed(tmp)
    _gate_marker(tmp, work.parent / "other-repo", os.getpid())
    code, _ = _stop(tmp, work)
    check("lane_compact stop: a gate in another tree does not stop the hold", code == 2)
    _seed(tmp)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _gate_marker(tmp, work, dead.pid)
    code, _ = _stop(tmp, work)
    check("lane_compact stop: a gate marker whose pid is gone is ignored", code == 2)
    _seed(tmp)
    (tmp / "gate-inflight").mkdir()
    (tmp / "gate-inflight" / "broken.json").write_text("{not json", encoding="utf-8")
    code, _ = _stop(tmp, work)
    check("lane_compact stop: an unreadable gate marker counts as a running gate -> allowed", code == 0)

    _seed(tmp)
    code, _ = _stop(tmp, work, {"stop_hook_active": True})
    check("lane_compact stop: turn already continuing after a refusal -> allowed", code == 0)
    code, _ = _stop(tmp, work, env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    check("lane_compact stop: headless child -> allowed", code == 0)
    code, _ = _stop(tmp, work, env={"APP_LAUNCHER_SESSION_ID": ""})
    check("lane_compact stop: not a launcher session -> allowed", code == 0)
    with mock.patch.object(lane, "CONFIG_PATH", tmp / "missing.toml"):
        code, _ = _stop(tmp, work)
    check("lane_compact stop: thresholds.toml unreadable -> allowed", code == 0)
    off = tmp / "off.toml"
    off.write_text("enabled = false\ndefault = 10\n", encoding="utf-8")
    with mock.patch.object(lane, "CONFIG_PATH", off):
        code, _ = _stop(tmp, work)
    check("lane_compact stop: enabled = false -> allowed", code == 0)
    with mock.patch.object(lane, "stop_refusal", side_effect=RuntimeError("boom")):
        code, _ = _stop(tmp, work)
    check("lane_compact stop: an exception in the lane path -> allowed (fails open)", code == 0)

    # Real hook subprocesses: the Claude refusal wire, and a Codex-shaped payload.
    _seed(tmp)
    env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": LANE_SID}
    claude = {"hook_event_name": "Stop", "session_id": TRANSCRIPT_SID, "cwd": str(work),
              "transcript_path": str(tmp / "t.jsonl"), "stop_hook_active": False}
    code, _out, err = run("chief_waiter_stop", claude, extra_env=env)
    check("lane_compact stop e2e: Claude payload over threshold -> exit 2, instruction on stderr",
          code == 2 and "lane-compact" in err, err[:300])
    _seed(tmp)
    codex = {"hook_event_name": "Stop", "session_id": "codex-thread-1", "turn_id": "t1", "cwd": str(work),
             "transcript_path": None, "model": "gpt-5", "stop_hook_active": False, "last_assistant_message": "ok"}
    code, _out, err = run("chief_waiter_stop", codex, extra_env=env)
    check("lane_compact stop e2e: Codex payload (no statusline snapshot) -> allowed, nothing written",
          code == 0 and not (tmp / "lane-compact-nudge").exists(), err[:300])


def _sessionstart_checks(check: _Checker, tmp: Path, work: Path) -> None:
    handoffs = tmp / "lane-handoffs"
    handoffs.mkdir(exist_ok=True)
    handoff = handoffs / f"{LANE_SID}.md"
    handoff.write_text("## lane\nnext: finish the gate, then /issue-finish\n", encoding="utf-8")
    env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp), "APP_LAUNCHER_SESSION_ID": LANE_SID}
    payload = {"hook_event_name": "SessionStart", "session_id": TRANSCRIPT_SID, "cwd": str(work), "source": "compact"}

    def context(body: Dict[str, object], extra: Optional[Dict[str, str]] = None) -> str:
        code, out, _err = run("chief_handover_sessionstart", body, extra_env={**env, **(extra or {})})
        if code != 0 or not out.strip():
            return ""
        try:
            return str(json.loads(out)["hookSpecificOutput"]["additionalContext"])
        except (ValueError, KeyError, TypeError):
            return out

    got = context(payload)
    check("lane_compact resume: compact start in a non-chief repo -> handoff in additionalContext",
          "finish the gate, then /issue-finish" in got, got[:200])
    check("lane_compact resume: a startup is not handed the handoff",
          "finish the gate" not in context({**payload, "source": "startup"}))
    check("lane_compact resume: another launcher session is not handed this lane's handoff",
          "finish the gate" not in context(payload, {"APP_LAUNCHER_SESSION_ID": "someone-else"}))
    handoff.write_text("y" * 9000, encoding="utf-8")
    got = context(payload)
    check("lane_compact resume: an over-long handoff is cut and points at the file",
          got.count("y") < 6100 and "lane-handoffs" in got)
    old = time.time() - 7200
    os.utime(handoff, (old, old))
    check("lane_compact resume: a handoff from before this compaction is not re-injected", not context(payload))


def _writer_checks(check: _Checker, tmp: Path, work: Path, lane) -> None:
    skills = REPO / "skills" / "_lib"
    gate_evidence = _import("gate_evidence", skills)
    sys.path.insert(0, str(skills))
    try:
        from chief import compact  # noqa: PLC0415
    finally:
        sys.path.remove(str(skills))
    check("lane_compact spelling: hooks and skills name the same folders",
          lane.GATE_DIRNAME == gate_evidence.GATE_INFLIGHT_DIRNAME
          and lane.HANDOFF_DIRNAME == compact.LANE_HANDOFF_DIRNAME
          and lane.QUEUED_DIRNAME == compact.LANE_QUEUED_DIRNAME)

    shutil.rmtree(tmp / "gate-inflight", ignore_errors=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _gate_marker(tmp, work, dead.pid, name=str(dead.pid))
    with mock.patch.dict(os.environ, {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}):
        with gate_evidence.inflight_marker(work, "gate"):
            during = gate_evidence.gate_in_flight(work / "sub"), lane.gate_in_flight(str(work))
        after = gate_evidence.gate_in_flight(work), lane.gate_in_flight(str(work))
    check("gate_evidence inflight_marker: both readers see the gate while it runs", during == (True, True))
    check("gate_evidence inflight_marker: the marker is gone once the gate ends", after == (False, False))
    check("gate_evidence inflight_marker: a hard-killed gate's marker is reaped on the next gate",
          not (tmp / "gate-inflight" / f"{dead.pid}.json").exists())

    refusal = compact.lane_compact_refusal
    check("lane-compact refusal: unknown gate state refuses", "unknown" in (refusal(5, None) or ""))
    check("lane-compact refusal: running gate refuses", "gate is running" in (refusal(5, True) or ""))
    check("lane-compact refusal: missing handoff refuses", "missing" in (refusal(None, False) or ""))
    check("lane-compact refusal: stale handoff refuses", "stale" in (refusal(3600, False) or ""))
    check("lane-compact refusal: fresh handoff, no gate -> proceeds", refusal(30, False) is None)

    code, out, err = _chief_ops_lane_compact()
    check("chief_ops lane-compact: outside a launcher session -> REFUSED (exit 1), nothing posted",
          code == 1 and "REFUSED=not a launcher session" in out, out + err)

    if not Path(POWERSHELL).is_file():
        check("statusline snapshot: Windows PowerShell present", False, POWERSHELL)
        return
    payload = {"session_id": "snap-1", "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
               "context_window": {"used_percentage": 61.6, "context_window_size": 1000000}, "cwd": str(work)}
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(tmp)}
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                           str(REPO / "statusline-command.ps1")], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", timeout=60, env=env)
    try:
        snap = json.loads((tmp / "session-context" / "snap-1.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        snap = {}
    check("statusline snapshot: per-session file carries percent, window and model id",
          proc.returncode == 0 and snap.get("used_percentage") == 61.6
          and snap.get("context_window_size") == 1000000 and snap.get("model_id") == "claude-opus-5-5",
          proc.stderr[:200])
    with mock.patch.dict(os.environ, {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}):
        reading, _why = lane.read_snapshot("snap-1", time.time())
    check("statusline snapshot: the hook reads what the statusline wrote",
          reading == {"percent": 62, "model": "claude-opus-5-5", "window": 1000000}, str(reading))


def _chief_ops_lane_compact() -> Tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if k != "APP_LAUNCHER_SESSION_ID"}
    proc = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"), "lane-compact",
                           "--base-url", "http://127.0.0.1:9"], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _lane_compact_unit_checks() -> Tuple[int, int]:
    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="lane_compact_"))
    work = tmp / "work-tree"
    (work / "sub").mkdir(parents=True)
    try:
        lane = _import("lane_compact", HOOKS)
        _threshold_checks(check, lane)
        _stop_checks(check, tmp, work, lane)
        _stop_edge_checks(check, tmp, work, lane)
        _sessionstart_checks(check, tmp, work)
        _writer_checks(check, tmp, work, lane)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total
