"""A critical machine metric goes through the chief mailbox as `kind=machine-critical` (fleet-config#2083).

Drives the real `chief_ops.py` reader (`inbox`, `ack`) against a temp inbox and
pins docs/chief-mailbox.md's "Machine events" rules: the head, detail and
offender lines, a missing field reading `-` rather than rejecting the event, the
offender cap, the event counted as pending by file name and claimed and acked
like every kind, two events of one incident (host + metric + first_seen)
collapsing to one with one `ACK=` line that retires both, different incidents
and events without a full key staying separate, and worker lines
rendering unchanged beside it. Nothing touches the real `chief-inbox` or a live
launcher, and no clock or port is involved: the events are synthetic files.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_machine_critical.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from _mailbox_support import publish, run_cli, stamp  # noqa: E402
from _suite import _h, check, co  # noqa: E402,F401

FIRST_ID = "5e6f708192a3b4c5d6e7f80910213243"
REPEAT_ID = "6f708192a3b4c5d6e7f8091021324354"
NEW_INCIDENT_ID = "708192a3b4c5d6e7f809102132435465"
OTHER_METRIC_ID = "8192a3b4c5d6e7f80910213243546576"
NO_KEY_A_ID = "92a3b4c5d6e7f8091021324354657687"
NO_KEY_B_ID = "a3b4c5d6e7f809102132435465768798"
BARE_ID = "b4c5d6e7f80910213243546576879809"
V2_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
INCIDENT = "2026-11-15T16:20:05Z"
VERDICT = "http://127.0.0.1:8000/admin/api/machines/health"


def machine_event(event_id: str, **fields) -> dict:
    # The shape the launcher's health publisher writes: no worker, no excerpt.
    return {"v": 2, "id": event_id, "kind": "machine-critical", "event": "MachineCritical",
            "host": "example-host", "metric": "tcp_ephemeral_ports", "value": 61200, "threshold": 60000,
            "offenders": [
                {"name": "example-app-webapp", "pid": 4242, "owner": "fleet", "repo": "example-app", "value": 18400},
                {"name": "other.exe", "pid": 977, "owner": "non-fleet", "repo": None, "value": 9100}],
            "verdict_url": VERDICT, "first_seen": INCIDENT,
            "launcher_sid": None, "repo": None, "number": None, "agent": None, "dispatch_id": None,
            "ts": stamp(30), **fields}


def head(event_id: str, metric: str = "tcp_ephemeral_ports", value="61200", extra: str = "") -> str:
    return (f"- machine example-host critical metric={metric} value={value} threshold=60000{extra} "
            f"kind=machine-critical id={event_id} dispatch=-")


tmp = Path(tempfile.mkdtemp(prefix="chief_machine_critical_"))
try:
    inbox = tmp / "inbox"

    # ---- one event, rendered and retired like any kind ----
    publish(inbox, f"1700000000000-{V2_ID}.json", {
        "v": 2, "id": V2_ID, "kind": "turn-end", "event": "Stop", "launcher_sid": "c1b2c3d4e5f60718",
        "repo": "fleet-config", "number": 1455, "agent": "claude", "ts": stamp(150), "dispatch_id": "d1",
        "excerpt": "Done: PR opened.", "excerpt_source": "payload"})
    publish(inbox, f"1700000000001-{FIRST_ID}.json", machine_event(FIRST_ID))
    publish(inbox, f"1700000000002-{BARE_ID}.json", {
        "v": 2, "id": BARE_ID, "kind": "machine-critical", "event": "MachineCritical", "ts": stamp(20),
        "offenders": "not a list"})

    check(co.count_pending(inbox) == 3, "count_pending: a machine-critical event counts like every kind (3 of 3)")
    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    check(code == 0 and lines[0] == "INBOX=events n=3 reoffered=0 dropped=0",
          f"inbox: header counts all three events (exit {code}, got {lines[:1]})")
    at = lines.index(head(FIRST_ID))
    check(lines[at + 1] == f"  first_seen={INCIDENT} verdict={VERDICT}",
          f"inbox: the detail line names the incident start and the verdict (got {lines[at + 1]!r})")
    check(lines[at + 2] == "  offender example-app-webapp pid=4242 owner=fleet repo=example-app value=18400",
          f"inbox: a fleet offender row names its owner and repo (got {lines[at + 2]!r})")
    check(lines[at + 3] == "  offender other.exe pid=977 owner=non-fleet repo=- value=9100",
          f"inbox: a non-fleet offender row reads repo=- (got {lines[at + 3]!r})")
    bare = lines.index(f"- machine - critical metric=- value=- threshold=- kind=machine-critical id={BARE_ID} dispatch=-")
    check(lines[bare + 1] == "  first_seen=- verdict=-" and lines[bare + 2] == "  offenders: none listed",
          "inbox: missing fields read '-' and a non-list offenders reads 'none listed', the event is still delivered")
    check("- fleet-config#1455: turn ended (claude) sid=c1b2c3d4 age=2m "
          f"kind=turn-end id={V2_ID} dispatch=d1" in lines, "inbox: a v2 turn-end line is unchanged beside it")
    ack_line = next(line for line in lines if line.startswith("ACK="))
    ids = ack_line.split()[2:]
    check(sorted(ids) == sorted([V2_ID, FIRST_ID, BARE_ID]), f"inbox: every event id is on the ACK line (got {ids})")
    code, out = run_cli(inbox, "ack", *ids)
    check(code == 0 and f"ACKED={len(ids)}" in out, f"ack: a machine-critical id retires like any other (exit {code}, {out.strip()!r})")
    check(co.count_pending(inbox) == 0, "count_pending: nothing pending once everything is acked")

    # ---- the offender cap ----
    many = [{"name": f"proc{n}", "pid": n, "owner": "unknown", "repo": None, "value": 100 - n} for n in range(8)]
    publish(inbox, f"1700000000010-{FIRST_ID}.json", machine_event(FIRST_ID, offenders=many))
    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    shown = [line for line in lines if line.startswith("  offender ")]
    check(len(shown) == 5 and "  offenders: 3 more at the verdict" in lines,
          f"inbox: only the top five offenders render, the rest are counted (got {len(shown)} rows)")
    run_cli(inbox, "ack", FIRST_ID)

    # ---- one incident collapses to one event ----
    publish(inbox, f"1700000000020-{FIRST_ID}.json", machine_event(FIRST_ID))
    publish(inbox, f"1700000000021-{REPEAT_ID}.json", machine_event(REPEAT_ID, value=61900))
    publish(inbox, f"1700000000022-{NEW_INCIDENT_ID}.json",
            machine_event(NEW_INCIDENT_ID, first_seen="2026-11-15T19:00:00Z"))
    publish(inbox, f"1700000000023-{OTHER_METRIC_ID}.json", machine_event(OTHER_METRIC_ID, metric="udp_ports", value=900))
    publish(inbox, f"1700000000024-{NO_KEY_A_ID}.json", machine_event(NO_KEY_A_ID, first_seen=None))
    publish(inbox, f"1700000000025-{NO_KEY_B_ID}.json", machine_event(NO_KEY_B_ID, first_seen=None))
    check(co.count_pending(inbox) == 6, "count_pending: the file-name count does not fold events (6 of 6)")

    code, out = run_cli(inbox, "inbox")
    lines = out.rstrip("\n").splitlines()
    check(lines[0] == "INBOX=events n=5 reoffered=0 dropped=0",
          f"inbox: two events of one incident collapse to one (header got {lines[0]!r})")
    check(head(FIRST_ID, extra=" repeats=1") in lines,
          "inbox: the oldest event of the incident is kept and shows repeats=1")
    check(not any(f"id={REPEAT_ID}" in line for line in lines), "inbox: the folded event renders no line of its own")
    check(any(f"id={NEW_INCIDENT_ID}" in line for line in lines),
          "inbox: a later first_seen is a new incident and stays separate")
    check(head(OTHER_METRIC_ID, metric="udp_ports", value="900") in lines,
          "inbox: another metric on the same host stays separate")
    check(any(f"id={NO_KEY_A_ID}" in line for line in lines) and any(f"id={NO_KEY_B_ID}" in line for line in lines),
          "inbox: events without a first_seen are never folded")
    ack_line = next(line for line in lines if line.startswith("ACK="))
    ids = ack_line.split()[2:]
    check(REPEAT_ID in ids and len(ids) == 6, f"inbox: the one ACK line names the folded id too (got {len(ids)} ids)")
    check(len(list((inbox / ".claimed").glob("*.json"))) == 6,
          "inbox: all six files are claimed and none is deleted at fold time")
    code, out = run_cli(inbox, "ack", *ids)
    check(code == 0 and f"ACKED={len(ids)}" in out, f"ack: one ack retires the kept and the folded event (exit {code}, {out.strip()!r})")
    check(co.count_pending(inbox) == 0 and not list((inbox / ".claimed").glob("*.json")),
          "ack: nothing is left pending or claimed")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_machine_critical")
