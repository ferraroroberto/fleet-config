"""Generic "is any live process holding this directory?" probe (fleet-config#571).

Why this exists
----------------
`/cleanup-fleet-all`'s teardown may treat a leftover `<repo>-wt-<N>` directory
as inert rather than as residue, but only after proving five conditions, one of
which is "no live holder". The only sanctioned proof used to be a repo's own
`tests/e2e/_browser_sweep.py` — which exists in **4 of 14 fleet repos**. In the
other ten the condition was unprovable by construction, so any leftover
directory was guaranteed RESIDUE and guaranteed to halt the run, whatever was
actually in it. The exception written specifically to stop inert shells halting
runs only worked in the repos that happen to ship a Playwright sweeper.

The conflation was between a *generic* question — is anything alive holding
this path? — and a *Playwright-specific* instrument. This module answers the
generic question, from fleet-config's own venv, needing nothing at all from the
target repo: no `tests/e2e/`, no Playwright, no venv. `_browser_sweep.py`
remains the better instrument for classifying leaked browser helpers where it
exists; its absence is no longer evidence of anything.

What counts as a holder
-----------------------
A live process whose **command line** or **executable path** names the
directory (or anything inside it). That catches the two real shapes: a process
launched from a script inside the worktree, and a process running the
worktree's own `.venv\\Scripts\\python.exe` — the 2026-08-06 `local-llm-hub`
case, two `src.tts_server` processes started from the worktree's interpreter.

Windows' process table carries no working directory, so a process merely `cd`-ed
into the path with nothing else naming it is invisible here. That limit is
stated rather than papered over: this probe answers "no *nameable* holder", and
an unprovable probe reports `UNKNOWN`, never `CLEAR`.

The probe's own process tree is excluded — the path is on this script's command
line by definition, and on its PowerShell child's. Ancestors are excluded too:
the shell that invoked the probe cannot be an independent holder of the
directory it was asked about.

Subcommand
----------
  check <path>
    Prints:
      STATUS=CLEAR|LIVE|UNKNOWN
      LIVE=<count>
      HOLDER=<pid> <exe> :: <command line>   (one line per live holder)
      REASON=<text>   (only when STATUS=UNKNOWN)

Always exits 0 — this reports, it never blocks. `UNKNOWN` is its own state and
must never be folded into `CLEAR`: a probe that could not run leaves the
directory residue, exactly as before. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# `$PID` is the probe's own PowerShell child; the caller pairs it with this
# process's ancestors to exclude the whole probing tree.
_PS_SCRIPT = (
    "$ErrorActionPreference='Stop'; "
    "$p = Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine; "
    "[Console]::Out.Write((ConvertTo-Json @{self=$PID; processes=@($p)} -Depth 4 -Compress))"
)


class Probe(NamedTuple):
    status: str  # "CLEAR" | "LIVE" | "UNKNOWN"
    holders: list[dict]
    reason: Optional[str]


def normalize(path: str) -> str:
    """Windows path comparison key: separator- and case-insensitive.

    Command lines quote paths inconsistently and mix `/` with `\\`, so both
    sides are folded to one shape before matching.
    """
    return str(path).replace("/", "\\").rstrip("\\").lower()


def names_path(text: Optional[str], key: str) -> bool:
    """True when `text` mentions `key` — the directory or anything inside it."""
    return bool(text) and key in normalize(text)


def ancestors(pid: int, processes: Iterable[dict]) -> set[int]:
    """`pid` plus every process above it, so the probing tree excludes itself.

    Cycle-safe: a malformed parent chain (or pid 0 pointing at itself) stops at
    the first pid already seen rather than spinning.
    """
    parent = {int(p["ProcessId"]): int(p.get("ParentProcessId") or 0)
              for p in processes if p.get("ProcessId") is not None}
    seen: set[int] = set()
    cur = pid
    while cur and cur not in seen:
        seen.add(cur)
        cur = parent.get(cur, 0)
    return seen


def holders_for(path: str, processes: Iterable[dict], exclude: Iterable[int] = ()) -> list[dict]:
    """Live processes naming `path`, minus the excluded pids. Pure."""
    key = normalize(path)
    skip = set(exclude)
    found = []
    for p in processes:
        try:
            pid = int(p["ProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid in skip:
            continue
        if names_path(p.get("CommandLine"), key) or names_path(p.get("ExecutablePath"), key):
            found.append({
                "pid": pid,
                "name": p.get("Name") or "",
                "exe": p.get("ExecutablePath") or "",
                "cmdline": (p.get("CommandLine") or "").strip(),
            })
    return sorted(found, key=lambda h: h["pid"])


def _query() -> tuple[Optional[list[dict]], Optional[int], Optional[str]]:
    """(processes, powershell pid, error) — never raises."""
    if sys.platform != "win32":
        return None, None, f"process table probe is Windows-only (running on {sys.platform})"
    try:
        r = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _PS_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, f"could not run the process-table query: {exc}"
    if r.returncode != 0:
        first = (r.stderr or "").strip().splitlines()
        return None, None, f"process-table query failed (exit {r.returncode})" + (f": {first[0]}" if first else "")
    try:
        payload = json.loads(r.stdout or "")
    except ValueError as exc:
        return None, None, f"process-table query returned unreadable output: {exc}"
    procs = payload.get("processes")
    if not isinstance(procs, list):
        return None, None, "process-table query returned no process list"
    return procs, payload.get("self"), None


def probe(path: str) -> Probe:
    """Classify `path`: LIVE (nameable holders), CLEAR (none), UNKNOWN (couldn't ask)."""
    processes, ps_pid, err = _query()
    if err is not None:
        return Probe("UNKNOWN", [], err)
    exclude = ancestors(os.getpid(), processes)
    if ps_pid:
        exclude.add(int(ps_pid))
    holders = holders_for(path, processes, exclude)
    return Probe("LIVE" if holders else "CLEAR", holders, None)


def cmd_check(path: str) -> None:
    result = probe(path)
    print(f"STATUS={result.status}")
    print(f"LIVE={len(result.holders)}")
    for h in result.holders:
        print(f"HOLDER={h['pid']} {h['exe'] or h['name']} :: {h['cmdline']}")
    if result.reason:
        print(f"REASON={result.reason}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generic live-holder probe for a directory.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("path")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        cmd_check(args.path)


if __name__ == "__main__":
    main()
