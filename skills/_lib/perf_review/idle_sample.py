"""The live idle sample of `/perf-sweep`: CPU seconds a minute and processes started a minute, per app.

A source check cannot see a new background loop or a process started on every pass; a running app shows it as CPU
spent and children spawned with nobody using it. The sample watches only the process tree rooted at the app's own
listener PIDs (the PIDs netstat reports for its port), so a busy neighbour never leaks into the figure. It reads the
process table and sends nothing to the app: no restart, no stop, no signal.

An app whose listener cannot be found, whose process table cannot be read, or that restarted mid-window has no
figure at all (`state: "unknown"` with the reason). A missing reading is never folded into zero.

The process table is injectable (`ProcRow` lists), so the tests drive a fake one with a frozen clock.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from fleet_core import hooks_state, runtime_files
from no_window import NO_WINDOW

POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
DEFAULT_WINDOW_S = 60.0
DEFAULT_INTERVAL_S = 3.0  # a child that lives under ~3 s can fall between two reads, so the count is a floor
TABLE_TIMEOUT_S = 30
# A rise is flagged when both hold, so a 0.1 -> 0.2 s/min wobble stays quiet: it grew by this factor AND by this
# much in absolute terms (CPU seconds a minute / processes started a minute).
RISE_FACTOR = 1.5
RISE_MIN_CPU_S = 1.0
RISE_MIN_PROCS = 1.0

_TABLE_PS = (
    "Get-CimInstance Win32_Process | ForEach-Object { "
    "[pscustomobject]@{ p = [int]$_.ProcessId; pp = [int]$_.ParentProcessId; "
    "c = ([DateTimeOffset]$_.CreationDate).ToUnixTimeMilliseconds() / 1000.0; "
    "t = ([double]$_.KernelModeTime + [double]$_.UserModeTime) / 1e7 } } | ConvertTo-Json -Compress"
)


class ProcRow(NamedTuple):
    pid: int
    ppid: int
    created: float  # epoch seconds
    cpu_s: float    # kernel + user CPU seconds used so far


def read_process_table() -> Optional[List[ProcRow]]:
    """Every running process, or None when the table cannot be read (never an empty list for a failed read)."""
    try:
        res = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _TABLE_PS], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=TABLE_TIMEOUT_S, creationflags=NO_WINDOW)
        data = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if isinstance(data, dict):
        data = [data]
    if not data:
        return None
    try:
        return [ProcRow(int(d["p"]), int(d["pp"]), float(d["c"]), float(d["t"])) for d in data]
    except (KeyError, TypeError, ValueError):
        return None


def listener_pids(port: int) -> Optional[Set[int]]:
    """PIDs listening on `port` from `netstat -ano -p tcp`; None when netstat cannot be read, empty when none listen."""
    try:
        res = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, encoding="oem", errors="replace",
                             timeout=TABLE_TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    pids: Set[int] = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or not parts[0].upper().startswith("TCP"):
            continue
        if parts[2] not in ("0.0.0.0:0", "[::]:0") and parts[3].upper() != "LISTENING":
            continue  # netstat localises the state word, so the wildcard foreign address is the other test
        if parts[1].rsplit(":", 1)[-1] == str(port) and parts[4].isdigit():
            pids.add(int(parts[4]))
    return pids


def tree(table: List[ProcRow], roots: Set[int]) -> Dict[Tuple[int, float], ProcRow]:
    """The roots and every descendant, keyed `(pid, created)` so a reused PID is a different process.

    A child whose creation time precedes its parent's names a reused parent PID, not a real descendant.
    """
    by_parent: Dict[int, List[ProcRow]] = {}
    for r in table:
        by_parent.setdefault(r.ppid, []).append(r)
    found = {(r.pid, r.created): r for r in table if r.pid in roots}
    frontier = list(found.values())
    while frontier:
        parent = frontier.pop()
        for child in by_parent.get(parent.pid, []):
            key = (child.pid, child.created)
            if key not in found and child.created >= parent.created:
                found[key] = child
                frontier.append(child)
    return found


def unknown(reason: str) -> dict:
    return {"state": "unknown", "reason": reason}


def sample(port: Optional[int], window_s: float = DEFAULT_WINDOW_S, interval_s: float = DEFAULT_INTERVAL_S,
           table_fn: Callable[[], Optional[List[ProcRow]]] = read_process_table,
           pids_fn: Callable[[int], Optional[Set[int]]] = listener_pids,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> dict:
    """`{"state": "measured", "cpu_s_min", "procs_min", "window_s", "procs"}` or `{"state": "unknown", "reason"}`.

    CPU is each tree member's growth between its first and last sighting (a process born inside the window counts
    from zero), summed. Processes started are the tree members created after the window opened, the roots excluded.
    """
    if not port:
        return unknown("no port declared")
    if window_s <= 0:
        return unknown("idle sample disabled")
    roots = pids_fn(port)
    if roots is None:
        return unknown("listener table unreadable")
    if not roots:
        return unknown("no listener on the port")
    t0 = clock()
    first_seen: Dict[Tuple[int, float], float] = {}
    last_seen: Dict[Tuple[int, float], float] = {}
    root_keys: Optional[Set[Tuple[int, float]]] = None
    t_last = t0
    while True:
        table = table_fn()
        if table is None:
            return unknown("process table unreadable")
        now = clock()
        members = tree(table, roots)
        if root_keys is None:
            root_keys = {k for k, r in members.items() if r.pid in roots}
            if not root_keys:
                return unknown("listener not in the process table")
        elif not root_keys <= members.keys():
            return unknown("app restarted during the sample")
        for key, r in members.items():
            first_seen.setdefault(key, r.cpu_s if r.created < t0 else 0.0)
            last_seen[key] = r.cpu_s
        t_last = now
        if now - t0 >= window_s:
            break
        sleep(interval_s)
    elapsed = t_last - t0
    if elapsed <= 0:
        return unknown("sample window had no duration")
    cpu = sum(max(0.0, last_seen[k] - first_seen[k]) for k in last_seen)
    started = sum(1 for k in last_seen if k not in root_keys and k[1] >= t0)
    scale = 60.0 / elapsed
    return {"state": "measured", "cpu_s_min": round(cpu * scale, 2), "procs_min": round(started * scale, 2),
            "window_s": round(elapsed, 1), "procs": len(last_seen)}


def rise(previous: Optional[dict], current: dict) -> List[str]:
    """`["cpu"]`, `["procs"]`, both, or `[]`: which idle figures rose materially since the last measured run.

    Either side unknown claims nothing, so a missing reading is never read as a drop to zero or a jump from it.
    """
    if not previous or previous.get("state", "measured") != "measured" or current.get("state") != "measured":
        return []
    out = []
    for name, key, floor in (("cpu", "cpu_s_min", RISE_MIN_CPU_S), ("procs", "procs_min", RISE_MIN_PROCS)):
        before, now = previous.get(key), current.get(key)
        if before is not None and now is not None and now >= before * RISE_FACTOR and now - before >= floor:
            out.append(name)
    return out


def ledger_path() -> Path:
    return hooks_state.state_dir() / "perf-review" / "idle.jsonl"


def previous_sample(repo: str, path: Optional[Path] = None) -> Optional[dict]:
    """The latest measured record for `repo`; a torn or foreign line is skipped."""
    try:
        lines = (path or ledger_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("repo") == repo and rec.get("state") == "measured":
            return rec
    return None


def record(repo: str, result: dict, when: str, path: Optional[Path] = None) -> None:
    """Append a measured sample to the ledger; an unknown one is not stored, so it can never become a baseline."""
    if result.get("state") != "measured":
        return
    target = path or ledger_path()
    runtime_files.make_dirs(target.parent, parents=True, exist_ok=True)
    with runtime_files.open_for_write(target, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"repo": repo, "ts": when, **result}, separators=(",", ":")) + "\n")
