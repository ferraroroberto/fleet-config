"""Unit tests for skills/_lib/grokbot/relay.py: send, read, the refusals and the call log.

Everything runs against a loopback fake launcher that records every POST, so no real session is
ever messaged. The hooks state directory points at a temp folder, and the code tree is snapshotted
before and after to prove the relay writes nothing beside the code.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/grokbot/test_relay.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from grokbot import refusals, relay  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

tmp = Path(tempfile.mkdtemp(prefix="grokbot-relay-test-"))
state = tmp / "state"
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(state)

CHIEF, CHANNEL, WORKER, EXTERNAL, DEAD = ("c1" * 16, "d2" * 16, "e3" * 16, "f4" * 16, "a5" * 16)
CLONE_A, CLONE_B = "ab" + "0" * 30, "ab" + "1" * 30
BOARD = {"columns": {
    "claude_turn": [
        {"session_id": CHIEF, "alive": True, "kind": "pty", "label": "chief", "project": "fleet-config"},
        {"session_id": WORKER, "alive": True, "kind": "pty", "label": "", "project": "app-launcher"},
        {"session_id": CLONE_A, "alive": True, "kind": "pty", "label": "", "project": "life-os"},
        {"session_id": EXTERNAL, "alive": True, "kind": "external", "label": "", "project": "ghost"},
    ],
    "your_turn": [
        {"session_id": CHANNEL, "alive": True, "kind": "pty",
         "label": "telegram:owner", "project": "life-os"},
        {"session_id": CLONE_B, "alive": True, "kind": "pty", "label": "", "project": "life-os"},
        {"session_id": DEAD, "alive": False, "kind": "pty", "label": "", "project": "dead"},
    ],
}}
EXCHANGE = {"available": True, "source": "log", "assistant": {
    "timestamp": "2026-11-04T05:00:00Z",
    "text": "done; key is sk-" + "A" * 30 + " and the rest is fine"}}


class FakeLauncher:
    def __init__(self):
        self.posts = []
        self.board = BOARD
        self.exchange = EXCHANGE
        self.post_status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path == "/api/board":
                    self._reply(200, outer.board) if outer.board is not None else self.send_error(500)
                elif self.path.endswith("/exchange"):
                    self._reply(200, outer.exchange)
                else:
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.posts.append((self.path, body))
                self._reply(outer.post_status, {"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(fake, *argv):
    """(exit code, stdout) of one relay.main call against the fake launcher."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = relay.main(["--base-url", fake.url, *argv])
    return code, out.getvalue()


def message(text, name="msg.txt"):
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def tree_snapshot():
    roots = [REPO / "skills", REPO / "hooks", REPO / "docs", REPO / "tests"]
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size)
            for root in roots for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def log_lines():
    path = state / relay.LOG_NAME
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


