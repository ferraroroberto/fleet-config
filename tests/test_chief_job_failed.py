"""A failed app-launcher Job goes through the chief mailbox as `kind=job-failed` (fleet-config#1539).

Drives the real `chief_ops.py` readers (`inbox`, `wait-event`) against a temp
inbox and pins docs/chief-mailbox.md's "Job events" rules: the head line and
detail line a Job event renders as, a null `exit_code`/`repo` reading `-`,
`not-confirmed` never reading as a plain failure, any trigger rendered as-is,
the event counted as pending by file name like every kind, and v1, v2 and
needs-input events rendering unchanged beside it. Nothing touches the real
`chief-inbox` or a live launcher.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_job_failed.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_ops as co  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

JOB_ID = "6b1f0c2e9d3a4f58a7c4e1d2b3a49586"
NOT_CONFIRMED_ID = "7c2a1d3f0e4b5a69b8d5f2e3c4b5a697"
REAPED_ID = "8d3b2e4a1f5c6b7ac9e6a3f4d5c6b7a8"
V2_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
NEEDS_INPUT_ID = "1a2b3c4d5e6f70819293a4b5c6d7e8f9"


def stamp(seconds_ago: int) -> str:
    # The readers filter by the real clock (24 h TTL), so events carry real stamps.
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_event(event_id: str, **fields) -> dict:
    return {"v": 2, "id": event_id, "kind": "job-failed", "event": "JobFailed",
            "job_id": "job-tracker", "job_name": "Job tracker snapshot", "run_id": "20261022-050000",
            "exit_code": 1, "outcome": "failed", "trigger": "schedule",
            "script_path": "E:/automation/life-os/run.bat", "repo": "life-os",
            "launcher_sid": None, "number": None, "agent": None, "dispatch_id": None,
            "ts": stamp(120), "excerpt": "Traceback\nValueError: stale", "excerpt_source": "job-output",
            **fields}


def publish(inbox: Path, name: str, event: dict) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(json.dumps(event), encoding="utf-8")


def run_cli(inbox: Path, *argv: str) -> "tuple[int, str]":
    old_dir, old_request = co.chief_inbox_dir, co._request
    co.chief_inbox_dir = lambda: inbox
    co._request = lambda base, path: {}  # the timeout path's board digest, never a live launcher
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(list(argv))
    finally:
        co.chief_inbox_dir, co._request = old_dir, old_request
    return code, out.getvalue()


HEAD = "- job Job tracker snapshot failed exit=1 run=20261022-050000 repo=life-os"
DETAIL = "  job_id=job-tracker trigger=schedule script=E:/automation/life-os/run.bat"

tmp = Path(tempfile.mkdtemp(prefix="chief_job_failed_"))
try:
    # ---- inbox: a job event beside v1, v2 and needs-input events ----
    inbox = tmp / "inbox"
    v1 = {"event": "Stop", "launcher_sid": "a1b2c3d4e5f60718", "repo": "fleet-config",
          "number": 999, "agent": "claude", "ts": stamp(150)}
    publish(inbox, "1700000000000-0a1b2c3d.json", v1)
    publish(inbox, f"1700000000001-{V2_ID}.json", {
        **v1, "launcher_sid": "c1b2c3d4e5f60718", "number": 1455, "v": 2, "id": V2_ID,
        "kind": "turn-end", "dispatch_id": "d1", "excerpt": "Done: PR opened.", "excerpt_source": "payload"})
    publish(inbox, f"1700000000002-{NEEDS_INPUT_ID}.json", {
        **v1, "launcher_sid": "d1b2c3d4e5f60718", "number": 1456, "v": 2, "id": NEEDS_INPUT_ID,
        "kind": "needs-input", "event": "Notification", "dispatch_id": "d2",
        "excerpt": "permission: Bash git push (parent)", "excerpt_source": "prompt"})
    publish(inbox, f"1700000000003-{JOB_ID}.json", job_event(JOB_ID))
    publish(inbox, f"1700000000004-{NOT_CONFIRMED_ID}.json", job_event(
        NOT_CONFIRMED_ID, outcome="not-confirmed", trigger="webhook", job_name="Nightly sync"))
    publish(inbox, f"1700000000005-{REAPED_ID}.json", job_event(
        REAPED_ID, exit_code=None, repo=None, trigger="some-future-trigger", job_name="Reaped job",
        excerpt=None, excerpt_source=None, excerpt_unavailable="output.log is missing or empty"))

    check(co.count_pending(inbox) == 6, "count_pending: a job-failed event counts like every kind (6 of 6 pending)")

    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    check(code == 0 and lines[0] == "INBOX=events n=6 reoffered=0 dropped=0",
          f"inbox: header counts all six events (exit {code}, got {lines[:1]})")
    check(f"{HEAD} kind=job-failed id={JOB_ID} dispatch=-" in lines,
          "inbox: a job-failed event renders 'job <name> failed exit=<n> run=<id> repo=<repo>' plus kind/id/dispatch")
    at = lines.index(f"{HEAD} kind=job-failed id={JOB_ID} dispatch=-")
    check(lines[at + 1:at + 5] == [DETAIL, "  excerpt (job-output, 27 chars):", "  | Traceback", "  | ValueError: stale"],
          f"inbox: the detail line and the excerpt lines follow the head line (got {lines[at + 1:at + 5]})")
    check("- job Nightly sync failed exit=1 run=20261022-050000 repo=life-os outcome=not-confirmed "
          f"kind=job-failed id={NOT_CONFIRMED_ID} dispatch=-" in lines,
          "inbox: a not-confirmed run is marked outcome=not-confirmed, never a bare failure")
    check("  job_id=job-tracker trigger=webhook script=E:/automation/life-os/run.bat" in lines,
          "inbox: the webhook trigger renders as-is")
    reaped = f"- job Reaped job failed exit=- run=20261022-050000 repo=- kind=job-failed id={REAPED_ID} dispatch=-"
    check(reaped in lines, "inbox: a null exit_code and repo read '-'")
    at = lines.index(reaped)
    check(lines[at + 1:at + 3] == ["  job_id=job-tracker trigger=some-future-trigger script=E:/automation/life-os/run.bat",
                                   "  excerpt: unavailable (output.log is missing or empty)"],
          f"inbox: an unknown trigger renders as-is and an absent excerpt says why (got {lines[at + 1:at + 3]})")
    check("- fleet-config#999: turn ended (claude) sid=a1b2c3d4 age=2m kind=? id=0a1b2c3d dispatch=-" in lines,
          "inbox: a v1 event line is unchanged beside the job events")
    check("- fleet-config#1455: turn ended (claude) sid=c1b2c3d4 age=2m "
          f"kind=turn-end id={V2_ID} dispatch=d1" in lines,
          "inbox: a v2 turn-end line is unchanged")
    check("- fleet-config#1456: needs input (claude) sid=d1b2c3d4 age=2m "
          f"kind=needs-input id={NEEDS_INPUT_ID} dispatch=d2" in lines,
          "inbox: a needs-input line is unchanged")
    ack_line = next(line for line in lines if line.startswith("ACK="))
    check(JOB_ID in ack_line and NOT_CONFIRMED_ID in ack_line and REAPED_ID in ack_line,
          "inbox: every job event id is on the ACK line")

    ids = ack_line.split()[2:]
    code, out = run_cli(inbox, "ack", *ids)
    check(code == 0 and f"ACKED={len(ids)}" in out, f"ack: a job-failed id retires like any other (exit {code}, {out.strip()!r})")
    check(co.count_pending(inbox) == 0, "count_pending: nothing pending once everything is acked")

    # ---- wait-event: the same events through the v1 reader ----
    inbox2 = tmp / "inbox2"
    publish(inbox2, "1700000000000-0a1b2c3d.json", v1)
    publish(inbox2, f"1700000000003-{JOB_ID}.json", job_event(JOB_ID))
    code, out = run_cli(inbox2, "wait-event", "--timeout", "10", "--interval", "0.1")
    lines = out.rstrip("\n").splitlines()
    check(code == 0 and lines[0] == "WAIT_EVENT=events n=2", f"wait-event: header counts both events (got {lines[:1]})")
    check(lines[1] == "- fleet-config#999: turn ended (claude) sid=a1b2c3d4 age=2m",
          f"wait-event: the v1 line is byte-for-byte unchanged (got {lines[1]!r})")
    check(f"{HEAD} kind=job-failed" in lines and DETAIL in lines,
          "wait-event: a job-failed event renders the head line tagged with its kind, and the detail line")
    check("  | ValueError: stale" in lines, "wait-event: the excerpt lines follow")
    check(lines[-1] == "Next: `exchange <sid>` for each, `verify` any completion claim, then absorb or escalate.",
          "wait-event: the closing Next: line is unchanged")
    check(not list(inbox2.glob("*.json")), "wait-event: the job event was claimed exactly once")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_job_failed")
