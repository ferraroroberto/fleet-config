"""Acceptance checks that every `restart_cmd` in the registry can run.

A wave restarts a repo's webapp through `restart_and_verify_webapp.py` using the
`restart_cmd` in `hooks/projects.toml`; a recipe naming a module, class or
method that is not there, or calling `start()` with arguments the method does
not take, is only discovered when a wave needs it and leaves the app down. The
check reads the repo's source statically (no import, nothing is started) and
reports every recipe it cannot confirm as a problem: unknown is not a pass.

Two recipe shapes exist: `python -c "from <module> import <Class>; <Class>().<method>(...)"`
and a script run from the repo root (`.\\tray.bat --restart`).
"""
from __future__ import annotations

import ast
import re
import shlex
import tempfile
import tomllib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from acceptance.shared import REPO, _Checker

_PYTHON_C = re.compile(r'-c\s+"(?P<code>.*)"\s*$')


def _module_file(root: Path, module: str) -> Optional[Path]:
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _signature_problems(fn: ast.FunctionDef, call: ast.Call, *, bound: bool) -> List[str]:
    """Call arguments `fn` cannot accept; `bound` drops the leading `self`."""
    args = fn.args
    positional = [a.arg for a in args.posonlyargs + args.args][1 if bound else 0:]
    keyword_ok = set(positional) | {a.arg for a in args.kwonlyargs}
    n_defaults = len(args.defaults)
    required = set(positional[: len(positional) - n_defaults]) | {
        a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None
    }
    problems: List[str] = []
    if len(call.args) > len(positional) and args.vararg is None:
        problems.append(f"{fn.name}() takes {len(positional)} positional arguments, the recipe passes {len(call.args)}")
    given = set(positional[: len(call.args)])
    for kw in call.keywords:
        if kw.arg is None:
            continue
        if kw.arg not in keyword_ok and args.kwarg is None:
            problems.append(f"{fn.name}() has no parameter {kw.arg!r}")
        given.add(kw.arg)
    missing = sorted(required - given)
    if missing and not any(kw.arg is None for kw in call.keywords):
        problems.append(f"{fn.name}() needs {missing}, the recipe does not pass them")
    return problems


def _python_recipe_problems(code: str, root: Path) -> List[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"the python -c snippet does not parse: {exc.msg}"]
    modules: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                modules[alias.asname or alias.name] = node.module
    problems: List[str] = []
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Call) and isinstance(n.func.value.func, ast.Name)
    ]
    if not calls:
        return ["no `Class().method(...)` call found in the python -c snippet"]
    for call in calls:
        ctor = call.func.value
        cls_name, method = ctor.func.id, call.func.attr
        module = modules.get(cls_name)
        if module is None:
            problems.append(f"{cls_name} is not imported in the snippet")
            continue
        source = _module_file(root, module)
        if source is None:
            problems.append(f"module {module} has no file under the repo")
            continue
        try:
            module_tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            problems.append(f"cannot read {module}: {exc}")
            continue
        cls = next((n for n in module_tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
        if cls is None:
            problems.append(f"class {cls_name} is not defined in {module}")
            continue
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        for fn_name, node in (("__init__", ctor), (method, call)):
            fn = methods.get(fn_name)
            if fn is None:
                if fn_name == "__init__" and not (ctor.args or ctor.keywords):
                    continue
                problems.append(f"{cls_name} defines no {fn_name}() in {module}")
                continue
            problems.extend(_signature_problems(fn, node, bound=True))
    return problems


def restart_cmd_problems(cmd: str, root: Path) -> List[str]:
    """Why `cmd` cannot run from `root`; empty when every part was confirmed."""
    text = cmd.strip()
    if text.startswith("&"):
        text = text[1:].strip()
    match = _PYTHON_C.search(text)
    if match:
        return _python_recipe_problems(match.group("code"), root)
    try:
        head = shlex.split(text.replace("\\", "/"))[0]
    except (ValueError, IndexError):
        return ["the recipe is empty or does not tokenise"]
    script = root / head
    if script.suffix.lower() in (".bat", ".cmd", ".ps1", ".py") and script.is_file():
        return []
    if script.suffix.lower() in (".bat", ".cmd", ".ps1", ".py"):
        return [f"script {head} is not in the repo"]
    return [f"unrecognised recipe shape {head!r}: only `python -c` and repo scripts can be checked"]


def _fixture_repo(base: Path) -> Path:
    (base / "app" / "webapp").mkdir(parents=True)
    (base / "app" / "webapp" / "manager.py").write_text(
        "class WebappManager:\n"
        "    def start(self) -> None:\n        pass\n"
        "class WaitingManager:\n"
        "    def start(self, wait=False) -> None:\n        pass\n"
        "    def needs(self, who) -> None:\n        pass\n",
        encoding="utf-8",
    )
    (base / "tray.bat").write_text("@echo off\n", encoding="utf-8")
    return base


def _py(call: str, cls: str = "WebappManager", module: str = "app.webapp.manager") -> str:
    return f'& .\\.venv\\Scripts\\python.exe -c "from {module} import {cls}; {call}"'


def _restart_cmd_unit_checks() -> Tuple[int, int]:
    """Each way a recipe cannot run is reported, on a synthetic repo (red-first)."""
    check = _Checker()
    with tempfile.TemporaryDirectory() as tmp:
        root = _fixture_repo(Path(tmp))
        cases: List[Tuple[str, str, bool]] = [
            ("a matching python recipe is clean", _py("WebappManager().start()"), True),
            ("an optional keyword the method takes is clean",
             _py("WaitingManager().start(wait=True)", "WaitingManager"), True),
            ("a script in the repo is clean", r"& .\tray.bat --restart", True),
            ("a keyword the method lacks is a problem (wait=True on start(self))",
             _py("WebappManager().start(wait=True)"), False),
            ("a missing module is a problem",
             _py("WebappManager().start()", module="app.tray.manager"), False),
            ("a missing class is a problem", _py("Missing().start()", "Missing"), False),
            ("a missing method is a problem", _py("WebappManager().restart()"), False),
            ("a missing required argument is a problem",
             _py("WaitingManager().needs()", "WaitingManager"), False),
            ("a script missing from the repo is a problem", r"& .\gone.bat --restart", False),
            ("a shape it cannot check is a problem, not a pass", "curl http://127.0.0.1/restart", False),
        ]
        for case, cmd, clean in cases:
            problems = restart_cmd_problems(cmd, root)
            check(f"restart_cmd: {case}", (not problems) == clean, "; ".join(problems))
    return check.failures, check.total


def _restart_cmd_registry_check() -> Tuple[int, int, int]:
    """Every `restart_cmd` in the tracked registry runs against its checkout.

    The inputs are sibling checkouts, so a recipe that fails there is reported
    but does not gate this repo (fleet-config#562); a repo not on this disk is
    skipped, never counted as passing.
    """
    check = _Checker()
    toml = tomllib.loads((REPO / "hooks" / "projects.toml.example").read_text(encoding="utf-8"))
    for name, table in sorted(toml.items()):
        cmd = table.get("restart_cmd") if isinstance(table, dict) else None
        if not cmd:
            continue
        root = Path(str(table.get("cwd_prefix", "")))
        if not root.is_dir():
            check.skipped += 1
            print(f"SKIP  restart_cmd: {name} has no checkout at its cwd_prefix on this machine")
            continue
        problems = restart_cmd_problems(cmd, root)
        check.advisory(f"restart_cmd: {name} recipe can run against its checkout", not problems, "\n".join(problems))
    return check.failures, check.total, check.skipped
