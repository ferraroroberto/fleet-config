"""The `structural-gate` verdict for a recurring bucket's cleanup branch, scope included.

A branch passes on one valid `Structural-check:` line in its commit bodies: a
path in the branch diff, or `not encodable:` with a real reason. A check that
passes also carries a scope, so the orchestrator knows whether to file a
scaffold promotion:

- `not encodable` -> `n/a`.
- The check is a component project-scaffolding already publishes (its `[components]`
  catalog, read from the scaffold's `origin/<default>`), or the branch only switches
  on an existing `[conventions_ratchet]` rule -> `scaffold-owned`, decided here.
- Otherwise the builder declares `Structural-scope: repo-specific: <why>` or
  `Structural-scope: generalizable: <rule-name>`; a missing or malformed line fails
  with `no-scope`.

A catalog that cannot be read makes the verdict `unknown`, never a pass: without it,
a scaffold-owned check cannot be told from one that needs a promotion.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_toml  # noqa: E402
import git_run  # noqa: E402
from structure_debt.baseline import Unknown, read_ref_file  # noqa: E402

# A `not encodable` or `repo-specific` reason shorter than this is a shrug, not a reason.
MIN_NOT_ENCODABLE_WORDS = 8
MIN_SCOPE_WHY_WORDS = 8
# The fleet sibling layout: this file is <automation>/fleet-config/skills/_lib/structural/scope.py.
DEFAULT_SCAFFOLD = Path(__file__).resolve().parents[4] / "project-scaffolding"
CONVENTIONS_SCRIPT = "scripts/conventions_ratchet.py"
CONVENTIONS_BASELINE = ".conventions-ratchet-baseline.json"

_DECLARATION_RE = re.compile(r"^[ \t]*Structural-check:[ \t]*(.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_NOT_ENCODABLE_RE = re.compile(r"^not[ \t]+encodable[ \t]*:?[ \t]*(.*)$", re.IGNORECASE)
_SCOPE_RE = re.compile(r"^[ \t]*Structural-scope:[ \t]*(.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_SCOPE_VALUE_RE = re.compile(r"^(repo-specific|generalizable)[ \t]*:[ \t]*(.*)$", re.IGNORECASE)
# A rule name is one identifier (kebab, snake or camel case), never prose.
_RULE_NAME_RE = re.compile(r"^`?([A-Za-z][A-Za-z0-9_-]{0,63})`?$")


def normalize_decl_path(raw: str) -> str:
    p = raw.strip().strip("`").strip().replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def slugify(name: str) -> str:
    """The rule name in kebab-case: `NoWindow`, `no_window` and `no window` are all `no-window`."""
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name.strip())
    return re.sub(r"[^a-z0-9]+", "-", split.lower()).strip("-")


def parse_scope(commit_messages: str) -> Optional[dict]:
    """The first valid `Structural-scope:` line, or None when there is none.

    `{"scope": "repo-specific", "why": ...}` needs a reason of at least
    `MIN_SCOPE_WHY_WORDS` words; `{"scope": "generalizable", "name": <slug>}`
    needs one identifier as the rule name.
    """
    for m in _SCOPE_RE.finditer(commit_messages or ""):
        v = _SCOPE_VALUE_RE.match(m.group(1))
        if not v:
            continue
        kind, rest = v.group(1).lower(), v.group(2).strip()
        if kind == "repo-specific" and len(rest.split()) >= MIN_SCOPE_WHY_WORDS:
            return {"scope": kind, "why": rest}
        name = _RULE_NAME_RE.match(rest)
        if kind == "generalizable" and name and slugify(name.group(1)):
            return {"scope": kind, "name": slugify(name.group(1))}
    return None


def parse_catalog(fleet_toml_text: Optional[str], conventions_source: Optional[str]) -> Optional[dict]:
    """`{"components": [src, ...], "rules": [rule, ...]}` from the scaffold's files, or None when unreadable.

    `components` is the `src` of every `[components]` entry; `rules` is the
    conventions ratchet's `RULES` tuple, read by parsing the script, never running it.
    """
    data = fleet_toml.parse(fleet_toml_text)
    table = (data or {}).get("components")
    if not isinstance(table, dict):
        return None
    components = sorted(normalize_decl_path(e["src"]) for e in table.values()
                        if isinstance(e, dict) and isinstance(e.get("src"), str) and e["src"])
    rules = _conventions_rules(conventions_source)
    if not components or rules is None:
        return None
    return {"components": components, "rules": rules}


def _conventions_rules(source: Optional[str]) -> Optional[list[str]]:
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RULES" for t in node.targets)
                and isinstance(node.value, ast.Tuple)):
            names = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            return names or None
    return None


def rules_switched_on(base_toml: Optional[str], head_toml: Optional[str]) -> set[str]:
    """The `[conventions_ratchet]` rules enforced at head but not at base."""
    def enforced(text: Optional[str]) -> set[str]:
        table = (fleet_toml.parse(text) or {}).get("conventions_ratchet")
        if not isinstance(table, dict) or table.get("enabled") is not True:
            return set()
        rules = table.get("rules")
        return {r for r in rules if isinstance(r, str)} if isinstance(rules, list) else set()
    return enforced(head_toml) - enforced(base_toml)


def scaffold_owned(path: str, catalog: dict, switched_on: set[str]) -> bool:
    """A published component (the path or anything under it), or an existing conventions rule switched on."""
    if any(path == src or path.startswith(src.rstrip("/") + "/") for src in catalog["components"]):
        return True
    switches_rule = path == fleet_toml.FILENAME or path.endswith(CONVENTIONS_BASELINE)
    return switches_rule and bool(switched_on) and switched_on <= set(catalog["rules"])


def structural_verdict(
    recurrence: Optional[str],
    commit_messages: str,
    changed_paths: list[str],
    catalog: Optional[dict] = None,
    switched_on: frozenset[str] | set[str] = frozenset(),
) -> dict:
    """The deterministic structural-gate verdict from already-read inputs.

    An `unknown` recurrence stays `unknown`, never `n/a`. `catalog` is
    `parse_catalog`'s result; None means it could not be read.
    """
    if recurrence is None or recurrence == "not-recurring":
        return {"state": "n/a"}
    if recurrence != "recurring":
        return {"state": "unknown", "reason": "recurrence-unknown"}
    decls = [m.group(1) for m in _DECLARATION_RE.finditer(commit_messages or "")]
    if not decls:
        return {"state": "fail", "reason": "no-declaration"}
    changed = {normalize_decl_path(p) for p in changed_paths}
    first_failure = None
    for decl in decls:
        ne = _NOT_ENCODABLE_RE.match(decl.strip())
        if ne:
            if len(ne.group(1).split()) >= MIN_NOT_ENCODABLE_WORDS:
                return {"state": "pass", "kind": "not-encodable", "scope": "n/a"}
            first_failure = first_failure or "empty-reason"
            continue
        path = normalize_decl_path(decl)
        if path and path in changed:
            return _scoped({"state": "pass", "kind": "check", "path": path},
                           commit_messages, catalog, set(switched_on))
        first_failure = first_failure or "path-not-in-diff"
    return {"state": "fail", "reason": first_failure}


def _scoped(verdict: dict, commit_messages: str, catalog: Optional[dict], switched_on: set[str]) -> dict:
    if catalog is None:
        return {**verdict, "state": "unknown", "reason": "catalog-unknown", "scope": "unknown"}
    if scaffold_owned(verdict["path"], catalog, switched_on):
        return {**verdict, "scope": "scaffold-owned"}
    declared = parse_scope(commit_messages)
    if declared is None:
        return {**verdict, "state": "fail", "reason": "no-scope"}
    scoped = {**verdict, "scope": declared["scope"]}
    return {**scoped, "name": declared["name"]} if "name" in declared else scoped


def read_catalog(scaffold_root: Path) -> tuple[Optional[dict], Optional[str]]:
    """`(catalog, None)` from the scaffold's `origin/<default>`, or `(None, reason)`."""
    ref = git_run.resolve_default_branch_ref(scaffold_root, candidates=("origin/main",), final_fallback="")
    if not ref:
        return None, f"no origin default branch ref in {scaffold_root}"
    try:
        catalog = parse_catalog(read_ref_file(scaffold_root, ref, fleet_toml.FILENAME),
                                read_ref_file(scaffold_root, ref, CONVENTIONS_SCRIPT))
    except Unknown as exc:
        return None, str(exc)
    return (catalog, None) if catalog else (None, f"no [components] table or RULES tuple on {ref}")


def branch_switched_on(repo_path: str, base: str) -> Optional[set[str]]:
    """The conventions rules this branch switches on against `base`, or None on a git failure."""
    repo = Path(repo_path)
    try:
        return rules_switched_on(read_ref_file(repo, base, fleet_toml.FILENAME),
                                 read_ref_file(repo, "HEAD", fleet_toml.FILENAME))
    except Unknown:
        return None


def verdict_line(v: dict) -> str:
    """The gate's one output line: `STRUCTURAL=<state>` plus kind, path, scope, name and reason."""
    line = f"STRUCTURAL={v['state']}"
    for key in ("kind", "path", "scope", "name", "reason"):
        if key in v:
            line += f" {key.upper()}={v[key]}"
    return line
