"""Unit tests for skills/_lib/grokbot/status.py: the reading, `ensure` and the outage ping.

Everything runs against a fake process table, a temp status file, a fake launcher and a
fake notifier. `subprocess.Popen`/`run` are replaced for the whole file with a function
that fails the test, so a path that reached the real app launch or the real Telegram
transport would fail loudly instead of starting Grok Bot or sending a message.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/grokbot/test_status.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from grokbot import status as gb  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

_real_run, _real_popen = subprocess.run, subprocess.Popen


def _no_real_spawn(*args, **kwargs):
    raise AssertionError(f"a real subprocess was spawned: {args[:1]}")


subprocess.run = _no_real_spawn
subprocess.Popen = _no_real_spawn

tmp = Path(tempfile.mkdtemp(prefix="grokbot-test-"))
EXE = tmp / "Grok Bot.exe"
EXE.write_bytes(b"")
STATUS = tmp / "desktop-status.json"
OUTAGE = tmp / "outage.json"
OTHER_EXE = str(tmp / "other.exe")


class World:
    """A fake process table: pid -> image path, plus what the name scan returns."""

    def __init__(self):
        self.images = {}
        self.listed = []
        self.image_error = None
        self.scan_error = None

    def image_of(self, pid):
        if self.image_error:
            raise self.image_error
        return self.images.get(pid)

    def pids_named(self, name):
        if self.scan_error:
            raise self.scan_error
        return list(self.listed)


def write_status(pid=100, signed_in=True, **extra):
    record = {"version": 1, "pid": pid, **extra}
    if signed_in is not None:
        record["signedIn"] = signed_in
    STATUS.write_text(json.dumps(record), encoding="utf-8")


def read(world):
    return gb.read_status(exe=EXE, status_file=STATUS, image_of=world.image_of, pids_named=world.pids_named)


try:
    # ---- read_status: each state, and unknown is never up ------------------------
    w = World()
    w.images[100] = str(EXE)
    write_status(100, True)
    st = read(w)
    check(st.state == gb.UP and st.pid == 100, "pid alive at the installed exe + signedIn true -> up")

    write_status(100, False)
    check(read(w).state == gb.SIGNED_OUT, "same process, signedIn false -> signed-out")

    write_status(100, None)
    check(read(w).state == gb.UNKNOWN, "a status file without signedIn -> unknown, not up")

    w = World()
    write_status(100, True)
    check(read(w).state == gb.DOWN, "the file's pid is not running and nothing else is -> down")

    w.images[100] = OTHER_EXE
    check(read(w).state == gb.DOWN, "a reused pid under another exe is never up (pid confirmed by exe path)")

    w = World()
    w.images[100] = str(EXE).upper()
    write_status(100, True)
    check(read(w).state == gb.UP, "exe path comparison ignores case")

    w = World()
    STATUS.unlink()
    check(read(w).state == gb.DOWN, "no status file and no process -> down")

    w.images[7], w.listed = str(EXE), [7]
    st = read(w)
    check(st.state == gb.UNKNOWN and st.pid == 7 and "starting" in st.reason,
          "no status file but the app is running -> unknown (may be starting), never down")

    w = World()
    STATUS.write_text("{not json", encoding="utf-8")
    check(read(w).state == gb.DOWN, "a corrupt status file is no record; no process -> down")
    STATUS.write_text(json.dumps({"pid": "100", "signedIn": True}), encoding="utf-8")
    check(read(w).state == gb.DOWN, "a non-integer pid is no record")

    w = World()
    w.images[100] = str(EXE)
    write_status(100, True)
    w.image_error = PermissionError(5, "access denied")
    check(read(w).state == gb.UNKNOWN, "an unreadable process image -> unknown")
    w = World()
    write_status(555, True)
    w.scan_error = OSError("tasklist exited 1")
    check(read(w).state == gb.UNKNOWN, "an unreadable process table -> unknown")

    missing = gb.read_status(exe=tmp / "nope.exe", status_file=STATUS,
                             image_of=World().image_of, pids_named=World().pids_named)
    check(missing.state == gb.UNKNOWN and "not installed" in missing.reason, "no install -> unknown")

    # ---- digest_line --------------------------------------------------------------
    w = World()
    w.images[100] = str(EXE)
    write_status(100, True)
    kw = dict(exe=EXE, status_file=STATUS, image_of=w.image_of, pids_named=w.pids_named)
    check(gb.digest_line(**kw) == "grokbot=up", "digest token for up")
    write_status(100, False)
    check(gb.digest_line(**kw) == "grokbot=signed-out", "digest token for signed-out")
    check(gb.digest_line(**{**kw, "exe": tmp / "nope.exe"}).startswith("grokbot=unknown ("),
          "digest token for unknown carries the reason")

    def boom(pid):
        raise ValueError("kaboom")

    check(gb.digest_line(**{**kw, "image_of": boom}) == "grokbot=unknown (probe failed: kaboom)",
          "a probe that crashes renders unknown, never up")

    # ---- ensure -------------------------------------------------------------------
    class Rig:
        def __init__(self, world, bring_up=False):
            self.world, self.bring_up = world, bring_up
            self.launched, self.pings, self.ping_result = [], [], True

        def launch(self, exe):
            self.launched.append(exe)
            if self.bring_up:
                self.world.images[200] = str(EXE)
                write_status(200, True)

        def notify(self, text):
            self.pings.append(text)
            return self.ping_result

        def run(self, **over):
            return gb.ensure(exe=EXE, status_file=STATUS, outage_file=OUTAGE, image_of=self.world.image_of,
                             pids_named=self.world.pids_named, launch=self.launch, notify=self.notify,
                             settle_seconds=0.0, sleep=lambda s: None, **over)

    def fresh():
        for f in (OUTAGE, STATUS):
            if f.exists():
                f.unlink()

    def outage():
        return json.loads(OUTAGE.read_text(encoding="utf-8"))

    # up: nothing started, nothing sent, episode closed
    fresh()
    w = World()
    w.images[100] = str(EXE)
    write_status(100, True)
    OUTAGE.write_text(json.dumps({"bad_checks": 1, "pinged": True}), encoding="utf-8")
    rig = Rig(w)
    res = rig.run()
    check(res.action == "none" and not rig.launched and not rig.pings, "ensure on an up app starts and sends nothing")
    check(outage() == {"bad_checks": 0, "pinged": False}, "an up reading closes the outage episode")

    # down and the start works: launched once, minimized helper called with the install exe, episode stays clean
    fresh()
    w = World()
    rig = Rig(w, bring_up=True)
    res = rig.run()
    check(res.action == "started" and rig.launched == [EXE] and res.status.state == gb.UP,
          "ensure starts a down app once and confirms it up")
    check(outage() == {"bad_checks": 0, "pinged": False} and not rig.pings,
          "a successful start is not an outage: no ping, counter clear")

    # down and the start does not take: first check quiet, second pings once, third stays quiet
    fresh()
    w = World()
    rig = Rig(w)
    r1 = rig.run()
    check(r1.status.state == gb.DOWN and r1.bad_checks == 1 and not rig.pings, "first failed check: counted, no ping")
    r2 = rig.run()
    check(r2.bad_checks == 2 and len(rig.pings) == 1 and r2.pinged, "second failed check: exactly one ping")
    r3 = rig.run()
    check(r3.bad_checks == 3 and len(rig.pings) == 1, "a continuing outage never pings twice")
    check(len(rig.launched) == 3, "ensure keeps trying to start the app every check")
    check(all(ord(c) < 128 for c in rig.pings[0]), "the ping text is ASCII (a Windows command line is not UTF-8-safe)")
    w.images[300] = str(EXE)
    write_status(300, True)
    rig.run()
    check(outage() == {"bad_checks": 0, "pinged": False}, "recovery closes the episode")
    w.images.clear()
    rig.run()
    rig.run()
    check(len(rig.pings) == 2, "the next outage is a new episode with its own single ping")

    # unknown never starts anything and never moves the episode
    fresh()
    w = World()
    w.images[7], w.listed = str(EXE), [7]
    OUTAGE.write_text(json.dumps({"bad_checks": 1, "pinged": False}), encoding="utf-8")
    rig = Rig(w)
    res = rig.run()
    check(res.action == "refused" and not rig.launched and not rig.pings, "ensure refuses to start on unknown")
    check(outage() == {"bad_checks": 1, "pinged": False}, "an unknown check leaves the episode untouched")
    w = World()
    w.scan_error = OSError("table unreadable")
    write_status(555, True)
    rig = Rig(w)
    check(rig.run().action == "refused" and not rig.launched, "an unreadable process table refuses too")

    # signed-out: running app is left alone, counted, pinged with its own text
    fresh()
    w = World()
    w.images[100] = str(EXE)
    write_status(100, False)
    rig = Rig(w)
    rig.run()
    res = rig.run()
    check(not rig.launched and len(rig.pings) == 1 and "signed out" in rig.pings[0],
          "signed-out twice: no relaunch, one ping naming the sign-in")

    # a launch that raises is a failed check, not a crash
    fresh()
    w = World()
    rig = Rig(w)

    def broken_launch(exe):
        raise OSError("no such file")

    rig.launch = broken_launch
    res = rig.run()
    check(res.action == "start-failed" and res.status.state == gb.DOWN and res.bad_checks == 1,
          "a launch error is start-failed and counts as a failed check")

    # a ping that is not delivered is retried, not marked sent
    fresh()
    w = World()
    rig = Rig(w)
    rig.ping_result = False
    rig.run()
    res = rig.run()
    check(not res.pinged and len(rig.pings) == 1, "an undelivered ping is not recorded as sent")
    rig.ping_result = True
    res = rig.run()
    check(res.pinged and len(rig.pings) == 2, "the undelivered ping is retried on the next check")

    # the episode state cannot be written: reported, never silent
    fresh()
    blocker = tmp / "a-file"
    blocker.write_text("x", encoding="utf-8")
    w = World()
    rig = Rig(w)
    res = gb.ensure(exe=EXE, status_file=STATUS, outage_file=blocker / "sub" / "o.json", image_of=w.image_of,
                    pids_named=w.pids_named, launch=rig.launch, notify=rig.notify, settle_seconds=0.0,
                    sleep=lambda s: None)
    check(res.state_error != "", "an unwritable episode file is reported in the result")

    # ---- the real defaults, built but never executed ------------------------------
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    subprocess.run = fake_run
    check(gb.send_attention("hello") is True, "send_attention reports a zero exit as delivered")
    argv = seen["argv"]
    check(Path(argv[1]).name == "notify_send.py" and argv[2:4] == ["--category", "attention"] and argv[-1] == "hello",
          "the default notifier is hooks/notify_send.py on the attention category")
    check(seen["kwargs"]["creationflags"] == gb.NO_WINDOW, "the notifier spawn suppresses the console window")
    subprocess.run = _no_real_spawn

    def fake_popen(argv, **kwargs):
        seen["popen"], seen["popen_kwargs"] = argv, kwargs

    subprocess.Popen = fake_popen
    if sys.platform == "win32":
        gb.launch_minimized(EXE)
        check(seen["popen"] == [str(EXE)] and seen["popen_kwargs"]["creationflags"] == gb.NO_WINDOW,
              "launch_minimized starts exactly the install exe, console-less")
        check(seen["popen_kwargs"]["startupinfo"].wShowWindow == 7, "the app is started minimized without taking focus")
    else:
        _h.skip("launch_minimized needs Windows STARTUPINFO")
    subprocess.Popen = _no_real_spawn

    # ---- the board digest ----------------------------------------------------------
    sys.path.insert(0, str(REPO / "skills" / "_lib"))
    from chief import digest  # noqa: E402

    board = {"columns": {}, "github": {"available": True}, "live_sessions": {"available": True}}
    check(digest.format_board_digest(board, grokbot_line="grokbot=down").splitlines()[-1] == "grokbot=down",
          "the digest ends with the grokbot token when given one")
    check("grokbot" not in digest.format_board_digest(board), "a digest built without the token has none")

    import chief_ops as co  # noqa: E402

    def fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        return board if path == "/api/board" else {"jobs": []}

    prior_request, prior_line = co.launcher._request, gb.digest_line
    co.launcher._request, gb.digest_line = fake_request, lambda **kw: "grokbot=unknown (fixture)"
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            co.cmd_board(argparse.Namespace(base_url=co.DEFAULT_BASE_URL, json=False))
    finally:
        co.launcher._request, gb.digest_line = prior_request, prior_line
    check("grokbot=unknown (fixture)" in out.getvalue(), "`chief_ops.py board` prints the grokbot token")
finally:
    subprocess.run, subprocess.Popen = _real_run, _real_popen
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_grokbot_status")
