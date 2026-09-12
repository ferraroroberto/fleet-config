"""Unit tests for skills/_lib/chief_managed.py (fleet-config#443).

Exercises `mark`/`prune_rows` and the CLI directly against a throwaway state
file — no real `~/.claude/hooks/state/chief-managed.json` touched. Every "is
this sid marked" assertion reads back through the one production reader,
`hooks/notify_on_idle.chief_managed_state()`, so the writer and that reader
are proven to agree on the file and its key (fleet-config#850) — the drift
that left the guard inert in fleet-config#835.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_managed.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_managed as cm  # noqa: E402

sys.path.insert(0, str(REPO / "hooks"))
import notify_on_idle  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# Anchored to the real clock, deliberately — NOT a pinned instant (fleet-config#493).
# `mark()` prunes against its `now=` on every write, and a hardcoded date would make
# the markers written here stale relative to any later real-clock prune. Assertions
# that depend on a *specific* elapsed interval pass an explicit `now=` (see
# `stale_now` below). Do not re-pin this to a literal date.
NOW = datetime.now(timezone.utc)

LAUNCHER_ENV = "APP_LAUNCHER_SESSION_ID"


def reader_state(sid: str, target: Path) -> Tuple[bool, str]:
    """What the production reader decides for a session whose launcher id is `sid`.

    `chief_managed_state()` takes its identity from the environment, never an
    argument (fleet-config#835), so the env var is what this drives.
    """
    os.environ[LAUNCHER_ENV] = sid
    return notify_on_idle.chief_managed_state(path=target)


MANAGED = (True, notify_on_idle.CHIEF_MANAGED)
NOT_DISPATCHED = (False, notify_on_idle.CHIEF_NOT_DISPATCHED)

tmp = Path(tempfile.mkdtemp(prefix="chief_managed_"))
prior_launcher_sid = os.environ.get(LAUNCHER_ENV)
try:
    target = tmp / cm.STATE_FILENAME

    check(reader_state("sid-1", target) == NOT_DISPATCHED, "missing state file -> reader: not dispatched")

    row = cm.mark("sid-1", "app-launcher", 528, now=NOW, path=target)
    check(row["repo"] == "app-launcher" and row["number"] == 528, "mark records repo/number")
    check(reader_state("sid-1", target) == MANAGED, "mark -> reader recognises the sid as managed")
    check(reader_state("sid-2", target) == NOT_DISPATCHED, "unrelated sid -> reader: not dispatched")

    # A second mark for a different sid must not clobber the first.
    cm.mark("sid-2", "photo-ocr", 12, now=NOW, path=target)
    check(reader_state("sid-1", target) == MANAGED, "second mark preserves the first sid")
    check(reader_state("sid-2", target) == MANAGED, "second mark is itself recorded")

    # A marker older than the 24h TTL is pruned away.
    stale_now = NOW + timedelta(hours=25)
    rows = cm.read_rows(target)
    check({"sid-1", "sid-2"} <= set(rows), "sanity: both markers are on disk before the prune")
    pruned = cm.prune_rows(rows, now=stale_now)
    check("sid-1" not in pruned and "sid-2" not in pruned, "25h-old markers are pruned")

    # mark() itself re-prunes on write -- a fresh mark at a later time drops stale peers.
    cm.mark("sid-3", "whatsapp-radar", 7, now=stale_now, path=target)
    rows_after = cm.read_rows(target)
    check(set(rows_after) == {"sid-3"}, "mark() prunes stale rows as a side effect of writing")
    check(reader_state("sid-1", target) == NOT_DISPATCHED, "pruned sid -> reader: not dispatched")

    try:
        cm.mark("", "app-launcher", 1, path=target)
        empty_sid_raised = False
    except ValueError:
        empty_sid_raised = True
    check(empty_sid_raised, "mark raises on an empty sid")

    # CLI entry point (fleet-config#474) -- the shape an out-of-tree caller
    # (app-launcher's board.py, a different repo) shells out to, since it
    # cannot import this module directly across the repo boundary. The reader
    # resolves its default path too, so this also proves both sides land on
    # the same file under the state-dir override.
    cli_env = dict(os.environ)
    cli_env["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    cli_result = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "chief_managed.py"),
         "mark", "sid-cli", "app-launcher", "641"],
        capture_output=True, text=True, env=cli_env,
    )
    check(cli_result.returncode == 0, f"CLI mark exits 0 (stderr={cli_result.stderr!r})")
    check("MARKED sid=sid-cli" in cli_result.stdout, "CLI mark prints MARKED line")
    prior_state_dir = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    try:
        os.environ[LAUNCHER_ENV] = "sid-cli"
        check(notify_on_idle.chief_managed_state() == MANAGED,
              "CLI mark -> reader (default path) recognises the sid as managed")
    finally:
        if prior_state_dir is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = prior_state_dir
finally:
    if prior_launcher_sid is None:
        os.environ.pop(LAUNCHER_ENV, None)
    else:
        os.environ[LAUNCHER_ENV] = prior_launcher_sid
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_chief_managed")
