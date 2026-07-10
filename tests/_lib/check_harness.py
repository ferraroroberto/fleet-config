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
"""

from __future__ import annotations

from typing import List


class CheckHarness:
    def __init__(self) -> None:
        self.fails: List[str] = []

    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            self.fails.append(msg)

    def report_and_exit(self, label: str) -> None:
        """Print a pass/fail summary; exit 1 if any check failed, else 0."""
        if self.fails:
            print(f"FAILED {len(self.fails)} check(s):")
            for f in self.fails:
                print(f"  - {f}")
            raise SystemExit(1)
        print(f"{label}: all checks pass")
        raise SystemExit(0)
