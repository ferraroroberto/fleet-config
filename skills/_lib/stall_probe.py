"""Always-on host stall probe (fleet-config#1106). stdlib only.

Whole-box stalls of 7-25 s freeze every local server at once and turn whatever
gate is running red. This probe catches the next one with timestamps:

- the main loop times `sleep(0.1)`; a wake more than `--threshold` (1 s) late is
  scheduler starvation (`kind: sleep`);
- a thread GETs an in-memory static file from the probe's own loopback server
  once a second; a request slower than the threshold is a stalled network path
  (`kind: http`).

A machine sleep looks like a stall to both legs (the monotonic clock runs through
it) but is not one, so a gap that is mostly sleep is logged as `kind: suspend`
instead and kept out of the stall count (fleet-config#1123). The test is the
divergence between the monotonic clock and `QueryUnbiasedInterruptTime`, which
does not tick while the machine sleeps. A suspend record carries the System log's
Kernel-Power / Power-Troubleshooter events around it (sleep reason, wake source),
so a sleep nobody asked for stays visible.

Each stall is one JSON line in `stalls.jsonl` (UTC, per the three-clocks rule).
At most once per `EVIDENCE_COOLDOWN_S` a separate thread records the machine's
state just after the stall: memory counters through PDH's English-name API
(the box's counter names may be localized), `GlobalMemoryStatusEx`, TCP state
counts from one `netstat`, the launcher's running jobs, and the scheduled tasks
and Defender scans that ran in the last two minutes (one PowerShell call). Each
source that fails records its error instead of a value -- never a silent gap.

`status.json` carries a heartbeat every `HEARTBEAT_S`, so "no stalls logged"
can be told apart from "the probe was not running". One instance at a time:
the second one exits 2 (`ALREADY_RUNNING`).

Report-only: nothing here changes a machine setting (TCP, port range, WSL).

    python stall_probe.py run [--threshold 1.0] [--state-dir DIR]   # foreground
    python stall_probe.py start                                      # background, no window
    python stall_probe.py status                                     # heartbeat + stall count
    python stall_probe.py install-logon                              # Startup-folder launcher, no admin
    python stall_probe.py uninstall-logon
"""
from __future__ import annotations

import argparse
import ctypes
import http.server
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooks_state import state_dir  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

