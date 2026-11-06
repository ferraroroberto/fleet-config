"""A raised Settings worker cap goes through the chief mailbox as `kind=cap-raised`.

Drives the real `chief_ops.py` reader (`inbox`) against a temp
inbox and pins docs/chief-mailbox.md's "Cap events" rules: the one-line
`worker cap raised old=<n> new=<n>` render, a non-integer bound reading `-`
rather than rejecting the event, the event counted as pending by file name and
claimed and acked like every kind, and worker, job-failed and needs-input lines
rendering unchanged beside it. Nothing touches the real `chief-inbox` or a live
launcher.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_cap_raised.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from _mailbox_support import publish, run_cli, stamp  # noqa: E402
from _suite import _h, check, co  # noqa: E402,F401

CAP_ID = "2b3c4d5e6f708192a3b4c5d6e7f80910"
ODD_ID = "3c4d5e6f708192a3b4c5d6e7f8091021"
V2_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def cap_event(event_id: str, **fields) -> dict:
    # Exactly the shape app-launcher writes: no worker, no excerpt.
    return {"v": 2, "id": event_id, "kind": "cap-raised", "event": "CapRaised", "old": 2, "new": 3,
            "launcher_sid": None, "repo": None, "number": None, "agent": None, "dispatch_id": None,
            "ts": stamp(30), **fields}


tmp = Path(tempfile.mkdtemp(prefix="chief_cap_raised_"))
try:
    inbox = tmp / "inbox"
    v1 = {"event": "Stop", "launcher_sid": "a1b2c3d4e5f60718", "repo": "fleet-config",
          "number": 999, "agent": "claude", "ts": stamp(150)}
    publish(inbox, "1700000000000-0a1b2c3d.json", v1)
    publish(inbox, f"1700000000001-{V2_ID}.json", {
        **v1, "launcher_sid": "c1b2c3d4e5f60718", "number": 1455, "v": 2, "id": V2_ID,
        "kind": "turn-end", "dispatch_id": "d1", "excerpt": "Done: PR opened.", "excerpt_source": "payload"})
    publish(inbox, f"1700000000002-{CAP_ID}.json", cap_event(CAP_ID))
    publish(inbox, f"1700000000003-{ODD_ID}.json", cap_event(ODD_ID, old="two", new=None))

    check(co.count_pending(inbox) == 4, "count_pending: a cap-raised event counts like every kind (4 of 4 pending)")

    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    check(code == 0 and lines[0] == "INBOX=events n=4 reoffered=0 dropped=0",
          f"inbox: header counts all four events (exit {code}, got {lines[:1]})")
    check(f"- worker cap raised old=2 new=3 kind=cap-raised id={CAP_ID} dispatch=-" in lines,
          "inbox: a cap-raised event renders 'worker cap raised old=<n> new=<n>' plus kind/id/dispatch")
    check(f"- worker cap raised old=- new=- kind=cap-raised id={ODD_ID} dispatch=-" in lines,
          "inbox: a non-integer bound reads '-' and the event is still delivered")
    at = lines.index(f"- worker cap raised old=2 new=3 kind=cap-raised id={CAP_ID} dispatch=-")
    check(not lines[at + 1].startswith("  "),
          f"inbox: a cap-raised event has no excerpt or detail lines (got {lines[at + 1]!r})")
    check("- fleet-config#999: turn ended (claude) sid=a1b2c3d4 age=2m kind=? id=0a1b2c3d dispatch=-" in lines,
          "inbox: a v1 event line is unchanged beside the cap event")
    check("- fleet-config#1455: turn ended (claude) sid=c1b2c3d4 age=2m "
          f"kind=turn-end id={V2_ID} dispatch=d1" in lines,
          "inbox: a v2 turn-end line is unchanged")
    ack_line = next(line for line in lines if line.startswith("ACK="))
    check(CAP_ID in ack_line and ODD_ID in ack_line, "inbox: every cap event id is on the ACK line")

    ids = ack_line.split()[2:]
    code, out = run_cli(inbox, "ack", *ids)
    check(code == 0 and f"ACKED={len(ids)}" in out, f"ack: a cap-raised id retires like any other (exit {code}, {out.strip()!r})")
    check(co.count_pending(inbox) == 0, "count_pending: nothing pending once everything is acked")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_cap_raised")
