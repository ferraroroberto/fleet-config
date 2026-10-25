"""chief_ops session commands: say/stop prefix resolution, the wait-event inbox, the wake doorbell, and context + self-compact with the launcher stubbed.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_session_commands.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import contextlib
import io
import time

from _suite import _exchange_columns, _h, check, co  # noqa: E402


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


# ---- wait-event: the chief inbox, claimed exactly once (fleet-config#999) ----

import json  # noqa: E402
import threading  # noqa: E402

_inbox_tmp = Path(tempfile.mkdtemp(prefix="chief_inbox_ops_"))
try:
    inbox = _inbox_tmp / "chief-inbox"
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

    def publish(name: str, **fields) -> None:
        inbox.mkdir(exist_ok=True)
        row = {"event": "Stop", "launcher_sid": "a1b2c3d4e5f6", "repo": "fleet-config",
               "number": 999, "agent": "claude", "ts": "2026-09-27T11:59:30Z", **fields}
        (inbox / name).write_text(json.dumps(row), encoding="utf-8")

    def real_ts() -> str:
        # wait_for_events filters with the real clock, so its events must be
        # stamped with it too -- a fixed date expires 24 h later (fleet-config#1084).
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    check(co.claim_batch(inbox, now=now) == [], "claim_batch: a missing inbox is an empty batch")
    publish("1-a.json")
    publish("2-b.json", event="SessionEnd", repo="app-launcher", number=7)
    (inbox / ".event.x1.tmp").write_text("half-written", encoding="utf-8")
    (inbox / "3-c.json").write_text("{not json", encoding="utf-8")
    publish("4-d.json", ts="2026-09-25T11:00:00Z")
    batch = co.claim_batch(inbox, now=now)
    check([e["number"] for e in batch] == [999, 7],
          "claim_batch: claims published events oldest first; skips temps, junk, and events past the TTL")
    check(co.claim_batch(inbox, now=now) == [], "claim_batch: a second claim re-reports nothing (claimed once)")
    check(sorted(p.name for p in inbox.iterdir()) == [".event.x1.tmp"],
          "claim_batch: claimed files and the claim folder are gone; an unpublished temp is untouched")

    digest = co.format_inbox_digest(batch, now=now)
    check(digest.startswith("WAIT_EVENT=events n=2") and "fleet-config#999: turn ended (claude)" in digest
          and "app-launcher#7: session ended" in digest and "sid=a1b2c3d4" in digest,
          "format_inbox_digest: one line per event naming issue, kind, agent and sid")

    # A burst settles into one wake: an event published during the settle window joins the batch.
    publish("5-e.json", number=1, ts=real_ts())
    slept = []
    def settle_sleep(seconds):
        slept.append(seconds)
        publish("6-f.json", number=2, ts=real_ts())
    got = co.wait_for_events(inbox, timeout=10, interval=1, sleep=settle_sleep)
    check([e["number"] for e in got] == [1, 2] and slept == [co.WAIT_EVENT_SETTLE_SECONDS],
          "wait_for_events: a burst within the settle window arrives as one wake")

    # Empty inbox: returns [] once the (fake) clock passes the timeout, never loops forever.
    ticks = iter(range(0, 1000, 5))
    got = co.wait_for_events(inbox, timeout=12, interval=5, sleep=lambda s: None, clock=lambda: next(ticks))
    check(got == [], "wait_for_events: an empty inbox returns [] at the timeout")

    # Real timing: a writer thread's event makes the waiter return within the interval.
    old_dir = co.mailbox.chief_inbox_dir
    co.mailbox.chief_inbox_dir = lambda: inbox
    try:
        threading.Timer(0.5, lambda: publish("7-g.json", number=3, ts=real_ts())).start()
        started = time.monotonic()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(["wait-event", "--timeout", "30", "--interval", "0.2"])
        took = time.monotonic() - started
        check(code == 0 and "WAIT_EVENT=events n=1" in out.getvalue() and took < 0.5 + 0.2 + co.WAIT_EVENT_SETTLE_SECONDS + 3,
              f"wait-event CLI: exits soon after an event lands (took {took:.1f}s)")

        board_calls = []
        old_request = co.launcher._request
        co.launcher._request = lambda base, path: board_calls.append(path) or {}
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = co.main(["wait-event", "--timeout", "0.3", "--interval", "0.1"])
        finally:
            co.launcher._request = old_request
        check(code == 0 and out.getvalue().startswith("WAIT_EVENT=timeout") and board_calls == ["/api/board"],
              "wait-event CLI: an empty inbox times out with the board digest")
    finally:
        co.mailbox.chief_inbox_dir = old_dir
finally:
    shutil.rmtree(_inbox_tmp, ignore_errors=True)


# ---- wake: doorbell or waiter, from the launcher's switch (fleet-config#1457) ----
# Only a confirmed `true` drops the waiter; anything unread arms it, never leaving
# the chief with no wake path. `_request` is stubbed: no real launcher is read.

import urllib.error as _wake_urlerr  # noqa: E402

check(co.resolve_wake_mode({"chief_doorbell": True}) == ("WAKE=doorbell doorbell=on", True),
      "resolve_wake_mode: the switch on means the doorbell wakes the chief")
check(co.resolve_wake_mode({"chief_doorbell": False}) == ("WAKE=waiter doorbell=off", True),
      "resolve_wake_mode: the switch off means arm the wait-event waiter")
for _bad in ({}, {"chief_doorbell": "true"}, {"chief_doorbell": 1}, {"chief_doorbell": None}):
    _line, _known = co.resolve_wake_mode(_bad)
    check(_line.startswith("WAKE=waiter doorbell=unknown reason=") and _known is False,
          f"resolve_wake_mode: a missing or non-boolean setting is unknown, and arms the waiter ({_bad!r})")
_line, _known = co.resolve_wake_mode(None, "launcher unreachable")
check(_line == "WAKE=waiter doorbell=unknown reason=launcher unreachable" and _known is False,
      "resolve_wake_mode: an unread launcher is unknown, and arms the waiter")


def _run_wake(reply):
    calls: list = []

    def fake_request(base, path):
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
    return code, out.getvalue().strip(), calls


_code, _out, _calls = _run_wake({"chief_doorbell": True, "port": 8445})
check(_code == 0 and _out == "WAKE=doorbell doorbell=on" and _calls == ["/api/config"],
      "wake CLI: reads the launcher's /api/config and reports the doorbell")
_code, _out, _calls = _run_wake({"chief_doorbell": False})
check(_code == 0 and _out == "WAKE=waiter doorbell=off", "wake CLI: switch off -> waiter, exit 0")
_code, _out, _calls = _run_wake(_wake_urlerr.URLError("refused"))
check(_code == 1 and _out == "WAKE=waiter doorbell=unknown reason=launcher unreachable",
      "wake CLI: an unreachable launcher is unknown (exit 1) and says arm the waiter")
_code, _out, _calls = _run_wake(_wake_urlerr.HTTPError("u", 404, "nf", None, None))
check(_code == 1 and _out == "WAKE=waiter doorbell=unknown reason=launcher config unreadable (HTTP 404)",
      "wake CLI: an HTTP error is unknown, named apart from unreachable")


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

    def fake_request(base, path, method="GET", body=None):
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
finally:
    co.launcher._request, co.rails.caller_session_id = _saved[0], _saved[1]
    if _saved[2] is None:
        os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
    else:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = _saved[2]
    shutil.rmtree(_compact_tmp, ignore_errors=True)

_h.report_and_exit("test_session_commands")
