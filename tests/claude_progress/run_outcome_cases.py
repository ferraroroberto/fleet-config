"""Enumerates run-outcome evidence and drives it through the real `_verdict`.

Shared by the golden-table test and its recorder. The cases set the formatter's
own evidence fields and call `scheduled_runner._verdict`, so the table pins what
a drained run reports (exit code and final status line), not how the code
reaches it.
"""
from __future__ import annotations

import itertools
import re
from types import SimpleNamespace
from typing import Iterator, Optional

from _suite import cp

# The outcome-bearing facts. Each is an independent switch; the child exit code
# (0 and 1) is the other axis, so the enumeration is 2 x 2**11 = 4096 cases.
FACTS = (
    "stalled",
    "orphans",        # owned descendants alive after the provider exited, not reaped
    "transient",      # the CLI reported an upstream 5xx
    "provider_error", # an auth failure was classified
    "kill",           # the background-task kill message on stderr
    "self_reported",  # the run printed SCHEDULED-RUN-FAILED
    "result_error",   # the terminal result event said failed
    "unfinished",     # a tool was still open at exit
    "saw_result",     # a terminal result event arrived
    "burst",          # three unknown records inside the shutdown window
    "tool_use",       # the run invoked something
)
# A child can exit with one of the reserved codes by itself. These are pinned
# against a clean base and against each single fact flipped.
NATIVE_EXITS = (2, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 127, 130)
_BASE = {name: False for name in FACTS} | {"saw_result": True, "tool_use": True}
# Paths the enumeration cannot reach: cancellation, a child with no exit status,
# and orphans the runner reaped.
SPECIAL = {
    "cancelled_confirmed": dict(child_exit=0, cancelled=True, cancel_confirmed=True),
    "cancelled_unconfirmed": dict(child_exit=0, cancelled=True, cancel_confirmed=False),
    "cancelled_pipes_open": dict(child_exit=0, cancelled=True, cancel_confirmed=True, reader_eof=False),
    "cancelled_and_stalled": dict(child_exit=1, cancelled=True, cancel_confirmed=True, stalled=True),
    "no_exit_status": dict(child_exit=None),
    "no_exit_status_transient": dict(child_exit=None, transient=True),
    "reaped_orphans_clean": dict(child_exit=0, reaped=True),
    "reaped_orphans_no_tools": dict(child_exit=0, reaped=True, tool_use=False),
    "reaped_orphans_nonzero": dict(child_exit=1, reaped=True),
}
_NOW = 100.0
_STATUS_RE = re.compile(r"^\[[0-9:]+\] (?P<status>.*?) · exit (?P<code>\d+)(?: · no terminal result event)?$")


def build_formatter(lines: list[str], facts: dict[str, bool]) -> "cp.ProgressFormatter":
    """A formatter holding exactly the evidence `facts` names."""
    formatter = cp.ProgressFormatter(emit=lines.append, clock=lambda: _NOW)
    formatter._saw_transient_api_error = facts["transient"]
    formatter._provider_error = "auth" if facts["provider_error"] else ""
    formatter._saw_kill_signature = facts["kill"]
    formatter._saw_self_reported_failure = facts["self_reported"]
    formatter._result_error = facts["result_error"]
    formatter._saw_result = facts["saw_result"]
    formatter._saw_tool_use = facts["tool_use"]
    if facts["unfinished"]:
        formatter._tools["t1"] = "Bash"
    if facts["burst"]:
        formatter._unknown = 3
        formatter._unknown_timestamps = [_NOW] * 3
    return formatter


def drive(facts: dict[str, bool], child_exit: Optional[int], *, cancelled: bool = False,
          cancel_confirmed: bool = False, reader_eof: bool = True,
          reaped: bool = False) -> tuple[int, str]:
    """Run the facts through `_verdict`; return (exit code, final status text)."""
    lines: list[str] = []
    formatter = build_formatter(lines, facts)
    orphaned = facts["orphans"] or reaped
    outcome = {"exit_code": child_exit, "orphaned": orphaned,
               "orphan_count": 2 if orphaned else 0, "orphan_members": [],
               "reap_names": ["grep"] if reaped else None, "drain_message": None}
    stall_state = {"stalled": facts["stalled"], "cancelled": cancelled,
                   "cancel_confirmed": cancel_confirmed, "stop_complete": False}
    readers = (SimpleNamespace(eof=reader_eof), SimpleNamespace(eof=reader_eof))
    code = cp._verdict(formatter, outcome, stall_state, readers)
    match = _STATUS_RE.match(lines[-1])
    assert match and int(match["code"]) == code, lines[-1]
    return code, match["status"]


def _bits(facts: dict[str, bool]) -> str:
    return "".join("1" if facts[name] else "0" for name in FACTS)


def all_cases() -> Iterator[tuple[str, dict[str, bool], Optional[int], dict]]:
    """Every pinned case as (key, facts, child exit, drive options), in a stable order."""
    for child_exit in (0, 1):
        for values in itertools.product((False, True), repeat=len(FACTS)):
            facts = dict(zip(FACTS, values))
            yield f"enum:{child_exit}:{_bits(facts)}", facts, child_exit, {}
    for child_exit in NATIVE_EXITS:
        yield f"native:{child_exit}:base", dict(_BASE), child_exit, {}
        for name in FACTS:
            facts = dict(_BASE) | {name: not _BASE[name]}
            yield f"native:{child_exit}:flip-{name}", facts, child_exit, {}
    for name, options in SPECIAL.items():
        options = dict(options)
        child_exit = options.pop("child_exit")
        facts = dict(_BASE)
        for fact in tuple(options):
            if fact in FACTS:
                facts[fact] = options.pop(fact)
        yield f"special:{name}", facts, child_exit, options