THRESHOLD_S = 1.0
SLEEP_S = 0.1
HTTP_EVERY_S = 1.0
HTTP_TIMEOUT_S = 60.0
HEARTBEAT_S = 300.0
EVIDENCE_COOLDOWN_S = 30.0
SUSPEND_SHARE = 0.5     # a gap is a suspend when more than this share of it was machine sleep
POWER_SETTLE_S = 8.0    # Power-Troubleshooter logs the wake a few seconds after it
POWER_LOOKBACK_S = 30.0
MAX_LOG_BYTES = 5_000_000
LAUNCHER_JOBS_URL = "https://127.0.0.1:8445/api/jobs"
PDH_COUNTERS = (
    r"\Memory\Committed Bytes", r"\Memory\Commit Limit", r"\Memory\Available MBytes",
    r"\Memory\Pages Input/sec", r"\Memory\Page Faults/sec", r"\Processor(_Total)\% Processor Time",
    r"\PhysicalDisk(_Total)\Avg. Disk sec/Transfer", r"\PhysicalDisk(_Total)\Current Disk Queue Length",
    r"\Process(Memory Compression)\Working Set", r"\Process(VmmemWSL)\Working Set",
    r"\TCPv4\Connections Established",
)
# Scheduled tasks and Defender scans that ran in the last two minutes: one call, only on a stall.
_ACTIVITY_PS = (
    "$since=(Get-Date).AddMinutes(-2);"
    "$t=@(Get-ScheduledTask | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue | "
    "Where-Object { $_.LastRunTime -gt $since } | ForEach-Object { $_.TaskPath + $_.TaskName });"
    "$d=Get-MpComputerStatus -ErrorAction SilentlyContinue;"
    "function U($x){ if($x){ $x.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null } };"
    "[pscustomobject]@{tasks=$t; defender_quick_end_utc=(U $d.QuickScanEndTime); defender_full_end_utc=(U $d.FullScanEndTime)} "
    "| ConvertTo-Json -Compress"
)
# Sleep/resume events in a window (suspend records only): Kernel-Power 42 = entering sleep (with its
# reason), 107 = resumed, 524 = critical battery trigger, 187 = an API-requested power state change;
# Power-Troubleshooter 1 = returned from a low power state; Kernel-General 1 = the clock correction.
_POWER_PS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$s=[datetime]::Parse('{start}');$e=[datetime]::Parse('{end}');"
    "try{{$ev=@(Get-WinEvent -FilterHashtable @{{LogName='System';StartTime=$s;EndTime=$e;Id=1,42,107,187,524}} -ErrorAction Stop | "
    "Where-Object {{ $_.ProviderName -match 'Kernel-Power|Power-Troubleshooter|Kernel-General' }} | Sort-Object TimeCreated | "
    "ForEach-Object {{ [pscustomobject]@{{id=$_.Id;provider=$_.ProviderName;"
    "time_utc=$_.TimeCreated.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ');"
    "message=(($_.Message -split \"`r?`n\" | Where-Object {{ $_.Trim() }}) -join ' | ')}} }})}}"
    "catch{{ if($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*'){{ $ev=@() }} else {{ throw }} }};"
    "ConvertTo-Json -InputObject $ev -Compress"
)
POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def utc(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def probe_dir(base: Optional[Path] = None) -> Path:
    return (base or state_dir()) / "stall-probe"


class Log:
    """Append-only stall log plus the heartbeat file; rotates once at MAX_LOG_BYTES."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.stalls = folder / "stalls.jsonl"
        self.status = folder / "status.json"
        self._lock = threading.Lock()

    def append(self, record: Dict[str, Any]) -> None:
        with self._lock:
            if self.stalls.exists() and self.stalls.stat().st_size > MAX_LOG_BYTES:
                os.replace(self.stalls, self.stalls.with_suffix(".jsonl.1"))
            with self.stalls.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")

    def beat(self, info: Dict[str, Any]) -> None:
        tmp = self.status.with_suffix(".tmp")
        tmp.write_text(json.dumps(info, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.status)


# ---- evidence ----------------------------------------------------------------

class _PdhValue(ctypes.Structure):
    _fields_ = [("status", ctypes.c_ulong), ("pad", ctypes.c_ulong), ("value", ctypes.c_double)]


def pdh_counters(names=PDH_COUNTERS, interval_s: float = 1.0) -> Dict[str, Any]:
    """English-named PDH counters over one interval (rates need two samples)."""
    pdh = ctypes.WinDLL("pdh")
    query = ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)):
        return {"error": "PdhOpenQueryW failed"}
    try:
        handles = {}
        for name in names:
            handle = ctypes.c_void_p()
            status = pdh.PdhAddEnglishCounterW(query, name, None, ctypes.byref(handle))
            handles[name] = handle if status == 0 else f"add failed {status & 0xFFFFFFFF:#x}"
        pdh.PdhCollectQueryData(query)
        time.sleep(interval_s)
        pdh.PdhCollectQueryData(query)
        out: Dict[str, Any] = {}
        for name, handle in handles.items():
            if isinstance(handle, str):
                out[name] = handle
                continue
            value = _PdhValue()
            status = pdh.PdhGetFormattedCounterValue(handle, 0x200, None, ctypes.byref(value))  # PDH_FMT_DOUBLE
            out[name] = round(value.value, 2) if status == 0 else f"read failed {status & 0xFFFFFFFF:#x}"
        return out
    finally:
        pdh.PdhCloseQuery(query)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_ext", ctypes.c_ulonglong)]


def memory_status() -> Dict[str, Any]:
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"error": "GlobalMemoryStatusEx failed"}
    gb = 1 << 30
    return {"load_pct": status.load, "phys_avail_gb": round(status.avail_phys / gb, 2),
            "commit_used_gb": round((status.total_page - status.avail_page) / gb, 2),
            "commit_limit_gb": round(status.total_page / gb, 2)}


def tcp_states() -> Dict[str, Any]:
    """Counts per TCP state from one `netstat -ano -p tcp` (port-exhaustion check, #440)."""
    res = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                         encoding="oem", errors="replace", timeout=30, creationflags=NO_WINDOW)
    counts: Dict[str, int] = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) == 5 and parts[0].upper() == "TCP":
            counts[parts[3]] = counts.get(parts[3], 0) + 1
    return counts if res.returncode == 0 else {"error": f"netstat exit {res.returncode}"}


