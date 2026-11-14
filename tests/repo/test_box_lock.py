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
check("UNKNOWN box_load_lock" in done.stderr and "not a failure" in done.stderr,
      f"a lock timeout is reported as UNKNOWN, not as the command failing ({done.stderr!r})")
with contextlib.redirect_stdout(io.StringIO()) as out:
    box_lock.main(["status"])
check("fake-burn" in out.getvalue(), "status lists the live holder")
burn.kill()
burn.wait()


# ---- fairness: a waiting gate is not starved by a holder that re-acquires ----
class FakeClock:
    """A clock that only moves when the waiter sleeps, so a 30-minute wait takes no time."""

    def __init__(self, on_sleep=None) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self.sleeps)


def drop(marker) -> None:
    """Release a marker the test may not have been granted (the red run must keep going)."""
    if marker is not None:
        box_lock.release(marker)


def fresh_folder() -> Path:
    return Path(tempfile.mkdtemp(prefix="box-lock-fair-"))


def tickets(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(box_lock.QUEUE + "."))


quiet = lambda _line: None  # noqa: E731
folder = fresh_folder()
sweep_app, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "perf-sweep", folder)
regrab = {}


def sweep_between_apps(sleeps: int) -> None:
    """The sweep finishes one app and asks for the next one straight away."""
    if sleeps != 1:
        return
    box_lock.release(sweep_app)
    regrab["marker"], regrab["blockers"] = box_lock.try_acquire(box_lock.EXCLUSIVE, "perf-sweep", folder)


clock = FakeClock(sweep_between_apps)
try:
    with box_lock.held(box_lock.SHARED, "lane-gate", 30, folder=folder, poll_seconds=5, report=quiet,
                       clock=clock, sleep=clock.sleep) as gate_marker:
        check(regrab.get("marker") is None,
              "the sweep re-asking between apps is refused while a gate is queued")
        check(any("lane-gate" in str(b.get("repo")) for b in regrab.get("blockers") or []),
              f"the refusal names the queued gate ({regrab.get('blockers')})")
        check(clock.now < 60, f"the gate gets the gap after one poll, not after its timeout ({clock.now:.0f}s)")
        check(tickets(folder) == [], "the gate's ticket is gone once it holds the lock")
        check(box_lock.try_acquire(box_lock.EXCLUSIVE, "perf-sweep", folder)[0] is None,
              "the sweep waits for the gate that took the gap")
except box_lock.BoxLockTimeout as exc:
    check(False, f"a gate behind a multi-app sweep acquires between apps, it timed out instead ({exc})")
sweep_next, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "perf-sweep", folder)
check(sweep_next is not None, "the sweep resumes once the gate is done")
if sweep_next is not None:
    drop(sweep_next)
if regrab.get("marker") is not None:
    box_lock.release(regrab["marker"])

# ---- a queued burn is served in arrival order, and no newcomer jumps it ----
folder = fresh_folder()
holder_gate, _ = box_lock.try_acquire(box_lock.SHARED, "lane-gate", folder)
first = box_lock.enqueue(box_lock.EXCLUSIVE, "burn-one", folder, now_ns=lambda: 1_000)
second = box_lock.enqueue(box_lock.EXCLUSIVE, "burn-two", folder, now_ns=lambda: 2_000)
box_lock.release(holder_gate)
own, blockers = box_lock.try_acquire(box_lock.EXCLUSIVE, "burn-two", folder, ticket=second)
check(own is None and [b["repo"] for b in blockers] == ["burn-one"],
      f"the younger queued burn defers to the older one ({blockers})")
own, blockers = box_lock.try_acquire(box_lock.SHARED, "late-gate", folder)
check(own is None and [b["repo"] for b in blockers] == ["burn-one", "burn-two"],
      f"a newcomer gate does not overtake queued burns ({blockers})")
own, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "burn-one", folder, ticket=first)
check(own is not None, "the older queued burn takes the free lock")
drop(own)
box_lock.dequeue(first, folder)
own, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "burn-two", folder, ticket=second)
check(own is not None, "the next burn goes once the first has left the queue")
drop(own)
box_lock.dequeue(second, folder)

# ---- gates queued behind a burn do not block each other ----
folder = fresh_folder()
queued_gate = box_lock.enqueue(box_lock.SHARED, "gate-one", folder, now_ns=lambda: 1_000)
own, _ = box_lock.try_acquire(box_lock.SHARED, "gate-two", folder)
check(own is not None, "a queued gate does not hold back another gate")
drop(own)
box_lock.dequeue(queued_gate, folder)

# ---- a dead waiter loses its place; an unsettled one keeps it ----
folder = fresh_folder()
waiter = sleeper()
name = f"{box_lock.QUEUE}.{box_lock.SHARED}.{waiter.pid}.5.deadwaiter.json"
(folder / name).write_text('{"repo": "ghost"}', encoding="utf-8")
own, blockers = box_lock.try_acquire(box_lock.EXCLUSIVE, "t", folder, probe=lambda _pid: None)
check(own is None and blockers and blockers[0]["repo"] == "ghost" and (folder / name).exists(),
      "a waiter whose pid cannot be probed keeps its place in the queue")
waiter.kill()
waiter.wait()
own, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "t", folder)
check(own is not None and not (folder / name).exists(), "a dead waiter's ticket is reclaimed")
drop(own)

# ---- a waiter that gives up leaves no ticket behind ----
folder = fresh_folder()
holder_burn, _ = box_lock.try_acquire(box_lock.EXCLUSIVE, "perf-sweep", folder)
clock = FakeClock()
try:
    with box_lock.held(box_lock.SHARED, "lane-gate", 30, folder=folder, poll_seconds=5, report=quiet,
                       clock=clock, sleep=clock.sleep):
        check(False, "a gate waits out its timeout behind a sweep that never yields")
except box_lock.BoxLockTimeout as exc:
    check(clock.now >= 30 * 60 and "perf-sweep" in str(exc),
          f"the timeout comes at the fake deadline and names the holder ({exc})")
check(tickets(folder) == [], "a timed-out waiter removes its ticket")
drop(holder_burn)

# ---- the acceptance run reports a lock timeout as UNKNOWN (exit 3), not a failed check ----
sys.path.insert(0, str(ROOT / "tests"))
import run_acceptance  # noqa: E402


@contextlib.contextmanager
def never_free(*_args, **_kwargs):
    raise box_lock.BoxLockTimeout("box load lock: no gate slot after 30 min -- held by burn pid 1 (perf-sweep)")
    yield  # pragma: no cover


real_held = box_lock.held
box_lock.held = never_free
try:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = run_acceptance.main()
finally:
    box_lock.held = real_held
check(code == box_lock.TIMEOUT_EXIT and "UNKNOWN" in out.getvalue() and "FAIL" not in out.getvalue(),
      f"a lock timeout is exit 3 with an UNKNOWN line, never a FAIL ({code}, {out.getvalue()!r})")

_h.report_and_exit("test_box_lock")
