"""Hook-payload acceptance matrix + foreign-harness shape parity (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (a) --
drive each Tier-1 hook with a synthetic Claude-shaped and Grok-shaped payload
and assert the expected exit code, including the py_syntax_check fixture
files and the Grok Build parity cases (fleet-config#491). The cases are data,
one module per hook family under `hook_cases/`; this module concatenates them
in a fixed order and runs them. `run_hook_matrix()` returns `(failures, total)`
so `tests/run_acceptance.py`'s `run_unit()` dispatch loop folds it in exactly
like every other check.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Tuple

from acceptance.hook_cases import Case
from acceptance.hook_cases.commit_guards import PRE_COMMIT_CASES, SECRET_SCAN_CASES
from acceptance.hook_cases.fakes import FAKE_AKIA, FAKE_GHP, FAKE_SK, FAKE_XOXB
from acceptance.hook_cases.grok_shape import grok_cases
from acceptance.hook_cases.lifecycle_hooks import (
    NOTIFY_ON_IDLE_CASES,
    SESSION_START_CASES,
    py_syntax_cases,
)
from acceptance.hook_cases.safe_kill import SAFE_KILL_CASES
from acceptance.hook_cases.shell_guards import (
    CONTEXT_FILTER_HOOK_CASES,
    DOCS_GUARD_CASES,
    VENV_CASES,
    WINDOWS_PATH_CASES,
)
from acceptance.shared import assert_exit, run

# Re-exported: `checks_dispatch` and `checks_guards` import the fakes from here.
__all__ = ["FAKE_AKIA", "FAKE_GHP", "FAKE_SK", "FAKE_XOXB", "matrix_cases", "run_hook_matrix"]


def matrix_cases() -> Tuple[List[Case], Path]:
    """Every `(label, hook, payload, expected exit)` case, plus the temp dir
    holding the py_syntax_check fixture files, which the caller removes.
    `checks_dispatch` replays the same cases through `hook_dispatch.py`."""
    tmp = Path(tempfile.mkdtemp(prefix="fleet-config-test-"))
    cases: List[Case] = [
        *PRE_COMMIT_CASES,
        *SECRET_SCAN_CASES,
        *SAFE_KILL_CASES,
        *VENV_CASES,
        *WINDOWS_PATH_CASES,
        *DOCS_GUARD_CASES,
        *CONTEXT_FILTER_HOOK_CASES,
        *py_syntax_cases(tmp),
        *NOTIFY_ON_IDLE_CASES,
        *SESSION_START_CASES,
        *grok_cases(),
    ]
    return cases, tmp


def run_hook_matrix() -> Tuple[int, int]:
    cases, tmp = matrix_cases()
    failures = 0
    for name, hook, payload, expected in cases:
        code, _stdout, stderr = run(hook, payload)
        if not assert_exit(name, expected, code, stderr):
            failures += 1

    # The py_syntax_check fixture files are only ever read by the cases just run.
    shutil.rmtree(tmp, ignore_errors=True)

    return failures, len(cases)
