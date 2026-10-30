"""Static single-home gate (fleet-config#1556).

Several things in this repo have one named home -- the `.fleet.toml` reader, the
atomic file write, the `-wt-` separator, the Windows pid probe, git's global
option walk -- and each used to collect a second, third or seventh private copy
that diverged from the first. A copy never fails a test: it keeps working, and
only the next fix lands in one place and not the others. So the gate is
structural: parse the runtime trees and fail on any file outside a rule's home
that does the thing itself.

`KNOWN` lists the files that already did before the gate existed and are not
yet routed through the home. It can only shrink: a new file that re-implements
a home fails, and a listed file that no longer does must come off the list.
"""
from __future__ import annotations

import ast
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Set, Tuple

from acceptance.shared import REPO, _Checker
from acceptance.spawn_scanner import _runtime_py_files


class Rule(NamedTuple):
    name: str
    home: str  # what a hit should have called instead
    trees: Tuple[str, ...]  # repo-relative prefixes the rule applies to
    homes: FrozenSet[str]  # files allowed to do it: the home itself
    known: FrozenSet[str]  # pre-existing offenders, to be burned down
    hit: Callable[[ast.AST], bool]  # does this module do the thing?


def _strings(tree: ast.AST) -> List[str]:
    """Every string constant outside docstrings and bare string statements."""
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _calls(tree: ast.AST, owner: str, names: Set[str]) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in names
               and isinstance(n.func.value, ast.Name) and n.func.value.id == owner for n in ast.walk(tree))


def _reads_fleet_toml_itself(tree: ast.AST) -> bool:
    return _calls(tree, "tomllib", {"load", "loads"}) and any(".fleet.toml" in s for s in _strings(tree))


def _names_an_attribute(*attrs: str) -> Callable[[ast.AST], bool]:
    wanted = set(attrs)
    return lambda tree: any(
        (isinstance(n, ast.Attribute) and n.attr in wanted) or (isinstance(n, ast.Name) and n.id in wanted)
        for n in ast.walk(tree))


RULES: Tuple[Rule, ...] = (
    Rule("fleet_toml", "skills/_lib/fleet_core/fleet_toml.py (`load_checked` / `load_state`)",
         ("skills/", ".claude/skills/"), frozenset({"skills/_lib/fleet_core/fleet_toml.py"}),
         frozenset({
             ".claude/skills/system-map/build_data.py",
             "skills/_lib/perf_review/report.py",
             "skills/_lib/structure_debt/baseline.py",
             "skills/_lib/vendored_drift.py",
         }),
         _reads_fleet_toml_itself),
    Rule("atomic_write", "skills/_lib/fleet_core/hooks_state.py `atomic_write_text`",
         ("skills/", ".claude/skills/", "config_edit.py"), frozenset({"skills/_lib/fleet_core/hooks_state.py"}),
         frozenset({
             "config_edit.py",
             "skills/_lib/chief/mailbox.py",
             "skills/_lib/design_review/capture.py",
             "skills/_lib/stall_probe.py",
         }),
         lambda tree: _calls(tree, "os", {"replace"})),
    Rule("wt_sep", "skills/_lib/worktree_residue.py `WT_SEP`",
         ("skills/", ".claude/skills/"), frozenset({"skills/_lib/worktree_residue.py"}), frozenset(),
         lambda tree: any("-wt-" in s and "<" not in s for s in _strings(tree))),  # `<repo>-wt-<N>` is prose, not the separator
    Rule("pid_probe", "skills/_lib/active_issue.py `pid_state`",
         ("skills/", ".claude/skills/"), frozenset({"skills/_lib/active_issue.py"}), frozenset(),
         _names_an_attribute("GetExitCodeProcess")),
    Rule("git_global_options", "hooks/_lib.py `git_globals`",
         ("hooks/",), frozenset({"hooks/_lib.py"}), frozenset(),
         _names_an_attribute("GIT_GLOBAL_OPTS_WITH_VALUE")),
)


def offenders(rule: Rule, sources: Dict[str, str]) -> List[str]:
    """Repo-relative paths in `sources` (label -> text) that do `rule`'s thing outside its home."""
    found = []
    for label, text in sorted(sources.items()):
        if not label.startswith(rule.trees) or label in rule.homes:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if rule.hit(tree):
            found.append(label)
    return found


def _repo_sources() -> Dict[str, str]:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in _runtime_py_files()}


def _single_home_unit_check() -> Tuple[int, int]:
    check = _Checker()
    sources = _repo_sources()
    for rule in RULES:
        hits = set(offenders(rule, sources))
        new = sorted(hits - rule.known)
        check(f"single_home[{rule.name}]: no new copy outside {rule.home}", not new,
              "re-implemented in:\n" + "\n".join(new) + f"\ncall {rule.home} instead")
        paid = sorted(rule.known - hits)
        check(f"single_home[{rule.name}]: every KNOWN offender still offends (burn-down is a delete)", not paid,
              "no longer offends, remove from KNOWN:\n" + "\n".join(paid))

    by_name = {r.name: r for r in RULES}
    docstring = '"""reads .fleet.toml with tomllib.loads and -wt- and os.replace"""\nx = 1\n'
    check("single_home: a docstring that merely names the pattern is not a hit",
          not offenders(by_name["wt_sep"], {"skills/a.py": docstring})
          and not offenders(by_name["fleet_toml"], {"skills/a.py": docstring}))
    check("single_home: a `-wt-` string literal outside the home is a hit",
          offenders(by_name["wt_sep"], {"skills/a.py": 'x = f"{n}-wt-1"\n'}) == ["skills/a.py"])
    check("single_home: `tomllib.loads` of a `.fleet.toml` path outside the home is a hit",
          offenders(by_name["fleet_toml"], {"skills/a.py": 'import tomllib\ntomllib.loads(open(".fleet.toml").read())\n'})
          == ["skills/a.py"])
    check("single_home: the home itself and other trees are exempt",
          not offenders(by_name["wt_sep"], {"skills/_lib/worktree_residue.py": 'x = "-wt-"\n', "hooks/a.py": 'x = "-wt-"\n'}))
    return check.failures, check.total
