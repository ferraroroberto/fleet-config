"""Which audit checks and prompt-audit lint rules a repo's enabled ratchets already enforce.

The weekly audits ask this before scanning, so a class of finding the ship-time gate
owns is not re-detected every week: `/codebase-audit` skips the check and prints its
`gate-enforced:` line, `dead_code_leads.py` skips the matching lead pass, and
`/prompt-audit` drops the lint rules `[instruction_ratchet]` enforces for the files
inside that ratchet's scope.

The read is `reader.read_repo`'s: `origin/<default>`, never the working tree, and every
enabled ratchet's baseline must parse. Only `adopted` claims anything; `unknown` (no
default ref, an unreadable `.fleet.toml`, baseline or vendored instruction ratchet) and
`not_adopted` claim nothing, so the audit scans every check: the skip never widens on
uncertainty. The baselined hit count travels with each claim, since existing debt is
burned down by issues, not hidden by the skip.

Usage: `enforced.py <repo>` prints the `GATE=` and `gate-enforced:` lines; `--json` the verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import fleet_toml  # noqa: E402
from gate_coverage import reader  # noqa: E402
from structure_debt.baseline import Unknown, read_ref_file  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

# Each ratchet table -> the (codebase-audit bucket label, check) pairs it enforces. A ratchet
# owns one detectable class inside a bucket, never the bucket's judgment, so the rest of the
# bucket is still read. `instruction_ratchet` maps to prompt-audit rules and
# `conventions_ratchet` to its enabled rules, below.
CHECKS: dict[str, tuple[tuple[str, str], ...]] = {
    "static_ratchet": (
        ("duplication", "copy-pasted blocks (jscpd)"),
        ("stale", "unused imports and variables (ruff F401, F841)"),
        ("bug", "ruff bug (B) and security (S) findings"),
    ),
    "docs_ratchet": (
        ("documentation", "README.md over 40 KiB"),
        ("documentation", "issue references, dated headings and changelog headings in README.md and docs/"),
    ),
    "comment_ratchet": (("claude-md-drift", "issue-number history in code comments"),),
    "structure_ratchet": (
        ("maintainability", "files over 1,000 lines, functions over 100 lines, flat directories over 40 modules"),
    ),
    "doc_refs_ratchet": (
        ("documentation", "README.md, CLAUDE.md and docs/ references to paths, modules or links that do not exist"),
    ),
    "dead_code_ratchet": (("stale", "functions, classes and methods nothing in the tracked tree refers to"),),
    "instruction_ratchet": (),
    "conventions_ratchet": (),
}
CONVENTIONS: dict[str, tuple[str, str]] = {
    "no-window": ("claude-md-drift", "subprocess spawns without creationflags"),
    "logging-not-print": ("claude-md-drift", "print() where the logging module belongs"),
    "no-drive-path": ("claude-md-drift", "hardcoded drive-letter paths"),
    "secret-compare": ("claude-md-drift", "secrets compared without a constant-time compare"),
    "git-wrapper": ("claude-md-drift", "raw git spawns outside the git wrapper"),
}
# The prompt-audit lint rules the vendored instruction ratchet enforces (its `RULES`). R-13 and
# R-37 stay with the weekly audit: the ratchet cannot decide them from one tree.
INSTRUCTION_RULES = ("R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10", "R-11",
                     "R-12", "R-14", "R-15", "R-16", "R-17", "R-30", "R-34", "R-35", "R-36", "R-45")
INSTRUCTION_DEFAULT_DEST = "scripts/instruction_ratchet.py"
# The ratchet's own default `skill_dirs`.
INSTRUCTION_DEFAULT_SKILL_DIRS = (".claude/skills",)
# A key of the vendored ratchet's `RULES` dict (`"R-01": ("anthropic", ...)`); `NOT_ENFORCED`
# maps to strings, so its keys never match.
_RULE_KEY = re.compile(r'^\s*"(R-\d+)":\s*\(', re.M)


def _str_list(table: dict, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Unknown(f"{fleet_toml.FILENAME} [instruction_ratchet] {key} is not a list of strings")
    return tuple(value)


def _instruction(repo: Path, ref: str, data: dict) -> dict:
    """The instruction ratchet's rules and file scope, the rules narrowed to what this repo's vendored copy declares."""
    table = data.get("instruction_ratchet") or {}
    vendored = (data.get("vendored") or {}).get("instruction_ratchet")
    dest = vendored.get("dest", INSTRUCTION_DEFAULT_DEST) if isinstance(vendored, dict) else INSTRUCTION_DEFAULT_DEST
    if not isinstance(dest, str):
        raise Unknown(f"{fleet_toml.FILENAME} [vendored] instruction_ratchet dest is not a string")
    text = read_ref_file(repo, ref, dest)
    if text is None:
        raise Unknown(f"the vendored instruction ratchet {dest} is not on {ref}")
    declared = set(_RULE_KEY.findall(text))
    return {"rules": [r for r in INSTRUCTION_RULES if r in declared],
            "skill_dirs": list(_str_list(table, "skill_dirs", INSTRUCTION_DEFAULT_SKILL_DIRS)),
            "extra_files": list(_str_list(table, "extra_files", ()))}


def read_gate(repo_path: str) -> dict:
    """The repo's gate-enforced checks.

    `{"state": "adopted", "checks": [{ratchet, bucket, check, baselined}], "prompt": {...} | None}`,
    or `not_adopted` / `unknown` (with a `reason`) and nothing enforced.
    """
    base = reader.read_repo(repo_path)
    if base["state"] != "adopted":
        return {"state": base["state"], "reason": base.get("reason", ""), "checks": [], "prompt": None}
    if base["unknown"]:
        # The digest keeps counting around an uncountable baseline; the audit must not, or it would
        # skip checks the gate may not actually enforce.
        return {"state": "unknown", "reason": "; ".join(base["unknown"].values()), "checks": [], "prompt": None}
    repo, ref, hits = Path(repo_path), base["ref"], base["hits"]
    try:
        data = fleet_toml.parse(read_ref_file(repo, ref, fleet_toml.FILENAME)) or {}
        prompt = _instruction(repo, ref, data) if "instruction_ratchet" in hits else None
    except Unknown as exc:
        return {"state": "unknown", "reason": str(exc), "checks": [], "prompt": None}
    if prompt is not None:
        prompt["baselined"] = hits["instruction_ratchet"]
    checks = [{"ratchet": table, "bucket": bucket, "check": what, "baselined": n}
              for table, n in sorted(hits.items()) for bucket, what in CHECKS[table]]
    checks += [{"ratchet": f"conventions_ratchet:{rule}", "bucket": CONVENTIONS[rule][0],
                "check": CONVENTIONS[rule][1], "baselined": hits["conventions_ratchet"]}
               for rule in base["rules"] if rule in CONVENTIONS]
    return {"state": "adopted", "reason": "", "checks": checks, "prompt": prompt}


def enforces(gate: dict, ratchet: str) -> bool:
    """True only when `ratchet` is a confirmed enabled table of an `adopted` gate."""
    if gate.get("state") != "adopted":
        return False
    return (ratchet == "instruction_ratchet" and gate.get("prompt") is not None) or any(
        c["ratchet"] == ratchet for c in gate["checks"])


def in_instruction_scope(rel: str, prompt: dict) -> bool:
    """Whether the instruction ratchet scans the repo-relative posix path `rel`.

    Its scope: root `CLAUDE.md` and `AGENTS.md`, `.claude/rules/*.md`, `extra_files`, and the
    `.md` files inside a `<skill_dir>/<skill>/` folder (its `SKILL.md` and the docs it links to).
    """
    if rel in ("CLAUDE.md", "AGENTS.md") or rel in prompt["extra_files"]:
        return True
    if rel.startswith(".claude/rules/") and rel.endswith(".md") and rel.count("/") == 2:
        return True
    for d in prompt["skill_dirs"]:
        prefix = d.strip("/") + "/"
        if rel.startswith(prefix) and "/" in rel[len(prefix):] and rel.endswith(".md"):
            return True
    return False


def prompt_rules_for(gate: dict, rel: str) -> list[str]:
    """The lint rules the gate enforces for the file `rel`; empty unless adopted and in scope."""
    prompt = gate.get("prompt") if gate.get("state") == "adopted" else None
    return list(prompt["rules"]) if prompt and in_instruction_scope(rel, prompt) else []


def report_lines(gate: dict, repo: str) -> list[str]:
    """The `GATE=` header and one `gate-enforced:` line per enforced check, for an audit report."""
    if gate["state"] != "adopted":
        why = f"|reason={gate['reason']}" if gate.get("reason") else ""
        return [f"GATE={repo}|state={gate['state']}{why}|every check scanned"]
    lines = [f"GATE={repo}|state=adopted|checks={len(gate['checks'])}"]
    lines += [f"gate-enforced: {c['bucket']}: {c['check']} ({c['ratchet']}, {c['baselined']} baselined)"
              for c in gate["checks"]]
    if gate["prompt"]:
        p = gate["prompt"]
        lines.append(f"gate-enforced: prompt-audit lint {','.join(p['rules'])} (instruction_ratchet, "
                     f"{p['baselined']} baselined)")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--json", action="store_true", help="print the verdict as JSON")
    args = ap.parse_args(argv)
    path = Path(args.repo).resolve()
    gate = read_gate(str(path))
    print(json.dumps(gate, indent=2) if args.json else "\n".join(report_lines(gate, path.name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
