"""Static AST scan for a foreign-harness tool id leaking out of the hook_wire translation layer (fleet-config#1558).

`/codebase-audit`'s claude-md-drift bucket flagged the identical defect twice in one finding --
`branch_before_edit_guard.py`'s own `GUARDED_TOOLS` tuple and `docs_dated_filename_guard.py`'s `tool ==
"apply_patch"` branch both named Codex's own tool id directly instead of asking `_lib.edit_event()` (which already
recognizes it). Project CLAUDE.md's rule -- "a hook must never learn a second harness's key names, event
vocabulary, or tool ids; a harness sending something new means extending the maps there" -- had no enforcement
besides a human audit happening to notice it twice. This makes that static, the same AST-scan shape
`spawn_scanner.py` already uses for the NO_WINDOW/git-wrapper gates.

Scope is narrow and literal on purpose: only the exact string `"apply_patch"` is tracked, and only outside a
`dict` key -- `codex_attention.py`'s human-readable per-tool message table keys on the raw tool name for display,
never a gating decision, and must stay unflagged. A docstring or comment mentioning the id in prose never matches:
the check is exact-value equality against a `Constant`, not a substring search.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Tuple

from acceptance.shared import HOOKS, REPO, _Checker

# The one sanctioned home: `edit_event()` already recognizes `apply_patch` and
# every hook is meant to ask it, not re-learn the id itself.
_TOOL_ID_WIRE_HOME = "hooks/hook_wire/edit_events.py"
_FOREIGN_TOOL_IDS = {"apply_patch"}


def _hook_files() -> "list[Path]":
    """Every `.py` under `hooks/` (including its `hook_wire`/`guard_checks`-shaped
    subpackages), minus `__pycache__` -- the tree this rule applies to."""
    return sorted(p for p in HOOKS.rglob("*.py") if "__pycache__" not in p.parts)


def _dict_key_ids(tree) -> "set[int]":
    """`id()` of every AST node used as a `dict` literal's key anywhere in `tree`.

    A lookup table keyed by a harness's raw tool name for a human-readable
    message (`codex_attention.py`'s approval-prompt copy) is not a gating
    decision -- excluding dict keys is what keeps that pattern unflagged.
    """
    return {id(key) for node in ast.walk(tree) if isinstance(node, ast.Dict)
             for key in node.keys if key is not None}


def _tool_id_leaks_in_tree(tree, label: str) -> "list[str]":
    """Every `Constant` node in `tree` whose value is a tracked foreign tool id
    and which is not a dict key, as `label:line` strings."""
    excluded = _dict_key_ids(tree)
    return [f"{label}:{node.lineno}" for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in _FOREIGN_TOOL_IDS and id(node) not in excluded]


def _tool_id_leak_sites() -> "list[str]":
    """Every tracked tool-id literal under `hooks/`, excluding `_TOOL_ID_WIRE_HOME`."""
    offenders: list[str] = []
    for py in _hook_files():
        label = py.relative_to(REPO).as_posix()
        if label == _TOOL_ID_WIRE_HOME:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError) as exc:  # pragma: no cover - byte-compile catches these first
            offenders.append(f"{label}: unparseable ({exc})")
            continue
        offenders.extend(_tool_id_leaks_in_tree(tree, label))
    return offenders


def _tool_id_leak_unit_check() -> Tuple[int, int]:
    """No hook outside `edit_events.py` branches on a foreign harness's own tool id directly (fleet-config#1558).

    `EditTarget.operation` (`add`/`update`/`write`/`delete`/`rename`) is the normalized vocabulary every harness's
    edit already maps into, so a hook that needs to tell a new file from an update never needs the raw tool id.
    """
    check = _Checker()

    offenders = _tool_id_leak_sites()
    check(f"tool_id_leak: no hook literal-compares a foreign tool id outside {_TOOL_ID_WIRE_HOME} "
          "(ask `_lib.edit_event(payload).status` / `.targets[*].operation` instead)",
          not offenders, "tool-id leaks at:\n" + "\n".join(offenders))

    synthetic = (
        'GUARDED_TOOLS = ("Edit", "Write", "apply_patch")\n'
        'if tool not in GUARDED_TOOLS:\n'
        '    pass\n'
        'if tool == "apply_patch":\n'
        '    pass\n'
        'MESSAGES = {"apply_patch": "approval to edit files"}\n'
        'if change.operation not in {"add", "write"}:\n'
        '    pass\n'
    )
    synthetic_tree = ast.parse(synthetic)
    lines = {int(o.rsplit(":", 1)[1]) for o in _tool_id_leaks_in_tree(synthetic_tree, "synthetic")}
    check("tool_id_leak: a tuple literal carrying the id is reported at its own assignment",
          1 in lines, f"offending lines seen: {sorted(lines)}")
    check("tool_id_leak: a direct `== \"apply_patch\"` comparison is reported",
          4 in lines, f"offending lines seen: {sorted(lines)}")
    check("tool_id_leak: a dict key (a message lookup, not a gating decision) is NOT reported",
          6 not in lines, f"offending lines seen: {sorted(lines)}")
    check("tool_id_leak: a comparison against the normalized `operation` vocabulary is NOT reported",
          7 not in lines, f"offending lines seen: {sorted(lines)}")

    return check.failures, check.total