fake = FakeLauncher()
before_tree = tree_snapshot()
try:
    # --- send: delivery, the tag, every kind of target -------------------------------------
    code, out = run(fake, "send", WORKER[:8], "--file", message("please rebase the branch onto main"))
    path, body = fake.posts[-1]
    check(code == 0 and out.startswith("SENT target=app-launcher chars=34"), "send delivers to a live session and reports SENT")
    check(path == f"/api/claude-code/sessions/{WORKER}/input" and body["submit"] is True,
          "send posts once to the resolved full session id, submitted")
    check(body["data"].startswith(relay.SENDER_TAG + "\n") and body["data"].endswith("rebase the branch onto main"),
          "the delivered text carries the sender tag, then the message unchanged")
    check(len(fake.posts) == 1, "exactly one POST per send")

    for sid, label in ((CHIEF, "chief"), (CHANNEL, "telegram:owner")):
        before = len(fake.posts)
        code, out = run(fake, "send", sid[:8], "--file", message("status please"))
        check(code == 0 and f"target={label}" in out and len(fake.posts) == before + 1,
              f"send reaches the {label} session")

    # --- send: refusals send nothing and say why -------------------------------------------
    def refused(text, reason, label, session=None):
        before = len(fake.posts)
        code, out = run(fake, "send", session or WORKER[:8], "--file", message(text))
        check(code == 1 and out.startswith(f"REFUSED reason={reason}") and len(fake.posts) == before,
              f"{label}: refused as {reason}, nothing sent")
        return out

    secret = "sk-" + "B" * 30
    out = refused(f"use this key {secret}", "secret", "an API key")
    check(secret not in out, "the refusal never echoes the matched text")
    refused("TELEGRAM " + "123456789:" + "x" * 35, "secret", "a Telegram bot token")
    refused("-----BEGIN OPENSSH PRIVATE KEY-----", "secret", "a private key block")
    refused('password: "hunter2hunter2"', "secret", "a credential assignment")
    refused("   \n", "empty", "an empty file")
    for steer, kind in (("git push --force origin main", "force-push"),
                        ("then git reset --hard HEAD~3", "discard work"),
                        ("discard the uncommitted changes", "discard work"),
                        ("delete the branch feat/x", "delete or adopt a branch"),
                        ("git branch -D old", "delete or adopt a branch"),
                        ("tear down the worktree", "tear down a worktree"),
                        ("git worktree remove ../x", "tear down a worktree")):
        out = refused(steer, "destructive", steer)
        check(kind in out and "owner" in out, f"the destructive refusal names {kind} and the owner terminal")
    out = refused("hi", "unresolvable-session", "an unknown id", session="zzzzzzzz")
    refused("hi", "unresolvable-session", "an external card", session=EXTERNAL[:8])
    refused("hi", "unresolvable-session", "a dead session", session=DEAD[:8])
    out = refused("hi", "ambiguous-session", "a prefix naming two sessions", session="ab")
    check(CLONE_A not in out and CLONE_B not in out, "an ambiguous refusal does not print session ids")
    code, out = run(fake, "send", WORKER[:8], "--file", str(tmp / "missing.txt"))
    check(code == 1 and out.startswith("REFUSED reason=unreadable-file"), "a missing file is refused")
    fake.board = None
    refused("hi", "board-unreadable", "an unreadable board")
    fake.board = BOARD
    fake.post_status = 409
    before = len(fake.posts)
    code, out = run(fake, "send", WORKER[:8], "--file", message("hello"))
    check(code == 1 and out.startswith("FAILED") and "HTTP 409" in out and len(fake.posts) == before + 1,
          "a launcher error is reported as FAILED, still a single POST")
    fake.post_status = 200

    # --- read ------------------------------------------------------------------------------
    code, out = run(fake, "read", CHIEF[:8])
    check(code == 0 and "TARGET=chief" in out and "the rest is fine" in out, "read prints the session's last reply")
    check("sk-" + "A" * 30 not in out and "[redacted]" in out and "REDACTED=1" in out,
          "read redacts credential-shaped text and counts it")
    code, out = run(fake, "read", CHIEF[:8], "--tail", "9")
    check(code == 0 and out.rstrip().endswith("is fine"), "--tail keeps only the last characters")
    fake.exchange = {"available": False, "reason": "no_exchange"}
    code, out = run(fake, "read", CHIEF[:8])
    check(code == 1 and out.startswith("UNAVAILABLE") and "no_exchange" in out, "an unavailable reply is its own state")
    fake.exchange = EXCHANGE
    code, out = run(fake, "read", "zzzzzzzz")
    check(code == 1 and "unresolvable-session" in out, "read refuses a session that is not live")

    # --- the log ---------------------------------------------------------------------------
    lines = log_lines()
    check(len(lines) > 20 and all(" verb=" in line and " outcome=" in line and " chars=" in line for line in lines),
          "every call appends one line with verb, outcome and chars")
    joined = "\n".join(lines)
    check("rebase the branch" not in joined and secret not in joined and "sk-" not in joined,
          "the log holds no message body")
    check(not any(sid in joined for sid in (WORKER, CHIEF, CHANNEL, CLONE_A)) and WORKER[:8] not in joined,
          "the log holds no session id")
    check("verb=send target=chief outcome=sent" in joined and "outcome=refused-secret" in joined
          and "outcome=refused-destructive" in joined, "sends and refusals are both logged by outcome")

    # --- the log lives outside the code tree, and nothing else is written ------------------
    check(relay.log_path().parent == state and not relay._inside(relay.log_path(), REPO),
          "the log path is under the state directory, outside the repo")
    check(tree_snapshot() == before_tree, "no file in the code tree changed or appeared during the run")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(REPO / "skills" / "_lib" / "grokbot")
    before = len(fake.posts)
    code, out = run(fake, "send", WORKER[:8], "--file", message("hello"))
    check(code == 1 and out.startswith("REFUSED reason=log-unwritable") and len(fake.posts) == before,
          "a state directory inside the code tree refuses the send")
    blocker = tmp / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(blocker / "sub")
    code, out = run(fake, "send", WORKER[:8], "--file", message("hello"))
    check(code == 1 and out.startswith("REFUSED reason=log-unwritable") and len(fake.posts) == before,
          "an unwritable log refuses the send: no unaudited steer")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(state)

    # --- a direct run works (the script puts skills/_lib on its own path) ------------------
    proc = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "grokbot" / "relay.py"), "--base-url", fake.url,
         "read", CHIEF[:8]],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"}, check=False,
    )
    check(proc.returncode == 0 and "TARGET=chief" in proc.stdout, "relay.py runs directly as a script")

    # --- the pure checks -------------------------------------------------------------------
    check(refusals.destructive_family("please run the tests and report back") is None
          and refusals.secret_family("rebase onto main, then run the gate") is None,
          "ordinary steers pass both checks")
finally:
    fake.close()
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_grokbot_relay")
