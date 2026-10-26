"""A chief-managed worker's needs-input goes through the chief mailbox (fleet-config#1456).

Drives the real hook entry points in-process -- Claude's `notify_on_idle.main`
on a `permission_prompt`, Codex's `codex_attention.handle` on a
`PermissionRequest` -- with `subprocess.run` stubbed so the `chief-sid` lookup
answers from the test and any other spawn (a `say` into a chief terminal) is
recorded instead of run. Nothing here can reach a live launcher or a live
chief: the stub never executes anything, and Telegram is stubbed too.

Per harness: chief live -> exactly one `needs-input` event and no `say`, no
Telegram; no live chief -> the Telegram ping exactly as before and no event;
mailbox write fails -> the Telegram ping, with no retry. Then the real
`chief_ops.py wait-event` (a subprocess, as the chief runs it) reads the
written event, and the digest line it prints is pinned.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_needs_input.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import json
import os
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

sys.path.insert(0, str(HOOKS))
import _lib  # noqa: E402
import codex_attention  # noqa: E402
import notify_on_idle  # noqa: E402

_h = CheckHarness()
check = _h.check

WORKER_SID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
DISPATCH_ID = "d" * 32
OLD_PREFIX = "chief-managed worker needs input"
UNKNOWN_EXCERPT = "pending call: unknown (payload message: Claude needs your permission to use Bash)"

tmp = Path(tempfile.mkdtemp(prefix="chief_needs_input_"))
project_dir = tmp / "probe-project"
project_dir.mkdir()
registry = tmp / "projects.toml"
registry.write_text(f'[probe]\ncwd_prefix = "{project_dir.as_posix()}"\ntelegram_chat_attention = "CHAT"\n',
                    encoding="utf-8")

ENV_KEYS = ("APP_LAUNCHER_SESSION_ID", "APP_LAUNCHER_AGENT", "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_HOOKS_STATE_DIR", "CLAUDE_HOOKS_PROJECTS_TOML", "CLAUDE_SESSIONS_DIR")
saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
saved = (subprocess.run, notify_on_idle.notify_send.notify, notify_on_idle.board_link)

spawns: list = []
telegram: list = []
chief = {"rc": 0, "stdout": "CHIEF_SID=chief0123456789abcdef\n"}


def fake_run(argv, *args, **kwargs):
    """Answer `chief-sid` from the test; record everything, execute nothing."""
    spawns.append([str(part) for part in argv])
    if argv and str(argv[-1]) == "chief-sid":
        return subprocess.CompletedProcess(argv, chief["rc"], chief["stdout"], "")
    return subprocess.CompletedProcess(argv, 0, "", "")


def fresh_state(name: str) -> Path:
    state = tmp / name
    state.mkdir()
    (state / "chief-managed.json").write_text(json.dumps({WORKER_SID: {
        "repo": "fleet-config", "number": 1456, "dispatched_at": "2026-10-16T08:00:00Z",
        "dispatch_id": DISPATCH_ID}}), encoding="utf-8")
    return state


def use_state(state: Path, agent: str) -> None:
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(state)
    os.environ["APP_LAUNCHER_AGENT"] = agent
    spawns.clear()
    telegram.clear()


def events(state: Path) -> list:
    inbox = state / "chief-inbox"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(inbox.glob("*.json"))] \
        if inbox.is_dir() else []


def said() -> list:
    return [argv for argv in spawns if "say" in argv]


def lookups() -> list:
    return [argv for argv in spawns if argv and argv[-1] == "chief-sid"]


def claude_prompt(**extra) -> int:
    payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
               "message": "Claude needs your permission to use Bash",
               "session_id": "transcript-uuid", "cwd": str(project_dir), **extra}
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        notify_on_idle.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.stdin = old_stdin
    return 0


def codex_request(turn: str) -> bool:
    return codex_attention.handle({"hook_event_name": "PermissionRequest", "session_id": "codex-s",
                                   "turn_id": turn, "tool_name": "apply_patch",
                                   "cwd": str(project_dir)})


try:
    os.environ["APP_LAUNCHER_SESSION_ID"] = WORKER_SID
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
    os.environ["CLAUDE_HOOKS_PROJECTS_TOML"] = str(registry)
    os.environ["CLAUDE_SESSIONS_DIR"] = str(tmp / "no-sessions")
    subprocess.run = fake_run
    notify_on_idle.notify_send.notify = lambda message, chat, token=None, **kw: telegram.append(message) or True
    # The machine's private Board URL (env or ignored .env) must not leak into
    # the pinned ping text; both hooks reach it through this one function.
    notify_on_idle.board_link = lambda payload, registry=None: None

    # ---- Claude: permission_prompt with a live chief ----
    claude_state = fresh_state("claude-live")
    use_state(claude_state, "claude")
    rc = claude_prompt()
    written = events(claude_state)
    check(rc == 0,
          "claude: hook exits 0")
    check(len(written) == 1,
          "claude: chief live -> exactly one event written")
    event = written[0] if written else {}
    check(event.get("kind") == "needs-input" and event.get("v") == 2
          and event.get("repo") == "fleet-config" and event.get("number") == 1456
          and event.get("launcher_sid") == WORKER_SID and event.get("dispatch_id") == DISPATCH_ID
          and event.get("agent") == "claude",
          "claude: the event is kind=needs-input for the dispatched issue")
    check(event.get("excerpt") == UNKNOWN_EXCERPT
          and event.get("excerpt_source") == "prompt",
          "claude: no transcript -> `pending call: unknown` naming the payload message as its source")
    check(said() == [],
          "claude: nothing is typed into the chief (no say spawn)")
    check(telegram == [],
          "claude: no Telegram ping when the mailbox took it")
    check(OLD_PREFIX not in json.dumps(written, ensure_ascii=False),
          "claude: no worker text carries the old typed prefix")

    # ---- Claude: the excerpt names the pending call (fleet-config#1510) ----
    def tool_use(call_id: str, name: str, tool_input: dict, ts: str) -> dict:
        return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]}}

    def tool_result(call_id: str, ts: str) -> dict:
        return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]}}

    def write_transcript(path: Path, *entries: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    def excerpt_for(name: str, parent_entries: list, subagents: dict = None, **extra) -> str:
        """Run one permission_prompt over a synthetic transcript tree; the event's excerpt."""
        state = fresh_state(name)
        use_state(state, "claude")
        root = tmp / f"transcripts-{name}"
        session = f"sess-{name}"
        parent = root / f"{session}.jsonl"
        write_transcript(parent, *parent_entries)
        for agent, entries in (subagents or {}).items():
            write_transcript(root / session / "subagents" / f"agent-{agent}.jsonl", *entries)
        claude_prompt(**{"transcript_path": str(parent), "session_id": session, **extra})
        got = events(state)
        check(len(got) == 1 and said() == [] and telegram == [],
              f"{name}: one event in the mailbox, nothing typed, no Telegram")
        return str(got[0].get("excerpt")) if got else ""

    line = excerpt_for("pending-parent", [
        tool_use("t1", "Read", {"file_path": "a.txt"}, "2026-10-19T10:00:00Z"),
        tool_result("t1", "2026-10-19T10:00:01Z"),
        tool_use("t2", "Bash", {"command": "rm -rf build && make"}, "2026-10-19T10:00:02Z")])
    check(line == "permission: Bash rm -rf build && make (parent)",
          "pending parent Bash call -> names the tool, the command head and (parent)")

    line = excerpt_for("pending-sub", [
        tool_use("p1", "Agent", {"prompt": "go"}, "2026-10-19T10:00:00Z")],
        {"a0428d80ffff1234": [
            tool_use("s1", "Bash", {"command": "cat > ../x"}, "2026-10-19T10:01:00Z")]})
    check(line == "permission: Bash cat > ../x (sub-agent a0428d80)",
          "pending sub-agent call -> (sub-agent <id8>); the parent's own pending Agent call is not the gate")

    line = excerpt_for("pending-newest", [
        tool_use("p1", "Edit", {"file_path": "old.py"}, "2026-10-19T10:00:00Z")],
        {"b1b1b1b1x": [tool_use("s1", "Write", {"file_path": "new.py"}, "2026-10-19T10:05:00Z")]})
    check(line == "permission: Write new.py (sub-agent b1b1b1b1)",
          "pending calls in several transcripts -> the newest one wins")

    line = excerpt_for("pending-answered", [
        tool_use("t1", "Bash", {"command": "ls"}, "2026-10-19T10:00:00Z"),
        tool_result("t1", "2026-10-19T10:00:01Z")])
    check(line == UNKNOWN_EXCERPT,
          "every call answered -> `pending call: unknown`, not the idle text")

    line = excerpt_for("pending-no-message", [], message="")
    check(line == "pending call: unknown (default text: Claude Code awaits your input)",
          "nothing found and no payload message -> unknown, naming the default text as the source")

    line = excerpt_for("pending-long", [
        tool_use("t1", "Bash", {"command": "echo 1\n" + "x" * 900}, "2026-10-19T10:00:00Z")])
    check("\n" not in line and line.startswith("permission: Bash echo 1 xxx") and line.endswith("(parent)")
          and len(line) < 290,
          "a multi-line, oversized command stays one bounded line that keeps its (parent) tag")

    line = excerpt_for("pending-unreadable", [], transcript_path=str(tmp / "no" / "such.jsonl"))
    check(line == UNKNOWN_EXCERPT,
          "unreadable transcript -> `pending call: unknown`, hook still delivers")

    # ---- Claude: no live chief -> Telegram exactly as before ----
    claude_none = fresh_state("claude-none")
    use_state(claude_none, "claude")
    chief.update(rc=1, stdout="CHIEF_SID=none\n")
    claude_prompt()
    check(events(claude_none) == [],
          "claude: no live chief -> no event")
    check(telegram == ["🔔 [probe] Claude Code awaits your input"],
          "claude: no live chief -> the Telegram ping fires once, as before")
    check(said() == [],
          "claude: no live chief -> no say spawn")
    chief.update(rc=0, stdout="CHIEF_SID=chief0123456789abcdef\n")

    # ---- Claude: mailbox write fails -> Telegram, no retry ----
    claude_broken = fresh_state("claude-broken")
    (claude_broken / "chief-inbox").write_text("not a directory", encoding="utf-8")
    use_state(claude_broken, "claude")
    claude_prompt()
    check(telegram == ["🔔 [probe] Claude Code awaits your input"],
          "claude: failed mailbox write -> the Telegram ping fires once")
    check(len(lookups()) == 1 and said() == [],
          "claude: failed mailbox write -> not retried (one chief-sid lookup, no say)")

    # ---- Codex: PermissionRequest with a live chief ----
    codex_state = fresh_state("codex-live")
    use_state(codex_state, "codex")
    delivered = codex_request("t1")
    written = events(codex_state)
    check(delivered and len(written) == 1,
          "codex: chief live -> handled, exactly one event written")
    event = written[0] if written else {}
    check(event.get("kind") == "needs-input" and event.get("agent") == "codex"
          and event.get("number") == 1456
          and event.get("excerpt") == "Codex in probe awaits approval to edit files",
          "codex: the event is kind=needs-input with the permission context")
    check(said() == [],
          "codex: nothing is typed into the chief (no say spawn)")
    check(telegram == [],
          "codex: no Telegram ping when the mailbox took it")
    check(not codex_request("t1") and len(events(codex_state)) == 1,
          "codex: the same turn is not re-delivered")

    # ---- Codex: no live chief -> Telegram exactly as before ----
    codex_none = fresh_state("codex-none")
    use_state(codex_none, "codex")
    chief.update(rc=1, stdout="CHIEF_SID=none\n")
    codex_request("t2")
    check(events(codex_none) == [],
          "codex: no live chief -> no event")
    check(telegram == ["🔔 probe Codex awaits your input\nWaiting for: approval to edit files"],
          "codex: no live chief -> the Telegram ping fires once, as before")
    chief.update(rc=0, stdout="CHIEF_SID=chief0123456789abcdef\n")

    # ---- Codex: mailbox write fails -> Telegram, no retry ----
    codex_broken = fresh_state("codex-broken")
    (codex_broken / "chief-inbox").write_text("not a directory", encoding="utf-8")
    use_state(codex_broken, "codex")
    codex_request("t3")
    check(len(telegram) == 1,
          "codex: failed mailbox write -> the Telegram ping fires once")
    check(len(lookups()) == 1 and said() == [],
          "codex: failed mailbox write -> not retried (one chief-sid lookup, no say)")

    # ---- hooks/ carries no typed-message path any more ----
    leftovers = [p.name for p in HOOKS.glob("*.py")
                 if OLD_PREFIX in p.read_text(encoding="utf-8", errors="replace")]
    check(leftovers == [],
          "hooks/: no `🔔 chief-managed worker needs input` string remains")
