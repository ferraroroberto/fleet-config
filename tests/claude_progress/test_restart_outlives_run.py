"""A tray a lane restarts inside a scheduled run outlives the run and is not an owned descendant.

A fake recipe stands in for `tray.bat --restart`: it starts a long-lived child and exits. Run plainly under a
real `ProcessScope` the child is an owned descendant (the 118 / killed-with-the-run case); run through
`detached_restart.py` it is outside the job, still alive after the scope closes. No real tray, no burn.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_restart_outlives_run.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
from scheduled import detached_restart, process_scope

HELPER = ROOT / "skills" / "_lib" / "scheduled" / "detached_restart.py"
STILL_ACTIVE = 259
# The fake recipe: start a long-lived child with its own stdio (as the tray does), record its pid, exit 0.
RECIPE = (
    "import subprocess, sys\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
    "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "print('recipe ran')\n"
)


def _alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


@unittest.skipUnless(sys.platform == "win32", "the owned scope is a Windows Job Object")
class RestartOutlivesRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.recipe = Path(self.tmp.name) / "recipe.py"
        self.recipe.write_text(RECIPE, encoding="utf-8")
        self.pid_file = Path(self.tmp.name) / "child.pid"
        self.child_pids: list[int] = []
        self.addCleanup(self._reap_children)

    def _reap_children(self):
        for pid in self.child_pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)

    def _run_in_scope(self, command: list[str]):
        scope = process_scope.ProcessScope()
        self.addCleanup(scope.close)
        root = scope.launch(command)
        self.assertEqual(root.wait(timeout=60), 0, root.stderr.read().decode(errors="replace"))
        # The recipe writes the pid before it exits, so a clean root exit means the file exists; the bound only
        # absorbs filesystem lag under box load, and a miss reads as a named failure, not a FileNotFoundError.
        deadline = time.monotonic() + 30
        while not self.pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.pid_file.exists(), "the recipe exited 0 without recording its child's pid")
        child = int(self.pid_file.read_text())
        self.child_pids.append(child)
        return scope, child

    def test_plain_recipe_child_is_an_owned_descendant(self):
        """The control: the pre-fix shape. The child is in the job, so the run counts it and closing the job kills it."""
        scope, child = self._run_in_scope([sys.executable, str(self.recipe), str(self.pid_file)])
        self.assertTrue(_alive(child))
        self.assertGreaterEqual(scope.active(), 1, "an owned descendant is what turns a delivered run into 118")
        scope.close()
        deadline = time.monotonic() + 10
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(_alive(child), "kill-on-close takes the app down with the run")

    def test_recipe_through_the_helper_outlives_the_scope(self):
        scope, child = self._run_in_scope(
            [sys.executable, str(HELPER), "--", sys.executable, str(self.recipe), str(self.pid_file)])
        self.assertTrue(_alive(child))
        # The job's count settles a beat after the root's exit under box load; a real leak stays.
        deadline = time.monotonic() + 10
        while scope.active() and time.monotonic() < deadline:
            time.sleep(0.1)
        members = [(pid, scope.job.describe_process(pid)) for pid in scope.job.process_ids() or []]
        self.assertEqual(scope.active(), 0, f"no owned descendant, so the run cannot end 118 for it: {members}")
        scope.close()
        time.sleep(0.5)
        self.assertTrue(_alive(child), "the restarted app keeps serving after the run's job is gone")


class HelperContractTests(unittest.TestCase):
    def test_recipe_exit_code_and_output_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fail.py"
            script.write_text("print('verification failed'); raise SystemExit(7)\n", encoding="utf-8")
            done = subprocess.run([sys.executable, str(HELPER), "--", sys.executable, str(script)],
                                  capture_output=True, text=True, timeout=60,
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.assertEqual(done.returncode, 7)
        self.assertIn("verification failed", done.stdout)
        self.assertIn("BREAKAWAY=", done.stdout)

    def test_a_recipe_past_the_timeout_is_reported_not_killed(self):
        # `-c`, not a script file: a recipe still starting up at the 0.5 s timeout holds its script open, and the
        # temp-dir cleanup then fails on Windows (PermissionError) under load. No file, nothing to hold.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = detached_restart.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertEqual(code, detached_restart.TIMEOUT_EXIT)
        # The helper never kills a recipe it reports as running; the test reaps it so no sleeper outlives the run.
        pid = re.search(r"\(pid (\d+)\)", out.getvalue())
        self.assertIsNotNone(pid, out.getvalue())
        # SIGTERM is TerminateProcess on Windows: it reaps without a spawn, which the unit's spawn budget counts.
        with contextlib.suppress(OSError):
            os.kill(int(pid.group(1)), signal.SIGTERM)

    def test_empty_recipe_is_refused(self):
        with self.assertRaises(SystemExit):
            detached_restart.main(["--"])


if __name__ == "__main__":
    unittest.main()