def running_jobs(url: str = LAUNCHER_JOBS_URL) -> Any:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with urllib.request.urlopen(url, timeout=20, context=ctx) as resp:
        jobs = json.loads(resp.read().decode("utf-8")).get("jobs") or []
    return sorted(str(job.get("id")) for job in jobs if job.get("running") is True)


def recent_activity() -> Any:
    res = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _ACTIVITY_PS],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=90, creationflags=NO_WINDOW)
    if res.returncode != 0:
        return {"error": f"powershell exit {res.returncode}: {res.stderr.strip()[:200]}"}
    return json.loads(res.stdout) if res.stdout.strip() else {}


def unbiased_s() -> Optional[float]:
    """Seconds of interrupt time, which -- unlike `time.monotonic()` -- stops while the machine sleeps."""
    value = ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(value)):
        return None
    return value.value / 1e7


def power_events(start_wall: float, end_wall: float) -> Dict[str, Any]:
    """The System log's sleep/resume events between two wall-clock times, plus the sleep reason and
    wake source pulled out of them (`None` when no event carries one -- not 'no sleep')."""
    ps = _POWER_PS.format(start=utc(start_wall - POWER_LOOKBACK_S), end=utc(end_wall))
    res = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=90, creationflags=NO_WINDOW)
    if res.returncode != 0:
        return {"error": f"powershell exit {res.returncode}: {res.stderr.strip()[:200]}"}
    parsed = json.loads(res.stdout) if res.stdout.strip() else []
    return summarize_power_events(parsed if isinstance(parsed, list) else [parsed])


def summarize_power_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    def field(label: str) -> Optional[str]:
        for event in reversed(events):
            found = re.search(rf"{label}:\s*([^|]+)", str(event.get("message", "")))
            if found:
                return found.group(1).strip()
        return None
    trimmed = [{**event, "message": str(event.get("message", ""))[:300]} for event in events][:20]
    return {"sleep_reason": field("Sleep Reason"), "wake_source": field("Wake Source"), "events": trimmed}


def _safely(fn) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- one failed source is recorded, never fatal
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def capture_evidence() -> Dict[str, Any]:
    sources = {"pdh": pdh_counters, "memory": memory_status, "tcp_states": tcp_states,
               "launcher_running_jobs": running_jobs, "recent_activity": recent_activity}
    started = time.monotonic()
    out = {name: _safely(fn) for name, fn in sources.items()}
    out["captured_at"], out["capture_s"] = utc(), round(time.monotonic() - started, 1)
    return out


# ---- the probe ---------------------------------------------------------------

class _Static(http.server.BaseHTTPRequestHandler):
    body = b"stall-probe\n"

    def do_GET(self) -> None:  # noqa: N802 -- http.server's name
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args) -> None:
        pass


