"""Tests for the chief mailbox v2 reader in skills/_lib/chief_ops.py (fleet-config#1455).

`inbox` claims into `.claimed/<claimed_at_ms>-<id>.json`, `ack` retires, and
an unacked claim is re-offered after the stale window -- driven with an
injected millisecond clock, never a sleep. `inbox --count` is the file-name
pending rule docs/chief-mailbox.md defines for a second implementation, so
its cases here are the doc's cases. `cmd_dispatch` records a `dispatch_id`.
A claimed file that cannot be read yet is never dropped by either reader
(fleet-config#1465), driven with injected read failures; the real race is
the opt-in `tests/probe_claim_race.py`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_mailbox.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_managed  # noqa: E402
import chief_ops as co  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

T0 = 1_792_000_000_000  # a fixed "now", in ms
STALE_MS = int(co.CLAIM_STALE_SECONDS * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish(inbox: Path, name: str, **fields) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    row = {"v": 2, "kind": "turn-end", "event": "Stop", "launcher_sid": "a1b2c3d4e5f6",
           "repo": "fleet-config", "number": 1455, "agent": "claude", "ts": iso(T0 - 60_000),
           "dispatch_id": "d1", "excerpt": "done", "excerpt_source": "payload", **fields}
    (inbox / name).write_text(json.dumps(row), encoding="utf-8")


def cli(inbox: Path, *argv: str) -> "tuple[int, str]":
    """`chief_ops.main` against `inbox`, at the injected clock T0."""
    old_dir, old_now = co.mailbox.chief_inbox_dir, co.mailbox._now_ms
    co.mailbox.chief_inbox_dir, co.mailbox._now_ms = (lambda: inbox), (lambda: T0)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(list(argv))
    finally:
        co.mailbox.chief_inbox_dir, co.mailbox._now_ms = old_dir, old_now
    return code, out.getvalue()


tmp = Path(tempfile.mkdtemp(prefix="chief_mailbox_"))
try:
    # ---- claim -> ack retires a batch -------------------------------------
    inbox = tmp / "a" / "chief-inbox"
    check(co.count_pending(inbox, T0) == 0 and co.claim_mailbox(inbox, T0) == ([], [], 0),
          "a missing inbox: nothing pending, an empty batch")
    publish(inbox, f"{T0 - 2000}-{'a' * 32}.json", id="a" * 32)
    publish(inbox, f"{T0 - 1000}-{'b' * 32}.json", id="b" * 32, kind="session-end", event="SessionEnd")
    # A v1 event (no id, short hex suffix) already sitting in the inbox.
    (inbox / f"{T0 - 500}-0a1b2c3d.json").write_text(json.dumps({
        "event": "Stop", "launcher_sid": "c1", "repo": "x", "number": 1, "agent": "pi",
        "ts": iso(T0 - 60_000)}), encoding="utf-8")
    events, reoffered, dropped = co.claim_mailbox(inbox, T0)
    check([e["id"] for e in events] == ["a" * 32, "b" * 32, "0a1b2c3d"] and reoffered == [] and dropped == 0,
          "inbox: claims every pending event oldest first; a v1 event's id is its file-name suffix")
    check(sorted(p.name for p in (inbox / ".claimed").iterdir())
          == sorted(f"{T0}-{i}.json" for i in ("a" * 32, "b" * 32, "0a1b2c3d"))
          and not list(inbox.glob("*.json")),
          "inbox: a claim is a rename to .claimed/<claimed_at_ms>-<id>.json; nothing is deleted")
    check(co.count_pending(inbox, T0 + 1) == 0, "count: fresh claims are not pending")
    check(co.claim_mailbox(inbox, T0 + 1) == ([], [], 0), "inbox: a fresh claim is not re-offered")
    acked, missing = co.ack_events(inbox, ["a" * 32, "b" * 32, "0a1b2c3d"])
    check(len(acked) == 3 and missing == [] and not list((inbox / ".claimed").iterdir()),
          "ack: deletes each id's claimed file")
    check(co.count_pending(inbox, T0 + 10 * STALE_MS) == 0
          and co.claim_mailbox(inbox, T0 + 10 * STALE_MS) == ([], [], 0),
          "ack: a retired batch is never re-offered")

    # ---- no ack, then N minutes later: the same ids again -----------------
    inbox = tmp / "b" / "chief-inbox"
    publish(inbox, f"{T0}-{'c' * 32}.json", id="c" * 32)
    publish(inbox, f"{T0}-{'e' * 32}.json", id="e" * 32)
    first, _, _ = co.claim_mailbox(inbox, T0)
    check(co.count_pending(inbox, T0 + STALE_MS - 1) == 0, "count: a claim one ms short of stale is not pending")
    check(co.count_pending(inbox, T0 + STALE_MS) == 2, "count: a claim exactly at the stale age is pending")
    check(co.claim_mailbox(inbox, T0 + STALE_MS - 1)[0] == [], "inbox: before the stale window nothing is re-offered")
    again, reoffered, _ = co.claim_mailbox(inbox, T0 + STALE_MS)
    check(sorted(e["id"] for e in again) == sorted(e["id"] for e in first) == ["c" * 32, "e" * 32]
          and sorted(reoffered) == ["c" * 32, "e" * 32],
          "inbox: an unacked batch is re-offered with the same ids after the stale window")
    check(sorted(p.name for p in (inbox / ".claimed").iterdir())
          == [f"{T0 + STALE_MS}-{'c' * 32}.json", f"{T0 + STALE_MS}-{'e' * 32}.json"],
          "inbox: a re-offered event is re-claimed under the new claim time")
    digest = co.format_mailbox_batch(again, reoffered, 0, now=datetime.fromtimestamp((T0 + STALE_MS) / 1000, tz=timezone.utc))
    check(digest.startswith("INBOX=events n=2 reoffered=2 dropped=0")
          and f"id={'c' * 32} dispatch=d1 re-offered" in digest
          and f"ACK=chief_ops.py ack {'c' * 32} {'e' * 32}" in digest and "  | done" in digest,
          f"inbox digest: header, id + dispatch + re-offered per line, the excerpt, the ACK line ({digest!r})")
    # Acking after a re-offer moved the file back to the root still retires it.
    co.reoffer_stale_claims(inbox, T0 + 3 * STALE_MS)
    acked, missing = co.ack_events(inbox, ["c" * 32, "f" * 32])
    check(acked == ["c" * 32] and missing == ["f" * 32] and co.count_pending(inbox, T0 + 3 * STALE_MS) == 1,
          "ack: retires a re-offered root file too; an unknown id is reported missing")

    # ---- the pending rule, by file name alone -----------------------------
    inbox = tmp / "c" / "chief-inbox"
    claimed = inbox / ".claimed"
    claimed.mkdir(parents=True)
    (inbox / "123-pub.json").write_text("{not json -- counted by name, dropped by the reader", encoding="utf-8")
    (inbox / ".event.k3x9.tmp").write_text("temp", encoding="utf-8")  # an unpublished write
    (inbox / ".hidden.json").write_text("{}", encoding="utf-8")
    (inbox / "notes.txt").write_text("x", encoding="utf-8")
    (inbox / "dir.json").mkdir()
    (inbox / ".claim-42-99").mkdir()  # wait-event's private claim folder
    (inbox / ".claim-42-99" / "9-held.json").write_text("{}", encoding="utf-8")
    (claimed / f"{T0}-fresh.json").write_text("{}", encoding="utf-8")
    (claimed / f"{T0 - STALE_MS}-stale.json").write_text("{}", encoding="utf-8")
    (claimed / "no-claim-time.json").write_text("{}", encoding="utf-8")
    (claimed / f".{T0}-x.json.tmp").write_text("{}", encoding="utf-8")
    check(co.count_pending(inbox, T0) == 3,
          "count: one root *.json + one stale claim + one claim with no readable time; "
          "temps, dot-files, non-.json, directories and wait-event's claim folder never count")
    code, out = cli(inbox, "inbox", "--count")
    check(code == 0 and out.strip() == "PENDING=3" and (inbox / "123-pub.json").exists(),
          f"inbox --count: prints PENDING=<n> and claims nothing ({out.strip()!r})")
    events, reoffered, dropped = co.claim_mailbox(inbox, T0)
    check(dropped == 1 and sorted(reoffered) == ["no-claim-time", "stale"]
          and sorted(e["id"] for e in events) == ["no-claim-time", "stale"],
          "inbox: junk is dropped; a stale or unstamped claim is re-offered and read")
    check((inbox / ".event.k3x9.tmp").exists() and (inbox / ".claim-42-99" / "9-held.json").exists(),
          "inbox: never touches a temp or wait-event's claim folder")
    old = tmp / "d" / "chief-inbox"
    publish(old, f"{T0}-{'0' * 32}.json", ts=iso(T0 - int(co.INBOX_EVENT_TTL_SECONDS * 1000) - 1000))
    check(co.claim_mailbox(old, T0) == ([], [], 1), "inbox: an event past the 24 h TTL is dropped")

    # ---- inbox and wait-event coexist: no event is read twice -------------
    inbox = tmp / "e" / "chief-inbox"
    now_ms = co.mailbox._now_ms()
    for n in range(60):
        publish(inbox, f"{now_ms + n}-{n:032x}.json", number=n, ts=iso(now_ms))
    seen: "list[list]" = [[], []]
    gate = threading.Barrier(2)

    def v1_reader() -> None:
        gate.wait()
        seen[0] = [e["number"] for e in co.claim_batch(inbox)]

    def v2_reader() -> None:
        gate.wait()
        seen[1] = [e["number"] for e in co.claim_mailbox(inbox)[0]]

    threads = [threading.Thread(target=v1_reader), threading.Thread(target=v2_reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check(sorted(seen[0] + seen[1]) == list(range(60)),
          f"coexistence: wait-event and inbox racing split 60 events with none lost or doubled "
          f"({len(seen[0])} + {len(seen[1])})")
    check(co.claim_batch(inbox) == [], "coexistence: wait-event never reads a v2 claim")

    # ---- a claimed file that can't be read yet is never dropped (#1465) ----
    # Injected read failures stand in for the Windows race: another claimer's
    # rename handle still open on the file ("lock": a sharing violation), then
    # that claimer moving it out of this reader's claim ("move": not found).
    elsewhere = tmp / "other-claimer"
    elsewhere.mkdir()
    real_read, real_backoff = Path.read_text, co.mailbox.CLAIM_READ_BACKOFF_SECONDS
    plan: "dict[str, list]" = {}

    def injected_read(self, *args, **kwargs):
        steps = plan.get(self.stem.partition("-")[2])
        if self.parent.name.startswith(".claim") and steps:
            step = steps.pop(0)
            if step == "move":
                shutil.move(str(self), str(elsewhere / self.name))
                raise FileNotFoundError(2, "No such file or directory", str(self))
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real_read(self, *args, **kwargs)

    breadcrumbs: "list[str]" = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            breadcrumbs.append(record.getMessage())

    collector, old_level = Collect(), co.LOGGER.level
    co.LOGGER.addHandler(collector)
    co.LOGGER.propagate = False  # collected here, not printed into the gate's output
    co.LOGGER.setLevel(logging.INFO)
    ids = {n: f"{1465:028x}{n:04x}" for n in range(4)}

    def arm(inbox: Path) -> None:
        for n, event_id in ids.items():
            # Stamped by the real clock: wait-event filters its 24 h TTL by it.
            publish(inbox, f"{T0 + n}-{event_id}.json", number=n, ts=iso(co.mailbox._now_ms()))
        plan.clear()
        plan.update({ids[0]: ["lock", "lock"], ids[1]: ["move"],
                     ids[2]: ["lock"] * co.CLAIM_READ_ATTEMPTS})

    co.mailbox.CLAIM_READ_BACKOFF_SECONDS = 0.0
    Path.read_text = injected_read
    try:
        inbox = tmp / "h" / "chief-inbox"
        arm(inbox)
        batch = [e["number"] for e in co.claim_batch(inbox)]
        check(batch == [0, 3], f"read retry: wait-event delivers an event locked twice, then readable (got {batch})")
        check((elsewhere / f"{T0 + 1}-{ids[1]}.json").exists(),
              "read retry: wait-event never deletes a claim that moved to a concurrent claimer")
        check(sorted(p.name for p in inbox.iterdir()) == [f"{T0 + 2}-{ids[2]}.json"],
              "read retry: wait-event puts a still-locked claim back in the inbox, and leaves no claim folder")
        Path.read_text = real_read
        again = [e["number"] for e in co.claim_batch(inbox)]
        check(again == [2], f"read retry: the put-back event is delivered by the next batch (got {again})")
        check(any("retrying" in line for line in breadcrumbs)
              and any("concurrent claimer" in line for line in breadcrumbs)
              and any("put back in the inbox" in line for line in breadcrumbs),
              f"read retry: wait-event logs the retry, the skip and the put-back ({breadcrumbs})")

        breadcrumbs.clear()
        Path.read_text = injected_read
        inbox = tmp / "i" / "chief-inbox"
        arm(inbox)
        events, _, dropped = co.claim_mailbox(inbox, T0)
        numbers = [e["number"] for e in events]
        check(numbers == [0, 3] and dropped == 0,
              f"read retry: inbox delivers the twice-locked event and drops nothing (got {numbers}, dropped={dropped})")
        check((elsewhere / f"{T0}-{ids[1]}.json").exists(),
              "read retry: inbox never deletes a claim that moved to a concurrent claimer")
        check((inbox / co.CLAIMED_DIRNAME / f"{T0}-{ids[2]}.json").exists(),
              "read retry: inbox leaves a still-locked claim in .claimed/")
        Path.read_text = real_read
        events, reoffered, _ = co.claim_mailbox(inbox, T0 + STALE_MS)
        check([e["number"] for e in events if e["id"] == ids[2]] == [2] and ids[2] in reoffered,
              "read retry: inbox re-offers the still-locked claim once stale")
        check(any("unreadable after" in line for line in breadcrumbs),
              f"read retry: inbox logs the claim it left unread ({breadcrumbs})")
    finally:
        Path.read_text, co.mailbox.CLAIM_READ_BACKOFF_SECONDS = real_read, real_backoff
        co.LOGGER.removeHandler(collector)
        co.LOGGER.propagate = True
        co.LOGGER.setLevel(old_level)

    # ---- CLI: empty inbox, ack exit codes, an unlistable directory --------
    code, out = cli(tmp / "f" / "chief-inbox", "inbox")
    check(code == 0 and out.strip() == "INBOX=empty", f"inbox CLI: an empty inbox ({out.strip()!r})")
    code, out = cli(tmp / "f" / "chief-inbox", "ack", "nope")
    check(code == 1 and "ACKED=0" in out and "ACK_MISSING=nope" in out, "ack CLI: a missing id exits 1, named")
    blocker = tmp / "g"
    blocker.write_text("a file where the inbox dir should be", encoding="utf-8")
    code, out = cli(blocker, "inbox", "--count")
    check(code == 1 and out.startswith("PENDING=unknown"),
          f"inbox --count: an unlistable inbox is unknown, never 0 ({out.strip()!r})")

    # ---- wait-event's digest: v2 adds kind + excerpt, nothing else moves ---
    now_dt = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    base = {"launcher_sid": "a1b2c3d4e5f6", "repo": "fleet-config", "number": 1455,
            "agent": "codex", "ts": iso(T0 - 120_000)}
    digest = co.format_inbox_digest([
        {**base, "v": 2, "event": "Stop", "kind": "turn-end", "excerpt": "line one\nline two",
         "excerpt_source": "payload"},
        {**base, "v": 2, "event": "Stop", "kind": "needs-input", "excerpt": None,
         "excerpt_unavailable": "no last-assistant field and no transcript_path"},
        {**base, "event": "SessionEnd"},
    ], now=now_dt)
    check(digest.splitlines() == [
        "WAIT_EVENT=events n=3",
        "- fleet-config#1455: turn ended (codex) sid=a1b2c3d4 age=2m kind=turn-end",
        "  excerpt (payload, 17 chars):",
        "  | line one",
        "  | line two",
        "- fleet-config#1455: needs input (codex) sid=a1b2c3d4 age=2m kind=needs-input",
        "  excerpt: unavailable (no last-assistant field and no transcript_path)",
        "- fleet-config#1455: session ended (codex) sid=a1b2c3d4 age=2m",
        "Next: `exchange <sid>` for each, `verify` any completion claim, then absorb or escalate.",
    ], f"wait-event digest: v2 appends kind and indented excerpt lines; a v1 line is unchanged ({digest!r})")

    # ---- dispatch records a dispatch_id ------------------------------------
    registry = tmp / "chief-managed.json"
    row = chief_managed.mark("sid-1", "fleet-config", 1455, dispatch_id="f" * 32, path=registry)
    stored = json.loads(registry.read_text(encoding="utf-8"))["sid-1"]
    check(row["dispatch_id"] == stored["dispatch_id"] == "f" * 32, "mark: stores the optional dispatch_id")
    check("dispatch_id" not in chief_managed.mark("sid-2", "x", 1, path=registry),
          "mark: a row without a dispatch_id stays the old shape")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_chief_mailbox")
