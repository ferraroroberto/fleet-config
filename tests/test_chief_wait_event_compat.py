"""Pins `chief_ops.py wait-event`'s contract across the mailbox v2 change (fleet-config#1455).

The standing chief re-arms `wait-event` after every wake, and `skills/` is
junctioned live into its session, so a merge lands under a running waiter.
This file pins what that waiter relies on, and passes unchanged on the
pre-v2 tree:

- the output format: the `WAIT_EVENT=events n=N` header, one `- ` line per
  event (exact for v1 events; v2 may only *append* to the same line or add
  indented continuation lines), and the closing `Next:` line;
- claim exactly once: every root `*.json` is claimed and deleted on read,
  oldest first, never twice -- also under two concurrent claimers;
- old-format (v1) events already in the inbox are still read;
- a v2 `.claimed/` file and an unpublished temp are never touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_wait_event_compat.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_ops as co  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

V2_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
NEXT_LINE = "Next: `exchange <sid>` for each, `verify` any completion claim, then absorb or escalate."


def stamp(seconds_ago: int) -> str:
    # The waiter filters by the real clock (24 h TTL), so events carry real stamps.
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_wait_event(inbox: Path, *argv: str) -> "tuple[int, str]":
    old_dir, old_request = co.chief_inbox_dir, co._request
    co.chief_inbox_dir = lambda: inbox
    co._request = lambda base, path: {}  # the timeout path's board digest, never a live launcher
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(["wait-event", *argv])
    finally:
        co.chief_inbox_dir, co._request = old_dir, old_request
    return code, out.getvalue()


tmp = Path(tempfile.mkdtemp(prefix="chief_wait_compat_"))
try:
    inbox = tmp / "chief-inbox"
    claimed = inbox / ".claimed"
    claimed.mkdir(parents=True)
    v1 = {"event": "Stop", "launcher_sid": "a1b2c3d4e5f60718", "repo": "fleet-config",
          "number": 999, "agent": "claude", "ts": stamp(150)}
    (inbox / "1700000000000-0a1b2c3d.json").write_text(json.dumps(v1), encoding="utf-8")
    (inbox / "1700000000001-1a2b3c4d.json").write_text(json.dumps({
        **v1, "event": "SessionEnd", "launcher_sid": "b1b2c3d4e5f60718", "repo": "app-launcher",
        "number": 7, "agent": "codex"}), encoding="utf-8")
    (inbox / f"1700000000002-{V2_ID}.json").write_text(json.dumps({
        **v1, "launcher_sid": "c1b2c3d4e5f60718", "number": 1455, "v": 2, "id": V2_ID,
        "kind": "turn-end", "dispatch_id": "d1", "excerpt": "Done: PR opened.\nNext: review"}),
        encoding="utf-8")
    held = claimed / f"1700000000500-{'9' * 32}.json"
    held.write_text(json.dumps({**v1, "id": "9" * 32}), encoding="utf-8")
    temp = inbox / ".event.half.tmp"
    temp.write_text("half-written", encoding="utf-8")

    code, out = run_wait_event(inbox, "--timeout", "10", "--interval", "0.1")
    lines = out.rstrip("\n").splitlines()
    event_lines = [line for line in lines if line.startswith("- ")]
    check(code == 0 and lines[:1] == ["WAIT_EVENT=events n=3"],
          f"wait-event: header is exactly 'WAIT_EVENT=events n=3' (exit {code}, got {lines[:1]})")
    check(event_lines[:2] == ["- fleet-config#999: turn ended (claude) sid=a1b2c3d4 age=2m",
                              "- app-launcher#7: session ended (codex) sid=b1b2c3d4 age=2m"],
          f"wait-event: v1 event lines are byte-for-byte unchanged, oldest first (got {event_lines[:2]})")
    check(len(event_lines) == 3
          and event_lines[2].startswith("- fleet-config#1455: turn ended (claude) sid=c1b2c3d4 age=2m"),
          f"wait-event: a v2 event reads with the same line prefix (got {event_lines[2:]})")
    check(all(line.startswith(("WAIT_EVENT=", "- ", "  ", "Next:")) for line in lines),
          "wait-event: every other line is an indented continuation of an event line")
    check(lines[-1:] == [NEXT_LINE], f"wait-event: the closing line is unchanged (got {lines[-1:]})")
    check(not list(inbox.glob("*.json")), "wait-event: every root event was claimed and deleted on read")
    check(held.exists() and temp.exists(),
          "wait-event: a .claimed/ file and an unpublished temp are never touched")
    check(not [p for p in inbox.iterdir() if p.name.startswith(".claim-")],
          "wait-event: its private claim folder is removed")

    code, out = run_wait_event(inbox, "--timeout", "0.3", "--interval", "0.1")
    check(code == 0 and out.startswith("WAIT_EVENT=timeout after 0s (no worker events)"),
          "wait-event: a second run re-reports nothing and times out (claimed exactly once)")
    check(held.exists(), "wait-event: a stale-looking .claimed/ file is still not its to read")

    # Claim exactly once under two concurrent claimers.
    for n in range(60):
        (inbox / f"{1700000001000 + n}-{n:08x}.json").write_text(
            json.dumps({**v1, "number": n, "ts": stamp(5)}), encoding="utf-8")
    batches: "list[list]" = [[], []]
    gate = threading.Barrier(2)

    def claimer(slot: int) -> None:
        gate.wait()
        batches[slot] = co.claim_batch(inbox)

    threads = [threading.Thread(target=claimer, args=(slot,)) for slot in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    numbers = [e["number"] for e in batches[0] + batches[1]]
    check(sorted(numbers) == list(range(60)),
          f"claim_batch: two concurrent claimers split 60 events with none lost or doubled ({len(numbers)} seen, "
          f"lost {sorted(set(range(60)) - set(numbers))})")
    check(all(b == sorted(b, key=lambda e: e["number"]) for b in batches),
          "claim_batch: each claimer's batch is oldest first")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_wait_event_compat")
