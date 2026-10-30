"""A Grok Build session the chief can delegate read-only tasks to, and the owner can message.

The assistant is never a second orchestrator: it reads fleet state and answers
in one reply file. It holds no lanes and never dispatches, types into, stops or
merges anything. That boundary is mechanism, not prompt text, in two walls:

- a `PreToolUse` hook (`guard`) refuses every tool call that is not a read tool,
  one of the exact read commands, the poller, or a write of the reply file.
  Grok's `dontAsk` mode is not that closed list in a headless run: an unlisted
  command and a new file both ran under it on grok 1.0.70.
- `--deny` rules for every write verb, which Grok enforces whatever the folder
  trust and which beat the allow rules it merges from `~/.claude/settings.json`.

The hook sits in the session folder's `.claude/settings.json`; Grok runs project
hooks only in a trusted folder, so the child runs with `GROK_FOLDER_TRUST=0`
instead of a trust grant.

    grok_assistant.py start [--dry-run]   run the headless session (blocks)
    grok_assistant.py ask "<task>"        the chief hands it a task
    grok_assistant.py reply               print the latest reply
    grok_assistant.py poll                the session's monitor command
    grok_assistant.py guard               the session's PreToolUse hook
    grok_assistant.py setup              the owner's Telegram setup steps

State lives in `GROK_ASSISTANT_HOME` (default `~/.grok-assistant`), outside
every repo: `session/` is the Grok working folder holding `BRIEF.md` and
`reply.md`, `inbox.jsonl` holds the chief's tasks, `telegram.json` the
optional bot token and the owner's chat id. `poll` prints one line per new
task or owner message, which wakes the session through its monitor, and sends
a reply addressed to a Telegram message back to the owner's chat. The token is
read only by `poll`; the session is denied reading it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from no_window import NO_WINDOW  # noqa: E402

logger = logging.getLogger("fleet_chief")

REPO = Path(__file__).resolve().parents[3]
CHIEF_OPS = REPO / "skills" / "_lib" / "chief_ops.py"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
# Read verbs of chief_ops.py; every other verb writes or types into a session.
READ_VERBS = ("board", "sessions", "exchange", "issues")
WRITE_VERBS = ("dispatch", "say", "stop", "escalate", "plan", "sweep", "self-compact",
               "inbox", "ack", "verify", "wake", "wait-event")
GH_READS = ("issue view", "issue list", "pr view", "pr list", "pr checks", "run view", "run list")
GH_WRITES = ("issue create", "issue close", "issue comment", "issue edit", "issue reopen",
             "pr create", "pr merge", "pr close", "pr comment", "pr edit", "pr review",
             "run rerun", "run cancel", "api", "repo", "release", "workflow", "secret")
# Grok merges the user's `~/.claude/settings.json` allow rules into every session
# (`git push:*`, `python -c ':*`, `py <hook>.py:*` among them) and deny beats allow, so
# interpreters and shell wrappers are denied outright: they could run anything.
SHELL_WRITES = ("git ", "curl", "Invoke-WebRequest", "Invoke-RestMethod", "Remove-Item",
                "Set-Content", "Out-File", "New-Item", "rm ", "del ", "Stop-Process", "taskkill",
                "python", "py ", ".venv", "powershell", "pwsh", "cmd", "bash", "sh ",
                "chmod", "node", "npx")
# Twelve hours, then the run stops its monitor and exits; `start` again to resume.
BACKGROUND_WAIT_S = 43200
POLL_INTERVAL_S = 5

BRIEF = """# Grok assistant brief

You are an assistant to the fleet chief, a Claude session that orchestrates the owner's
repos. You are not the chief: you never dispatch, type into, stop or merge anything, never
edit a repo and never message anyone. You read fleet state and answer.

Messages reach you as monitor lines:
- `TASK task-<n> from chief: <text>` is a bounded task from the chief.
- `MSG tg-<n> from owner: <text>` is the owner writing from Telegram.