class Probe:
    def __init__(self, log: Log, threshold: float = THRESHOLD_S, evidence=capture_evidence) -> None:
        self.log, self.threshold, self.evidence = log, threshold, evidence
        self.sleep = time.sleep  # the timed wait; a test swaps in a late one
        self.unbiased, self.power_events = unbiased_s, power_events  # a test swaps in a fake clock and log
        self.power_settle_s = POWER_SETTLE_S
        self.stop = threading.Event()
        self.stalls = 0
        self.suspends = 0
        self._last_evidence = -EVIDENCE_COOLDOWN_S
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Static)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/probe.txt"

    def suspended(self, elapsed_s: float, unbiased_before: Optional[float]) -> Optional[float]:
        """Seconds of `elapsed_s` the machine spent asleep; `None` when the clock could not say."""
        unbiased_after = self.unbiased()
        if unbiased_before is None or unbiased_after is None:
            return None
        return round(max(0.0, elapsed_s - (unbiased_after - unbiased_before)), 2)

    def _suspend(self, record: Dict[str, Any], started_wall: float) -> None:
        """Log a sleep/resume gap with the System log's account of it. No heavy evidence: it would only
        show the wake-up catch-up, and it would spend the cooldown a real stall right after wake needs."""
        self.suspends += 1

        def capture() -> None:
            self.stop.wait(self.power_settle_s)
            power = _safely(lambda: self.power_events(started_wall, time.time() + 60))
            self.log.append({**record, "power": power})
        threading.Thread(target=capture, daemon=True).start()

    def stall(self, kind: str, started_wall: float, gap_s: float, suspended_s: Optional[float] = None) -> None:
        """Log one stall (or, when the gap was mostly machine sleep, one suspend); capture machine
        state on its own thread, rate-limited. `suspended_s=None` means the clock could not say."""
        record = {"kind": kind, "start_utc": utc(started_wall), "end_utc": utc(started_wall + gap_s),
                  "gap_s": round(gap_s, 2), "threshold_s": self.threshold, "suspended_s": suspended_s}
        if suspended_s is not None and suspended_s >= self.threshold and suspended_s > SUSPEND_SHARE * gap_s:
            self._suspend({**record, "kind": "suspend", "seen_by": kind}, started_wall)
            return
        self.stalls += 1
        now = time.monotonic()
        if now - self._last_evidence < EVIDENCE_COOLDOWN_S:
            record["evidence"] = "skipped: captured for a stall under 30 s ago"
            self.log.append(record)
            return
        self._last_evidence = now

        def capture() -> None:
            self.log.append({**record, "evidence": self.evidence()})
        threading.Thread(target=capture, daemon=True).start()

    def _http_loop(self) -> None:
        while not self.stop.wait(HTTP_EVERY_S):
            wall, started, unbiased_before = time.time(), time.monotonic(), self.unbiased()
            try:
                urllib.request.urlopen(self.url, timeout=HTTP_TIMEOUT_S).read()
            except Exception as exc:  # noqa: BLE001 -- a failed GET is a finding, not a crash
                self.log.append({"kind": "http-error", "start_utc": utc(wall), "error": str(exc)[:200]})
                continue
            took = time.monotonic() - started
            if took > self.threshold:
                self.stall("http", wall, took, self.suspended(took, unbiased_before))

    def run(self, duration_s: Optional[float] = None) -> None:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        threading.Thread(target=self._http_loop, daemon=True).start()
        began, next_beat = time.monotonic(), 0.0
        try:
            while not self.stop.is_set():
                wall, before, unbiased_before = time.time(), time.monotonic(), self.unbiased()
                self.sleep(SLEEP_S)
                elapsed = time.monotonic() - before
                if elapsed - SLEEP_S > self.threshold:
                    self.stall("sleep", wall, elapsed, self.suspended(elapsed, unbiased_before))
                if time.monotonic() >= next_beat:
                    self.log.beat({"pid": os.getpid(), "heartbeat_utc": utc(), "started_utc": utc(time.time() - (time.monotonic() - began)),
                                   "stalls_logged": self.stalls, "suspends_logged": self.suspends, "threshold_s": self.threshold, "heartbeat_every_s": HEARTBEAT_S})
                    next_beat = time.monotonic() + HEARTBEAT_S
                if duration_s is not None and time.monotonic() - began >= duration_s:
                    break
        finally:
            self.stop.set()
            self._server.shutdown()


def single_instance(folder: Path):
    """An exclusive byte lock held for the process's life, or None when another probe holds it."""
    import msvcrt  # Windows-only; imported here so the module still loads elsewhere
    handle = (folder / "probe.lock").open("a+b")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


# ---- logon start -------------------------------------------------------------

