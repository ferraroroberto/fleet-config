"""Tests for hooks/chief_inbox.py (fleet-config#999), through the real hook modules.

Drives Claude's `session_state`, Codex's `session_state_codex` and Pi's
`session_state_pi` as subprocesses against a temp `CLAUDE_HOOKS_STATE_DIR`,
for the four registry cases -- managed, not launcher-spawned, launcher-spawned
but not dispatched, and an unreadable registry -- and asserts exactly one
event file with the right fields, or none. Every hook still exits 0.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_inbox.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance.shared import HOOKS, PYTHON, hook_env  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(REPO / "hooks"))
import _lib  # noqa: E402

_h = CheckHarness()
check = _h.check

WORKER_SID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def drive(hook: str, payload: dict, state: Path, launcher_sid: str = "", agent: str = "") -> subprocess.CompletedProcess:
    env = hook_env({"CLAUDE_HOOKS_STATE_DIR": str(state),
                    "CLAUDE_SESSIONS_DIR": str(state / "no-sessions"),
                    "APP_LAUNCHER_AGENT": agent})
    if launcher_sid:
        env["APP_LAUNCHER_SESSION_ID"] = launcher_sid
    return subprocess.run([PYTHON, str(HOOKS / f"{hook}.py")], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env,
                          creationflags=_lib.NO_WINDOW)


def events(state: Path) -> list:
    inbox = state / "chief-inbox"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(inbox.glob("*.json"))] \
        if inbox.is_dir() else []


def fresh_state(registry) -> Path:
    state = Path(tempfile.mkdtemp(prefix="chief_inbox_"))
    if registry is not None:
        (state / "chief-managed.json").write_text(registry, encoding="utf-8")
    return state


MANAGED = json.dumps({WORKER_SID: {"repo": "fleet-config", "number": 999,
                                   "dispatched_at": "2026-09-27T10:00:00Z"}})
OTHER = json.dumps({"someone-else": {"repo": "x", "number": 1, "dispatched_at": "2026-09-27T10:00:00Z"}})

claude = lambda event: {"hook_event_name": event, "session_id": "claude-uuid", "cwd": str(REPO)}  # noqa: E731
pi = lambda event: {"event": event, "session_id": "pi-uuid", "cwd": str(REPO)}  # noqa: E731

HARNESSES = (
    # label, hook, payload(turn-end), payload(exit), default agent
    ("Claude", "session_state", claude("Stop"), claude("SessionEnd"), "claude"),
    ("Codex", "session_state_codex", claude("Stop"), claude("SessionEnd"), "codex"),
    ("Pi", "session_state_pi", pi("agent_settled"), pi("session_shutdown"), "pi"),
)

for label, hook, stop_payload, end_payload, default_agent in HARNESSES:
    # Managed worker: one row per event, carrying the dispatched issue.
    for event_name, payload in (("Stop", stop_payload), ("SessionEnd", end_payload)):
        state = fresh_state(MANAGED)
        try:
            res = drive(hook, payload, state, launcher_sid=WORKER_SID)
            rows = events(state)
            check(res.returncode == 0 and len(rows) == 1,
                  f"{label} {event_name}, managed: exactly one inbox event (exit {res.returncode}, {len(rows)} rows)")
            row = rows[0] if rows else {}
            check(row.get("event") == event_name and row.get("launcher_sid") == WORKER_SID
                  and row.get("repo") == "fleet-config" and row.get("number") == 999
                  and row.get("agent") == default_agent and row.get("ts"),
                  f"{label} {event_name}, managed: event/sid/repo/number/agent/ts are right ({row})")
        finally:
            shutil.rmtree(state, ignore_errors=True)

    # The launcher's own agent stamp wins over the adapter default.
    state = fresh_state(MANAGED)
    try:
        drive(hook, stop_payload, state, launcher_sid=WORKER_SID, agent="Codex")
        rows = events(state)
        check(rows and rows[0].get("agent") == "codex",
              f"{label}: APP_LAUNCHER_AGENT names the agent when stamped")
    finally:
        shutil.rmtree(state, ignore_errors=True)

    # Human-started (no launcher sid), launcher-spawned but not dispatched,
    # and an absent registry all write nothing.
    for case, registry, sid in (("not launcher-spawned", MANAGED, ""),
                                ("not dispatched", OTHER, WORKER_SID),
                                ("no registry yet", None, WORKER_SID)):
        state = fresh_state(registry)
        try:
            res = drive(hook, stop_payload, state, launcher_sid=sid)
            check(res.returncode == 0 and events(state) == [],
                  f"{label} Stop, {case}: no inbox event, hook exits 0")
        finally:
            shutil.rmtree(state, ignore_errors=True)

    # Unreadable registry: undetermined is logged, never folded into not-managed.
    state = fresh_state("{not json")
    try:
        res = drive(hook, stop_payload, state, launcher_sid=WORKER_SID)
        check(res.returncode == 0 and events(state) == [],
              f"{label} Stop, malformed registry: nothing written, hook exits 0")
        check("undetermined" in res.stderr,
              f"{label} Stop, malformed registry: logs an undetermined line ({res.stderr.strip()[:120]!r})")
    finally:
        shutil.rmtree(state, ignore_errors=True)

# A turn that is not a turn-end writes nothing even for a managed worker.
state = fresh_state(MANAGED)
try:
    drive("session_state", claude("UserPromptSubmit"), state, launcher_sid=WORKER_SID)
    check(events(state) == [], "Claude UserPromptSubmit, managed: no inbox event")
finally:
    shutil.rmtree(state, ignore_errors=True)

# Fail-open: an inbox path that cannot be a directory never breaks the hook.
state = fresh_state(MANAGED)
try:
    (state / "chief-inbox").write_text("a file where the dir should be", encoding="utf-8")
    res = drive("session_state", claude("Stop"), state, launcher_sid=WORKER_SID)
    check(res.returncode == 0, f"unwritable inbox: the worker's Stop still exits 0 (exit {res.returncode})")
finally:
    shutil.rmtree(state, ignore_errors=True)

_h.report_and_exit("test_chief_inbox")
