"""Unit tests for skills/_lib/stall_probe.py (fleet-config#1106).

A stall can't be caused on demand, so each detector is driven with an injected
delay: a late `sleep` for the scheduler leg, a slow loopback server for the
HTTP leg. Evidence capture is faked for the rate-limit logic, and its cheap
real sources (PDH, memory status, netstat) are run once for their shape.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_stall_probe.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import stall_probe as sp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


def records(folder: Path) -> list:
    path = folder / "stalls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


tmp = Path(tempfile.mkdtemp(prefix="stall_probe_"))
try:
    # ---- scheduler leg: a sleep that wakes late is a stall; heartbeat proves the probe ran ----
    folder = sp.probe_dir(tmp / "a")
    folder.mkdir(parents=True)
    calls = []
    probe = sp.Probe(sp.Log(folder), threshold=0.3, evidence=lambda: calls.append(1) or {"fake": True})
    late = iter([0.8])
    probe.sleep = lambda s: time.sleep(next(late, s))
    probe.run(duration_s=1.2)
    check(wait_for(lambda: any(r["kind"] == "sleep" for r in records(folder))), f"a late sleep is logged -- {records(folder)}")
    sleep_rec = next((r for r in records(folder) if r["kind"] == "sleep"), {})
    check(UTC.fullmatch(sleep_rec.get("start_utc", "")) and UTC.fullmatch(sleep_rec.get("end_utc", ""))
          and 0.7 <= sleep_rec.get("gap_s", 0) < 2 and sleep_rec.get("evidence") == {"fake": True},
          f"the stall record carries UTC start/end, the gap and the captured evidence -- {sleep_rec}")
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    check(UTC.fullmatch(status.get("heartbeat_utc", "")) and status.get("threshold_s") == 0.3,
          f"status.json carries a UTC heartbeat, so a quiet log can be told from a dead probe -- {status}")

    # ---- evidence is rate-limited: a second stall inside the cooldown is logged without it ----
    probe.stall("http", time.time(), 2.0)
    check(wait_for(lambda: any(r["kind"] == "http" for r in records(folder))), "a second stall is still logged")
    http_rec = next((r for r in records(folder) if r["kind"] == "http"), {})
    check(str(http_rec.get("evidence", "")).startswith("skipped") and len(calls) == 1,
          f"evidence is captured once per cooldown, not per stall -- {http_rec} calls={len(calls)}")

    # ---- a gap that was mostly machine sleep is a suspend, not a stall (fleet-config#1123) ----
    # Shapes copied from the System log of a real sleep/resume (Kernel-Power 42/107, Power-Troubleshooter 1).
    power_fixture = [
        {"id": 42, "provider": "Microsoft-Windows-Kernel-Power", "time_utc": "2026-10-01T07:44:55.735Z",
         "message": "The system is entering sleep. | Sleep Reason: Battery"},
        {"id": 107, "provider": "Microsoft-Windows-Kernel-Power", "time_utc": "2026-10-01T07:45:06.203Z",
         "message": "The system has resumed from sleep."},
        {"id": 1, "provider": "Microsoft-Windows-Power-Troubleshooter", "time_utc": "2026-10-01T07:46:46.280Z",
         "message": "The system has returned from a low power state. | Sleep Time: 07:44:55 | Wake Time: 07:46:45 | "
                    "Wake Source: Unknown, but possibily due to timer - the scheduled task 'X' requested the wake."},
    ]

    def suspend_probe(name: str, skip_s: float, gap_s: float, unbiased=None):
        """A probe whose first sleep is `gap_s` long, `skip_s` of it spent with the unbiased clock stopped."""
        folder_x = sp.probe_dir(tmp / name)
        folder_x.mkdir(parents=True)
        fake_calls: list = []
        probe_x = sp.Probe(sp.Log(folder_x), threshold=0.3, evidence=lambda: fake_calls.append(1) or {"fake": True})
        skipped = [0.0]
        probe_x.unbiased = unbiased or (lambda: time.monotonic() - skipped[0])
        probe_x.power_events = lambda _start, _end: sp.summarize_power_events(power_fixture)
        probe_x.power_settle_s = 0
        gaps = iter([gap_s])

        def asleep(s):
            gap = next(gaps, None)
            if gap is not None:
                skipped[0] += skip_s
            time.sleep(gap if gap is not None else s)
        probe_x.sleep = asleep
        probe_x.run(duration_s=gap_s + 0.5)
        return probe_x, folder_x, fake_calls

    probe_s, folder_s, evidence_calls = suspend_probe("s", skip_s=0.8, gap_s=0.9)
    check(wait_for(lambda: any(r["kind"] == "suspend" for r in records(folder_s))), f"a sleep/resume gap is logged -- {records(folder_s)}")
    suspend_rec = next((r for r in records(folder_s) if r["kind"] == "suspend"), {})
    check(not any(r["kind"] == "sleep" for r in records(folder_s)) and probe_s.stalls == 0 and probe_s.suspends == 1,
          f"...as a suspend, not a stall: the stall count stays 0 -- {records(folder_s)} stalls={probe_s.stalls}")
    check(suspend_rec.get("seen_by") == "sleep" and 0.7 <= suspend_rec.get("suspended_s", 0) <= 0.9
          and UTC.fullmatch(suspend_rec.get("start_utc", "")) and 0.8 <= suspend_rec.get("gap_s", 0) < 2,
          f"the suspend record carries UTC start/end, the gap, how much of it was sleep and which leg saw it -- {suspend_rec}")
    check(suspend_rec.get("power", {}).get("sleep_reason") == "Battery"
          and "scheduled task" in str(suspend_rec.get("power", {}).get("wake_source"))
          and len(suspend_rec.get("power", {}).get("events", [])) == 3,
          f"it carries the System log's sleep reason, wake source and the events around it -- {suspend_rec.get('power')}")
    check("evidence" not in suspend_rec and not evidence_calls,
          f"a suspend does not run the heavy capture (wake-up catch-up, and it would burn the cooldown) -- {suspend_rec}")
    status = json.loads((folder_s / "status.json").read_text(encoding="utf-8"))
    check(status.get("stalls_logged") == 0 and status.get("suspends_logged") == 1, f"the heartbeat counts them apart -- {status}")

    # a gap where the machine slept for under half of it is still a stall
    probe_m, folder_m, _ = suspend_probe("m", skip_s=0.35, gap_s=0.9)
    check(wait_for(lambda: any(r["kind"] == "sleep" for r in records(folder_m))) and probe_m.suspends == 0,
          f"a gap that was mostly awake stays a stall -- {records(folder_m)}")
    # a clock that can't answer must not read as 'not asleep' *or* 'asleep': the gap is a stall with suspended_s null
    probe_u, folder_u, _ = suspend_probe("u", skip_s=0.8, gap_s=0.9, unbiased=lambda: None)
    unknown_rec = next((r for r in records(folder_u) if r["kind"] == "sleep"), None)
    check(unknown_rec is not None and unknown_rec["suspended_s"] is None and probe_u.suspends == 0,
          f"an unreadable unbiased clock is recorded as unknown, not folded into either answer -- {records(folder_u)}")

    # ---- the pieces behind it ----
    summary = sp.summarize_power_events(power_fixture)
    check(summary["sleep_reason"] == "Battery" and summary["wake_source"].startswith("Unknown, but possibily due to timer"),
          f"sleep reason and wake source are pulled out of the event messages -- {summary}")
    empty = sp.summarize_power_events([])
    check(empty == {"sleep_reason": None, "wake_source": None, "events": []}, f"no events -> None fields, not made-up values -- {empty}")
    unbiased_now = sp.unbiased_s()
    check(isinstance(unbiased_now, float) and 0 < unbiased_now <= time.monotonic(),
          f"QueryUnbiasedInterruptTime reads, and never runs ahead of the monotonic clock (which runs through sleep) -- {unbiased_now}")
    quiet = sp._safely(lambda: sp.power_events(time.time() - 5, time.time()))
    check(isinstance(quiet, dict) and "error" not in quiet and quiet["events"] == [] and quiet["sleep_reason"] is None,
          f"the real System-log query runs, and an event-free window is an empty result, not an error -- {quiet}")
    folder_c = sp.probe_dir(tmp / "c")
    folder_c.mkdir(parents=True)
    log_c = sp.Log(folder_c)
    log_c.append({"kind": "sleep", "gap_s": 2.0})
    log_c.append({"kind": "suspend", "gap_s": 100.0})
    status_out = io.StringIO()
    with contextlib.redirect_stdout(status_out):
        sp.main(["status", "--state-dir", str(tmp / "c")])
    counted = json.loads(status_out.getvalue())
    check(counted["stall_lines"] == 1 and counted["suspend_lines"] == 1,
          f"`status` keeps suspends out of the stall-line count -- {counted}")

    # ---- HTTP leg: a slow loopback response is a stall ----
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(0.6)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_a):
            pass
    slow = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    folder_b = sp.probe_dir(tmp / "b")
    folder_b.mkdir(parents=True)
    probe_b = sp.Probe(sp.Log(folder_b), threshold=0.3, evidence=lambda: {})
    probe_b.url = f"http://127.0.0.1:{slow.server_address[1]}/probe.txt"
    probe_b.run(duration_s=2.5)
    slow.shutdown()
    check(any(r["kind"] == "http" and r["gap_s"] >= 0.5 for r in records(folder_b)),
          f"a slow loopback GET is logged as an http stall -- {records(folder_b)}")
    check(not any(r["kind"] == "sleep" for r in records(folder_b)), "no scheduler stall is invented on a quiet run")

    # ---- disk leg: a slow unbuffered read on one volume is a `kind: disk` stall; the other volume is the control ----
    class FakeReader:
        """Stands in for DiskReader: the first `slow_first` reads take `delay_s`, the rest are instant."""
        def __init__(self, delay_s: float = 0.0, slow_first: int = 0) -> None:
            self.delay_s, self.slow_first, self.reads, self.closed = delay_s, slow_first, 0, False

        def read(self) -> bytes:
            self.reads += 1
            if self.reads <= self.slow_first:
                time.sleep(self.delay_s)
            return b"\0" * 4096

        def close(self) -> None:
            self.closed = True

    def disk_probe(name: str, readers: dict, unbiased=None, opener=None):
        folder_d = sp.probe_dir(tmp / name)
        folder_d.mkdir(parents=True)
        calls_d: list = []
        probe_d = sp.Probe(sp.Log(folder_d), threshold=0.3, evidence=lambda: calls_d.append(1) or {"fake": True},
                           disk_dirs={vol: tmp / name / vol.strip(":") for vol in readers})
        probe_d.open_disk = opener or (lambda volume, _folder: readers[volume])
        if unbiased:
            probe_d.unbiased = unbiased
        probe_d.power_events = lambda _start, _end: sp.summarize_power_events([])
        probe_d.power_settle_s = 0
        probe_d.disk_every_s = 0.05
        return probe_d, folder_d, calls_d

    e_reader, c_reader = FakeReader(0.6, slow_first=1), FakeReader()
    probe_d, folder_d, disk_calls = disk_probe("d", {"E:": e_reader, "C:": c_reader})
    probe_d.run(duration_s=1.5)
    check(wait_for(lambda: any(r["kind"] == "disk" for r in records(folder_d))), f"a slow read is logged as a disk stall -- {records(folder_d)}")
    disk_recs = [r for r in records(folder_d) if r["kind"] == "disk"]
    check(len(disk_recs) == 1 and disk_recs[0]["volume"] == "E:" and 0.5 <= disk_recs[0]["gap_s"] < 2
          and UTC.fullmatch(disk_recs[0]["start_utc"]) and UTC.fullmatch(disk_recs[0]["end_utc"]) and disk_recs[0]["threshold_s"] == 0.3,
          f"the record names the volume and carries UTC start/end and the gap, and the fast control volume logs nothing -- {disk_recs}")
    check(probe_d.stalls == 1 and disk_calls == [1] and disk_recs[0].get("evidence") == {"fake": True},
          f"a disk stall counts as a stall and captures the evidence block -- stalls={probe_d.stalls} calls={disk_calls}")
    check(e_reader.reads > 3 and c_reader.reads > 3 and wait_for(lambda: e_reader.closed and c_reader.closed),
          f"each volume is read on its own cadence (a stuck E: read must not stop C:) and the readers are closed -- {e_reader.reads}/{c_reader.reads}")
    status_d = json.loads((folder_d / "status.json").read_text(encoding="utf-8"))
    check(sorted(status_d.get("disk_volumes", [])) == ["C:", "E:"] and set(status_d.get("disk_reads", {})) == {"C:", "E:"}
          and probe_d.disk_reads["E:"] > 3 and probe_d.disk_reads["C:"] > 3,
          f"the heartbeat names the volumes read and carries the per-volume read counts, so a quiet log can be told from a dead leg -- {status_d} {probe_d.disk_reads}")

    # a read that straddled a machine sleep is a suspend seen by the disk leg, not a disk stall
    skipped_d = [0.0]

    class SleepyReader(FakeReader):
        def read(self) -> bytes:
            if self.reads == 0:
                skipped_d[0] += 0.8
            return super().read()
    probe_z, folder_z, _ = disk_probe("z", {"E:": SleepyReader(0.9, slow_first=1)}, unbiased=lambda: time.monotonic() - skipped_d[0])
    probe_z.run(duration_s=1.6)
    check(wait_for(lambda: any(r["kind"] == "suspend" for r in records(folder_z))) and probe_z.stalls == 0
          and next(r for r in records(folder_z) if r["kind"] == "suspend").get("seen_by") == "disk"
          and not any(r["kind"] == "disk" for r in records(folder_z)),
          f"a read that spanned a machine sleep is a suspend, not a disk stall -- {records(folder_z)}")

    # a volume that cannot be read is a finding (`disk-error`), logged once per distinct error, and the leg keeps retrying
    attempts: list = []

    def failing_open(volume, _folder):
        attempts.append(volume)
        raise OSError("drive not ready")
    probe_f, folder_f, _ = disk_probe("f", {"E:": None}, opener=failing_open)
    probe_f.run(duration_s=1.0)
    errors = [r for r in records(folder_f) if r["kind"] == "disk-error"]
    check(len(errors) == 1 and errors[0]["volume"] == "E:" and "drive not ready" in errors[0]["error"] and UTC.fullmatch(errors[0]["start_utc"])
          and len(attempts) > 3 and probe_f.stalls == 0,
          f"an unreadable volume logs one disk-error and keeps retrying, without becoming a stall -- {errors} attempts={len(attempts)}")

    # E: slow / C: slow / both slow are told apart by `also_slow`, not by hand-correlating two records
    check(disk_recs[0]["also_slow"] == [] and disk_recs[0]["was_hung"] is False,
          f"E: slow alone: also_slow is empty, so it reads as 'that disk', not the storage stack -- {disk_recs[0]}")
    probe_b2, folder_b2, _ = disk_probe("both", {"E:": FakeReader(0.6, slow_first=1), "C:": FakeReader(0.6, slow_first=1)})
    probe_b2.run(duration_s=1.5)
    check(wait_for(lambda: sum(r["kind"] == "disk" for r in records(folder_b2)) == 2), f"both slow volumes are logged -- {records(folder_b2)}")
    both = {r["volume"]: r for r in records(folder_b2) if r["kind"] == "disk"}
    check(both["E:"]["also_slow"] == ["C:"] and both["C:"]["also_slow"] == ["E:"],
          f"both slow: each record names the other volume, whichever finished first -- {both}")
    probe_c2, folder_c2, _ = disk_probe("conly", {"E:": FakeReader(), "C:": FakeReader(0.6, slow_first=1)})
    probe_c2.run(duration_s=1.5)
    check(wait_for(lambda: any(r["kind"] == "disk" for r in records(folder_c2)))
          and [(r["volume"], r["also_slow"]) for r in records(folder_c2) if r["kind"] == "disk"] == [("C:", [])],
          f"C: slow alone is its own record with an empty also_slow -- {records(folder_c2)}")

    # a read that does not return is its own state (`disk-hung`), logged while it is still stuck, with evidence
    release = threading.Event()

    class HangingReader(FakeReader):
        def read(self) -> bytes:
            self.reads += 1
            if self.reads == 1:
                release.wait(10)
            return b"\0" * 4096
    hung_reader = HangingReader()
    probe_h, folder_h, hung_calls = disk_probe("h", {"E:": hung_reader, "C:": FakeReader()})
    probe_h.disk_hung_s = 0.4
    probe_h.run(duration_s=1.5)
    check(wait_for(lambda: any(r["kind"] == "disk-hung" for r in records(folder_h)), 3), f"a read that has not returned is logged -- {records(folder_h)}")
    hung = [r for r in records(folder_h) if r["kind"] == "disk-hung"]
    check(len(hung) == 1 and hung[0]["volume"] == "E:" and hung[0]["in_flight_s"] >= 0.4 and hung[0]["also_slow"] == []
          and UTC.fullmatch(hung[0]["start_utc"]) and hung[0]["evidence"] == {"fake": True} and "end_utc" not in hung[0],
          f"one disk-hung record per stuck read: volume, UTC start, how long in flight, evidence captured during the stall -- {hung}")
    check(not any(r["kind"] == "disk" for r in records(folder_h)) and probe_h.stalls == 0 and probe_h.disk_hung == 1,
          f"the stuck read is not folded into 'fine' nor into a completed stall while it is in flight -- {records(folder_h)}")
    release.set()
    check(wait_for(lambda: any(r["kind"] == "disk" for r in records(folder_h))), f"...and when it finally returns it logs the full gap -- {records(folder_h)}")
    done = next(r for r in records(folder_h) if r["kind"] == "disk")
    check(done["was_hung"] is True and done["gap_s"] >= 0.4 and done["volume"] == "E:", f"the completed record says it was the hung read -- {done}")

    # a hang that spanned a machine sleep is not reported as hung (the completed read logs a suspend)
    release_z = threading.Event()
    skipped_h = [0.0]

    class SleepyHang(FakeReader):
        def read(self) -> bytes:
            skipped_h[0] += 0.8
            release_z.wait(10)
            return b"\0" * 4096
    probe_y, folder_y, _ = disk_probe("y", {"E:": SleepyHang()}, unbiased=lambda: time.monotonic() - skipped_h[0])
    probe_y.disk_hung_s = 0.4
    probe_y.run(duration_s=1.2)
    time.sleep(0.2)
    check(not any(r["kind"] == "disk-hung" for r in records(folder_y)) and probe_y.disk_hung == 0,
          f"a read stuck across a machine sleep is not a disk-hung -- {records(folder_y)}")
    release_z.set()

    # the real unbuffered reader: sector-aligned 4 KB reads at random offsets of a prepared file
    disk_folder = tmp / "diskfile"
    one_mb = 1 << 20
    prepared = sp.prepare_disk_file(disk_folder, one_mb)
    check(prepared.stat().st_size == one_mb, f"the probe file is created at the requested size -- {prepared}")
    stamp = prepared.stat().st_mtime_ns
    check(sp.prepare_disk_file(disk_folder, one_mb) == prepared and prepared.stat().st_mtime_ns == stamp,
          "an existing probe file of the right size is reused, not rewritten")
    prepared.write_bytes(b"short")
    sp.prepare_disk_file(disk_folder, one_mb)
    check(prepared.stat().st_size == one_mb, "a probe file of the wrong size is rebuilt")
    content = prepared.read_bytes()
    reader = sp.DiskReader(prepared)
    try:
        chunks = [reader.read() for _ in range(5)]
    finally:
        reader.close()
    check(all(len(c) == sp.DISK_READ_BYTES and content.find(c) % sp.DISK_READ_BYTES == 0 for c in chunks),
          "each read returns 4 KB that sits at a 4 KB-aligned offset of the file (FILE_FLAG_NO_BUFFERING needs sector alignment)")
    check(len({c for c in chunks}) > 1, "the offset is random, not fixed")
    try:
        sp.DiskReader(disk_folder / "missing.bin")
        missing = None
    except OSError as exc:
        missing = exc
    check(missing is not None, f"opening a missing file raises instead of reading nothing -- {missing}")
    dirs = sp.default_disk_dirs(Path("E:/some/repo"), Path("C:/Users/x/state"))
    check(sorted(dirs) == ["C:", "E:"] and all(str(p).upper().startswith(v) for v, p in dirs.items())
          and sp.default_disk_dirs(Path("C:/a"), Path("C:/b")).keys() == {"C:"},
          f"volumes derive from where the repo and the state dir live (no hardcoded drive); one volume when they share a drive -- {dirs}")

    # ---- one instance at a time ----
    first = sp.single_instance(folder_b)
    second = sp.single_instance(folder_b)
    check(first is not None and second is None, "a second probe on the same state dir is refused")
    first.close()

    # ---- `start` never spawns a second probe while one holds the lock ----
    held = sp.single_instance(folder_b)
    started = subprocess.run([sys.executable, str(REPO / "skills" / "_lib" / "stall_probe.py"), "start",
                              "--state-dir", str(tmp / "b")], capture_output=True, text=True, timeout=30)
    check(held is not None and started.returncode == 0 and started.stdout.strip() == "ALREADY_RUNNING",
          f"start reports ALREADY_RUNNING and spawns nothing when a probe is up -- {started.stdout!r} {started.stderr!r}")
    held.close()

    # ---- logon start: a Startup-folder wrapper, always a temp dir here, never the real one ----
    startup = tmp / "Startup"
    fake_python = tmp / "python.exe"
    fake_python.write_bytes(b"")
    captured = io.StringIO()

    def run_cmd(fn, *a, **k):
        captured.seek(0)
        captured.truncate()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            code = fn(*a, **k)
        return code, captured.getvalue()

    code, text = run_cmd(sp.install_logon, folder_b, startup, fake_python)
    bat = startup / sp.STARTUP_BAT_NAME
    body = bat.read_bytes() if bat.exists() else b""
    check(code == 0 and text.startswith("INSTALLED") and body.startswith(b"@echo off\r\n")
          and str(fake_python).encode() in body and b" start >>" in body and b"\r\r" not in body,
          f"install writes the wrapper (CRLF, venv python, `start`) and reads it back -- {code} {text!r}")
    code, text = run_cmd(sp.install_logon, folder_b, startup, fake_python)
    check(code == 0 and text.startswith("ALREADY_INSTALLED") and bat.read_bytes() == body,
          f"a second install is a no-op -- {code} {text!r}")
    bat.write_bytes(b"stale")
    code, text = run_cmd(sp.install_logon, folder_b, startup, fake_python)
    check(code == 0 and text.startswith("UPDATED") and bat.read_bytes() == body, f"a stale wrapper is rewritten -- {code} {text!r}")
    code, text = run_cmd(sp.uninstall_logon, startup)
    check(code == 0 and text.startswith("UNINSTALLED") and not bat.exists(), f"uninstall removes it -- {code} {text!r}")
    code, text = run_cmd(sp.uninstall_logon, startup)
    check(code == 0 and text.startswith("NOT_INSTALLED"), f"a second uninstall is a no-op -- {code} {text!r}")
    blocker = tmp / "not-a-dir"
    blocker.write_bytes(b"")
    code, text = run_cmd(sp.install_logon, folder_b, blocker / "Startup", fake_python)
    check(code == 1 and text.startswith("WRITE_FAILED"), f"an unwritable Startup dir is WRITE_FAILED, not a crash -- {code} {text!r}")
    code, text = run_cmd(sp.install_logon, folder_b, startup, tmp / "missing.exe")
    check(code == 1 and "WRITE_FAILED" in text and not bat.exists(), f"a missing venv python is refused before writing -- {code} {text!r}")

    # ---- the real cheap evidence sources have the expected shape ----
    pdh = sp._safely(sp.pdh_counters)
    check(isinstance(pdh.get(r"\Memory\Committed Bytes"), float) and r"\Process(VmmemWSL)\Working Set" in pdh,
          f"PDH counters read by English name -- {pdh}")
    mem = sp._safely(sp.memory_status)
    check(mem.get("commit_limit_gb", 0) > 0 and "commit_used_gb" in mem, f"GlobalMemoryStatusEx shape -- {mem}")
    tcp = sp._safely(sp.tcp_states)
    check(isinstance(tcp, dict) and "error" not in tcp and sum(tcp.values()) > 0, f"netstat TCP state counts -- {tcp}")
    disk_pdh = sp._safely(lambda: sp.pdh_counters(sp.PDH_COUNTERS + sp.disk_counter_names(["C:"])))
    check(all(isinstance(disk_pdh.get(name), float) for name in sp.disk_counter_names(["C:"])) and sp.disk_counter_names(["C:"]),
          f"per-volume latency and queue counters read by English name, not just _Total -- {disk_pdh}")
    hwinfo = sp._safely(sp.hwinfo_process)
    check("error" not in hwinfo and isinstance(hwinfo.get("running"), bool) and "start_utc" in hwinfo,
          f"HWiNFO64's process state reads: running or not, plus its start time (null when unreadable) -- {hwinfo}")
    parsed = sp.parse_hwinfo(json.dumps({"running": True, "start_utc": "2026-10-04T05:09:05Z"}))
    check(parsed == {"running": True, "start_utc": "2026-10-04T05:09:05Z"}
          and sp.parse_hwinfo(json.dumps({"running": False, "start_utc": None})) == {"running": False, "start_utc": None}
          and "error" in sp.parse_hwinfo("") and "error" in sp.parse_hwinfo("not json"),
          f"the HWiNFO reply parses; an empty or garbled reply is an error, never read as 'not running' -- {parsed}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_stall_probe")