STARTUP_BAT_NAME = "FleetStallProbe.bat"
REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def startup_dir() -> Path:
    """The per-user Startup folder: a plain file write, no admin, no Task Scheduler."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable is not set")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _logon_bat_content(python: Path, log: Path) -> bytes:
    """`start` is idempotent (ALREADY_RUNNING when a probe holds the lock), so a logon that
    races a probe started by hand is safe. Its output lands in `startup.log`: a logon
    launch that dies silently is otherwise undiagnosable (cf. app-launcher#582)."""
    script = Path(__file__).resolve()
    return (
        "@echo off\r\n"
        f'cd /d "{REPO_ROOT}"\r\n'
        f'>>"{log}" echo [%date% %time%] logon start\r\n'
        f'"{python}" "{script}" start >>"{log}" 2>&1\r\n'
        f'>>"{log}" echo [%date% %time%] start returned errorlevel %ERRORLEVEL%\r\n'
    ).encode("utf-8")


def install_logon(folder: Path, target_dir: Optional[Path] = None, python: Path = VENV_PYTHON) -> int:
    """Write the Startup wrapper and read it back. Distinct outcomes: INSTALLED, UPDATED,
    ALREADY_INSTALLED (exit 0) and WRITE_FAILED (exit 1)."""
    path = (target_dir if target_dir is not None else startup_dir()) / STARTUP_BAT_NAME
    if not python.is_file():
        print(f"WRITE_FAILED: the venv interpreter is missing at {python} (create the repo .venv first)", file=sys.stderr)
        return 1
    data = _logon_bat_content(python, folder / "startup.log")
    try:
        existing = path.read_bytes() if path.is_file() else None
        if existing == data:
            print(f"ALREADY_INSTALLED {path}")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)  # bytes: text mode would turn our CRLF into CRCRLF
        if path.read_bytes() != data:
            raise OSError("read-back differs from what was written")
    except OSError as exc:
        print(f"WRITE_FAILED: {path}: {exc}", file=sys.stderr)
        return 1
    print(f"{'UPDATED' if existing is not None else 'INSTALLED'} {path}")
    return 0


def uninstall_logon(target_dir: Optional[Path] = None) -> int:
    path = (target_dir if target_dir is not None else startup_dir()) / STARTUP_BAT_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        print(f"NOT_INSTALLED {path}")
        return 0
    except OSError as exc:
        print(f"REMOVE_FAILED: {path}: {exc}", file=sys.stderr)
        return 1
    print(f"UNINSTALLED {path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("run", "start", "status", "install-logon", "uninstall-logon"))
    ap.add_argument("--threshold", type=float, default=THRESHOLD_S)
    ap.add_argument("--state-dir", default=None, help="override the hooks state dir (tests)")
    ap.add_argument("--startup-dir", default=None, help="override the Startup folder (tests)")
    args = ap.parse_args(argv)
    folder = probe_dir(Path(args.state_dir) if args.state_dir else None)
    startup = Path(args.startup_dir) if args.startup_dir else None
    if args.command == "uninstall-logon":
        return uninstall_logon(startup)
    folder.mkdir(parents=True, exist_ok=True)
    if args.command == "install-logon":
        return install_logon(folder, startup)
    if args.command == "status":
        status = json.loads((folder / "status.json").read_text(encoding="utf-8")) if (folder / "status.json").exists() else None
        lines = (folder / "stalls.jsonl").read_text(encoding="utf-8").splitlines() if (folder / "stalls.jsonl").exists() else []
        suspends = sum(1 for line in lines if json.loads(line).get("kind") == "suspend")
        print(json.dumps({"status": status, "stall_lines": len(lines) - suspends, "suspend_lines": suspends,
                          "log": str(folder / "stalls.jsonl")}))
        return 0 if status else 1
    if args.command == "start":
        held = single_instance(folder)
        if held is None:  # a probe is running: don't spawn a process that would only exit 2
            print("ALREADY_RUNNING")
            return 0
        held.close()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        cmd = [str(pythonw if pythonw.exists() else sys.executable), str(Path(__file__).resolve()), "run",
               "--threshold", str(args.threshold)] + (["--state-dir", args.state_dir] if args.state_dir else [])
        # Outlive the starting session's job when it allows breakaway (CREATE_BREAKAWAY_FROM_JOB), else plain.
        for extra in (subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000, subprocess.CREATE_NEW_PROCESS_GROUP):
            try:
                proc = subprocess.Popen(cmd, creationflags=NO_WINDOW | extra, close_fds=True, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                continue
            print(f"STARTED pid={proc.pid} breakaway={bool(extra & 0x01000000)} log={folder / 'stalls.jsonl'}")
            return 0
        print("ERROR: the probe could not be started", file=sys.stderr)
        return 1
    lock = single_instance(folder)
    if lock is None:
        print("ALREADY_RUNNING")
        return 2
    Probe(Log(folder), threshold=args.threshold).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
