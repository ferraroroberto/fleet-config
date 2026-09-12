"""Unit tests for the pure logic in skills/_lib/skip_delta.py (fleet-config#846).

Exercises `parse_pytest_output` / `compare` directly against realistic pytest
terminal output, plus the `capture` + `compare` CLI end-to-end through a
throwaway temp dir. No real fleet repo, no real pytest run.

The anchor case is the one that motivated the helper: the 2026-09-12
app-launcher merge round, where a fresh-checkout gate skipped two more tests
than the primary-checkout run and printed the same green.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_skip_delta.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import skip_delta as sd  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- fixtures: the app-launcher merge round that motivated this ----

PRIMARY_RUN = """\
============================= test session starts ==============================
platform win32 -- Python 3.12.4, pytest-8.2.0
collected 140 items

tests/e2e/test_boot.py ......                                            [  4%]
tests/e2e/test_terminal_reconnect.py ..                                  [ 12%]

=========================== short test summary info ============================
SKIPPED [15] tests/e2e/conftest.py:88: chrome not installed
SKIPPED [2] tests/e2e/test_theme.py:41: dark-mode fixture unavailable
================== 123 passed, 17 skipped in 402.15s (0:06:42) =================
"""

FRESH_CHECKOUT_RUN = """\
============================= test session starts ==============================
platform win32 -- Python 3.12.4, pytest-8.2.0
collected 140 items

tests/e2e/test_boot.py ......                                            [  4%]
tests/e2e/test_terminal_reconnect.py ss                                  [ 12%]

