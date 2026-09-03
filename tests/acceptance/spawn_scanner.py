"""Static AST spawn-flag scanner (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (c) --
`_spawn_sites_missing_flags` / `_spawn_import_style_offenders` walk every
`subprocess.*`/`.Popen(...)` call site under hooks/, skills/_lib/, and
.claude/skills/*/ and flag any missing `creationflags=...NO_WINDOW` (a
console-flash on a headless spawn, fleet-config#412), plus
`_no_window_unit_check`, the `_x_unit_check`-shaped wrapper the acceptance
matrix actually calls.

Also home to the sibling call-site gate `_git_wrapper_unit_check`
(fleet-config#677, fleet-config#728): the same trees, scanned for a
hand-rolled `subprocess.<spawn>(["git"/"gh", ...])` that bypasses the tier's
`run_git`/`run_gh` wrapper and so silently opts out of `GIT_OPTIONAL_LOCKS=0`
(fleet-config#667) or the `gh` UTF-8-decoding/NO_WINDOW combo (fleet-config#679).
Same shape of defect, same static-AST answer, hence the same module.
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


def _resolves_to_no_window(node) -> bool:
    """True if a `creationflags=` value provably resolves to the shared
    `NO_WINDOW` constant (directly, via attribute access, or OR'd into a
    combined-flags expression like `CREATE_NEW_PROCESS_GROUP | _lib.NO_WINDOW`
    for a long-lived child, cf. CLAUDE.md's `_no_window_flags()` pattern).

    Deliberately conservative: a bare `0`, a re-inlined
    `subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0` ternary (the
    exact drift CLAUDE.md forbids), or any other expression that isn't provably
    `NO_WINDOW` does NOT resolve, so it's reported as an offender rather than
    silently trusted (fleet-config#503)."""
    import ast

    if isinstance(node, ast.Name):
        return node.id == "NO_WINDOW"
    if isinstance(node, ast.Attribute):
        return node.attr == "NO_WINDOW"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _resolves_to_no_window(node.left) or _resolves_to_no_window(node.right)
    return False


def _missing_creationflags_in_tree(tree, label: str) -> "list[str]":
    """Every `subprocess.<spawn>(...)` call in a parsed `tree` that either omits
    `creationflags=` entirely or passes a value that doesn't provably resolve to
    `NO_WINDOW`, as `label:line` strings. Shared by the real file scan below and
    by the in-memory unit-test cases so both exercise the identical logic."""
    import ast

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # `from subprocess import Popen` would evade this Attribute match;
        # `_spawn_import_style_offenders` below asserts nobody uses it.
        if not (isinstance(fn, ast.Attribute) and fn.attr in _SPAWN_ATTRS
                and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        kw = next((kw for kw in node.keywords if kw.arg == "creationflags"), None)
        if kw is not None and _resolves_to_no_window(kw.value):
            continue
        offenders.append(f"{label}:{node.lineno}")
    return offenders


def _spawn_sites_missing_flags() -> "list[str]":
    """Every `subprocess.<spawn>(...)` under `_SPAWN_SCAN_DIRS` that omits
    `creationflags=` or passes a value that doesn't provably resolve to
    `NO_WINDOW`, as `path:line` strings. Parsed with `ast`, so a commented-out or
    string-literal example can't produce a false positive."""
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
            offenders.extend(_missing_creationflags_in_tree(tree, py.relative_to(REPO).as_posix()))
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


# The two files allowed to spawn `git`/`gh` directly: they *are* the wrappers.
# Every other runtime file must route through `_lib.run_git`/`_lib.run_gh`
# (hooks tier) or `git_run.run_git`/`git_run.run_gh` (skills tier) so
# `GIT_OPTIONAL_LOCKS=0`/UTF-8 decoding and `NO_WINDOW` apply everywhere, not
# just wherever someone remembered (fleet-config#677, fleet-config#728).
_GIT_WRAPPER_FILES = {"hooks/_lib.py", "skills/_lib/git_run.py"}
_GH_WRAPPER_FILES = _GIT_WRAPPER_FILES


def _argv_head_exe(node) -> "str | None":
    """The bare executable name of a spawn call's first positional argument,
    if it's a list literal whose first element is a string constant — else
    `None`.

    Deliberately literal-only: a spawn built from a variable can't be judged
    statically, and guessing would make the gate noisy rather than sound. The
    hand-rolled sites this exists to catch are all written out inline."""
    import ast

    if not node.args:
        return None
    argv = node.args[0]
    if not (isinstance(argv, ast.List) and argv.elts):
        return None
    head = argv.elts[0]
    if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
        return None
    return head.value.replace("\\", "/").rsplit("/", 1)[-1]


def _raw_exe_spawns_in_tree(tree, label: str, exe_names: "set[str]") -> "list[str]":
    """Every `subprocess.<spawn>([<exe>, ...], ...)` in a parsed `tree` whose
    argv head resolves to one of `exe_names`, as `label:line` strings. Shared
    by the `git` and `gh` file scans and their synthetic unit cases so all of
    them exercise the identical matcher."""
    import ast

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in _SPAWN_ATTRS
                and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        if _argv_head_exe(node) in exe_names:
            offenders.append(f"{label}:{node.lineno}")
    return offenders


def _raw_exe_spawn_sites(exe_names: "set[str]", wrapper_files: "set[str]") -> "list[str]":
    """Every hand-rolled spawn of `exe_names` under `_SPAWN_SCAN_DIRS`,
    excluding `wrapper_files` (the files that are supposed to contain one)."""
    import ast

    offenders: list[str] = []
    for rel in _SPAWN_SCAN_DIRS:
        for py in sorted((REPO / rel).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            label = py.relative_to(REPO).as_posix()
            if label in wrapper_files:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError) as exc:  # pragma: no cover - byte-compile catches these first
                offenders.append(f"{label}: unparseable ({exc})")
                continue
            offenders.extend(_raw_exe_spawns_in_tree(tree, label, exe_names))
    return offenders


def _raw_git_spawn_sites() -> "list[str]":
    """Every hand-rolled `git` spawn under `_SPAWN_SCAN_DIRS`, excluding the two
    wrapper files that are supposed to contain one."""
    return _raw_exe_spawn_sites({"git", "git.exe"}, _GIT_WRAPPER_FILES)


def _raw_gh_spawn_sites() -> "list[str]":
    """Every hand-rolled `gh` spawn under `_SPAWN_SCAN_DIRS`, excluding the two
    wrapper files that are supposed to contain one (fleet-config#728)."""
    return _raw_exe_spawn_sites({"gh", "gh.exe"}, _GH_WRAPPER_FILES)


def _git_wrapper_unit_check() -> Tuple[int, int]:
    """No runtime file hand-rolls a `git`/`gh` spawn around its tier's
    `run_git`/`run_gh` (#677, #728).

    `run_git` is not a style preference: it is where `GIT_OPTIONAL_LOCKS=0`
    lives, the fix for the stranded 0-byte `index.lock` that left nine repos
    unable to commit for fifteen days (fleet-config#667). `run_gh` is the same
    fix for the `gh` CLI's own drifted UTF-8-decoding/timeout/NO_WINDOW combo
    (fleet-config#728, fleet-config#679). A raw `subprocess.run(["git"/"gh",
    ...])` silently opts back out of either, and — exactly like an
    unsuppressed spawn — nothing at runtime says so: every affected command
    keeps exiting 0 and printing the right answer. So the gate is static, and
    it sits beside the NO_WINDOW scanner it mirrors.
    """
    check = _Checker()

    offenders = _raw_git_spawn_sites()
    check(f"git_wrapper: no hand-rolled `git` spawn in {', '.join(_SPAWN_SCAN_DIRS)} "
          f"(route through run_git; wrappers exempt: {', '.join(sorted(_GIT_WRAPPER_FILES))})",
          not offenders,
          "hand-rolled git spawns at:\n" + "\n".join(offenders))

    gh_offenders = _raw_gh_spawn_sites()
    check(f"gh_wrapper: no hand-rolled `gh` spawn in {', '.join(_SPAWN_SCAN_DIRS)} "
          f"(route through run_gh; wrappers exempt: {', '.join(sorted(_GH_WRAPPER_FILES))})",
          not gh_offenders,
          "hand-rolled gh spawns at:\n" + "\n".join(gh_offenders))

    import ast

    synthetic = (
        "import subprocess\n"
        "subprocess.run(['git', 'diff', '--cached'], creationflags=_lib.NO_WINDOW)\n"
        "subprocess.run([GIT_EXE, 'status'], creationflags=_lib.NO_WINDOW)\n"
        "subprocess.run(['gh', 'issue', 'view'], creationflags=_lib.NO_WINDOW)\n"
        "_lib.run_git(['-C', repo, 'diff', '--cached'])\n"
        "subprocess.Popen(['git', 'fetch'], creationflags=_lib.NO_WINDOW)\n"
        "_lib.run_gh(['issue', 'view'])\n"
        "subprocess.Popen(['gh', 'issue', 'comment'], creationflags=_lib.NO_WINDOW)\n"
    )
    git_lines = {int(o.rsplit(":", 1)[1])
                 for o in _raw_exe_spawns_in_tree(ast.parse(synthetic), "synthetic", {"git", "git.exe"})}
    check("git_wrapper: a literal `subprocess.run(['git', ...])` is reported",
          2 in git_lines, f"offending lines seen: {sorted(git_lines)}")
    check("git_wrapper: a non-literal argv head is NOT reported (unjudgeable statically)",
          3 not in git_lines, f"offending lines seen: {sorted(git_lines)}")
    check("git_wrapper: a non-git spawn (`gh`) is NOT reported",
          4 not in git_lines, f"offending lines seen: {sorted(git_lines)}")
    check("git_wrapper: a `run_git` call is NOT reported",
          5 not in git_lines, f"offending lines seen: {sorted(git_lines)}")
    check("git_wrapper: `subprocess.Popen(['git', ...])` is reported too",
          6 in git_lines, f"offending lines seen: {sorted(git_lines)}")

    gh_lines = {int(o.rsplit(":", 1)[1])
                for o in _raw_exe_spawns_in_tree(ast.parse(synthetic), "synthetic", {"gh", "gh.exe"})}
    check("gh_wrapper: a literal `subprocess.run(['gh', ...])` is reported",
          4 in gh_lines, f"offending lines seen: {sorted(gh_lines)}")
    check("gh_wrapper: a non-gh spawn (`git`) is NOT reported",
          2 not in gh_lines, f"offending lines seen: {sorted(gh_lines)}")
    check("gh_wrapper: a `run_gh` call is NOT reported",
          7 not in gh_lines, f"offending lines seen: {sorted(gh_lines)}")
    check("gh_wrapper: `subprocess.Popen(['gh', ...])` is reported too",
          8 in gh_lines, f"offending lines seen: {sorted(gh_lines)}")

    return check.failures, check.total


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
          "passes creationflags resolving to NO_WINDOW",
          not offenders,
          "missing/non-NO_WINDOW creationflags at:\n" + "\n".join(offenders))

    # Negative cases (fleet-config#503): the matcher must reject a value that
    # merely *has* the `creationflags` keyword but doesn't provably resolve to
    # NO_WINDOW — `creationflags=0` and the exact re-inlined ternary CLAUDE.md
    # forbids — while still accepting the two legitimate shapes.
    import ast

    negative_src = (
        "import subprocess\n"
        "import sys\n"
        "subprocess.run(cmd, creationflags=0)\n"
        "subprocess.run(cmd, creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0))\n"
        "subprocess.run(cmd, creationflags=_lib.NO_WINDOW)\n"
        "subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _lib.NO_WINDOW)\n"
        "subprocess.run(cmd)\n"
    )
    neg_offenders = _missing_creationflags_in_tree(ast.parse(negative_src), "synthetic")
    neg_lines = {int(o.rsplit(":", 1)[1]) for o in neg_offenders}
    check("no_window: creationflags=0 is reported as an offender",
          3 in neg_lines, f"offending lines seen: {sorted(neg_lines)}")
    check("no_window: re-inlined win32-ternary creationflags is reported as an offender",
          4 in neg_lines, f"offending lines seen: {sorted(neg_lines)}")
    check("no_window: creationflags=_lib.NO_WINDOW is NOT reported as an offender",
          5 not in neg_lines, f"offending lines seen: {sorted(neg_lines)}")
    check("no_window: combined CREATE_NEW_PROCESS_GROUP | _lib.NO_WINDOW is NOT reported as an offender",
          6 not in neg_lines, f"offending lines seen: {sorted(neg_lines)}")
    check("no_window: a spawn with no creationflags at all is reported as an offender",
          7 in neg_lines, f"offending lines seen: {sorted(neg_lines)}")

    return check.failures, check.total


