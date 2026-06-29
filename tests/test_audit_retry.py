"""Unit tests for the pure logic in skills/_lib/audit_retry.py.

No Task Scheduler writes — the attempt-counting state machine is exercised
directly (``decide`` / ``load_state`` / ``save_state``), and the ``arm`` / ``clear``
CLI is driven with a temp ``--state`` file and ``--dry-run`` so the scheduling
side effect is printed, not executed.

Run: `py tests/test_audit_retry.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import audit_retry as ar  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def _arm(state: Path, *extra: str) -> str:
    """Run `arm --dry-run` against a temp state file; return combined stdout."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "audit_retry.py"),
         "arm", "--state", str(state), "--dry-run",
         "--bat", r"E:\fake\run-weekly.bat", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check(proc.returncode == 0, f"arm exit 0 ({proc.stderr.strip()})")
    return proc.stdout


def _run(state: Path, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "audit_retry.py"),
         *args, "--state", str(state)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout


# ---- decide: launch counting + the arm/final boundary ----

check(ar.decide(0, 3) == (1, True), "decide first launch -> arm")
check(ar.decide(1, 3) == (2, True), "decide second launch -> arm")
check(ar.decide(2, 3) == (3, False), "decide third launch -> final, no arm")
check(ar.decide(0, 1) == (1, False), "decide max=1 -> first launch is final")
check(ar.decide(5, 3) == (6, False), "decide past cap stays final")

# ---- load_state / save_state round-trip + resilience ----

tmp = Path(tempfile.mkdtemp(prefix="audit_retry_"))
try:
    st = tmp / "state.json"
    check(ar.load_state(st) == {}, "load_state missing -> empty")
    ar.save_state(st, {"attempts": 2})
    check(ar.load_state(st) == {"attempts": 2}, "save/load round-trip")
    st.write_text("not json{", encoding="utf-8")
    check(ar.load_state(st) == {}, "load_state corrupt -> empty (no crash)")
    st.unlink()

    # ---- arm CLI: counts up, arms until the final attempt, then stops ----
    out1 = _arm(st)
    check("ATTEMPT=1/3" in out1 and "ARMED=yes" in out1, "arm #1 -> 1/3 armed")
    check("PSCMD=" in out1 and "Register-ScheduledTask" in out1, "arm #1 prints scheduler cmd")
    check('"E:\\fake\\run-weekly.bat" resume' in out1, "arm #1 relaunch passes resume")
    check("AddHours(4.0)" in out1, "arm #1 default ~4h trigger")
    check(json.loads(st.read_text(encoding="utf-8"))["attempts"] == 1, "arm #1 persists attempts=1")

    out2 = _arm(st)
    check("ATTEMPT=2/3" in out2 and "ARMED=yes" in out2, "arm #2 -> 2/3 armed")

    out3 = _arm(st)
    check("ATTEMPT=3/3" in out3 and "ARMED=no" in out3, "arm #3 -> 3/3 final")
    check("PSCMD=" not in out3, "arm #3 (final) registers no retry task")

    # custom --hours / --max flow through
    st2 = tmp / "state2.json"
    out_h = _arm(st2, "--hours", "5", "--max", "2")
    check("ATTEMPT=1/2" in out_h and "AddHours(5.0)" in out_h, "arm honours --hours/--max")

    # ---- status reflects the counter ----
    check("ATTEMPTS=3" in _run(st, "status"), "status reports attempts")

    # ---- clear zeroes the chain + removes state, idempotently ----
    out_c = _run(st, "clear", "--dry-run")
    check("CLEARED" in out_c, "clear prints CLEARED")
    check("Unregister-ScheduledTask" in out_c, "clear (dry-run) prints unregister cmd")
    check(not st.exists(), "clear removes the state file")
    check("ATTEMPTS=0" in _run(st, "status"), "status after clear -> 0")
    check("CLEARED" in _run(st, "clear", "--dry-run"), "clear is idempotent (no state -> no crash)")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

if _fails:
    print("FAIL test_audit_retry:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_audit_retry: all checks pass")
