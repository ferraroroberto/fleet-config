"""`docs/ratchets.md` and `docs/skills.md` stay in step with what they index.

Both pages are the one place a reader finds every check and every skill without
reading source, so a page that lags the tree reads as "this does not exist".
The ratchet index is compared with the `[*_ratchet]` tables and `[vendored]`
entries in `.fleet.toml`, the `*_ratchet_gate.py` files, each baseline's entry
count, and the rule registries of the vendored conventions and instruction
scripts. The skills index is compared with every skill directory under
`skills/` and `.claude/skills/`. Each comparison is a pure function over
collected facts, so the self-tests below prove a drifted input is reported.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Callable, Dict, List, Set, Tuple

from acceptance.shared import REPO, _Checker

RATCHETS_DOC = REPO / "docs" / "ratchets.md"
SKILLS_DOC = REPO / "docs" / "skills.md"
SCAFFOLD_DOCS = "https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/"
# Every ratchet table carries these; any other key is an option the index row names.
_STANDARD_KEYS = frozenset({"enabled", "baseline"})
_SKILL_TIERS = ("skills", ".claude/skills")
_SKILL_LINK = re.compile(r"\[`/([a-z0-9-]+)`\]\(([^)\s#]+)")


def _section(doc: str, heading: str) -> str:
    """The body under `## <heading>`, up to the next level-two heading ('' when absent)."""
    parts = doc.split(f"\n## {heading}\n", 1)
    return parts[1].split("\n## ", 1)[0] if len(parts) == 2 else ""


def _rows(section: str) -> Dict[str, List[str]]:
    """Table rows keyed by their first cell with backticks stripped."""
    rows: Dict[str, List[str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells
    return rows


def _ticked(cell: str) -> Set[str]:
    return set(re.findall(r"`([^`]+)`", cell))


def _literal(path: Path, name: str) -> Any:
    """A module-level literal from a vendored script, read without importing it."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise LookupError(f"{name} not found in {path.relative_to(REPO).as_posix()}")


def collect_ratchet_facts(repo: Path = REPO) -> Dict[str, Any]:
    """What the tree says the ratchets are; the index must agree with all of it."""
    fleet = tomllib.loads((repo / ".fleet.toml").read_text(encoding="utf-8"))
    tables = {k: v for k, v in fleet.items() if k.endswith("_ratchet") and isinstance(v, dict) and v.get("enabled")}
    counts = {}
    for name, table in tables.items():
        data = json.loads((repo / table["baseline"]).read_text(encoding="utf-8"))
        counts[name] = len(data.get("entries", []))
    gates = {p.stem[: -len("_gate")]: p.relative_to(repo).as_posix()
             for p in (repo / "tests" / "acceptance").rglob("*_ratchet_gate.py") if not p.name.startswith("test_")}
    instruction = repo / "scripts" / "instruction_ratchet.py"
    return {
        "tables": tables,
        "vendored": {k for k in fleet.get("vendored", {}) if k.endswith("_ratchet")},
        "counts": counts,
        "gates": gates,
        "conventions_known": set(_literal(repo / "scripts" / "conventions_ratchet.py", "RULES")),
        "instruction_enforced": set(_literal(instruction, "RULES")),
        "instruction_audit": set(_literal(instruction, "NOT_ENFORCED")),
    }


def _row_problems(name: str, cells: List[str], facts: Dict[str, Any]) -> List[str]:
    if len(cells) != 7:
        return [f"{name}: index row has {len(cells)} cells, expected 7"]
    _, fails_on, gate, options, baseline, entries, scaffold = cells
    table = facts["tables"][name]
    want_options = set(table) - _STANDARD_KEYS
    want_link = f"{SCAFFOLD_DOCS}{name.replace('_', '-')}.md"
    checks = [
        (bool(fails_on.strip("— ")), "'Fails on' is empty"),
        (gate.strip("`") == facts["gates"].get(name), f"gate is {gate}, tree has {facts['gates'].get(name)}"),
        (_ticked(options) == want_options, f"options {sorted(_ticked(options))} != .fleet.toml {sorted(want_options)}"),
        (baseline.strip("`") == table["baseline"], f"baseline is {baseline}, .fleet.toml has {table['baseline']}"),
        (entries == str(facts["counts"][name]), f"entries is {entries}, baseline holds {facts['counts'][name]}"),
        (want_link in scaffold, f"scaffold doc link is not {want_link}"),
    ]
    return [f"{name}: {msg}" for ok, msg in checks if not ok]


def _conventions_problems(doc: str, facts: Dict[str, Any]) -> List[str]:
    rows = _rows(_section(doc, "Conventions rules"))
    known = facts["conventions_known"]
    enabled = set(facts["tables"].get("conventions_ratchet", {}).get("rules", []))
    listed_on = {r for r, c in rows.items() if len(c) > 1 and c[1] == "on"}
    out = [f"conventions rule {r} is not in the index" for r in sorted(known - set(rows))]
    out += [f"conventions rule {r} is indexed but the vendored script has no such rule" for r in sorted(set(rows) - known)]
    if listed_on != enabled:
        out.append(f"conventions rules marked on {sorted(listed_on)} != .fleet.toml rules {sorted(enabled)}")
    out += [f"conventions rule {r} has no reason" for r, c in rows.items() if len(c) < 3 or not c[2].strip("— ")]
    return out


