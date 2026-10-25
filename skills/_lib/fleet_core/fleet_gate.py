"""Does each active fleet repo carry a recognised verification gate?

The rule: an `active` repo has a recognised gate, or it is `dormant`
(`docs/fleet-scope.md`). `gate_verdict()` answers it for one checkout and
`survey()` for the whole fleet; the `fleet_gate` acceptance check and the CLI
are two readers of the same answer.

A verdict is `gated`, `ungated`, or `unknown`. `unknown` means the facts could
not be established (no checkout, no default-branch ref, an unreadable
`package.json`) and is never counted as `gated`. Evidence is read from the
repo's committed default branch, so a dirty or off-branch checkout does not
change it.

CLI
---
  fleet_gate.py [--root <dir>] [--projects-toml <path>]

Prints the report. Always exits 0: another repo's contents are advisory here.

stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import fleet_repo_scan  # noqa: E402
import fleet_status  # noqa: E402
import git_run  # noqa: E402

GATED = "gated"
UNGATED = "ungated"
UNKNOWN = "unknown"

# The recognised gate forms; a repo needs one. A form is a tracked file on the
# default branch unless it says otherwise.
#   gate_cmd      the repo's own `projects.toml` table declares the command
#                 (app-launcher's `scripts/verify-before-ship.ps1`)
#   GATE_SCRIPTS  a script whose presence is the gate: the fleet's wrapper,
#                 and the Node repos' verify script
#   package.json  `scripts.verify` set to a non-empty command; a bare
#                 package.json is not a gate
#   RATCHET_NAMES a ratchet invoked from a test file under `tests/`, the form
#                 a pytest-wrapped ratchet takes
GATE_SCRIPTS = ("scripts/verify-before-ship.ps1", "scripts/verify.js")
PACKAGE_JSON = "package.json"
RATCHET_NAMES = ("static_ratchet", "docs_ratchet", "structure_ratchet")
TEST_PATHSPEC = "tests/*.py"  # git pathspec `*` crosses `/`, so nested tests match

_RATCHET_RE = "|".join(RATCHET_NAMES)


@dataclass(frozen=True)
class GateVerdict:
    state: str
    evidence: str  # the form and path that proved a gate, or why none could be established


@dataclass
class Survey:
    gated: Dict[str, str] = field(default_factory=dict)  # repo -> evidence
    ungated: List[str] = field(default_factory=list)
    dormant: List[str] = field(default_factory=list)
    unknown: Dict[str, str] = field(default_factory=dict)  # repo -> reason


def _unknown(reason: str) -> GateVerdict:
    return GateVerdict(UNKNOWN, reason)


def gate_verdict(repo_dir: Path, table: Optional[dict] = None) -> GateVerdict:
    """The verdict for one checkout. `table` is the repo's `projects.toml` table."""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").exists():
        return _unknown(f"no checkout at {repo_dir.name}")
    ref = fleet_repo_scan.default_ref(repo_dir)
    if ref is None:
        return _unknown("no default-branch ref")
    listing = git_run.run_git(["-C", str(repo_dir), "ls-tree", "-r", "--name-only", ref])
    if listing.returncode != 0:
        return _unknown(f"cannot read {ref}")
    tracked = set(listing.stdout.splitlines())

    gate_cmd = (table or {}).get("gate_cmd")
    if isinstance(gate_cmd, str) and gate_cmd.strip():
        return GateVerdict(GATED, "projects.toml gate_cmd")
    for script in GATE_SCRIPTS:
        if script in tracked:
            return GateVerdict(GATED, script)

    package_unreadable = False
    if PACKAGE_JSON in tracked:
        shown = git_run.run_git_bytes(["-C", str(repo_dir), "show", f"{ref}:{PACKAGE_JSON}"])
        try:
            if shown.returncode != 0:
                raise ValueError("git show failed")
            scripts = json.loads(shown.stdout.decode("utf-8")).get("scripts", {})
            verify = scripts.get("verify") if isinstance(scripts, dict) else None
            if isinstance(verify, str) and verify.strip():
                return GateVerdict(GATED, f"{PACKAGE_JSON} scripts.verify")
        except (ValueError, AttributeError):
            package_unreadable = True

    found = git_run.run_git(
        ["-C", str(repo_dir), "grep", "-l", "-E", _RATCHET_RE, ref, "--", TEST_PATHSPEC]
    )
    if found.returncode == 0 and found.stdout.strip():
        path = found.stdout.splitlines()[0].split(":", 1)[1]
        return GateVerdict(GATED, f"ratchet called from {path}")
    if found.returncode > 1:
        return _unknown(f"cannot search {ref}")

    if package_unreadable:
        return _unknown(f"{PACKAGE_JSON} does not parse")
    return GateVerdict(UNGATED, "no recognised gate form")


def survey(root: Path, projects_toml: Optional[Path] = None) -> Survey:
    """Every declared repo's verdict. Dormant repos are listed, not inspected."""
    tables = fleet_repo_scan.fleet_repo_tables(projects_toml)
    result = Survey()
    for row in fleet_status.list_status(projects_toml):
        name = row["name"]
        if row["status"] == fleet_status.DORMANT:
            result.dormant.append(name)
            continue
        verdict = gate_verdict(Path(root) / name, tables[name])
        if verdict.state == GATED:
            result.gated[name] = verdict.evidence
        elif verdict.state == UNGATED:
            result.ungated.append(name)
        else:
            result.unknown[name] = verdict.evidence
    return result


def format_report(s: Survey) -> str:
    """Counts plus names. The header says why this never gates anything."""
    lines = [
        "fleet gate survey (advisory: another repo's contents never fails the acceptance run)",
        f"{len(s.gated)} gated, {len(s.ungated)} ungated, {len(s.dormant)} dormant (skipped), "
        f"{len(s.unknown)} unknown",
    ]
    if s.ungated:
        lines.append("ungated: " + ", ".join(s.ungated))
    if s.unknown:
        lines.append("unknown: " + ", ".join(f"{n} ({why})" for n, why in s.unknown.items()))
    if s.dormant:
        lines.append("skipped (dormant): " + ", ".join(s.dormant))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Report which active fleet repos lack a recognised gate.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4],
                        help="directory holding the fleet checkouts (default: beside this repo)")
    parser.add_argument("--projects-toml", type=Path, help="read this registry instead of hooks/projects.toml")
    args = parser.parse_args(argv)
    try:
        print(format_report(survey(args.root, args.projects_toml)))
    except (fleet_repo_scan.ProjectsTomlMissing, fleet_status.FleetStatusError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
