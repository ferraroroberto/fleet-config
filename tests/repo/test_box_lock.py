"""box_lock.py: gates share, a burn runs alone, unknown is held, dead holders are reclaimed."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.environ["CLAUDE_HOOKS_STATE_DIR"] = tempfile.mkdtemp(prefix="box-lock-state-")
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import box_lock  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
TINY = 0.01  # minutes: long enough for a few polls, short enough for a suite


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            creationflags=NO_WINDOW)


def fake_marker(mode: str, pid: int, repo: str = "fake") -> Path:
    folder = box_lock.lock_dir()
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / f"{mode}.{pid}.fake{mode}.json"
    marker.write_text(f'{{"repo": "{repo}", "started": "now"}}', encoding="utf-8")
    return marker


# ---- an empty lock grants either mode, and release empties it again ----
check(box_lock.holders() == [], "no lock directory reads as no holders")
with box_lock.held(box_lock.EXCLUSIVE, "t", TINY) as marker:
    check(marker.exists(), "an exclusive hold writes its marker")
check(box_lock.holders() == [], "the marker is gone after the block")
try:
    with box_lock.held(box_lock.SHARED, "t", TINY):
        raise RuntimeError("gate blew up")
except RuntimeError:
    pass
check(box_lock.holders() == [], "the marker is released when the body raises")

# ---- a live gate: other gates share it, a burn is refused ----
gate = sleeper()
gate_marker = fake_marker(box_lock.SHARED, gate.pid, "fake-gate")
own, blockers = box_lock.try_acquire(box_lock.SHARED, "t")
check(own is not None, "a second gate shares the lock with a live gate")
box_lock.release(own)
own, blockers = box_lock.try_acquire(box_lock.EXCLUSIVE, "t")
check(own is None and [b["pid"] for b in blockers] == [gate.pid],
      f"a burn is refused while a gate holds the lock (blockers {blockers})")
check(len(list(box_lock.lock_dir().iterdir())) == 1,
      "a refused acquirer removes its own marker again")
try:
    with box_lock.held(box_lock.EXCLUSIVE, "t", TINY, poll_seconds=0.1, report=lambda _l: None):
        check(False, "a burn waits out its timeout behind a live gate")
except box_lock.BoxLockTimeout as exc:
    check("fake-gate" in str(exc) and str(gate.pid) in str(exc),
          f"the timeout names the holding gate ({exc})")

# ---- a gate that dies (crash, hard kill) frees the lock on the next look ----
gate.kill()
gate.wait()
own, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "t")
check(own is not None and not gate_marker.exists(),
      "a dead gate's marker is reclaimed and the burn proceeds")
box_lock.release(own)

# ---- a live burn: a gate waits, a second burn waits ----
burn = sleeper()
burn_marker = fake_marker(box_lock.EXCLUSIVE, burn.pid, "fake-burn")
for mode in box_lock.MODES:
    own, blockers = box_lock.try_acquire(mode, "t")
    check(own is None and blockers and blockers[0]["repo"] == "fake-burn",
          f"a {mode} is refused while a burn holds the lock")
burn.kill()
burn.wait()
check(box_lock.holders() == [] and not burn_marker.exists(),
      "a dead burn's marker is reclaimed")

# ---- unknown is held, never free ----
pending = fake_marker(box_lock.SHARED, 999_999_999)
own, blockers = box_lock.try_acquire(box_lock.EXCLUSIVE, "t", probe=lambda _pid: None)
check(own is None and blockers and blockers[0]["pid"] == 999_999_999,
      "a holder whose pid can't be probed counts as held")
check(pending.exists(), "an unknown holder's marker is never reclaimed")
pending.unlink()

not_a_dir = Path(tempfile.mkdtemp(prefix="box-lock-file-")) / "lock"
not_a_dir.write_text("", encoding="utf-8")
own, blockers = box_lock.try_acquire(box_lock.EXCLUSIVE, "t", folder=not_a_dir)
check(own is None and blockers is None, "an unusable lock directory is unknown, not free")
check(box_lock.holders(not_a_dir) is None, "holders() reports an unlistable lock as None")
try:
    with box_lock.held(box_lock.SHARED, "t", TINY, folder=not_a_dir, poll_seconds=0.1,
                       report=lambda _l: None):
        check(False, "a gate waits out its timeout on an unreadable lock")
except box_lock.BoxLockTimeout as exc:
    check("could not be read" in str(exc), f"the timeout says the state was unreadable ({exc})")

# ---- unrelated files in the lock directory are ignored ----
(box_lock.lock_dir() / "notes.txt").write_text("x", encoding="utf-8")
check(box_lock.holders() == [], "a non-marker file is not a holder")

# ---- CLI: run passes the command's exit code through, or 3 on timeout ----
cli = [sys.executable, box_lock.__file__, "run", "--mode", "shared", "--repo", "t",
       "--timeout-minutes", str(TINY), "--"]
done = subprocess.run([*cli, sys.executable, "-c", "print('gate-ran'); import sys; sys.exit(4)"],
                      capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW)
check(done.returncode == 4 and "gate-ran" in done.stdout,
      f"run passes the command's output and exit code through ({done.returncode}, {done.stdout!r})")
burn = sleeper()
fake_marker(box_lock.EXCLUSIVE, burn.pid, "fake-burn")
done = subprocess.run([*cli, sys.executable, "-c", "print('gate-ran')"],
                      capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW)
check(done.returncode == box_lock.TIMEOUT_EXIT and "gate-ran" not in done.stdout
      and "fake-burn" in done.stderr,
      f"run exits 3 without running the gate while a burn holds the lock ({done.returncode})")
with contextlib.redirect_stdout(io.StringIO()) as out:
    box_lock.main(["status"])
check("fake-burn" in out.getvalue(), "status lists the live holder")
burn.kill()
burn.wait()

_h.report_and_exit("test_box_lock")
