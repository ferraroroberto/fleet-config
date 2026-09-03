"""Unit tests for the active-issue marker helper and workflow wiring."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import active_issue as ai  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def _row(repo: str, number: int, age_hours: int = 0) -> dict:
    stamp = NOW - timedelta(hours=age_hours)
    return {
        "repo": repo,
        "number": number,
        "branch": f"feat/{number}-x",
        "started_at": ai._iso_z(stamp),
    }


tmp = Path(tempfile.mkdtemp(prefix="active_issue_"))
try:
    target = tmp / ai.STATE_FILENAME

    check(ai.read_rows(target) == {}, "missing state file -> empty mapping")
    target.write_text("{not json", encoding="utf-8")
    check(ai.read_rows(target) == {}, "corrupt state file -> empty mapping")

    original_resolver = ai.resolve_repo_name
    ai.resolve_repo_name = lambda repo: Path(repo).name.replace("-wt-7", "")
    try:
        first = ai.add_marker(tmp / "app-launcher", 528, "feat/528-active", now=NOW, path=target)
        check(first["repo"] == "app-launcher", "add: canonical repo recorded")
        check(set(ai.read_rows(target)) == {"app-launcher#528"}, "add: keyed by repo#number")

        ai.add_marker(tmp / "photo-ocr-wt-7", 73, "fix/73-x", now=NOW, path=target)
        check(set(ai.read_rows(target)) == {"app-launcher#528", "photo-ocr#73"},
              "add: unrelated rows preserved")

        removed = ai.remove_marker(tmp / "app-launcher", 528, now=NOW, path=target)
        check(removed is True and set(ai.read_rows(target)) == {"photo-ocr#73"},
              "remove: target cleared, unrelated row preserved")
        check(ai.remove_marker(tmp / "app-launcher", 999, now=NOW, path=target) is False,
              "remove: absent marker is idempotent")

        target.write_text(json.dumps({
            "old#1": _row("old", 1, age_hours=25),
            "fresh#2": _row("fresh", 2, age_hours=23),
            "bad#3": {"repo": "bad", "number": 3, "started_at": "garbage"},
        }), encoding="utf-8")
        ai.add_marker(tmp / "new", 4, "feat/4-new", now=NOW, path=target)
        check(set(ai.read_rows(target)) == {"fresh#2", "new#4"},
              "mutation prunes stale and malformed rows")

        # Force many overlapping read-modify-write transactions. The state
        # lock must preserve every independently-added marker.
        target.unlink()
        threads = [
            threading.Thread(
                target=ai.add_marker,
                args=(tmp / f"repo-{number}", number, f"feat/{number}"),
                kwargs={"now": NOW, "path": target},
            )
            for number in range(1, 13)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        check(all(not thread.is_alive() for thread in threads),
              "concurrent writers finish without deadlock")
        check(len(ai.read_rows(target)) == 12,
              "concurrent writers preserve all unrelated markers")
    finally:
        ai.resolve_repo_name = original_resolver

    # state_lock() stale-reclaim correctness (#469): age alone must never
    # displace a still-live holder, a genuinely abandoned lock must still be
    # reclaimable, and release must never delete a lock some other reclaimer
    # is now holding.
    slow_target = tmp / "slow-lock.json"
    order = []

    def _slow_holder() -> None:
        with ai.state_lock(slow_target, stale_after_seconds=0.05, timeout_seconds=5.0):
            order.append("A-enter")
            time.sleep(0.3)
            order.append("A-exit")

    def _contender() -> None:
        time.sleep(0.1)  # let A hold well past stale_after_seconds first
        with ai.state_lock(slow_target, stale_after_seconds=0.05, timeout_seconds=5.0):
            order.append("B-enter")

    t_a = threading.Thread(target=_slow_holder)
    t_b = threading.Thread(target=_contender)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)
    check(not t_a.is_alive() and not t_b.is_alive(), "slow-holder test threads finish")
    check(order == ["A-enter", "A-exit", "B-enter"],
          "slow-but-alive holder is never displaced by stale-reclaim")

    dead_target = tmp / "dead-lock.json"
    dead_lock_dir = dead_target.with_name(dead_target.name + ".lock")
    dead_proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], creationflags=NO_WINDOW
    )
    dead_proc.wait(timeout=10)
    dead_lock_dir.mkdir(parents=True)
    (dead_lock_dir / f"owner.{dead_proc.pid}.abandoned").touch()
    old_stamp = time.time() - 60
    os.utime(dead_lock_dir, (old_stamp, old_stamp))
    entered = False
    with ai.state_lock(dead_target, stale_after_seconds=0.05, timeout_seconds=5.0):
        entered = True
    check(entered, "a genuinely abandoned lock (dead pid) is still reclaimed")

    tamper_target = tmp / "tamper-lock.json"
    tamper_lock_dir = tamper_target.with_name(tamper_target.name + ".lock")
    cm = ai.state_lock(tamper_target, stale_after_seconds=30.0, timeout_seconds=5.0)
    cm.__enter__()
    # Simulate a reclaimer that decided we were dead: it wipes our marker and
    # writes its own, exactly as the stale-reclaim path does.
    for entry in tamper_lock_dir.iterdir():
        entry.unlink()
    reclaimer_owner = {"pid": os.getpid(), "token": "reclaimers-token"}
    (tamper_lock_dir / f"owner.{reclaimer_owner['pid']}.{reclaimer_owner['token']}").touch()
    cm.__exit__(None, None, None)
    check(tamper_lock_dir.exists(),
          "release never deletes a lock a reclaimer took over while held")
    check(ai._read_lock_owner(tamper_lock_dir) == reclaimer_owner,
          "the reclaimer's own owner record survives our release")
    shutil.rmtree(tamper_lock_dir, ignore_errors=True)

    # The checked-in skills are the executable lifecycle contract; pin every
    # branch that bypasses another workflow instead of relying on prose review.
    start_skill = (ROOT / "skills" / "issue-start" / "SKILL.md").read_text(encoding="utf-8")
    finish_skill = (ROOT / "skills" / "issue-finish" / "SKILL.md").read_text(encoding="utf-8")
    yolo_skill = (ROOT / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    batch_skill = (ROOT / "skills" / "issue-batch" / "SKILL.md").read_text(encoding="utf-8")
    check("active_issue.py add" in start_skill, "issue-start writes the marker")
    check("active_issue.py remove" in finish_skill, "issue-finish clears the marker")
    # issue-yolo's Phase 4 delegates its ship steps to `/issue-finish` verbatim
    # (fleet-config#728) rather than restating them, so the marker-clear command
    # now lives only in finish_skill's text -- confirm yolo actually delegates
    # there rather than checking for a since-removed literal restatement.
    check("run the full **`/issue-finish` skill**" in yolo_skill,
          "issue-yolo Phase 4 delegates to issue-finish (which clears the marker)")
    check("active_issue.py add" in batch_skill, "issue-batch worktree setup writes the marker")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_active_issue")
