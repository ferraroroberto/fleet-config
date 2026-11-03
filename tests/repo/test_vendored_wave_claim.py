"""The /propagate-vendored wave claim: one wave at a time, machine-wide.

Drives skills/_lib/vendored_wave/wave_claim.py against a throwaway hooks-state directory:
a second holder is refused while the first holds the claim, the same holder may re-acquire
(the skill re-runs the plan), a claim past the TTL is reclaimable, only the holder can
release, an unusable claim directory reports `unknown` rather than free, and the CLI prints
the `REFUSED=wave-running` line with a non-zero exit. No real fleet state is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_vendored_wave_claim.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from vendored_wave import plan as vw  # noqa: E402
from vendored_wave import wave_claim as wc  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

NOW = 1_000_000.0
TTL = 8.0


def _cli(argv: list) -> tuple:
    out = io.StringIO()
    with redirect_stdout(out):
        code = wc.main(argv)
    return code, out.getvalue()


root = Path(tempfile.mkdtemp(prefix="wave-claim-"))
try:
    lock = root / "propagate-vendored" / "wave.lock"

    first = wc.acquire(lock, "wave-a", NOW, TTL)
    check(first.state == wc.HELD and first.holder["holder"] == "wave-a", f"acquire: the first wave holds the claim ({first})")

    second = wc.acquire(lock, "wave-b", NOW + 60, TTL)
    check(second.state == wc.REFUSED and second.holder["holder"] == "wave-a",
          f"acquire: a second wave while one is held is refused, naming the holder ({second})")
    check(wc.refusal_line(second).startswith("REFUSED=wave-running holder=wave-a since="),
          f"refusal_line: names the claim, the holder and when it started ({wc.refusal_line(second)})")

    again = wc.acquire(lock, "wave-a", NOW + 120, TTL)
    check(again.state == wc.HELD, "acquire: the holder re-acquiring (the plan is re-run after a human approval) stays held")
    check(wc.acquire(lock, "wave-b", NOW + 130, TTL).state == wc.REFUSED, "acquire: a re-acquire does not open the claim to others")

    check(wc.release(lock, "wave-b") == wc.NOT_HOLDER and lock.exists(),
          "release: a wave that does not hold the claim cannot drop another's")
    check(wc.release(lock, "wave-a") == wc.RELEASED and not lock.exists(), "release: the holder frees the claim")
    check(wc.release(lock, "wave-a") == wc.ABSENT, "release: releasing a free claim is idempotent")
    check(wc.acquire(lock, "wave-b", NOW + 200, TTL).state == wc.HELD, "acquire: after release the next wave holds the claim")
    wc.release(lock, "wave-b")

    # ---- a crashed holder expires on the TTL ----
    wc.acquire(lock, "crashed", NOW, TTL)
    check(wc.acquire(lock, "next", NOW + TTL * 3600 - 1, TTL).state == wc.REFUSED, "ttl: just inside the TTL the claim still holds")
    reclaimed = wc.acquire(lock, "next", NOW + TTL * 3600 + 1, TTL)
    check(reclaimed.state == wc.HELD and reclaimed.holder["holder"] == "next", f"ttl: past the TTL the claim is reclaimable ({reclaimed})")
    wc.release(lock, "next")

    # ---- an unreadable claim state is its own state, never free ----
    blocker = root / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    unknown = wc.acquire(blocker / "wave.lock", "wave-a", NOW, TTL)
    check(unknown.state == wc.UNKNOWN and unknown.reason, f"unknown: a claim directory that cannot be created is unknown, not held ({unknown})")
    check(wc.refusal_line(unknown).startswith("WAVE_CLAIM=unknown reason="), "refusal_line: unknown prints its own line")

    torn = root / "propagate-vendored" / "torn.lock"
    torn.mkdir(parents=True)
    check(wc.acquire(torn, "wave-a", time.time(), TTL).state == wc.UNKNOWN,
          "unknown: a recent claim whose holder cannot be read is not reclaimed as free")
    check(wc.acquire(torn, "wave-a", time.time() + TTL * 3600 + 5, TTL).state == wc.HELD,
          "unknown: the same unreadable claim is reclaimable once it ages past the TTL")
    wc.release(torn, "wave-a")

    # ---- CLI: exit codes and the printed lines ----
    base = ["--lock-dir", str(lock)]
    code, out = _cli(["acquire", *base, "--holder", "wave-a"])
    check(code == 0 and out.startswith("WAVE_CLAIM=held holder=wave-a"), f"cli: acquire prints the held line and exits 0 ({code}, {out!r})")
    code, out = _cli(["acquire", *base, "--holder", "wave-b"])
    check(code == 1 and out.startswith("REFUSED=wave-running holder=wave-a since="), f"cli: a second acquire exits non-zero with REFUSED=wave-running ({code}, {out!r})")
    code, out = _cli(["status", *base])
    check(code == 0 and "WAVE_CLAIM=held holder=wave-a" in out, f"cli: status names the holder ({out!r})")
    code, out = _cli(["release", *base, "--holder", "wave-b"])
    check(code == 1 and lock.exists(), f"cli: release by a non-holder exits non-zero and keeps the claim ({code}, {out!r})")
    code, out = _cli(["release", *base, "--holder", "wave-a"])
    check(code == 0 and not lock.exists(), f"cli: release by the holder exits 0 ({code}, {out!r})")
    code, out = _cli(["status", *base])
    check(code == 0 and "WAVE_CLAIM=free" in out, f"cli: status of a free claim says free ({out!r})")
    code, out = _cli(["acquire", "--lock-dir", str(blocker / "wave.lock"), "--holder", "wave-a"])
    check(code == 3 and out.startswith("WAVE_CLAIM=unknown reason="), f"cli: an unusable claim directory exits 3 as unknown ({code}, {out!r})")

    # ---- plan.py: --claim refuses before scanning; no flag stays read-only and claim-free ----
    wc.acquire(lock, "other-wave", time.time(), TTL)
    out = io.StringIO()
    with redirect_stdout(out):
        code = vw.main(["plan", "--scaffold", str(root / "no-scaffold"), "--claim", "--holder", "mine", "--lock-dir", str(lock)])
    check(code == 1 and out.getvalue().startswith("REFUSED=wave-running holder=other-wave"),
          f"plan --claim: a running wave refuses the plan before any scan ({code}, {out.getvalue()!r})")
    out = io.StringIO()
    with redirect_stdout(out):
        code = vw.main(["plan", "--scaffold", str(root / "no-scaffold")])
    check(code == 0 and "scaffold not found" in out.getvalue(), "plan without --claim stays read-only and ignores a running wave (in-flight drivers keep working)")
    check(json.loads((lock / "meta.json").read_text(encoding="utf-8"))["holder"] == "other-wave", "plan: the dry run never touched the held claim")
    wc.release(lock, "other-wave")
    out = io.StringIO()
    with redirect_stdout(out):
        code = vw.main(["plan", "--scaffold", str(root / "no-scaffold"), "--claim", "--holder", "mine", "--lock-dir", str(lock)])
    check(code == 0 and lock.exists() and wc.status(lock).holder["holder"] == "mine",
          f"plan --claim: a free claim is taken for the wave ({code}, {out.getvalue()!r})")
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_wave_claim")