finally:
    subprocess.run, notify_on_idle.notify_send.notify, notify_on_idle.board_link = saved
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

try:
    # ---- the live chief's waiter reads it: real `chief_ops.py wait-event` ----
    # A subprocess, exactly as the chief launches it, against the Claude case's
    # state dir; --base-url points nowhere so the timeout path can never reach
    # a live launcher.
    env = hook_env({"CLAUDE_HOOKS_STATE_DIR": str(claude_state)})
    proc = subprocess.run(
        [PYTHON, str(REPO / "skills" / "_lib" / "chief_ops.py"), "wait-event",
         "--timeout", "6", "--interval", "0.5", "--base-url", "http://127.0.0.1:9"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env,
        creationflags=_lib.NO_WINDOW)
    lines = proc.stdout.splitlines()
    print("wait-event digest:\n" + proc.stdout)
    check(proc.returncode == 0,
          "wait-event: exits 0 on the needs-input event")
    check(lines[:1] == ["WAIT_EVENT=events n=1"],
          "wait-event: header counts the one event")
    check(len(lines) > 1
          and lines[1].startswith(f"- fleet-config#1456: needs input (claude) sid={WORKER_SID[:8]} age=")
          and lines[1].endswith(" kind=needs-input"),
          "wait-event: the event line names the issue, says needs input, and tags the kind")
    check(lines[2:4] == [f"  excerpt (prompt, {len(UNKNOWN_EXCERPT)} chars):", f"  | {UNKNOWN_EXCERPT}"],
          "wait-event: the pending-call line follows as an indented excerpt")
    check(lines[-1:] == ["Next: `exchange <sid>` for each, `verify` any completion claim, "
                         "then absorb or escalate."],
          "wait-event: the closing Next: line is unchanged")
    check(events(claude_state) == [],
          "wait-event: the event was claimed exactly once")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_needs_input")
