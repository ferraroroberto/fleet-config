"""A red GitHub Actions run goes through the chief mailbox as `kind=ci-failed` (fleet-config#1973).

Drives the real `chief_ops.py` reader (`inbox`) against a temp inbox and pins
docs/chief-mailbox.md's "CI events" rules: the head line and detail line a CI
event renders as, a missing field reading `-`, the sha shortened to 7
characters, the event counted as pending by file name like every kind, and v1,
v2, job-failed and needs-input events rendering unchanged beside it. Nothing
touches the real `chief-inbox` or a live launcher.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_ci_failed.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from _mailbox_support import publish, run_cli, stamp  # noqa: E402
from _suite import _h, check, co  # noqa: E402,F401

CI_ID = "3e4f5a6b7c8d49e0a1b2c3d4e5f60718"
BARE_ID = "4f5a6b7c8d9e40f1b2c3d4e5f6071829"
JOB_ID = "6b1f0c2e9d3a4f58a7c4e1d2b3a49586"
V2_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
NEEDS_INPUT_ID = "1a2b3c4d5e6f70819293a4b5c6d7e8f9"

SHA = "9f8e7d6c5b4a39281706f5e4d3c2b1a099887766"


def ci_event(event_id: str, **fields) -> dict:
    return {"v": 2, "id": event_id, "kind": "ci-failed", "event": "push",
            "repo": "example-app", "workflow": "CI", "run_id": 7001234567,
            "run_url": "https://github.com/example-owner/example-app/actions/runs/7001234567",
            "head_sha": SHA, "conclusion": "failure",
            "created_at": "2026-11-09T14:03:21Z", "failed_jobs": ["unit-tests", "lint"],
            "launcher_sid": None, "number": None, "agent": None, "dispatch_id": None,
            "ts": stamp(120), "excerpt": "FAILED tests/test_x.py::test_y\nAssertionError: 1 != 2",
            "excerpt_source": "ci-log", **fields}


HEAD = "- ci CI failed repo=example-app sha=9f8e7d6 run=7001234567"
DETAIL = ("  conclusion=failure event=push at=2026-11-09T14:03:21Z jobs=unit-tests, lint "
          "url=https://github.com/example-owner/example-app/actions/runs/7001234567")

tmp = Path(tempfile.mkdtemp(prefix="chief_ci_failed_"))
try:
    inbox = tmp / "inbox"
    neighbours = {
        "1700000000000-0a1b2c3d.json": dict(
            event="Stop", launcher_sid="a1b2c3d4e5f60718", repo="fleet-config", number=999,
            agent="claude", ts=stamp(150)),
        f"1700000000001-{V2_ID}.json": dict(
            v=2, id=V2_ID, kind="turn-end", event="Stop", launcher_sid="c1b2c3d4e5f60718",
            repo="fleet-config", number=1455, agent="claude", dispatch_id="d1", ts=stamp(150),
            excerpt="Done: PR opened.", excerpt_source="payload"),
        f"1700000000002-{NEEDS_INPUT_ID}.json": dict(
            v=2, id=NEEDS_INPUT_ID, kind="needs-input", event="Notification", launcher_sid="d1b2c3d4e5f60718",
            repo="fleet-config", number=1456, agent="claude", dispatch_id="d2", ts=stamp(150),
            excerpt="permission: Bash git push (parent)", excerpt_source="prompt"),
        f"1700000000003-{JOB_ID}.json": dict(
            v=2, id=JOB_ID, kind="job-failed", event="JobFailed", job_id="nightly", job_name="Nightly sync",
            run_id="20261022-050000", exit_code=1, outcome="failed", trigger="schedule",
            script_path="E:/automation/example/run.bat", repo="example-app", launcher_sid=None,
            number=None, agent=None, dispatch_id=None, ts=stamp(120),
            excerpt="Traceback", excerpt_source="job-output"),
    }
    for file_name, body in neighbours.items():
        publish(inbox, file_name, body)
    publish(inbox, f"1700000000004-{CI_ID}.json", ci_event(CI_ID))
    publish(inbox, f"1700000000005-{BARE_ID}.json", ci_event(
        BARE_ID, workflow="Nightly", run_id=7001234999, head_sha=None, conclusion="startup_failure",
        event="schedule", failed_jobs=[], run_url=None, created_at=None,
        excerpt=None, excerpt_source=None, excerpt_unavailable="the run has no failed step log"))

    check(co.count_pending(inbox) == 6, "count_pending: a ci-failed event counts like every kind (6 of 6 pending)")

    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    check(code == 0 and lines[0] == "INBOX=events n=6 reoffered=0 dropped=0",
          f"inbox: header counts all six events (exit {code}, got {lines[:1]})")
    head_line = f"{HEAD} kind=ci-failed id={CI_ID} dispatch=-"
    check(head_line in lines,
          "inbox: a ci-failed event renders 'ci <workflow> failed repo= sha=<sha7> run=' plus kind/id/dispatch")
    at = lines.index(head_line)
    check(lines[at + 1:at + 5] == [DETAIL, "  excerpt (ci-log, 53 chars):", "  | FAILED tests/test_x.py::test_y",
                                   "  | AssertionError: 1 != 2"],
          f"inbox: the detail line and the excerpt lines follow the head line (got {lines[at + 1:at + 5]})")
    bare = f"- ci Nightly failed repo=example-app sha=- run=7001234999 kind=ci-failed id={BARE_ID} dispatch=-"
    check(bare in lines, "inbox: a missing head_sha reads '-' and the event still renders")
    at = lines.index(bare)
    check(lines[at + 1:at + 3] == ["  conclusion=startup_failure event=schedule at=- jobs=- url=-",
                                   "  excerpt: unavailable (the run has no failed step log)"],
          f"inbox: missing failed_jobs, run_url and created_at read '-' and an absent excerpt says why "
          f"(got {lines[at + 1:at + 3]})")
    check("- job Nightly sync failed exit=1 run=20261022-050000 repo=example-app "
          f"kind=job-failed id={JOB_ID} dispatch=-" in lines,
          "inbox: a job-failed line is unchanged beside the ci event")
    check("- fleet-config#999: turn ended (claude) sid=a1b2c3d4 age=2m kind=? id=0a1b2c3d dispatch=-" in lines,
          "inbox: a v1 event line is unchanged")
    check("- fleet-config#1455: turn ended (claude) sid=c1b2c3d4 age=2m "
          f"kind=turn-end id={V2_ID} dispatch=d1" in lines,
          "inbox: a v2 turn-end line is unchanged")
    check("- fleet-config#1456: needs input (claude) sid=d1b2c3d4 age=2m "
          f"kind=needs-input id={NEEDS_INPUT_ID} dispatch=d2" in lines,
          "inbox: a needs-input line is unchanged")
    ack_line = next(line for line in lines if line.startswith("ACK="))
    check(CI_ID in ack_line and BARE_ID in ack_line, "inbox: every ci event id is on the ACK line")

    ids = ack_line.split()[2:]
    code, out = run_cli(inbox, "ack", *ids)
    check(code == 0 and f"ACKED={len(ids)}" in out, f"ack: a ci-failed id retires like any other (exit {code}, {out.strip()!r})")
    check(co.count_pending(inbox) == 0, "count_pending: nothing pending once everything is acked")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_ci_failed")
