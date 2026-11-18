"""The units the weekly burst runs and the per-change gate does not.

A unit lands here when it loads the CPU on purpose, waits out a real wall-clock bound, or spawns
interpreters in bulk to prove one hook behaviour. Run on every change they slow the box for every
lane at once, and a verdict taken on a loaded box is the least trustworthy one. Each is still
exercised weekly (`tests/run_weekly_units.py`, driven by `.claude/skills/weekly-burst/burst.py`),
both in the working tree and in a fresh detached clone.

One table row = one standalone suite file, the same shape as `standalone_dispatch`; a function unit
is listed in `WEEKLY_FUNCTION_UNITS`. A new burn proof is a row here, never one in the gate.
"""
from __future__ import annotations

from typing import Callable, Tuple

from acceptance.guard_checks.host_power_checks import _host_power_codex_unit_checks

WEEKLY_SUITES: Tuple[Tuple[str, str, str], ...] = (
    ("cpu_burn", "repo/test_cpu_burn.py",
     "the load-proof helper starts real CPU burners below normal priority and stops only its own, "
     "even when the helper is hard-killed (fleet-config#1076)"),
    ("hook_spawn_timeout", "acceptance/guard_checks/test_hook_spawn_timeout.py",
     "the shared hook-spawn bound is a hang guard: a deliberately slow 16 s start passes it and "
     "fails the former 15 s bound"),
    ("fleet_health", "audit/test_fleet_health.py",
     "the /fleet-health helpers under a scrubbed code page; three real 15 s poll sleeps make it a "
     "45 s wall-clock wait, so the cp1252-under-capture crash, a run date recomputed across "
     "midnight and a moved hub config stay caught weekly (fleet-config#812)"),
)

WEEKLY_FUNCTION_UNITS: Tuple[Callable[[], Tuple[int, int]], ...] = (
    _host_power_codex_unit_checks,
)
