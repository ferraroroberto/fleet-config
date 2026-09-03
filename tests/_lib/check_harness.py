"""Shared pass/fail harness for the standalone `tests/test_*.py` pure-logic
files (fleet-config#318).

Each of these files runs a flat list of `check(cond, msg)` assertions against
a helper's pure logic, then reports a summary and exits — before this module
existed, eight of them hand-rolled the identical `_fails: list[str] = []` /
`check()` / final report-and-exit trio, byte-for-byte, with only the exit-code
call style (`sys.exit` vs `raise SystemExit`) and the report-banner wording
drifting between copies. One shared harness now owns that.

Usage (matches the existing per-file shape so call sites barely change):

    from check_harness import CheckHarness
    _h = CheckHarness()
    check = _h.check

    check(1 + 1 == 2, "math works")
    ...
    _h.report_and_exit("test_whatever")

A fact the environment cannot always establish (e.g. Windows junction
creation, which needs a privilege this account may lack) is `_h.skip(...)`,
never folded into a plain `check(True, ...)` pass -- see `skip()` below and
fleet-config#730.
"""

from __future__ import annotations

from typing import List


class CheckHarness:
    def __init__(self) -> None:
        self.fails: List[str] = []
        self.skips: List[str] = []

    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            self.fails.append(msg)

    def skip(self, msg: str) -> None:
        """Record that a check could not be run at all -- its own state,
        never folded into a pass. Mirrors `tests/acceptance/shared.py`'s
        `_Checker.advisory`: a skip is reported loudly but does not fail the
        suite, because nothing this repo controls decides whether e.g. this
        account can create Windows junctions. `check(True, "... skipped")`
        reads as a verified pass in the summary even though nothing was
        verified -- the exact "unknown folded into passing" anti-pattern the
        global CLAUDE.md forbids (fleet-config#730)."""
        self.skips.append(msg)

    def report_and_exit(self, label: str) -> None:
        """Print a pass/fail/skip summary; exit 1 if any check failed, else 0.
        Skips never fail the suite (see `skip()`) but are always listed, so a
        run that verified nothing for a given fact is never silently green."""
        if self.fails:
            print(f"FAILED {len(self.fails)} check(s):")
            for f in self.fails:
                print(f"  - {f}")
            raise SystemExit(1)
        if self.skips:
            print(f"SKIPPED {len(self.skips)} check(s):")
            for s in self.skips:
                print(f"  - {s}")
        print(f"{label}: all checks pass")
        raise SystemExit(0)