def _instruction_problems(doc: str, facts: Dict[str, Any]) -> List[str]:
    section = _section(doc, "Instruction rules")
    lines = {key: next((ln for ln in section.splitlines() if key in ln), "") for key in ("Enforced", "Left to")}
    out = []
    for key, want in (("Enforced", facts["instruction_enforced"]), ("Left to", facts["instruction_audit"])):
        got = set(re.findall(r"R-\d+", lines[key]))
        if got != want:
            out.append(f"instruction rules '{key}' list {sorted(got)} != vendored script {sorted(want)}")
    return out


def ratchet_index_problems(doc: str, facts: Dict[str, Any]) -> List[str]:
    """Every way `docs/ratchets.md` disagrees with the collected facts."""
    rows = _rows(_section(doc, "Index"))
    sources = {"index rows": set(rows), ".fleet.toml tables": set(facts["tables"]),
               "gate files": set(facts["gates"]), "[vendored] entries": facts["vendored"]}
    everything = set().union(*sources.values())
    out = [f"{name}: missing from {where}" for name in sorted(everything)
           for where, names in sources.items() if name not in names]
    for name in sorted(set(rows) & set(facts["tables"])):
        out += _row_problems(name, rows[name], facts)
    return out + _conventions_problems(doc, facts) + _instruction_problems(doc, facts)


def collect_skill_names(repo: Path = REPO) -> Set[str]:
    return {p.parent.name for tier in _SKILL_TIERS for p in (repo / tier).glob("*/SKILL.md")
            if not p.parent.name.startswith("_")}


def skills_index_problems(doc: str, names: Set[str], exists: Callable[[str], bool]) -> List[str]:
    """Every skill directory has one entry in `docs/skills.md`, and every entry is a live skill and link."""
    links = dict(_SKILL_LINK.findall(doc))
    out = [f"skill /{n} has no docs/skills.md entry" for n in sorted(names - set(links))]
    out += [f"docs/skills.md lists /{n}, which has no skill directory" for n in sorted(set(links) - names)]
    out += [f"docs/skills.md entry /{n} links to {t}, which does not exist" for n, t in sorted(links.items()) if not exists(t)]
    return out


def _report(check: _Checker, case: str, problems: List[str]) -> None:
    check(case, not problems, "\n".join(problems))


def _self_tests(check: _Checker, doc: str, facts: Dict[str, Any], skills_doc: str, names: Set[str]) -> None:
    """Each drift a scratch edit can introduce is reported, or a green above proves nothing."""
    mutations = {
        "a conventions rule turned on in .fleet.toml": lambda f: f["tables"]["conventions_ratchet"]["rules"].append("secret-compare"),
        "a new rule in a vendored script": lambda f: f["conventions_known"].add("new-rule"),
        "a new [*_ratchet] table": lambda f: f["tables"].__setitem__("new_ratchet", {"enabled": True, "baseline": "x"}),
        "a new option key": lambda f: f["tables"]["docs_ratchet"].__setitem__("exclude", []),
        "a baseline burned down": lambda f: f["counts"].__setitem__("runtime_write_ratchet", f["counts"]["runtime_write_ratchet"] - 1),
        "a new instruction rule": lambda f: f["instruction_enforced"].add("R-99"),
    }
    for label, mutate in mutations.items():
        drifted = copy.deepcopy(facts)
        mutate(drifted)
        check(f"docs_index: {label} without an index update is reported", bool(ratchet_index_problems(doc, drifted)))
    check("docs_index: a new skill directory without a docs/skills.md entry is reported",
          bool(skills_index_problems(skills_doc, names | {"new-skill"}, lambda t: True)))
    check("docs_index: an entry for a removed skill is reported",
          bool(skills_index_problems(skills_doc + "\n- [`/gone`](skills/gone.md)", names, lambda t: True)))


def _docs_index_check() -> Tuple[int, int]:
    """The ratchet and skills index pages agree with the tree; returns (failures, total)."""
    check = _Checker()
    doc = RATCHETS_DOC.read_text(encoding="utf-8")
    skills_doc = SKILLS_DOC.read_text(encoding="utf-8")
    try:
        facts = collect_ratchet_facts()
    except (OSError, LookupError, KeyError, ValueError) as exc:
        check("docs_index: ratchet facts could be collected (NOT CONFIRMED)", False, repr(exc))
        return check.failures, check.total
    names = collect_skill_names()
    docs_dir = RATCHETS_DOC.parent
    _report(check, "docs_index: docs/ratchets.md matches .fleet.toml, gates, baselines and rule registries",
            ratchet_index_problems(doc, facts))
    _report(check, "docs_index: docs/skills.md lists every skill under skills/ and .claude/skills/",
            skills_index_problems(skills_doc, names, lambda t: (docs_dir / t).is_file()))
    _self_tests(check, doc, facts, skills_doc, names)
    return check.failures, check.total