Answer each one by overwriting `{reply}` with the id on the first line, then the answer:

    re: <id>
    <your answer, plain text, under 3000 characters>

A reply to a `tg-` id goes to the owner's chat; a `task-` reply is read by the chief.
Write the reply file only after the answer is complete, and once per message.

The only commands you can run (anything else is refused):
{commands}
"""

FIRST_PROMPT = """Read BRIEF.md in this folder and follow it. Start the monitor tool now, persistent,
description "assistant inbox", on this exact command:
{poll}
Then end your turn with the line READY and wait for monitor lines."""

SETUP = """Telegram setup for the Grok assistant (run once, by the owner):
1. In Telegram, message @BotFather, send /newbot and pick a name. It answers with a token.
   Use a new bot: a token another poller reads (a life-os bot) would split its messages.
2. Message the new bot once from your own account, then open
   https://api.telegram.org/bot<token>/getUpdates in a browser and note message.chat.id.
3. Write {path} (outside every repo, never committed) as:
   {{"token": "<token>", "chat_id": <your chat id>}}
4. Restart the assistant: grok_assistant.py start
Without that file the assistant runs on chief tasks only."""


def home_dir() -> Path:
    return Path(os.environ.get("GROK_ASSISTANT_HOME") or Path.home() / ".grok-assistant")


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


def read_commands(python: str) -> List[str]:
    """The exact read-only command lines the session may run, as the brief lists them."""
    ops = f"{python} {_posix(CHIEF_OPS)}"
    return [f"{ops} {verb}" for verb in READ_VERBS] + [f"gh {sub}" for sub in GH_READS]


def permission_args(home: Path, python: str) -> List[str]:
    """Grok `--allow`/`--deny` flags: reads, the reply file and the poller, nothing else."""
    session = home / "session"
    ops = f"{python} {_posix(CHIEF_OPS)}"
    allow = ["Read", "Grep", f"Edit({_posix(session / 'reply.md')})",
             f"Bash({python} {_posix(Path(__file__))} poll)"]
    for line in read_commands(python):
        allow += [f"Bash({line})", f"Bash({line} *)"]
    deny = [f"Bash({ops} {verb}*)" for verb in WRITE_VERBS]
    deny += [f"Bash(gh {sub}*)" for sub in GH_WRITES]
    deny += [f"Bash({cmd}*)" for cmd in SHELL_WRITES]
    deny += [f"Bash({python} {_posix(Path(__file__))} {verb}*)" for verb in ("ask", "start", "setup")]
    deny += [f"Edit({_posix(REPO.parent)}/**)", f"Edit({_posix(session / 'BRIEF.md')})",
             f"Edit({_posix(session / '.claude')}/**)",
             f"Read({_posix(home / 'telegram.json')})", "Read(**/.env)", "Edit(**/.env)",
             "WebFetch"]
    args: List[str] = []
    for rule in allow:
        args += ["--allow", rule]
    for rule in deny:
        args += ["--deny", rule]
    return args


def grok_argv(home: Path, python: str) -> List[str]:
    poll = f"{python} {_posix(Path(__file__))} poll"
    # Always-approve because a headless run cannot answer a prompt; the guard and
    # the deny rules are the boundary.
    return ["grok", "-p", FIRST_PROMPT.format(poll=poll), "--cwd", str(home / "session"),
            "--always-approve", "--no-subagents", "--disable-web-search",
            "--background-wait-timeout", str(BACKGROUND_WAIT_S),
            *permission_args(home, python)]


# Grok tool names (raw, or as the fleet wire normalizes them) that only read.
READ_TOOLS = {"Read", "Grep", "Glob", "todo_write", "get_command_or_subagent_output",
              "wait_commands_or_subagents"}
WRITE_TOOLS = {"Write", "Edit", "write", "edit", "search_replace", "hashline_edit"}
SHELL_TOOLS = {"Bash", "monitor"}
# Shell syntax that could chain, redirect or substitute past an allowed prefix.
SHELL_META = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")


def guard_decision(home: Path, python: str, tool: str, tool_input: dict) -> Optional[str]:
    """None to allow the call, else the refusal reason the model sees."""
    if tool in READ_TOOLS:
        return None
    if tool in WRITE_TOOLS:
        target = next((tool_input[k] for k in ("file_path", "path", "target_file", "filePath")
                       if isinstance(tool_input.get(k), str)), "")
        reply = home / "session" / "reply.md"
        if target and os.path.normcase(Path(target).resolve()) == os.path.normcase(reply.resolve()):
            return None
        return f"the assistant writes only {_posix(reply)}"
    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "").strip()
        if any(meta in command for meta in SHELL_META):
            return "one plain command per call: no chaining, pipes, redirects or substitution"
        poll_cmd = f"{python} {_posix(Path(__file__))} poll"
        if command == poll_cmd:
            return None
        if tool == "Bash" and any(command == line or command.startswith(line + " ")
                                  for line in read_commands(python)):
            return None
        return "not an allowed command; BRIEF.md lists the only commands the assistant may run"
    return f"the assistant may not use {tool or 'this tool'}"


def guard(home: Path, python: str) -> int:
    """PreToolUse hook body: refuse through the fleet wire so Grok gets its own dialect."""
    sys.path.insert(0, str(REPO / "hooks"))
    import _lib
    logging.basicConfig(filename=home / "guard.log", level=logging.INFO, format="%(asctime)s %(message)s")
    payload = _lib.read_stdin_json()
    tool, tool_input = _lib.tool_name(payload), _lib.tool_input(payload)
    reason = guard_decision(home, python, tool, tool_input)
    logger.info("ℹ️ %s keys=%s -> %s", tool, sorted(tool_input), reason or "allow")
    if reason is None:
        _lib.allow()
    _lib.block(f"grok assistant guard: {reason}")
    return 2


def lay_out(home: Path, python: str) -> None:
    session = home / "session"
    (session / ".claude").mkdir(parents=True, exist_ok=True)
    commands = "\n".join(f"- `{line}`" for line in read_commands(python))
    (session / "BRIEF.md").write_text(
        BRIEF.format(reply=_posix(session / "reply.md"), commands=commands), encoding="utf-8")
    (session / "reply.md").touch()
    hook = {"type": "command", "command": f"{python} {_posix(Path(__file__))} guard"}
    settings = {"hooks": {"PreToolUse": [{"hooks": [hook]}]}}
    (session / ".claude" / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")


def _load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)


def one_line(text: str) -> str:
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


def owner_lines(updates: List[dict], chat_id: int) -> Tuple[List[str], Optional[int]]:
    """Monitor lines for the owner's text messages; any other sender is dropped unseen."""
    lines: List[str] = []
    last: Optional[int] = None
    for update in updates:
        last = update.get("update_id", last)
        message = update.get("message") or {}
        if (message.get("chat") or {}).get("id") == chat_id and message.get("text"):
            lines.append(f"MSG tg-{update['update_id']} from owner: {one_line(message['text'])}")
    return lines, last


