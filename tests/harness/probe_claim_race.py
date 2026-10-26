"""Opt-in stress probe: two real concurrent claimers on a 60-event inbox; not part of the offline acceptance gate.

fleet-config#1465. On Windows two racing renames of one file can both succeed,
so a claimer whose read of a just-claimed file failed used to delete an event
the other claimer was moving -- about 2% of iterations lost one. This drives
the real race (no injection) for both reader pairs, `wait-event` x
`wait-event` (`claim_batch` twice) and `wait-event` x `inbox` (`claim_batch`
with `claim_mailbox`), in temp dirs only -- never the live inbox. The gate
pins the same rule deterministically with injected read failures
(tests/chief_ops/test_chief_mailbox.py).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/harness/probe_claim_race.py [--iterations 400]`
Exit 0 when no event was lost or doubled, 1 otherwise (the failing iterations are printed).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_ops as co  # noqa: E402

EVENTS = 60


def race(second_reader: str) -> "tuple[list[int], int]":
    """One iteration: `(missing event numbers, doubled count)`."""
    tmp = Path(tempfile.mkdtemp(prefix="claim_race_"))
    try:
        inbox = tmp / "chief-inbox"
        inbox.mkdir()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for n in range(EVENTS):
            (inbox / f"{1700000001000 + n}-{n:08x}.json").write_text(
                json.dumps({"event": "Stop", "number": n, "ts": stamp}), encoding="utf-8")
        batches: "list[list]" = [[], []]
        gate = threading.Barrier(2)

        def claimer(slot: int) -> None:
            gate.wait()
            if slot and second_reader == "inbox":
                batches[slot] = co.claim_mailbox(inbox)[0]
            else:
                batches[slot] = co.claim_batch(inbox)

        threads = [threading.Thread(target=claimer, args=(slot,)) for slot in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        numbers = [e["number"] for e in batches[0] + batches[1]]
        return sorted(set(range(EVENTS)) - set(numbers)), len(numbers) - len(set(numbers))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=400)
    args = parser.parse_args()
    failed = False
    for second_reader in ("wait-event", "inbox"):
        bad = []
        for iteration in range(args.iterations):
            missing, doubled = race(second_reader)
            if missing or doubled:
                bad.append((iteration, missing, doubled))
        lost = sum(len(missing) for _, missing, _ in bad)
        print(f"CLAIM_RACE pair=wait-event+{second_reader} iterations={args.iterations} "
              f"failed_iterations={len(bad)} lost_events={lost}")
        for iteration, missing, doubled in bad[:10]:
            print(f"  iteration {iteration}: lost {missing} doubled={doubled}")
        failed = failed or bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
