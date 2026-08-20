"""hooks/ ↔ skills/_lib/ tree-independence guard (fleet-config#564).

The two directories are junctioned into the agent homes **independently**
(`hooks/` → `~/.claude/hooks`, `skills/` → `~/.claude/skills`), so a hook must
stay importable with nothing but its own directory on `sys.path`. Every hook
that needs a skills-tier capability says so in prose — `notify_on_idle` reaches
`chief_ops.py` by subprocess "never a Python import"; `block_askuserquestion_chief`
restates it when explaining why an *intra*-`hooks/` import is fine — but the
rule was enforced nowhere, and `branch_before_edit_guard` had quietly crossed
the line with a module-top `sys.path.insert(.../skills/_lib)` + `import git_run`.

The cost of that one violation was fleet-wide and unbounded: an unguarded
top-level import in a `PreToolUse` hook on `Edit`/`Write`/`MultiEdit` dies at
*import* time if the sibling tree is ever renamed or absent — before any of the
hook's carefully-built fail-open logic can run — on every file edit in every
session. So the rule gets a gate instead of a paragraph.

Two mechanical parts:
  1. no module under `hooks/` reaches into `skills/` at all (no `sys.path`
     entry naming it, no import of a name that only exists there);
  2. the deliberate hooks-tier copies of the two `git_run` helpers agree
     behaviourally with the skills-tier originals, so the duplication cannot
     drift — the same contract `spawn_scanner` holds `NO_WINDOW` to.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

from acceptance.shared import HOOKS, REPO, _Checker

SKILLS_LIB = REPO / "skills" / "_lib"


def _skills_only_module_names() -> set:
    """Top-level module names that exist in `skills/_lib/` but not in `hooks/`.

    Importing one of these from a hook can only work by reaching across the
    boundary. Names present in *both* trees (there are none today, but a future
    `no_window.py` twin would be one) are excluded, because those resolve from
    the hook's own directory and are not a violation.
    """
    hooks_names = {p.stem for p in HOOKS.glob("*.py")}
    return {p.stem for p in SKILLS_LIB.glob("*.py")} - hooks_names


def _cross_tree_offenders() -> List[str]:
    """`path:line` for every hooks/ site that reaches into the skills tree."""
    skills_only = _skills_only_module_names()
    offenders: List[str] = []
    for py in sorted(HOOKS.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py))
        except (OSError, SyntaxError):  # pragma: no cover - unparseable file
            continue
        loc = py.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            # `import git_run` / `from git_run import ...` for a name that only
            # the sibling tree provides.
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in skills_only:
                        offenders.append(f"{loc}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in skills_only:
                    offenders.append(f"{loc}:{node.lineno} from {node.module} import ...")
            # `sys.path.insert(0, ... "skills" ...)` — catches the path-shim half
            # even when the subsequent import name is added to the sibling tree
            # later, and catches a `Path(...) / "skills" / "_lib"` spelling that
            # no import-name check could see.
            elif isinstance(node, ast.Call):
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr in ("insert", "append")):
                    continue
                if "sys.path" not in ast.unparse(func):
                    continue
                if any(isinstance(lit, ast.Constant) and lit.value == "skills"
                       for lit in ast.walk(node)):
                    offenders.append(f"{loc}:{node.lineno} sys.path entry naming skills/")
    return offenders


def _git_helper_parity(check: _Checker) -> None:
    """The hooks-tier `run_git` / `resolve_default_branch_ref` copies behave
    exactly like the skills-tier originals, on real temp repos."""
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(SKILLS_LIB))
    import _lib  # noqa: E402  (hooks/_lib.py)
    import git_run  # noqa: E402  (skills/_lib/git_run.py)

    tmp = Path(tempfile.mkdtemp(prefix="test_tree_boundary_"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        # The author email matches this machine's commit-email allowlist hook —
        # same value `checks_guards`'s own `git_repo` uses, for the same reason.
        for args in (
            ["init", "-q", "-b", "master"],
            ["config", "user.email", "35553560+ferraroroberto@users.noreply.github.com"],
            ["config", "user.name", "test"],
            ["commit", "-q", "--allow-empty", "-m", "init"],
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True,
                           capture_output=True, creationflags=_lib.NO_WINDOW)

        mine = _lib.run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
        theirs = git_run.run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
        check("tree_boundary: hooks/_lib.run_git matches git_run.run_git "
              "(returncode + stdout)",
              (mine.returncode, mine.stdout) == (theirs.returncode, theirs.stdout),
              f"hooks={(mine.returncode, mine.stdout)!r} skills={(theirs.returncode, theirs.stdout)!r}")

        # a remote-less repo on `master`: the candidate-probing path
        # branch_before_edit_guard._default_branch depends on.
        mine_ref = _lib.resolve_default_branch_ref(repo)
        theirs_ref = git_run.resolve_default_branch_ref(repo)
        check("tree_boundary: hooks/_lib.resolve_default_branch_ref agrees on a "
              "remote-less master repo",
              mine_ref == theirs_ref == "master",
              f"hooks={mine_ref!r} skills={theirs_ref!r}")

        # a non-repo path: both must fall through to the final fallback.
        outside = tmp / "not-a-repo"
        outside.mkdir()
        check("tree_boundary: hooks/_lib.resolve_default_branch_ref agrees off-repo",
              _lib.resolve_default_branch_ref(outside)
              == git_run.resolve_default_branch_ref(outside) == "main",
              f"hooks={_lib.resolve_default_branch_ref(outside)!r} "
              f"skills={git_run.resolve_default_branch_ref(outside)!r}")
    finally:
        # git's object store is read-only on Windows, so a plain rmtree can
        # raise on a perfectly successful run — never let cleanup fail the gate.
        shutil.rmtree(tmp, ignore_errors=True)


def _hooks_tree_boundary_check() -> Tuple[int, int]:
    """hooks/ never imports from skills/_lib, and the sanctioned copies agree."""
    check = _Checker()

    offenders = _cross_tree_offenders()
    check(
        "tree_boundary: no hooks/*.py reaches into skills/_lib "
        "(hooks must import with only their own dir on sys.path)",
        not offenders,
        "cross-tree reference(s) at:\n" + "\n".join(offenders),
    )

    # The scanner must actually be able to see a violation — a matcher that
    # silently stopped matching would make the gate above vacuously green.
    probe = ast.parse(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent.parent / 'skills' / '_lib'))\n"
        "import git_run\n"
    )
    seen = [
        n for n in ast.walk(probe)
        if (isinstance(n, ast.Import) and any(a.name == "git_run" for a in n.names))
        or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "insert" and "sys.path" in ast.unparse(n.func)
            and any(isinstance(c, ast.Constant) and c.value == "skills" for c in ast.walk(n)))
    ]
    check(
        "tree_boundary: the scanner still recognizes the pre-#564 violation shape",
        len(seen) == 2,
        f"probe matched {len(seen)} node(s), expected 2",
    )

    _git_helper_parity(check)

    return check.failures, check.total