def parse_reply(text: str) -> Tuple[Optional[str], str]:
    """`re: <id>` on the first line, then the body; (None, "") when the file is not a reply."""
    head, _, body = text.strip().partition("\n")
    if not head.lower().startswith("re:"):
        return None, ""
    return head[3:].strip(), body.strip()


Transport = Callable[[str, Dict[str, object]], dict]


def telegram_transport(token: str) -> Transport:
    def call(method: str, params: Dict[str, object]) -> dict:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(TELEGRAM_API.format(token=token, method=method), data=data)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    return call


def poll_once(home: Path, state: dict, telegram: Optional[Tuple[Transport, int]]) -> List[str]:
    """One pass: new chief tasks and owner messages as lines, and a new Telegram reply sent."""
    lines: List[str] = []
    inbox = home / "inbox.jsonl"
    if inbox.exists():
        rows = inbox.read_text(encoding="utf-8").splitlines()
        for row in rows[state.get("inbox_seen", 0):]:
            task = json.loads(row)
            lines.append(f"TASK {task['id']} from chief: {one_line(task['text'])}")
        state["inbox_seen"] = len(rows)
    if telegram is None:
        return lines
    call, chat_id = telegram
    result = call("getUpdates", {"offset": state.get("offset", 0), "timeout": 0})
    new, last = owner_lines(result.get("result") or [], chat_id)
    lines += new
    if last is not None:
        state["offset"] = last + 1
    reply = (home / "session" / "reply.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    if digest != state.get("reply_sent"):
        target, body = parse_reply(reply)
        if target and target.startswith("tg-") and body:
            call("sendMessage", {"chat_id": chat_id, "text": body[:4000]})
            logger.info("ℹ️ sent the reply to %s", target)
        state["reply_sent"] = digest
    return lines


def poll(home: Path) -> int:
    """The monitor loop: never exits on a transient failure, prints only message lines."""
    logging.basicConfig(filename=home / "poll.log", level=logging.INFO, format="%(asctime)s %(message)s")
    secret = _load_json(home / "telegram.json", {})
    telegram = None
    if secret.get("token") and secret.get("chat_id"):
        telegram = (telegram_transport(secret["token"]), int(secret["chat_id"]))
    else:
        logger.info("ℹ️ no telegram.json: chief tasks only")
    state_path = home / "poll_state.json"
    state = _load_json(state_path, {})
    while True:
        try:
            for line in poll_once(home, state, telegram):
                print(line, flush=True)  # logging-not-print-exempt: the monitor reads stdout lines
            state_path.write_text(json.dumps(state), encoding="utf-8")
        except (OSError, ValueError) as exc:
            logger.warning("⚠️ poll pass failed: %s", exc)
        time.sleep(POLL_INTERVAL_S)


def ask(home: Path, text: str) -> str:
    inbox = home / "inbox.jsonl"
    home.mkdir(parents=True, exist_ok=True)
    count = len(inbox.read_text(encoding="utf-8").splitlines()) if inbox.exists() else 0
    task_id = f"task-{count + 1}"
    with inbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": task_id, "text": text}) + "\n")
    return task_id


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start", help="lay out the session folder and run the headless session")
    start.add_argument("--dry-run", action="store_true", help="print the grok argv as JSON, run nothing")
    sub.add_parser("ask", help="hand the assistant a task").add_argument("text")
    sub.add_parser("reply", help="print the latest reply file")
    sub.add_parser("poll", help="the session's monitor command")
    sub.add_parser("guard", help="the session's PreToolUse hook")
    sub.add_parser("setup", help="print the owner's Telegram setup steps")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    home = home_dir()
    python = Path(sys.executable).as_posix()
    if args.cmd == "start":
        lay_out(home, python)
        argv_ = grok_argv(home, python)
        if args.dry_run:
            print(json.dumps(argv_, indent=1))  # logging-not-print-exempt: CLI output
            return 0
        env = dict(os.environ, GROK_FOLDER_TRUST="0", GROK_ASSISTANT_HOME=str(home))
        with (home / "session.log").open("a", encoding="utf-8") as log:
            return subprocess.run(argv_, cwd=home / "session", env=env, stdout=log,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  creationflags=NO_WINDOW).returncode
    if args.cmd == "ask":
        print(f"TASK_ID={ask(home, args.text)}")  # logging-not-print-exempt: KEY=value protocol
    elif args.cmd == "reply":
        path = home / "session" / "reply.md"
        print(path.read_text(encoding="utf-8") if path.exists() else "")  # logging-not-print-exempt: CLI output
    elif args.cmd == "poll":
        return poll(home)
    elif args.cmd == "guard":
        return guard(home, python)
    else:
        print(SETUP.format(path=_posix(home / "telegram.json")))  # logging-not-print-exempt: CLI output
    return 0


if __name__ == "__main__":
    sys.exit(main())
