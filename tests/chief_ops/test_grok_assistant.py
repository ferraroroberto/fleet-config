"""Unit tests for skills/_lib/chief/grok_assistant.py: the deny rules and the poller.

The permission flags are checked with a small matcher that follows Grok's
documented rule semantics (`~/.grok/docs/user-guide/22-permissions-and-safety.md`,
"Rule Matching Reference"): a Bash rule matches a segment as a prefix or as a
whole-string glob, a path rule is a glob where `*` stops at `/`, and a deny
beats every allow. The poller runs against a fake Telegram transport and a
temp home. No `grok`, no network, no real token.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_grok_assistant.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from chief import grok_assistant as ga  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

PY = "C:/venv/Scripts/python.exe"
OPS = f"{PY} {ga.CHIEF_OPS.resolve().as_posix()}"


def _rules(args):
    pairs = list(zip(args[::2], args[1::2], strict=True))
    return ([r for flag, r in pairs if flag == "--allow"], [r for flag, r in pairs if flag == "--deny"])


def _split(rule):
    m = re.fullmatch(r"(\w+)(?:\((.*)\))?", rule)
    return m.group(1), m.group(2)


def _bash_hit(pattern, command):
    return command.startswith(pattern) or fnmatch.fnmatchcase(command, pattern)


def _path_hit(pattern, path):
    regex = re.escape(pattern).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(regex, path) is not None


def decide(args, tool, target):
    """'deny', 'allow' or 'none' (Grok's dontAsk then refuses) for one tool call."""
    allow, deny = _rules(args)

    def hits(rule):
        name, pattern = _split(rule)
        if name != tool:
            return False
        if pattern is None:
            return True
        return _bash_hit(pattern, target) if tool == "Bash" else _path_hit(pattern, target)

    if any(hits(r) for r in deny):
        return "deny"
    return "allow" if any(hits(r) for r in allow) else "none"


tmp = Path(tempfile.mkdtemp(prefix="grok-assistant-test-"))
try:
    home = tmp / "home"
    args = ga.permission_args(home, PY)
    reply = (home / "session" / "reply.md").resolve().as_posix()

    # ---- the session may read fleet state ----
    for verb in ga.READ_VERBS:
        check(decide(args, "Bash", f"{OPS} {verb}") == "allow", f"allows chief_ops {verb}")
    check(decide(args, "Bash", f"{OPS} exchange abc123") == "allow", "allows a read verb with arguments")
    for sub in ga.GH_READS:
        check(decide(args, "Bash", f"gh {sub} 12 --repo o/r") == "allow", f"allows gh {sub}")
    check(decide(args, "Read", reply) == "allow" and decide(args, "Grep", "anything") == "allow",
          "allows the read tools")

    # ---- and write only its reply file ----
    check(decide(args, "Edit", reply) == "allow", "allows writing the reply file")
    check(decide(args, "Edit", (home / "session" / "BRIEF.md").resolve().as_posix()) == "deny",
          "denies rewriting its own brief")
    check(decide(args, "Edit", (home / "session" / "other.md").resolve().as_posix()) == "none",
          "any other file in its folder is unlisted, so dontAsk refuses it")
    fleet_file = (REPO.parent / "fleet-config" / "hooks" / "_lib.py").resolve().as_posix()
    check(decide(args, "Edit", fleet_file) == "deny", "denies editing any fleet repo")
    check(decide(args, "Edit", "C:/x/.env") == "deny", "denies editing an .env")

    # ---- it never dispatches, says, stops, merges or sends ----
    for verb in ga.WRITE_VERBS:
        check(decide(args, "Bash", f"{OPS} {verb} fleet-config#1") == "deny", f"denies chief_ops {verb}")
    for cmd in ("gh pr merge 5 --squash", "gh issue create -t x", "gh issue comment 3 -b y",
                "gh api repos/o/r/pulls/1/merge -X PUT", "gh pr close 2", "gh run rerun 9"):
        check(decide(args, "Bash", cmd) == "deny", f"denies `{cmd}`")
    # Allow rules Grok merges from ~/.claude/settings.json must still lose to a deny.
    for cmd in ("git push origin main", "git commit -m x", "python -c 'import os'",
                "py C:/Users/u/.claude/hooks/notify_complete.py --summary hi",
                ".venv/Scripts/python.exe -c 'x'", "powershell -NoProfile -Command Remove-Item x",
                "curl https://api.telegram.org",
                f"{PY} {ga.Path(ga.__file__).resolve().as_posix()} ask hi"):
        check(decide(args, "Bash", cmd) == "deny", f"denies `{cmd}`")
    check(decide(args, "Read", (home / "telegram.json").resolve().as_posix()) == "deny",
          "denies reading the bot token file")
    check(decide(args, "WebFetch", "https://example.com") == "deny", "denies web fetches")

    # ---- every allow is still allowed: no deny shadows a read the brief lists ----
    for line in ga.read_commands(PY):
        check(decide(args, "Bash", line) == "allow", f"the brief's command is runnable: {line[-30:]}")
    poll_cmd = f"{PY} {ga.Path(ga.__file__).resolve().as_posix()} poll"
    check(decide(args, "Bash", poll_cmd) == "allow", "the monitor's poll command is allowed")

    argv = ga.grok_argv(home, PY)
    check("--no-subagents" in argv and "--disable-web-search" in argv, "no subagents, no web search")

    # ---- the PreToolUse guard: the closed list dontAsk is not ----
    def g(tool, **tool_input):
        return ga.guard_decision(home, PY, tool, tool_input)

    poll_line = f"{PY} {ga.Path(ga.__file__).resolve().as_posix()} poll"
    check(g("Read", target_file="x") is None and g("Glob", path=".") is None, "guard: read tools pass")
    check(g("Bash", command=f"{OPS} board") is None, "guard: an exact read command passes")
    check(g("Bash", command="gh issue view 5 --json title") is None, "guard: a gh read with arguments passes")
    check(g("monitor", command=poll_line, persistent=True) is None, "guard: the poller monitor passes")
    check(g("write", file_path=reply, content="re: x") is None, "guard: writing the reply file passes")
    check(g("write", file_path=reply.replace("/", "\\"), content="x") is None,
          "guard: the reply file passes in Windows spelling too")
    for tool, tool_input, why in (
        ("Bash", {"command": "Copy-Item BRIEF.md copy.md"}, "an unlisted command"),
        ("Bash", {"command": f"{OPS} say abc hi"}, "a chief_ops write verb"),
        ("Bash", {"command": f"{OPS} boardx"}, "a verb that only shares a read verb's prefix"),
        ("Bash", {"command": "gh issue list ; Get-Date"}, "a chained command"),
        ("Bash", {"command": "gh pr view 1 | Out-File x"}, "a pipe"),
        ("Bash", {"command": "gh pr view $(Get-Date)"}, "a substitution"),
        ("monitor", {"command": "Get-Content x -Wait"}, "a monitor on anything but the poller"),
        ("monitor", {"command": f"{OPS} board"}, "a read command used as a monitor"),
        ("write", {"file_path": str(home / "session" / "notes.md"), "content": "x"}, "another file"),
        ("Write", {"file_path": str(home / "session" / ".claude" / "settings.json")}, "its own hook settings"),
        ("search_replace", {"file_path": fleet_file}, "a fleet file"),
        ("write", {"content": "x"}, "a write with no path"),
        ("Task", {"prompt": "x"}, "a subagent"),
        ("WebFetch", {"url": "https://example.com"}, "a web fetch"),
        ("", {}, "an unnamed tool"),
    ):
        check(g(tool, **tool_input) is not None, f"guard: refuses {why}")

    # ---- the poller: owner-only, tasks, replies ----
    lines, last = ga.owner_lines([
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "status\nplease"}},
        {"update_id": 11, "message": {"chat": {"id": 99}, "text": "hi, I am not the owner"}},
        {"update_id": 12, "message": {"chat": {"id": 42}}},
    ], 42)
    check(lines == ["MSG tg-10 from owner: status / please"], "only the owner's text reaches the session")
    check(last == 12, "the offset moves past dropped updates too")

    check(ga.parse_reply("re: tg-10\nall green\n") == ("tg-10", "all green"), "parses a reply")
    check(ga.parse_reply("draft without a header") == (None, ""), "a header-less file is not a reply")

    ga.lay_out(home, PY)
    check("re: <id>" in (home / "session" / "BRIEF.md").read_text(encoding="utf-8"), "the brief is laid out")
    first = ga.ask(home, "digest the board")
    check(first == "task-1" and ga.ask(home, "second") == "task-2", "ask numbers tasks")

    sent = []

    def fake(method, params):
        if method == "getUpdates":
            return {"result": [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "hello"}}]
                    if params["offset"] == 0 else []}
        sent.append(params)
        return {"ok": True}

    state = {}
    out = ga.poll_once(home, state, (fake, 42))
    check(out == ["TASK task-1 from chief: digest the board", "TASK task-2 from chief: second",
                  "MSG tg-7 from owner: hello"], "one pass prints tasks then owner messages")
    check(ga.poll_once(home, state, (fake, 42)) == [], "a second pass prints nothing new")

    (home / "session" / "reply.md").write_text("re: tg-7\nall green", encoding="utf-8")
    ga.poll_once(home, state, (fake, 42))
    ga.poll_once(home, state, (fake, 42))
    check(sent == [{"chat_id": 42, "text": "all green"}], "a Telegram reply is sent once, to the owner")

    (home / "session" / "reply.md").write_text("re: task-1\nboard is fine", encoding="utf-8")
    ga.poll_once(home, state, (fake, 42))
    check(len(sent) == 1, "a chief task's reply stays in the file, not Telegram")

    check(ga.poll_once(home, {}, None)[:1] == ["TASK task-1 from chief: digest the board"],
          "with no telegram.json the poller still feeds chief tasks")
    check("token" not in json.dumps(state), "the poll state never holds the token")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_grok_assistant")
