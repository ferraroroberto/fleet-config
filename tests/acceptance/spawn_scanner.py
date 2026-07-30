"""Static AST spawn-flag scanner (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (c) --
`_spawn_sites_missing_flags` / `_spawn_import_style_offenders` walk every
`subprocess.*`/`.Popen(...)` call site under hooks/, skills/_lib/, and
.claude/skills/*/ and flag any missing `creationflags=...NO_WINDOW` (a
console-flash on a headless spawn, fleet-config#412), plus
`_no_window_unit_check`, the `_x_unit_check`-shaped wrapper the acceptance
matrix actually calls.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker

# Directories whose Python is *runtime* code — it spawns executables under a
# console-less parent (a scheduled `claude -p` job, a tray, a hook). `tests/` is
# excluded on purpose: the acceptance suite runs from a real console, and several
# cases assert on spawn kwargs, so forcing the flag there would be noise.
_SPAWN_SCAN_DIRS = ("hooks", "skills", ".claude/skills")
_SPAWN_ATTRS = {"run", "Popen", "call", "check_output", "check_call"}


def _spawn_sites_missing_flags() -> "list[str]":
    """Every `subprocess.<spawn>(...)` under `_SPAWN_SCAN_DIRS` that omits
    `creationflags=`, as `path:line` strings. Parsed with `ast`, so a commented-
    out or string-literal example can't produce a false positive."""
    import ast

    offenders: list[str] = []
    for rel in _SPAWN_SCAN_DIRS:
        for py in sorted((REPO / rel).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError) as exc:  # pragma: no cover - byte-compile catches these first
                offenders.append(f"{py.relative_to(REPO).as_posix()}: unparseable ({exc})")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                # `from subprocess import Popen` would evade this Attribute match;
                # `_spawn_import_style_offenders` below asserts nobody uses it.
                if not (isinstance(fn, ast.Attribute) and fn.attr in _SPAWN_ATTRS
                        and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
                    continue
                if any(kw.arg == "creationflags" for kw in node.keywords):
                    continue
                offenders.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
    return offenders


def _spawn_import_style_offenders() -> "list[str]":
    """Files under `_SPAWN_SCAN_DIRS` using `from subprocess import <spawn>`.

    That form is bare-name-called, so the AST scan above cannot see it. Keeping
    the count at zero is what makes the scan a sound gate rather than a partial
    one — hence a check of its own rather than a silently-broadened matcher."""
    import ast

    offenders: list[str] = []
    for rel in _SPAWN_SCAN_DIRS:
        for py in sorted((REPO / rel).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError):  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom) and node.module == "subprocess"
                        and any(a.name in _SPAWN_ATTRS for a in node.names)):
                    offenders.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
    return offenders


def _no_window_unit_check() -> Tuple[int, int]:
    """Windows console suppression on every runtime subprocess spawn (#412).

    The global CLAUDE.md convention ("Subprocess spawns must suppress the console
    window (Windows)", #399) is invisible at runtime on this box — an unsuppressed
    spawn only misbehaves under a *console-less* parent, which is exactly where
    nobody is watching: the scheduled `claude -p` jobs behind every
    `run-weekly.bat`. So the gate is static: parse the runtime trees and assert
    every spawn carries `creationflags`, plus assert the two tiers' `NO_WINDOW`
    definitions agree so the intentional duplication cannot drift.
    """
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(REPO / "skills" / "_lib"))
    import _lib  # noqa: E402
    import no_window  # noqa: E402

    check = _Checker()

    expected = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    check("no_window: skills/_lib NO_WINDOW == CREATE_NO_WINDOW on win32, else 0",
          no_window.NO_WINDOW == expected, f"got {no_window.NO_WINDOW!r}, want {expected!r}")
    check("no_window: hooks/_lib NO_WINDOW agrees with the skills-tier copy",
          _lib.NO_WINDOW == no_window.NO_WINDOW,
          f"hooks={_lib.NO_WINDOW!r} skills={no_window.NO_WINDOW!r}")

    import_offenders = _spawn_import_style_offenders()
    check("no_window: no runtime file uses `from subprocess import <spawn>` "
          "(would evade the scan below)",
          not import_offenders, "\n".join(import_offenders))

    offenders = _spawn_sites_missing_flags()
    check(f"no_window: every subprocess spawn in {', '.join(_SPAWN_SCAN_DIRS)} "
          "passes creationflags",
          not offenders,
          "missing creationflags=NO_WINDOW at:\n" + "\n".join(offenders))

    return check.failures, check.total


