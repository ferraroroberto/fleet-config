"""Bounded self-relaunch ("dead-man's switch") for the unattended /audit-fleet run.

Why this exists
---------------
The weekly `/audit-fleet` job runs headless via `claude -p`. A full fleet sweep
(the orchestrator's own Opus turns plus the 3-wide window of sub-agents) can
exhaust the rolling **5-hour session rate limit** mid-run — when that happens the
process either has its sub-agents 429 or dies outright, and the rest of the fleet
is silently dropped until next week (fleet-config#222).

There is **no in-process way to read the live session %**: Claude Code feeds
`rate_limits.five_hour.used_percentage` to the statusline via stdin JSON only at
TUI render time, never persists it to disk, and the statusline does not render in
headless `claude -p`. So a "check my % and wait" gate is impossible in the exact
run that matters. Instead we lean on two things the skill already has — it is
**idempotent** (every audited repo updates its per-repo ledger, so a re-run skips
done repos for free) — and a **dead-man's switch**:

  * The skill *arms* a one-shot Windows scheduled task ~N hours out at the START
    of the heavy phase (before any sub-agent), via ``arm``. If the process dies
    of a session limit, the task still fires and re-launches the audit as a
    ``resume`` continuation, which the ledger gate resumes where it left off.
  * A clean finish *disarms* it via ``clear``.

A retry-count guard caps the chain so a persistently-limited window can't loop
forever; ``clear`` resets the chain on any clean completion. ~N hours (default 4)
is long enough for the rolling 5h window to recover meaningfully; if a retry
still lands too early it simply re-arms and the staircase converges within the
cap.

This is the same design principle as ``audit_issue.py`` / ``notify_complete.py``:
the correctness-critical, idempotency-critical decision lives in Python, not in
the model.

Subcommands
-----------
  arm    --hours H --max M [--state P] [--task T] [--bat B] [--dry-run]
         Count this launch; if it is not the final allowed attempt, register the
         one-shot relaunch task. Prints ``ATTEMPT=n/M`` and ``ARMED=yes|no``.

  clear  [--state P] [--task T] [--dry-run]
         Unregister the relaunch task and zero the chain. Idempotent — safe to
         call when nothing is armed. Prints ``CLEARED``.

  status [--state P]
         Prints ``ATTEMPTS=n``.

The scheduling itself is the only OS side effect; ``--dry-run`` prints the
PowerShell command instead of running it, so the pure attempt-counting state
machine (``decide`` / ``load_state`` / ``save_state``) is unit-testable with a
temp ``--state`` file and no Task Scheduler writes.

stdlib only; the OS call shells out to the absolute Windows PowerShell 5.1 path
(``pwsh`` on PATH is a 0-byte WindowsApps stub that fails non-interactively).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

# Absolute Windows PowerShell 5.1 — never bare `pwsh` (a WindowsApps reparse stub
# that fails when spawned non-interactively). Forward slashes are fine for exec.
POWERSHELL = r"C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

DEFAULT_TASK = "ClaudeAuditFleetRetry"
DEFAULT_BAT = r"E:\automation\fleet-config\.claude\skills\audit-fleet\run-weekly.bat"
DEFAULT_STATE = Path.home() / ".claude" / "audit-fleet-retry.json"


# ---- pure state machine (unit-tested without Task Scheduler) ---------------

def decide(attempts_before: int, max_attempts: int) -> tuple[int, bool]:
    """Given prior launch count, return (attempts_after, armed).

    ``attempts_after`` counts THIS launch. ``armed`` is True when this is not the
    final allowed attempt, i.e. a retry should be scheduled. With ``max=3``:
    launch 1 -> (1, True), launch 2 -> (2, True), launch 3 -> (3, False) — three
    launches, two retries.
    """
    attempts_after = attempts_before + 1
    return attempts_after, attempts_after < max_attempts


def load_state(path: Path) -> dict:
    """Read the chain state; a missing/corrupt file is an empty (fresh) chain."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---- scheduled-task plumbing (the only OS side effect) ---------------------

