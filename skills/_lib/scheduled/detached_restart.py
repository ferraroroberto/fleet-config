"""Run a repo's restart recipe so the app it starts outlives a scheduled run (stdlib only).

A scheduled run owns every descendant in a kill-on-close Job Object. A tray a lane
restarts on purpose lands in that job, so the run either ends 118 (owned descendants
still running) or the teardown kills the app it just restarted. The runner's job
allows breakaway, and this helper starts the recipe with CREATE_BREAKAWAY_FROM_JOB:
the recipe and everything it starts, the tray included, are then outside the job.
The runner keeps owning every other descendant, so a genuine leak still scores 118.

    detached_restart.py [--cwd DIR] [--timeout SECONDS] -- <recipe argv...>

e.g. `-- C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "& './tray.bat' --restart"`.

The recipe runs to completion in the foreground and its output and exit code are
passed through; its output goes to a file, not a pipe, so a tray that keeps the
handle open cannot hold this process. Prints one `BREAKAWAY=yes|no` line: `no` means
the surrounding job refused breakaway and the recipe ran as an ordinary descendant
(the app will not outlive a scheduled run). Exit 124 = the recipe was still running
at the timeout (not confirmed, never killed mid-restart).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from no_window import NO_WINDOW

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DEFAULT_TIMEOUT_SECONDS = 300.0
TIMEOUT_EXIT = 124


def _spawn(argv: list[str], cwd: Optional[str], out) -> tuple[subprocess.Popen, bool]:
    """Start the recipe, outside the surrounding job when that job allows it."""
    if sys.platform != "win32":
        return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW, start_new_session=True), True
    # A new process group keeps a Ctrl+C aimed at the lane away from the tray. The first
    # attempt fails with an OSError when the job forbids breakaway; the second still runs it.
    for breakaway in (True, False):
        extra = subprocess.CREATE_NEW_PROCESS_GROUP | (CREATE_BREAKAWAY_FROM_JOB if breakaway else 0)
        try:
            return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, creationflags=NO_WINDOW | extra), breakaway
        except OSError:
            if not breakaway:
                raise
    raise AssertionError("unreachable")


def run(argv: list[str], cwd: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    with tempfile.TemporaryFile() as out:
        proc, breakaway = _spawn(argv, cwd, out)
        print(f"BREAKAWAY={'yes' if breakaway else 'no'}", flush=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = TIMEOUT_EXIT
            print(f"❌ restart recipe still running after {timeout:.0f}s (pid {proc.pid}); not confirmed", flush=True)
        out.seek(0)
        sys.stdout.write(out.read().decode("utf-8", errors="replace"))
    return code


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cwd", help="working directory for the recipe (the repo root)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("recipe", nargs=argparse.REMAINDER, help="-- then the recipe command")
    args = parser.parse_args(argv)
    recipe = args.recipe[1:] if args.recipe[:1] == ["--"] else args.recipe
    if not recipe:
        parser.error("a recipe command is required after --")
    return run(recipe, args.cwd, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
