"""Regression tests for the three defects /fleet-health kept surviving on
hand-applied workarounds (fleet-config#812).

Three consecutive weekly runs delivered a digest only because the attending
agent re-derived three fixes from the previous run's ledger entry. Each one
below is pinned here so the scheduled run stops depending on rediscovery.

**UTF-8 stdout under capture** (5th occurrence). Both entry points print
report text; Windows falls back to cp1252 when stdout is a pipe, so a single
arrow raises UnicodeEncodeError and exits 1 -- in the scheduled run only,
never in a terminal, which is why it shipped five times. Driven as a real
subprocess with a pipe for stdout and `PYTHONUTF8`/`PYTHONIOENCODING` scrubbed
from the child env: a test that inherits a UTF-8 environment proves nothing.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_health.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "fleet-health"

sys.path.insert(0, str(REPO / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# U+2192 RIGHTWARDS ARROW is the character the live run actually died on, and
# U+2248 / an emoji are the other two shapes the ledger carries. None of the
# three is representable in cp1252, so any one of them alone exits 1.
ARROW = "\u2192"
NON_CP1252 = "64.47 " + ARROW + " 106.09 GB \u2248 +41.6 \U0001fa7a"


def _captured(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run a skill entry point with stdout+stderr on a **pipe**, never a tty.

    The child env is scrubbed of `PYTHONUTF8`/`PYTHONIOENCODING` so Python
    picks the Windows ANSI code page exactly as it does under an app-launcher
    job. Output is decoded here, not by the child, so a crash stays visible as
    a traceback rather than being masked by this process's own encoding.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    proc = subprocess.run([sys.executable, *argv], cwd=str(cwd), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=120, creationflags=NO_WINDOW)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


tmp = Path(tempfile.mkdtemp(prefix="fleet_health_test_"))
try:
    # ------------------------------------------------------------- 1. UTF-8

    # ---- ledger.py previous: the step-1 crash, against a realistic entry ----
    root = tmp / "ledger-root"
    root.mkdir()
    (root / "fleet-health.md").write_text(
        "# Fleet health ledger\n\n## 2026-01-02\n\n### box \u2014 2026-01-02\n\n"
        "**Findings** \u2014 RAM " + NON_CP1252 + "\n\n## 2026-01-01\n\nolder\n",
        encoding="utf-8")

    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "previous"], REPO)
    check(code == 0,
          "ledger.py previous: exits 0 under captured stdout (got %s)" % code)
    check("UnicodeEncodeError" not in out,
          "ledger.py previous: no UnicodeEncodeError under captured stdout")
    check(ARROW in out,
          "ledger.py previous: the arrow the live run died on round-trips to stdout")
    check("PREVIOUS_RUN=2026-01-02" in out,
          "ledger.py previous: still reports the newest run heading")

    # ---- ledger.py append: a non-cp1252 entry writes, then reads back ----
    entries = tmp / "entries.md"
    entries.write_text(
        "### box \u2014 2026-01-03\n\n**Findings** \u2014 " + NON_CP1252 + "\n",
        encoding="utf-8")
    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "append",
         "--date", "2026-01-03", "--file", str(entries)], REPO)
    check(code == 0,
          "ledger.py append: exits 0 under captured stdout (got %s)" % code)
    code, out = _captured(
        [str(SKILL / "ledger.py"), "--root", str(root), "previous"], REPO)
    check(code == 0 and ARROW in out and "PREVIOUS_RUN=2026-01-03" in out,
          "ledger.py append -> previous: a non-cp1252 entry round-trips through the ledger")

    # ---- capture.py collect: emit() prints hub-supplied reasons ----
    # `runs` is empty, so collect reads the skipped entries straight out of the
    # state file and makes no HTTP call -- the print path is the whole test.
    out_dir = tmp / "run-utf8"
    out_dir.mkdir()
    (out_dir / ".run-state.json").write_text(json.dumps({
        "run_date": "2026-01-02",
        "ledger_root": str(root),
        "out_dir": str(out_dir),
        "targets": {}, "runs": {},
        "skipped": [{"id": "peer", "detail": "no-hub",
                     "reason": "no hub answering " + NON_CP1252}],
    }), encoding="utf-8")
    code, out = _captured(
        [str(SKILL / "capture.py"), "--out-dir", str(out_dir), "collect"], REPO)
    check("UnicodeEncodeError" not in out,
          "capture.py collect: no UnicodeEncodeError printing a hub-supplied reason")
    check(ARROW in out,
          "capture.py collect: a non-cp1252 reason reaches stdout intact")
    check(code == 4,
          "capture.py collect: still exits 4 when nothing was captured (got %s)" % code)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_health")