def _ps(command: str, dry_run: bool) -> None:
    """Run a PowerShell one-liner. Degrade-don't-block: a scheduler failure warns
    on stderr but never raises — the unattended run must not abort over it."""
    if dry_run:
        print("PSCMD=" + command)
        return
    try:
        r = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            sys.stderr.write(f"WARN audit_retry: scheduler call failed (exit {r.returncode}): "
                             f"{(r.stderr or '').strip()}\n")
    except OSError as exc:  # powershell.exe missing / unspawnable
        sys.stderr.write(f"WARN audit_retry: could not run scheduler: {exc}\n")


def register_task(hours: float, task: str, bat: str, dry_run: bool) -> None:
    # New-ScheduledTaskTrigger computes the fire time in PowerShell ((Get-Date).AddHours)
    # so we sidestep schtasks' locale-dependent /SD /ST date parsing entirely. The
    # action re-runs the weekly wrapper with `resume`, marking the relaunch as a
    # continuation (the skill keeps counting the chain instead of resetting it).
    command = (
        f"$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c \"{bat}\" resume'; "
        f"$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours({hours}); "
        f"Register-ScheduledTask -TaskName '{task}' -Action $a -Trigger $t -Force | Out-Null"
    )
    _ps(command, dry_run)


def unregister_task(task: str, dry_run: bool) -> None:
    command = f"Unregister-ScheduledTask -TaskName '{task}' -Confirm:$false -ErrorAction SilentlyContinue"
    _ps(command, dry_run)


# ---- subcommands -----------------------------------------------------------

def cmd_arm(hours: float, max_attempts: int, state_path: Path, task: str, bat: str,
            dry_run: bool) -> None:
    state = load_state(state_path)
    attempts, armed = decide(int(state.get("attempts", 0)), max_attempts)
    state["attempts"] = attempts
    if armed:
        register_task(hours, task, bat, dry_run)
        state["scheduled_at"] = (_dt.datetime.now() + _dt.timedelta(hours=hours)).isoformat(timespec="seconds")
        state["task"] = task
    else:
        # Final attempt: no further retry. Drop any stale scheduling metadata.
        state.pop("scheduled_at", None)
    save_state(state_path, state)
    print(f"ATTEMPT={attempts}/{max_attempts}")
    print(f"ARMED={'yes' if armed else 'no'}")


def cmd_clear(state_path: Path, task: str, dry_run: bool) -> None:
    unregister_task(task, dry_run)
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        sys.stderr.write(f"WARN audit_retry: could not remove state file: {exc}\n")
    print("CLEARED")


def cmd_status(state_path: Path) -> None:
    print(f"ATTEMPTS={int(load_state(state_path).get('attempts', 0))}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Bounded self-relaunch for /audit-fleet.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("arm")
    a.add_argument("--hours", type=float, default=4.0)
    a.add_argument("--max", type=int, default=3, dest="max_attempts")
    a.add_argument("--state", type=Path, default=DEFAULT_STATE)
    a.add_argument("--task", default=DEFAULT_TASK)
    a.add_argument("--bat", default=DEFAULT_BAT)
    a.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("clear")
    c.add_argument("--state", type=Path, default=DEFAULT_STATE)
    c.add_argument("--task", default=DEFAULT_TASK)
    c.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("status")
    s.add_argument("--state", type=Path, default=DEFAULT_STATE)

    args = ap.parse_args(argv)
    if args.cmd == "arm":
        cmd_arm(args.hours, args.max_attempts, args.state, args.task, args.bat, args.dry_run)
    elif args.cmd == "clear":
        cmd_clear(args.state, args.task, args.dry_run)
    elif args.cmd == "status":
        cmd_status(args.state)


if __name__ == "__main__":
    main()
