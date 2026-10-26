"""Tests for hooks/chief_inbox.py (fleet-config#999), through the real hook modules.

Drives Claude's `session_state`, Codex's `session_state_codex` and Pi's
`session_state_pi` as subprocesses against a temp `CLAUDE_HOOKS_STATE_DIR`,
for the four registry cases -- managed, not launcher-spawned, launcher-spawned
but not dispatched, and an unreadable registry -- and asserts exactly one
event file with the right fields, or none. Every hook still exits 0.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_inbox.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tests"))
from acceptance.shared import HOOKS, PYTHON, hook_env  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(REPO / "hooks"))
import _lib  # noqa: E402

_h = CheckHarness()
check = _h.check

WORKER_SID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def drive(hook: str, payload: dict, state: Path, launcher_sid: str = "", agent: str = "",
          entrypoint: str = "") -> subprocess.CompletedProcess:
    env = hook_env({"CLAUDE_HOOKS_STATE_DIR": str(state),
                    "CLAUDE_SESSIONS_DIR": str(state / "no-sessions"),
                    "APP_LAUNCHER_AGENT": agent})
    if launcher_sid:
        env["APP_LAUNCHER_SESSION_ID"] = launcher_sid
    if entrypoint:
        env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
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

# A `claude -p` child spawned inside a managed lane inherits the lane's
# APP_LAUNCHER_SESSION_ID; Claude Code stamps it CLAUDE_CODE_ENTRYPOINT=sdk-cli
# (probed live, fleet-config#1281). Its turn/session end is not the lane's, and
# its Board row must not carry the lane's launcher id (the exact join would
# hand the lane's card the child's status and transcript).
for event_name in ("Stop", "SessionEnd"):
    state = fresh_state(MANAGED)
    try:
        res = drive("session_state", claude(event_name), state, launcher_sid=WORKER_SID, entrypoint="sdk-cli")
        check(res.returncode == 0 and events(state) == [],
              f"Claude {event_name}, print-mode child of a managed lane: no inbox event under the parent's sid")
    finally:
        shutil.rmtree(state, ignore_errors=True)
    # The lane itself (interactive `cli`) still reports its own turn/session end.
    state = fresh_state(MANAGED)
    try:
        drive("session_state", claude(event_name), state, launcher_sid=WORKER_SID, entrypoint="cli")
        rows = events(state)
        check(len(rows) == 1 and rows[0].get("launcher_sid") == WORKER_SID,
              f"Claude {event_name}, the interactive lane itself: its own event is still written ({rows})")
    finally:
        shutil.rmtree(state, ignore_errors=True)

for entrypoint, expected in (("sdk-cli", None), ("cli", WORKER_SID)):
    state = fresh_state(MANAGED)
    try:
        drive("session_state", claude("UserPromptSubmit"), state, launcher_sid=WORKER_SID, entrypoint=entrypoint)
        board = json.loads((state / "sessions-state.json").read_text(encoding="utf-8"))
        stamped = (board.get("claude-uuid") or {}).get("launcher_session_id", "missing")
        check(stamped == expected,
              f"session_state row, entrypoint {entrypoint}: launcher_session_id is {expected!r} (got {stamped!r})")
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

# ---- mailbox v2 fields (fleet-config#1455), through the same real adapters ----

import re  # noqa: E402

import chief_inbox  # noqa: E402

DISPATCHED = json.dumps({WORKER_SID: {"repo": "fleet-config", "number": 1455,
                                      "dispatched_at": "2026-10-16T10:00:00Z", "dispatch_id": "d" * 32}})
REPLY = "Built it.\nPR #1 is open; review, then /issue-finish."


def files(state: Path) -> list:
    inbox = state / "chief-inbox"
    return sorted(p.name for p in inbox.glob("*.json")) if inbox.is_dir() else []


V2_CASES = (
    # label, hook, turn-end payload carrying the harness's own reply field
    ("Claude", "session_state", {**claude("Stop"), "last_assistant_message": REPLY}),
    ("Codex", "session_state_codex", {**claude("Stop"), "last_assistant_message": REPLY}),
    ("Pi", "session_state_pi", {**pi("agent_settled"), "last_assistant_message": REPLY}),
)
for label, hook, payload in V2_CASES:
    state = fresh_state(DISPATCHED)
    try:
        drive(hook, payload, state, launcher_sid=WORKER_SID)
        rows, names = events(state), files(state)
        row = rows[0] if rows else {}
        check(len(names) == 1 and re.fullmatch(r"\d{13}-[0-9a-f]{32}\.json", names[0])
              and names[0][14:-5] == row.get("id"),
              f"{label} v2: the file is <published_ms>-<id>.json and the id matches ({names})")
        check(row.get("v") == 2 and row.get("kind") == "turn-end" and row.get("event") == "Stop"
              and row.get("dispatch_id") == "d" * 32,
              f"{label} v2: v, kind, the v1 event and the dispatch's dispatch_id ({row})")
        check(row.get("excerpt") == REPLY.strip() and row.get("excerpt_source") == "payload"
              and "excerpt_unavailable" not in row,
              f"{label} v2: the excerpt is the harness's own final-reply field ({row.get('excerpt')!r})")
    finally:
        shutil.rmtree(state, ignore_errors=True)

# Session end, a row with no dispatch_id, and no reply source at all.
for label, hook, payload in (("Claude", "session_state", claude("SessionEnd")),
                             ("Pi", "session_state_pi", pi("session_shutdown"))):
    state = fresh_state(MANAGED)
    try:
        drive(hook, payload, state, launcher_sid=WORKER_SID)
        row = (events(state) or [{}])[0]
        check(row.get("kind") == "session-end" and row.get("dispatch_id") is None
              and row.get("excerpt") is None and row.get("excerpt_unavailable"),
              f"{label} v2 exit: kind session-end, dispatch_id null, excerpt null with a reason ({row})")
    finally:
        shutil.rmtree(state, ignore_errors=True)

# Transcript fallback: Claude/Pi/Codex on-disk shapes, an empty reply field,
# truncation, an unreadable path and a tail with no reply -- never "".
tmp = Path(tempfile.mkdtemp(prefix="chief_inbox_tx_"))
try:
    long_reply = "x" * 1000 + "END" + "y" * 1000
    shapes = {
        "claude": [{"type": "user", "message": {"role": "user", "content": "go"}},
                   {"type": "assistant", "message": {"role": "assistant", "content": [
                       {"type": "text", "text": "Claude final"}, {"type": "tool_use", "name": "x"}]}},
                   {"type": "system", "subtype": "stop"}],
        "pi": [{"type": "message", "message": {"role": "assistant", "content": [
                   {"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "Pi final"}]}},
               {"type": "message", "message": {"role": "toolResult", "content": []}}],
        "codex": [{"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                        "content": [{"type": "output_text", "text": "Codex final"}]}},
                  {"type": "event_msg", "payload": {"type": "token_count"}}],
        "long": [{"type": "assistant", "message": {"role": "assistant", "content": long_reply}}],
        "none": [{"type": "user", "message": {"role": "user", "content": "only a prompt"}}],
    }
    paths = {}
    for name, lines in shapes.items():
        paths[name] = tmp / f"{name}.jsonl"
        paths[name].write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    for name, expected in (("claude", "Claude final"), ("pi", "Pi final"), ("codex", "Codex final")):
        got = chief_inbox.excerpt_fields({"last_assistant_message": "  ", "transcript_path": str(paths[name])})
        check(got == {"excerpt": expected, "excerpt_source": "transcript"},
              f"excerpt: a blank reply field falls back to the {name} transcript's last reply ({got})")
    got = chief_inbox.excerpt_fields({"transcript_path": str(paths["long"])})
    check(len(got["excerpt"]) == chief_inbox.EXCERPT_CHARS and got["excerpt"].endswith("y" * 1000),
          "excerpt: bounded to the last EXCERPT_CHARS characters of the reply")
    for case, payload in (("no transcript reply", {"transcript_path": str(paths["none"])}),
                          ("unreadable transcript", {"transcript_path": str(tmp / "missing.jsonl")}),
                          ("no source at all", {})):
        got = chief_inbox.excerpt_fields(payload)
        check(got.get("excerpt") is None and isinstance(got.get("excerpt_unavailable"), str)
              and got["excerpt_unavailable"],
              f"excerpt, {case}: null with a reason, never an empty string ({got})")
    # The tail read is bounded: a reply older than the window is not found.
    big = tmp / "big.jsonl"
    big.write_text(json.dumps(shapes["claude"][1]) + "\n"
                   + json.dumps({"type": "user", "message": {"role": "user", "content": "p" * chief_inbox.TRANSCRIPT_TAIL_BYTES}})
                   + "\n", encoding="utf-8")
    got = chief_inbox.excerpt_fields({"transcript_path": str(big)})
    check(got.get("excerpt") is None and "KiB" in got.get("excerpt_unavailable", ""),
          f"excerpt: only the last TRANSCRIPT_TAIL_BYTES are read ({got.get('excerpt_unavailable')})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_inbox")
