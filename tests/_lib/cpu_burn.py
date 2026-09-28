"""Run a command under a CPU burn at below-normal priority (fleet-config#1076).

The "red under load on `main`, green with the fix" proof (#1056, #1069) used
to hand-roll its burners at normal priority. 24 of them on the 16-core box
starved a scheduled life-os job that ran at the same time: it got 0.4 CPU
seconds in 5 minutes, and its watchdog killed it. This helper runs the burners
*and* the command under proof at ``BELOW_NORMAL_PRIORITY_CLASS``. The two
still contend with each other, which is what the proof needs, while scheduled
jobs and live sessions at normal priority preempt both. The command's
descendants inherit the class: Windows gives a child its parent's class when
that parent is below normal and the spawn names no class of its own.

Usage (from the repo root, through this repo's venv):

    python tests/_lib/cpu_burn.py [--burners N] [--runs K] [--over-cores]
                                  [--max-minutes M] -- <command> [args...]

``--burners`` defaults to the core count, and more than that is refused
unless ``--over-cores`` says the extra load is needed to reproduce. Each run's
exit code and wall time is printed, then ``BURN-RESULT: red R of K`` (exit 1
when any run was red, 0 when all were green, 2 on a usage error).

A below-normal burn is gentler on the command than the old normal-priority
one, because the system services a process start waits on are no longer
starved. On the 16-core box, 24 burners reproduced neither the #1056 nor the
#1069 red; 64 (``--over-cores``) reproduced both, 3 of 3, while the fixed code
stayed green. Normal-priority probes ran at their idle times throughout, so
oversubscribing below normal priority costs the rest of the machine nothing.

Cleanup is scoped to the processes this helper started. The burners are
stopped in a ``finally``, and each one also exits by itself when its stdin
pipe from this helper closes (so a hard-killed helper takes its burners with
it) or after ``--max-minutes``, whichever comes first. Nothing here looks up
processes by name, and nothing else is ever signalled.

stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Iterator, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

# Windows-only constant; POSIX lowers the niceness instead (see _lower_priority).
BELOW_NORMAL = (subprocess.BELOW_NORMAL_PRIORITY_CLASS
                if sys.platform == "win32" else 0)
POSIX_NICENESS = 10
DEFAULT_MAX_MINUTES = 120.0

# A busy loop plus a watcher thread: a blocking read of the helper's stdin pipe
# returns at EOF, which is when the helper exits for any reason, and ends the
# burner. The deadline is a second bound for a pipe that somehow stays open.
_BURNER_CODE = (
    "import os, sys, threading, time\n"
    "def _watch():\n"
    "    sys.stdin.buffer.read()\n"
    "    os._exit(0)\n"
    "threading.Thread(target=_watch, daemon=True).start()\n"
    "end = time.monotonic() + float(sys.argv[1])\n"
    "while time.monotonic() < end:\n"
    "    pass\n"
)


def core_count() -> int:
    return os.cpu_count() or 1


def _lower_priority() -> None:
    os.nice(POSIX_NICENESS)


def below_normal_kwargs() -> dict:
    """Popen kwargs that start a process below normal priority, windowless."""
    if sys.platform == "win32":
        return {"creationflags": NO_WINDOW | BELOW_NORMAL}
    return {"preexec_fn": _lower_priority}


def start_burners(count: int, max_minutes: float = DEFAULT_MAX_MINUTES
                  ) -> List[subprocess.Popen]:
    """Start ``count`` busy-loop burners below normal priority."""
    procs: List[subprocess.Popen] = []
    try:
        for _ in range(count):
            procs.append(subprocess.Popen(
                [sys.executable, "-c", _BURNER_CODE, str(max_minutes * 60)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **below_normal_kwargs()))
    except BaseException:
        stop_burners(procs)
        raise
    return procs


def stop_burners(procs: Sequence[subprocess.Popen], timeout: float = 10.0) -> None:
    """Stop exactly the burners in ``procs``: close each pipe, then terminate."""
    for proc in procs:
        with contextlib.suppress(OSError):
            if proc.stdin is not None:
                proc.stdin.close()
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
    deadline = time.monotonic() + timeout
    for proc in procs:
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextlib.contextmanager
def burning(count: int, max_minutes: float = DEFAULT_MAX_MINUTES
            ) -> Iterator[List[subprocess.Popen]]:
    """Hold ``count`` below-normal burners for the body, stopping them after."""
    procs = start_burners(count, max_minutes)
    try:
        yield procs
    finally:
        stop_burners(procs)


def _own_stdio() -> dict:
    """This process's stdout/stderr as explicit handles for a child.

    A windowless child given no std handles writes to its own hidden console,
    so a proof run's output (the very lines saying why it went red) was lost.
    Passing the handles makes Windows hand them over. A stream with no file
    descriptor (a test's ``redirect_stdout``) leaves the default.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        return {"stdout": sys.stdout.fileno(), "stderr": sys.stderr.fileno()}
    except (AttributeError, OSError, ValueError):
        return {}


def run_below_normal(command: Sequence[str]) -> int:
    """Run ``command`` below normal priority, sharing this process's output."""
    return subprocess.run(list(command), **_own_stdio(),
                          **below_normal_kwargs()).returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="cpu_burn.py",
        description="Run a command under a below-normal-priority CPU burn.")
    parser.add_argument("--burners", type=int, default=core_count(),
                        help="burner processes (default: the core count)")
    parser.add_argument("--runs", type=int, default=1,
                        help="times to run the command under the burn")
    parser.add_argument("--over-cores", action="store_true",
                        help="allow more burners than cores")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                        help="burner self-exit deadline")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="-- then the command under proof")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given (put it after --)")
    # CreateProcess resolves neither a relative forward-slash path nor a .cmd
    # shim on PATH, so the helper resolves the program the way a shell would.
    program = shutil.which(command[0])
    if program is None:
        parser.error(f"command not found: {command[0]}")
    command = [program, *command[1:]]
    if args.burners < 1 or args.runs < 1 or args.max_minutes <= 0:
        parser.error("--burners, --runs and --max-minutes must be positive")
    cores = core_count()
    if args.burners > cores and not args.over_cores:
        parser.error(f"{args.burners} burners is more than the {cores} cores; "
                     f"pass --over-cores if the proof needs the extra load")

    print(f"ℹ️ burn: {args.burners} burner(s) on {cores} cores, below normal "
          f"priority; {args.runs} run(s) of: {' '.join(command)}", flush=True)
    if args.burners > cores:
        print(f"⚠️ burn: {args.burners} burners exceeds the core count "
              f"(--over-cores)", flush=True)
    red = 0
    with burning(args.burners, args.max_minutes):
        for run in range(1, args.runs + 1):
            started = time.monotonic()
            code = run_below_normal(command)
            red += code != 0
            verdict = "green" if code == 0 else "red"
            print(f"ℹ️ burn run {run}/{args.runs}: exit {code} ({verdict}) "
                  f"in {time.monotonic() - started:.1f}s", flush=True)
    print(f"BURN-RESULT: red {red} of {args.runs}", flush=True)
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