=========================== short test summary info ============================
SKIPPED [15] tests/e2e/conftest.py:88: chrome not installed
SKIPPED [2] tests/e2e/test_theme.py:41: dark-mode fixture unavailable
SKIPPED [2] tests/e2e/test_terminal_reconnect.py:197: could not launch PTY session (HTTP 404: {"detail":"unknown app app-launcher"})
================== 121 passed, 19 skipped in 398.02s (0:06:38) =================
"""

# Same suite, gate run without `-rs`: the count is knowable, the names are not.
COUNT_ONLY_RUN = """\
============================= test session starts ==============================
collected 140 items
................................................ss                       [100%]
================== 121 passed, 19 skipped in 398.02s (0:06:38) =================
"""


# ---- parsing ----

primary = sd.parse_pytest_output(PRIMARY_RUN)
check(primary.total == 17, "parse: skip count comes off the terminal summary line")
check(primary.named_count == 17 and primary.names_complete,
      "parse: the -rs block names every skip the summary counted")
check(primary.named[0].location == "tests/e2e/conftest.py:88" and primary.named[0].count == 15,
      "parse: SKIPPED [n] multiplicity is kept, not collapsed to one")

fresh = sd.parse_pytest_output(FRESH_CHECKOUT_RUN)
pty = [s for s in fresh.named if "terminal_reconnect" in s.location]
check(len(pty) == 1 and pty[0].count == 2,
      "parse: the location is split at <path>:<line>, not at the first colon")
check(pty[0].reason.startswith("could not launch PTY session (HTTP 404:"),
      "parse: a reason containing its own colons survives intact")

count_only = sd.parse_pytest_output(COUNT_ONLY_RUN)
check(count_only.total == 19 and count_only.named == () and not count_only.names_complete,
      "parse: a run without -rs yields a count but no names")

no_summary = sd.parse_pytest_output("collected 3 items\nInterrupted: the run died\n")
check(no_summary.total is None and "never established" in no_summary.reason,
      "parse: no summary line -> total is None, never a confident 0")

no_tests = sd.parse_pytest_output("====== no tests ran in 0.01s ======\n")
check(no_tests.total is None,
      "parse: a run that collected nothing established nothing -- None, not 0")

clean = sd.parse_pytest_output("====== 140 passed in 12.30s ======\n")
check(clean.total == 0, "parse: a summary with no 'skipped' term really is zero skips")
check(clean.summary_line == "140 passed in 12.30s",
      "parse: pytest's '=' padding is stripped, so a KEY= prefix stays readable")

decoy = sd.parse_pytest_output(
    "tests/test_x.py::test_when_2_skipped_things_happen PASSED\n"
    "====== 1 passed in 0.10s ======\n"
)
check(decoy.total == 0,
      "parse: a test name containing '2 skipped' is not mistaken for the summary line")

# `-v` names skips inline instead of in a short-summary block.
verbose = sd.parse_pytest_output(
    "tests/test_a.py::test_one PASSED                       [ 33%]\n"
    "tests/test_a.py::test_two SKIPPED (needs chrome)       [ 66%]\n"
    "tests/test_a.py::test_three SKIPPED (needs chrome)     [100%]\n"
    "====== 1 passed, 2 skipped in 0.12s ======\n"
)
check(verbose.total == 2 and verbose.names_complete,
      "parse: -v per-test SKIPPED lines are a valid naming source")
check(verbose.named[0].reason == "needs chrome",
      "parse: the -v parenthesised reason is captured")

# `-rA` prints both spellings of the same skip -- counting both would double it.
both_forms = sd.parse_pytest_output(
    "tests/test_a.py::test_two SKIPPED (needs chrome)       [ 66%]\n"
    "tests/test_a.py::test_three SKIPPED (needs chrome)     [100%]\n"
    "=========================== short test summary info ============================\n"
    "SKIPPED [2] tests/test_a.py:5: needs chrome\n"
    "====== 1 passed, 2 skipped in 0.12s ======\n"
)
check(both_forms.named_count == 2 and both_forms.names_complete,
      "parse: the -rs block wins outright over -v lines, so -rA never double-counts")

backslashed = sd.parse_pytest_output(
    "SKIPPED [1] tests\\e2e\\test_x.py:12: nope\n====== 1 skipped in 0.10s ======\n"
)
check(backslashed.named[0].location == "tests/e2e/test_x.py:12",
      "parse: Windows backslashes are normalised so two checkouts compare")


# ---- comparison ----

d = sd.compare(primary, fresh)
check(d.status == "INCREASED" and d.delta == 2,
      "compare: the fresh checkout skipping more is INCREASED, with the delta")
check(d.set_status == "CHANGED" and d.names == "complete",
      "compare: a fully-named increase reports the set as CHANGED")
check(len(d.new) == 1 and d.new[0].location == "tests/e2e/test_terminal_reconnect.py:197"
      and d.new[0].count == 2,
      "compare: the tests that stopped running are named, with their increase")

d = sd.compare(primary, primary)
check(d.status == "SAME" and d.set_status == "SAME" and d.new == (),
      "compare: an identical run is SAME on both facts")

d = sd.compare(fresh, primary)
check(d.status == "DECREASED" and d.delta == -2 and d.set_status == "SAME",
      "compare: running more than the baseline is DECREASED, not a failure")

# Same count, different tests -- the silent-loss shape with no count to catch it.
shifted = sd.parse_pytest_output(
    "SKIPPED [15] tests/e2e/conftest.py:88: chrome not installed\n"
    "SKIPPED [2] tests/e2e/test_other.py:9: no registry\n"
    "====== 123 passed, 17 skipped in 400.00s ======\n"
)
d = sd.compare(primary, shifted)
check(d.status == "SAME" and d.set_status == "CHANGED",
      "compare: an unchanged count with a changed set is still reported as CHANGED")
check(len(d.new) == 1 and d.new[0].location == "tests/e2e/test_other.py:9",
      "compare: the newly-skipped test is named even when the count did not move")

d = sd.compare(count_only, count_only)
check(d.status == "SAME" and d.set_status == "UNCONFIRMED" and d.names == "none",
      "compare: equal counts with no names is UNCONFIRMED, never a quiet SAME")
check("-rs" in d.reason, "compare: UNCONFIRMED says how to establish the fact")

d = sd.compare(primary, count_only)
check(d.status == "INCREASED" and d.names == "partial" and d.set_status == "UNCONFIRMED",
      "compare: a count-only current run still yields the count fact, names partial")

d = sd.compare(primary, no_summary)
check(d.status == "UNKNOWN" and d.delta is None and "current run" in d.reason,
      "compare: an unparseable current run is UNKNOWN, never folded into SAME")

d = sd.compare(no_summary, primary)
check(d.status == "UNKNOWN" and "baseline run" in d.reason,
      "compare: an unparseable baseline is UNKNOWN and says which side failed")


# ---- serialization ----

blob = sd.run_to_dict(primary, "primary checkout")
restored = sd.run_from_dict(json.loads(json.dumps(blob)))
check(restored == primary, "serialize: a baseline round-trips through JSON byte-for-byte")

try:
    sd.run_from_dict({"schema": 99})
    check(False, "serialize: an unknown baseline schema is rejected")
except ValueError as exc:
    check("unsupported baseline schema" in str(exc),
          "serialize: an unknown baseline schema is rejected")


# ---- CLI end-to-end ----

PYTHON = sys.executable
CLI = REPO / "skills" / "_lib" / "skip_delta.py"

tmp = Path(tempfile.mkdtemp(prefix="skip-delta-"))
try:
    base_out = tmp / "primary.txt"
    base_out.write_text(PRIMARY_RUN, encoding="utf-8")
    fresh_out = tmp / "fresh.txt"
    fresh_out.write_text(FRESH_CHECKOUT_RUN, encoding="utf-8")
    baseline_json = tmp / "baseline.json"

    res = subprocess.run(
        [PYTHON, str(CLI), "capture", str(base_out),
         "--label", "primary checkout", "--out", str(baseline_json)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check(res.returncode == 0 and "STATUS=CAPTURED" in res.stderr and "SKIPPED=17" in res.stderr,
          "CLI capture: reports what it captured on stderr")
    check(json.loads(baseline_json.read_text(encoding="utf-8"))["label"] == "primary checkout",
          "CLI capture: --out writes the baseline JSON")

    res = subprocess.run(
        [PYTHON, str(CLI), "compare", str(fresh_out), "--baseline", str(baseline_json)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = res.stdout
    check(res.returncode == 0, "CLI compare: report-only, always exits 0")
    check("STATUS=INCREASED" in out and "DELTA=+2" in out,
          "CLI compare: the count verdict and a signed delta")
    check("NEW=tests/e2e/test_terminal_reconnect.py:197 (+2): could not launch PTY session" in out,
          "CLI compare: names the test that stopped running, with its reason")
    check("BASELINE_SUMMARY=" in out and "CURRENT_SUMMARY=" in out,
          "CLI compare: echoes both summary lines verbatim, so a broken run is visible")

    res = subprocess.run(
        [PYTHON, str(CLI), "compare", str(fresh_out), "--baseline", str(tmp / "nope.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check("STATUS=UNKNOWN" in res.stdout and "baseline unusable" in res.stdout,
          "CLI compare: a missing baseline is UNKNOWN, never a quiet pass")

    # stdin path, used when a gate is piped straight in rather than saved.
    res = subprocess.run(
        [PYTHON, str(CLI), "compare", "-", "--baseline", str(baseline_json)],
        input=FRESH_CHECKOUT_RUN, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    check("STATUS=INCREASED" in res.stdout, "CLI compare: reads the current run from stdin")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_skip_delta")
