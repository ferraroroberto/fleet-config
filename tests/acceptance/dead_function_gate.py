"""Static dead-function gate.

A top-level function nothing in production calls is dead even when a test
exercises it: the test pins behavior no runtime path reaches, so it stays green
while the code rots. The gate parses the runtime trees and fails on any
top-level function whose name is never read as a name, attribute or import
outside its own `def`. A call from `tests/` does not count, and neither does
prose: a docstring or SKILL.md line that names the function is how the dead
ones used to look alive.
"""
from __future__ import annotations

import ast
from typing import Dict, List, Set, Tuple

from acceptance.shared import REPO, _Checker
from acceptance.spawn_scanner import _runtime_py_files


def _referenced(tree: ast.AST) -> Set[str]:
    refs: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.alias):
            refs.add(node.name.rsplit(".", 1)[-1])
    return refs


def dead_functions(sources: Dict[str, str]) -> List[str]:
    """`label:line name` for each top-level function in `sources` that no source in it references."""
    trees = {}
    for label, text in sources.items():
        try:
            trees[label] = ast.parse(text)
        except SyntaxError:
            continue
    refs: Set[str] = set()
    for tree in trees.values():
        refs |= _referenced(tree)
    return [f"{label}:{node.lineno} {node.name}"
            for label, tree in sorted(trees.items())
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not (node.name.startswith("__") and node.name.endswith("__"))
            and node.name not in refs]


def _dead_function_unit_check() -> Tuple[int, int]:
    check = _Checker()
    sources = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace")
               for p in _runtime_py_files()}
    dead = dead_functions(sources)
    check("dead_function: every runtime top-level function has a production caller", not dead,
          "no production caller (a test or a docstring does not count), delete or wire in:\n" + "\n".join(dead))

    check("dead_function: an uncalled function is reported with its line",
          dead_functions({"a.py": "def used():\n    pass\n\ndef orphan():\n    pass\n\nused()\n"})
          == ["a.py:4 orphan"])
    check("dead_function: a call from another module, an import and an attribute all count",
          not dead_functions({"a.py": "def f():\n    pass\n\ndef g():\n    pass\n\ndef h():\n    pass\n",
                              "b.py": "from a import f\nimport a\na.g()\nx = h\n"}))
    check("dead_function: a docstring or a string that names the function is not a caller",
          dead_functions({"a.py": '"""calls :func:`orphan`."""\ndef orphan():\n    """orphan"""\nx = "orphan"\n'})
          == ["a.py:2 orphan"])
    return check.failures, check.total
