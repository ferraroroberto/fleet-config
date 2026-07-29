"""Unit tests for skills/_lib/chief_managed.py (fleet-config#443).

Exercises `mark`/`is_managed`/`prune_rows` directly against a throwaway
state file — no real `~/.claude/hooks/state/chief-managed.json` touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_managed.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_managed as cm  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# Anchored to the real clock, deliberately — NOT a pinned instant (fleet-config#493).
# `is_managed()` reads back through `prune_rows(read_rows(...))` with no `now=`, so it
# always judges freshness against the *real* current time and the 24h TTL. A hardcoded
# NOW therefore ages out: the four "is this marker live" assertions below passed on the
# day the test was written and began failing ~24h later, leaving the acceptance gate
# standing red at 2/341. Freshness assertions must be relative to now for the same
# reason the code they exercise is.
#
# Determinism is not lost: every assertion that depends on a *specific* elapsed
# interval still passes an explicit `now=` (see `stale_now` below), which is the part
# that genuinely needs to be time-independent. Do not re-pin this to a literal date.
NOW = datetime.now(timezone.utc)


tmp = Path(tempfile.mkdtemp(prefix="chief_managed_"))
try:
    target = tmp / cm.STATE_FILENAME

    check(cm.is_managed("sid-1", path=target) is False, "missing state file -> not managed")

    row = cm.mark("sid-1", "app-launcher", 528, now=NOW, path=target)
    check(row["repo"] == "app-launcher" and row["number"] == 528, "mark records repo/number")
    check(cm.is_managed("sid-1", path=target) is True, "mark -> is_managed True")
    check(cm.is_managed("sid-2", path=target) is False, "unrelated sid -> not managed")

    # A second mark for a different sid must not clobber the first.
    cm.mark("sid-2", "photo-ocr", 12, now=NOW, path=target)
    check(cm.is_managed("sid-1", path=target) is True, "second mark preserves the first sid")
    check(cm.is_managed("sid-2", path=target) is True, "second mark is itself recorded")

    # A marker older than the 24h TTL is pruned away.
    stale_now = NOW + timedelta(hours=25)
    check(cm.is_managed("sid-1", path=target) is True, "sanity: still fresh relative to NOW")
    rows = cm.read_rows(target)
    pruned = cm.prune_rows(rows, now=stale_now)
    check("sid-1" not in pruned and "sid-2" not in pruned, "25h-old markers are pruned")

    # mark() itself re-prunes on write -- a fresh mark at a later time drops stale peers.
    cm.mark("sid-3", "whatsapp-radar", 7, now=stale_now, path=target)
    rows_after = cm.read_rows(target)
    check(set(rows_after) == {"sid-3"}, "mark() prunes stale rows as a side effect of writing")

    try:
        cm.mark("", "app-launcher", 1, path=target)
        empty_sid_raised = False
    except ValueError:
        empty_sid_raised = True
    check(empty_sid_raised, "mark raises on an empty sid")

    # CLI entry point (fleet-config#474) -- the shape an out-of-tree caller
    # (app-launcher's board.py, a different repo) shells out to, since it
    # cannot import this module directly across the repo boundary.
    cli_env = dict(os.environ)
    cli_env["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    cli_result = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "chief_managed.py"),
         "mark", "sid-cli", "app-launcher", "641"],
        capture_output=True, text=True, env=cli_env,
    )
    check(cli_result.returncode == 0, f"CLI mark exits 0 (stderr={cli_result.stderr!r})")
    check("MARKED sid=sid-cli" in cli_result.stdout, "CLI mark prints MARKED line")
    check(cm.is_managed("sid-cli", path=target) is True, "CLI mark lands in the shared state file")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_chief_managed")
