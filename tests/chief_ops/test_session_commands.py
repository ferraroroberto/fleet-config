"""chief_ops session commands: say/stop prefix resolution, say --verify transport recovery, the wake doorbell, and context + self-compact with the launcher stubbed.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_session_commands.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

from _suite import _exchange_columns, _h, check, co  # noqa: E402
from chief import say_recovery  # noqa: E402

# ---- say/stop resolve the prefix too (fleet-config#681) ----------------------
# `board`/`sessions` only ever print the 8-char prefix, so that is the only
# form an operator (or chief, per SKILL.md) has. `exchange` resolved it and
# `say`/`stop` did not: `say <prefix>` POSTed to a session id that does not
# exist, and `--verify` then read the resulting empty card as PENDING —
# "delivery likely, unconfirmed" for a steer that was never accepted at all.

def _run_sid_cmd(fn, sid, *, kill=False):
    """Drive cmd_say (plain) or cmd_stop against a stubbed transport, returning
    (exit_code, stdout, the sid the endpoint was actually called with)."""
    seen = {"path": None}

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            return {"columns": _exchange_columns}
        seen["path"] = path
        return {}

    _orig = (co.launcher._request, co.launcher.post_session_input)
    co.launcher._request = _fake_request

    def _fake_post(base_url, s, text):
        seen["path"] = f"/api/claude-code/sessions/{s}/input"
        return {"ok": True, "reason": "ok", "error": None}

    co.launcher.post_session_input = _fake_post
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            args = argparse.Namespace(sid=sid, file=str(_prefix_brief), verify=False,
                                      timeout=0.0, poll_interval=0.0, kill=kill,
                                      base_url=co.DEFAULT_BASE_URL)
            rc = fn(args)
        return rc, buf.getvalue(), seen["path"]
    finally:
        co.launcher._request, co.launcher.post_session_input = _orig


_prefix_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_prefix_"))
try:
    _prefix_brief = _prefix_tmp / "brief.md"
    _prefix_brief.write_text("steer text", encoding="utf-8")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "ad3e8bbb")
    check(_path == "/api/claude-code/sessions/ad3e8bbbcccccccccccccccccccccccc/input",
          f"cmd_say: an 8-char board prefix is resolved before the POST — called {_path!r}")
    check("SENT sid=ad3e8bbbcccccccccccccccccccccccc" in _out and _rc == 0,
          "cmd_say: the reported sid is the resolved one, so the operator can correlate it")

    _rc, _out, _path = _run_sid_cmd(co.cmd_stop, "ad3e8bbb")
    check(_path == "/api/claude-code/sessions/ad3e8bbbcccccccccccccccccccccccc/stop",
          f"cmd_stop: an 8-char board prefix is resolved before the stop — called {_path!r}")
    check("STOPPED sid=ad3e8bbbcccccccccccccccccccccccc" in _out and _rc == 0,
          "cmd_stop: reports the resolved sid")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "d235f29")
    check(_rc == 1 and "UNRESOLVABLE" in _out and _path is None,
          "cmd_say: an ambiguous prefix refuses and never sends — same rule as cmd_exchange")
    _rc, _out, _path = _run_sid_cmd(co.cmd_stop, "d235f29")
    check(_rc == 1 and "UNRESOLVABLE" in _out and _path is None,
          "cmd_stop: an ambiguous prefix refuses and never stops the wrong session")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "deadbeefdeadbeefdeadbeefdeadbeef")
    check(_path == "/api/claude-code/sessions/deadbeefdeadbeefdeadbeefdeadbeef/input",
          "cmd_say: an id matching no live session is passed through unchanged (real endpoint decides)")
finally:
    shutil.rmtree(_prefix_tmp, ignore_errors=True)


# ---- wake: doorbell or waiter, from the launcher's switch (fleet-config#1457) ----
# Only a confirmed `true` drops the waiter; anything unread arms it, never leaving
# the chief with no wake path. `_request` is stubbed: no real launcher is read.

import urllib.error as _wake_urlerr  # noqa: E402

_FALLBACK_LINE = "WAKE=doorbell doorbell=on fallback=waiter seconds=1800"
check(co.resolve_wake_mode({"chief_doorbell": True}) == (_FALLBACK_LINE, True),
      "resolve_wake_mode: the switch on means the doorbell wakes the chief, with a long fallback waiter (#1938)")
check(co.mailbox.FALLBACK_WAITER_SECONDS == 1800 and co.mailbox.FALLBACK_OLDER_THAN_SECONDS == 600,
      "resolve_wake_mode: the fallback is a 30-minute poll for events older than 10 minutes")
check(co.resolve_wake_mode({"chief_doorbell": False}) == ("WAKE=waiter doorbell=off", True),
      "resolve_wake_mode: the switch off means arm a waiter")
for _bad in ({}, {"chief_doorbell": "true"}, {"chief_doorbell": 1}, {"chief_doorbell": None}):
    _line, _known = co.resolve_wake_mode(_bad)
    check(_line.startswith("WAKE=waiter doorbell=unknown reason=") and _known is False,
          f"resolve_wake_mode: a missing or non-boolean setting is unknown, and arms the waiter ({_bad!r})")
_line, _known = co.resolve_wake_mode(None, "launcher unreachable")
check(_line == "WAKE=waiter doorbell=unknown reason=launcher unreachable" and _known is False,
      "resolve_wake_mode: an unread launcher is unknown, and arms the waiter")

# ---- the launcher's own fallback ring replaces the waiter (fleet-config#2237) ----
check(co.resolve_wake_mode({"chief_doorbell": True, "chief_fallback_minutes": 30})
      == ("WAKE=doorbell doorbell=on fallback=launcher minutes=30", True),
      "resolve_wake_mode: doorbell on with the launcher fallback names no waiter")
check(co.resolve_wake_mode({"chief_doorbell": False, "chief_fallback_minutes": 5})
      == ("WAKE=launcher doorbell=off fallback=launcher minutes=5", True),
      "resolve_wake_mode: doorbell off with the launcher fallback still names no waiter")
_line, _known = co.resolve_wake_mode({"chief_fallback_minutes": 30})
check(_line.startswith("WAKE=launcher doorbell=unknown fallback=launcher minutes=30 reason=") and _known is False,
      "resolve_wake_mode: an unread doorbell stays unknown while the launcher fallback covers the wake")
for _off in (0, -1, True, "30", 1.5, None):
    check(co.resolve_wake_mode({"chief_doorbell": True, "chief_fallback_minutes": _off}) == (_FALLBACK_LINE, True)
          and co.resolve_wake_mode({"chief_doorbell": False, "chief_fallback_minutes": _off})
          == ("WAKE=waiter doorbell=off", True),
          f"resolve_wake_mode: a fallback of {_off!r} is off and keeps the waiter rule unchanged")


_SETTINGS_PATH = "/api/board/chief/settings"
_WAKE_SETTINGS = {"settings": {"chief_reoffer_minutes": 20}}
_wake_full = ""  # the whole `wake` output of the last `_run_wake`, `TIMINGS` line included


def _run_wake(reply):
    """`(exit code, the WAKE= line, config reads)`; the settings read behind the `TIMINGS` line is stubbed apart."""
    global _wake_full
    calls: list = []

    def fake_request(base, path, **_kw):
        if path == _SETTINGS_PATH:
            return _WAKE_SETTINGS
        calls.append(path)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    old_request = co.launcher._request
    co.launcher._request = fake_request
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(["wake"])
    finally:
        co.launcher._request = old_request
    _wake_full = out.getvalue().strip()
    return code, _wake_full.splitlines()[0], calls


_code, _out, _calls = _run_wake({"chief_doorbell": True, "port": 8445})
check(_code == 0 and _out == _FALLBACK_LINE and _calls == ["/api/config"],
      "wake CLI: reads the launcher's /api/config and reports the doorbell")
_code, _out, _calls = _run_wake({"chief_doorbell": False})
check(_code == 0 and _out == "WAKE=waiter doorbell=off", "wake CLI: switch off -> waiter, exit 0")
_code, _out, _calls = _run_wake(_wake_urlerr.URLError("refused"))
check(_code == 1 and _out == "WAKE=waiter doorbell=unknown reason=launcher unreachable",
      "wake CLI: an unreachable launcher is unknown (exit 1) and says arm the waiter")
_code, _out, _calls = _run_wake(_wake_urlerr.HTTPError("u", 404, "nf", None, None))
check(_code == 1 and _out == "WAKE=waiter doorbell=unknown reason=launcher config unreadable (HTTP 404)",
      "wake CLI: an HTTP error is unknown, named apart from unreachable")
_code, _out, _calls = _run_wake({"chief_doorbell": True, "chief_fallback_minutes": 30})
check(_code == 0 and _out == "WAKE=doorbell doorbell=on fallback=launcher minutes=30",
      "wake CLI: reports the launcher fallback from /api/config (#2237)")
check(_wake_full.splitlines()[1:] == ["TIMINGS rering=60s(default: setting missing) reoffer=20m(settings) "
                                      "hold=10m(default: setting missing) stall=45m(default: setting missing)"],
      "wake CLI: a TIMINGS line follows, naming the value in use and its source (#2238)")
_run_wake(_wake_urlerr.URLError("refused"))
check(_wake_full.splitlines()[1:] == ["TIMINGS rering=60s(default: launcher unreachable) "
                                      "reoffer=15m(default: launcher unreachable) hold=10m(default: launcher unreachable) "
                                      "stall=45m(default: launcher unreachable)"],
      "wake CLI: an unreachable launcher reports every timing as the default, with the reason (#2238)")


# ---- context + self-compact (fleet-config#1052): launcher always stubbed ----
# No test here reaches a real launcher or compacts a real session: `_request`,
# the caller id and the state dir are all replaced for the block's duration.

import urllib.error as _urlerr  # noqa: E402

line, pct = co.format_context_line("abcdef1234", {"available": True, "percent": 17})
check(line == "CONTEXT sid=abcdef12 percent=17" and pct == 17, "format_context_line: a known percentage")
line, pct = co.format_context_line("abcdef1234", {"available": True, "percent": None})
check(pct is None and "percent=unknown" in line, "format_context_line: a null percentage is unknown, never 0")
line, pct = co.format_context_line("abcdef1234", {"available": False, "percent": None, "reason": "no statusline"})
check(pct is None and "reason=no statusline" in line, "format_context_line: unavailable carries its reason")

check(co.resolve_compact_threshold(None, 40) == (40, "threshold 40 (--threshold)"), "threshold: --threshold wins")
check(co.resolve_compact_threshold({"auto_compact_threshold": 45}, None) == (45, "threshold 45 (Settings)"),
      "threshold: the Settings value when present")
check(co.resolve_compact_threshold({"auto_compact_threshold": 0}, None)[0] is None, "threshold: 0 in Settings is off")
thr, note = co.resolve_compact_threshold(None, None, "launcher unreachable")
check(thr == 30 and note == "threshold 30, default: launcher unreachable",
      "threshold: an unreachable launcher falls back to 30 and says so")
thr, note = co.resolve_compact_threshold({}, None)
check(thr == 30 and "default: setting missing" in note, "threshold: a missing field falls back, named distinctly")

refusals = {
    "unknown": co.self_compact_refusal(None, 30, 10, 10),
    "below": co.self_compact_refusal(17, 30, 10, 10),
    "stale handover": co.self_compact_refusal(35, 30, 3600, 10),
    "stale plan": co.self_compact_refusal(35, 30, 10, 3600),
    "missing handover": co.self_compact_refusal(35, 30, None, 10),
}
check(all(refusals.values()) and len(set(refusals.values())) == len(refusals),
      f"self_compact_refusal: unknown / below / stale handover / stale plan / missing each refuse, each with its own message")
check("unknown" in refusals["unknown"] and "below threshold (17% < 30%)" == refusals["below"]
      and refusals["stale handover"].startswith("handover log is stale")
      and refusals["stale plan"].startswith("plan is stale"),
      "self_compact_refusal: the messages name the condition")
check(co.self_compact_refusal(30, 30, 10, 10) is None, "self_compact_refusal: at the threshold with a fresh refresh -> proceed")

_compact_tmp = Path(tempfile.mkdtemp(prefix="self_compact_"))
_saved = (co.launcher._request, co.rails.caller_session_id, os.environ.get("CLAUDE_HOOKS_STATE_DIR"))
try:
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_compact_tmp)
    (_compact_tmp / "chief-handover.md").write_text("fresh", encoding="utf-8")
    (_compact_tmp / "chief-plan.json").write_text("{}", encoding="utf-8")
    CHIEF = "chief000000000000000000000000001"
    posts: list = []
    world = {"percent": 42, "settings": {"auto_compact_threshold": 30}, "caller": CHIEF}

    def fake_request(base, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board/chief/settings":  # the chief timings: no setting stored, so the defaults apply
            return {"settings": {}}
        if path == "/api/board":
            return {"columns": {"claude_turn": [{"alive": True, "label": "chief", "session_id": CHIEF}]}}
        if path.endswith("/context"):
            return {"available": True, "percent": world["percent"]}
        if path == "/api/board/chief-settings":
            if world["settings"] == "404":
                raise _urlerr.HTTPError(path, 404, "Not Found", {}, None)
            return world["settings"]
        if path.endswith("/input"):
            posts.append((path, body))
            return {"reason": "deferred"}
        raise AssertionError(f"unexpected launcher call {method} {path}")

    co.launcher._request = fake_request
    co.rails.caller_session_id = lambda env=None: world["caller"]

    def self_compact(*extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(["self-compact", *extra])
        return code, out.getvalue()

    code, out = self_compact()
    check(code == 0 and "COMPACT_QUEUED" in out and "verdict=deferred" in out and "threshold 30 (Settings)" in out,
          "self-compact: over threshold with a fresh refresh queues /compact and reports the deferred verdict")
    check(len(posts) == 1 and posts[0][0] == f"/api/claude-code/sessions/{CHIEF}/input"
          and posts[0][1]["data"].startswith("/compact ") and "in-flight lanes" in posts[0][1]["data"],
          "self-compact: posts exactly one /compact <focus> to its own session only")

    posts.clear()
    world["percent"] = 12
    code, out = self_compact()
    check(code == 1 and "REFUSED=below threshold" in out and not posts, "self-compact: below threshold refuses, posts nothing")
    world["percent"] = None
    code, out = self_compact()
    check(code == 1 and "REFUSED=context percentage unknown" in out and not posts, "self-compact: unknown percentage refuses")
    world["percent"] = 42
    os.utime(_compact_tmp / "chief-plan.json", (time.time() - 7200, time.time() - 7200))
    code, out = self_compact()
    check(code == 1 and "REFUSED=plan is stale" in out and not posts, "self-compact: a stale plan refuses")
    os.utime(_compact_tmp / "chief-plan.json", None)
    world["settings"] = {"auto_compact_threshold": 0}
    code, out = self_compact()
    check(code == 1 and "REFUSED=auto-compact is off" in out and not posts, "self-compact: 0 in Settings is off")
    world["settings"] = "404"
    code, out = self_compact()
    check(code == 0 and "threshold 30, default: launcher has no chief-settings endpoint (HTTP 404)" in out,
          "self-compact: a launcher without the endpoint falls back to 30, named as such")
    posts.clear()
    world["caller"] = "worker0000000000000000000000002"
    code, out = self_compact("--threshold", "10")
    check(code == 1 and "REFUSED=the caller is not the live chief" in out and not posts,
          "self-compact: never compacts a session other than the caller's own chief")
    world["caller"] = None
    code, out = self_compact()
    check(code == 1 and "REFUSED=not a launcher session" in out and not posts, "self-compact: needs a launcher session")

    world["caller"] = CHIEF
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = co.main(["context"])
    check(code == 0 and "percent=42" in out.getvalue(), "context CLI: defaults to the chief's session")
    world["percent"] = None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = co.main(["context"])
    check(code == 1 and "percent=unknown" in out.getvalue(), "context CLI: a null percentage prints unknown and exits 1")

    # ---- compact-due + the inbox line (fleet-config#1747) ----
    check((_compact_tmp / "chief-compact-queued").is_file(),
          "self-compact: a queued /compact leaves the marker the Stop hook reads")
    status = co.compact.compact_status_line
    check(status(61, 30) == "COMPACT_DUE percent=61 threshold=30", "compact_status_line: over -> COMPACT_DUE")
    check(status(30, 30).startswith("COMPACT_DUE "), "compact_status_line: at the threshold is due")
    check(status(12, 30) == "COMPACT_NOT_DUE percent=12 threshold=30", "compact_status_line: under -> not due")
    check(status(None, 30, "no statusline") == "COMPACT_DUE=unknown reason=no statusline",
          "compact_status_line: unknown is its own state, never not-due")
    check(status(61, None) == "COMPACT_OFF", "compact_status_line: threshold off -> COMPACT_OFF")

    def run(*argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(list(argv))
        return code, out.getvalue()

    world["settings"] = {"auto_compact_threshold": 30}
    world["caller"] = CHIEF
    world["percent"] = 61
    code, out = run("compact-due")
    check(code == 0 and out.strip() == "COMPACT_DUE percent=61 threshold=30", "compact-due: over the threshold")
    world["percent"] = 12
    code, out = run("compact-due")
    check(code == 0 and out.strip() == "COMPACT_NOT_DUE percent=12 threshold=30", "compact-due: under the threshold")
    world["percent"] = None
    code, out = run("compact-due")
    check(code == 1 and out.strip().startswith("COMPACT_DUE=unknown"), "compact-due: unknown percent exits 1, not 'not due'")
    world["percent"] = 61
    world["settings"] = {"auto_compact_threshold": 0}
    code, out = run("compact-due")
    check(code == 0 and out.strip() == "COMPACT_OFF", "compact-due: 0 in Settings is off")
    world["settings"] = {"auto_compact_threshold": 30}

    _inbox_dir = _compact_tmp / "inbox"
    _saved_inbox = co.mailbox.chief_inbox_dir
    co.mailbox.chief_inbox_dir = lambda: _inbox_dir
    try:
        code, out = run("inbox")
        check(code == 0 and "COMPACT_DUE percent=61 threshold=30" in out.splitlines(), "inbox: prints COMPACT_DUE when over")
        world["percent"] = 12
        code, out = run("inbox")
        check(code == 0 and "COMPACT" not in out, "inbox: silent under the threshold")
        world["percent"] = None
        code, out = run("inbox")
        check(code == 0 and "COMPACT_DUE=unknown" in out, "inbox: prints COMPACT_DUE=unknown, never folds it into fine")
        world["percent"] = 61
        world["caller"] = "worker0000000000000000000000002"
        code, out = run("inbox")
        check(code == 0 and "COMPACT" not in out, "inbox: a caller that is not the live chief never sees the line")
        world["caller"] = None
        code, out = run("inbox")
        check(code == 0 and "COMPACT" not in out, "inbox: outside a launcher session makes no launcher call")
        code, out = run("inbox", "--count")
        check(code == 0 and "COMPACT" not in out, "inbox --count: stays a pure count")
    finally:
        co.mailbox.chief_inbox_dir = _saved_inbox
finally:
    co.launcher._request, co.rails.caller_session_id = _saved[0], _saved[1]
    if _saved[2] is None:
        os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
    else:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = _saved[2]
    shutil.rmtree(_compact_tmp, ignore_errors=True)

# ---- escalate: a hung notify_send is its own failure, never a hang -----------
# `escalate` shells out to hooks/notify_send.py; with no bound a dead network held
# the chief's turn forever. An expiry prints ESCALATED=no and names the timeout.

def _run_escalate_with_hung_send():
    import subprocess

    from chief import commands as chief_commands

    seen = {}

    def _hang(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    saved = (chief_commands.subprocess.run, chief_commands.read_brief)
    chief_commands.subprocess.run = _hang
    chief_commands.read_brief = lambda _file: "brief"
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = chief_commands.cmd_escalate(argparse.Namespace(file=None))
    finally:
        chief_commands.subprocess.run, chief_commands.read_brief = saved
    return code, out.getvalue(), err.getvalue(), seen


_esc_code, _esc_out, _esc_err, _esc_seen = _run_escalate_with_hung_send()
check(_esc_seen.get("timeout"), "escalate bounds the notify_send spawn")
check(_esc_code == 1 and "ESCALATED=no" in _esc_out,
      f"escalate: a hung send exits 1 with ESCALATED=no, got {_esc_code}: {_esc_out!r}")
check("did not finish within" in _esc_err, f"escalate: the expiry names the timeout, got {_esc_err!r}")


# ---- say --verify recovers from a transport failure without double-sending (fleet-config#2192) ----
# A resend is gated by the exact-text transcript check; every transport edge is a fake and nothing waits.

_SR_SID = "ad3e8bbbcccccccccccccccccccccccc"
_SR_STEER = "the gate failure on line 12 is pre-existing, see #622"
_SR_NOT_SENT, _SR_MAYBE_SENT = co.launcher.NOT_SENT, co.launcher.MAYBE_SENT


def _sr_fail(kind, error="connection refused"):
    return {"ok": False, "reason": None, "http_status": None, "transport": kind, "error": error}


def _sr_http(code, reason=None, transport=None):
    return {"ok": False, "reason": reason, "http_status": code, "transport": transport,
            "error": f"HTTP {code}: x"}


_SR_OK = {"ok": True, "reason": "ok", "http_status": None, "transport": None, "error": None}


class _SayFake:
    """A launcher whose POSTs follow `posts` (one entry each, the last repeating). An entry is a
    POST result, or `(result, landed)` for a POST whose text reaches the transcript even though the
    caller saw a failure (the timeout/reset case)."""

    def __init__(self, posts, *, readable=True, copies=0, board_failures=0):
        self.posts = posts
        self.readable = readable
        self.copies = copies
        self.board_failures = board_failures
        self.post_count = 0
        self.transcript_reads = 0
        self.board_reads = 0

    def post(self, base_url, sid, text):
        entry = self.posts[min(self.post_count, len(self.posts) - 1)]
        self.post_count += 1
        result, landed = entry if isinstance(entry, tuple) else (entry, entry.get("ok", False))
        if landed:
            self.copies += 1
        return result

    def request(self, base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            self.board_reads += 1
            if self.board_reads <= self.board_failures:
                raise _urlerr.URLError(ConnectionRefusedError("refused"))
            return {"columns": _exchange_columns}
        if path.endswith("/transcript"):
            self.transcript_reads += 1
            if not self.readable:
                return {"available": False, "reason": "no_transcript"}
            return {"available": True,
                    "entries": [{"kind": "user", "text": _SR_STEER}] * self.copies
                    + [{"kind": "assistant", "text": _SR_STEER}]}
        return {}


_sr_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_say_recovery_"))
_sr_brief = _sr_tmp / "brief.md"
_sr_brief.write_text(_SR_STEER, encoding="utf-8")


def _sr_run(fake, *, sid=_SR_SID, verify=True):
    """Drive `cmd_say` against `fake`: `(exit code, stdout, the fake)`."""
    saved = (co.launcher._request, co.launcher.post_session_input, co.launcher.RECOVERY_BACKOFF,
             co.launcher.fetch_exchange_marker, co.launcher.fetch_session_card)
    co.launcher._request = fake.request
    co.launcher.post_session_input = fake.post
    co.launcher.RECOVERY_BACKOFF = (0.0, 0.0, 0.0)
    # Exchange movement is no proof of delivery: it is deliberately "advanced" so a verdict that
    # leaned on it would show up as a wrong DELIVERED.
    co.launcher.fetch_exchange_marker = lambda *a, **k: {"available": True,
                                                         "timestamp": "2999-01-01T00:00:00Z"}
    co.launcher.fetch_session_card = lambda *a, **k: {}
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = co.cmd_say(argparse.Namespace(
                sid=sid, file=str(_sr_brief), verify=verify, timeout=0.0, poll_interval=0.0,
                base_url=co.DEFAULT_BASE_URL))
        return rc, buf.getvalue(), fake
    finally:
        (co.launcher._request, co.launcher.post_session_input, co.launcher.RECOVERY_BACKOFF,
         co.launcher.fetch_exchange_marker, co.launcher.fetch_session_card) = saved


try:
    # Webapp down for attempt 1, back for attempt 2: text absent after 1, present after 2.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_NOT_SENT), _SR_OK]))
    check(rc == 0 and out.startswith("DELIVERED sid=" + _SR_SID), f"down then back -> DELIVERED: {out!r}")
    check(f.post_count == 2 and f.copies == 1, f"down then back -> exactly 2 POSTs, one copy ({f.post_count}, {f.copies})")

    # Down for attempt 1, back with the exact text already in the transcript: no second POST.
    rc, out, f = _sr_run(_SayFake([(_sr_fail(_SR_MAYBE_SENT, "timed out"), True)]))
    check(rc == 0 and out.startswith("DELIVERED") and f.post_count == 1,
          f"text already in the transcript after a failed POST -> 1 POST, DELIVERED: {out!r} {f.post_count}")

    # A timeout after a POST whose text never landed: at most 3 attempts, never past the third.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_MAYBE_SENT, "timed out")]))
    check(f.post_count == 3 and f.copies == 0, f"all timeouts, text absent -> 3 POSTs and no more ({f.post_count})")
    check(rc == 1 and out.startswith("PENDING") and "attempts=3" in out,
          f"all timeouts, text absent, transcript readable -> PENDING with the attempt count: {out!r}")

    # A timeout whose POST landed on the second try: one copy only, DELIVERED.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_MAYBE_SENT, "timed out"), (_sr_fail(_SR_MAYBE_SENT, "timed out"), True)]))
    check(f.post_count == 2 and f.copies == 1 and rc == 0,
          f"a timed-out POST that landed is never followed by a third ({f.post_count}, {f.copies}, rc={rc})")

    # 503 is the session-host saying nothing was sent: retried.
    rc, out, f = _sr_run(_SayFake([_sr_http(503, transport=_SR_NOT_SENT), _SR_OK]))
    check(f.post_count == 2 and rc == 0, f"a session-host 503 is retried once, then DELIVERED ({f.post_count})")

    # Authoritative negatives: one POST, verdict per the existing contract.
    for code, reason in ((502, "not_ingested"), (409, "dropped"), (400, "dropped"), (403, "dropped")):
        rc, out, f = _sr_run(_SayFake([_sr_http(code, reason)]))
        check(f.post_count == 1 and rc == 1 and out.startswith("STRANDED"),
              f"HTTP {code} -> never retried, STRANDED ({f.post_count}): {out!r}")
        check(f.transcript_reads == 1, f"HTTP {code} -> only the baseline transcript read, no resend check")
    rc, out, f = _sr_run(_SayFake([_sr_http(404)]))
    check(f.post_count == 1 and rc == 1 and out.startswith("UNKNOWN"),
          f"an unclassified HTTP answer is not retried and is UNKNOWN: {out!r}")

    # Transcript unreadable: UNKNOWN, never DELIVERED or STRANDED.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_MAYBE_SENT, "timed out")], readable=False))
    check(f.post_count == 1 and rc == 1 and out.startswith("UNKNOWN"),
          f"timeout with an unreadable transcript -> no resend, UNKNOWN ({f.post_count}): {out!r}")
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_NOT_SENT)], readable=False))
    check(f.post_count == 3 and rc == 1 and out.startswith("UNKNOWN"),
          f"refused connection, transcript unreadable throughout -> 3 POSTs, UNKNOWN ({f.post_count}): {out!r}")
    check("STRANDED" not in out and "DELIVERED" not in out, "an unreadable transcript is never STRANDED or DELIVERED")

    # An identical earlier steer in the transcript is not mistaken for this one.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_NOT_SENT), _SR_OK], copies=1))
    check(f.post_count == 2 and f.copies == 2 and rc == 0,
          f"an earlier identical steer is the baseline, not proof: resent once ({f.post_count}, {f.copies})")

    # An assistant turn quoting the steer is not a user turn.
    check(say_recovery.user_turn_count({"entries": [{"kind": "assistant", "text": _SR_STEER}]}, _SR_STEER) == 0,
          "only user turns count as the steer")

    # Launcher down during the sid resolve, back within the window: the steer goes out once.
    rc, out, f = _sr_run(_SayFake([_SR_OK], board_failures=2), sid="ad3e8bbb")
    check(f.post_count == 1 and rc == 0 and ("sid=" + _SR_SID) in out,
          f"launcher down for the sid lookup, then back -> one POST to the resolved id ({f.post_count}): {out!r}")
    rc, out, f = _sr_run(_SayFake([_SR_OK], board_failures=99), sid="ad3e8bbb")
    check(f.board_reads == 3, f"the sid lookup stops after 3 reads ({f.board_reads})")

    # Plain say stays a single POST, and reports the failure instead of retrying.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_NOT_SENT)]), verify=False)
    check(f.post_count == 1 and rc == 1 and out.startswith("FAILED"),
          f"plain say never resends ({f.post_count}): {out!r}")

    # Plain say whose POST timed out may have landed: UNCONFIRMED, never a FAILED that invites a resend.
    rc, out, f = _sr_run(_SayFake([_sr_fail(_SR_MAYBE_SENT, "timed out")]), verify=False)
    check(f.post_count == 1 and rc == 1 and out.startswith("UNCONFIRMED="),
          f"plain say after a timeout is UNCONFIRMED and sent once ({f.post_count}): {out!r}")

    # The real post_session_input classifies what a failed call proves.
    def _post_raising(exc):
        saved = co.launcher._request

        def _raise(*a, **k):
            raise exc

        co.launcher._request = _raise
        try:
            return co.launcher.post_session_input(co.DEFAULT_BASE_URL, _SR_SID, _SR_STEER)
        finally:
            co.launcher._request = saved

    _cases = (
        (_urlerr.URLError(ConnectionRefusedError("refused")), _SR_NOT_SENT),
        (_urlerr.URLError(OSError("unreachable")), _SR_NOT_SENT),
        (ConnectionResetError("reset"), _SR_MAYBE_SENT),
        (TimeoutError("timed out"), _SR_MAYBE_SENT),
        (_urlerr.URLError(TimeoutError("timed out")), _SR_MAYBE_SENT),
        (_urlerr.HTTPError("u", 503, "unavailable", None, io.BytesIO(b"")), _SR_NOT_SENT),
        (_urlerr.HTTPError("u", 502, "bad gateway", None, io.BytesIO(b"")), None),
        (_urlerr.HTTPError("u", 404, "nf", None, io.BytesIO(b"")), None),
    )
    for exc, want in _cases:
        got = _post_raising(exc)
        check(got["ok"] is False and got["transport"] == want,
              f"post_session_input: {type(exc).__name__} {exc} -> transport={want!r}, got {got['transport']!r}")
    for code, want in ((502, "not_ingested"), (409, "dropped"), (400, "dropped"), (403, "dropped"), (500, None)):
        got = _post_raising(_urlerr.HTTPError("u", code, "x", None, io.BytesIO(b"")))
        check(got["reason"] == want, f"post_session_input: HTTP {code} -> reason {want!r}, got {got['reason']!r}")

    # A GET that fails for a non-transient reason is raised at once, not waited on.
    _reads = []
    _saved = (co.launcher._request, co.launcher.RECOVERY_BACKOFF)

    def _always_404(base, path, **k):
        _reads.append(path)
        raise _urlerr.HTTPError("u", 404, "nf", None, io.BytesIO(b""))

    co.launcher._request, co.launcher.RECOVERY_BACKOFF = _always_404, (0.0, 0.0, 0.0)
    try:
        try:
            co.launcher.request_with_recovery(co.DEFAULT_BASE_URL, "/api/board")
            _raised = False
        except _urlerr.HTTPError:
            _raised = True
    finally:
        co.launcher._request, co.launcher.RECOVERY_BACKOFF = _saved
    check(_raised and len(_reads) == 1, f"request_with_recovery: a 404 is not retried ({len(_reads)} reads)")
finally:
    shutil.rmtree(_sr_tmp, ignore_errors=True)


_h.report_and_exit("test_session_commands")
