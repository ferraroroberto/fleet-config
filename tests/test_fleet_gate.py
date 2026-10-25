"""Fleet gate survey: an active repo has a recognised gate, or it is dormant.

Everything runs against throwaway fixture repos and registries, never the
live fleet. Each fixture commits to `main`, so the survey reads the same
default-branch tree it reads on a real checkout.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
from fleet_core import fleet_gate as fg  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

base = Path(tempfile.mkdtemp(prefix="fleet-gate-"))
root = base / "fleet"


def _git(repo: Path, *args: str) -> None:
    # An empty hooks dir keeps the machine's global commit hooks out of the fixtures.
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", f"core.hooksPath={base / 'no-hooks'}", *args],
        capture_output=True, check=True,
    )


def _repo(name: str, files: Optional[Dict[str, str]] = None) -> Path:
    """A repo whose `main` commit holds exactly `files`."""
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for rel, text in (files or {"README.md": "x\n"}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _verdict(name: str, table: Optional[dict] = None) -> fg.GateVerdict:
    return fg.gate_verdict(root / name, table)


# ---- each recognised form, and its negative -----------------------------------

_repo("ps1", {"scripts/verify-before-ship.ps1": "exit 0\n"})
v = _verdict("ps1")
check(v.state == fg.GATED and "verify-before-ship.ps1" in v.evidence, f"verify-before-ship.ps1 is a gate: {v}")

_repo("ratchet", {"tests/test_gate.py": "import static_ratchet\n"})
v = _verdict("ratchet")
check(v.state == fg.GATED and "tests/test_gate.py" in v.evidence, f"a ratchet called from a test file is a gate: {v}")

_repo("nested", {"tests/acceptance/docs_gate.py": "run('docs_ratchet')\n"})
check(_verdict("nested").state == fg.GATED, "a ratchet in a nested test file is a gate")

_repo("verifyjs", {"scripts/verify.js": "process.exit(0)\n"})
check(_verdict("verifyjs").state == fg.GATED, "scripts/verify.js is a gate")

_repo("npm", {"package.json": '{"scripts": {"verify": "node scripts/x.js"}}\n'})
v = _verdict("npm")
check(v.state == fg.GATED and "package.json" in v.evidence, f"package.json scripts.verify is a gate: {v}")

_repo("bare-npm", {"package.json": '{"name": "x", "scripts": {"start": "node ."}}\n'})
check(_verdict("bare-npm").state == fg.UNGATED, "a package.json without a verify script is not a gate")
_repo("no-scripts", {"package.json": '{"name": "x"}\n'})
check(_verdict("no-scripts").state == fg.UNGATED, "a package.json with no scripts block is not a gate")
_repo("empty-verify", {"package.json": '{"scripts": {"verify": ""}}\n'})
check(_verdict("empty-verify").state == fg.UNGATED, "an empty verify script is not a gate")

_repo("plain")
v = _verdict("plain")
check(v.state == fg.UNGATED, f"a repo with no gate form is ungated: {v}")
check(_verdict("plain", {"cwd_prefix": "X:/plain", "gate_cmd": "pytest"}).state == fg.GATED,
      "a gate_cmd in the repo's registry table is a gate")
check(_verdict("plain", {"cwd_prefix": "X:/plain", "gate_cmd": ""}).state == fg.UNGATED,
      "an empty gate_cmd is not a gate")
check(_verdict("plain", {"cwd_prefix": "X:/plain"}).state == fg.UNGATED, "a table without gate_cmd is not a gate")

_repo("name-only", {"docs/static_ratchet.md": "about static_ratchet\n", "src/docs_ratchet.py": "x = 1\n"})
check(_verdict("name-only").state == fg.UNGATED, "a ratchet named outside tests/ is not a gate")

# ---- the committed default branch decides, not the working tree ---------------

dirty = _repo("dirty")
(dirty / "scripts").mkdir()
(dirty / "scripts" / "verify-before-ship.ps1").write_text("exit 0\n", encoding="utf-8")
check(_verdict("dirty").state == fg.UNGATED, "an untracked gate file does not count")

off = _repo("off-branch", {"scripts/verify-before-ship.ps1": "exit 0\n"})
_git(off, "checkout", "-q", "-b", "feature")
_git(off, "rm", "-q", "scripts/verify-before-ship.ps1")
_git(off, "commit", "-q", "-m", "drop gate on the branch")
check(_verdict("off-branch").state == fg.GATED, "a checkout on another branch is read from its default branch")

# ---- unknown is its own state, never a pass -----------------------------------

v = _verdict("not-cloned", {"cwd_prefix": "X:/not-cloned", "gate_cmd": "pytest"})
check(v.state == fg.UNKNOWN and "no checkout" in v.evidence, f"a missing checkout is unknown even with a gate_cmd: {v}")

(root / "not-git").mkdir()
check(_verdict("not-git").state == fg.UNKNOWN, "a directory that is not a repo is unknown")

_repo("bad-json", {"package.json": "{not json"})
v = _verdict("bad-json")
check(v.state == fg.UNKNOWN and "package.json" in v.evidence, f"an unparsable package.json is unknown: {v}")
_repo("bad-json-gated", {"package.json": "{not json", "scripts/verify.js": "x\n"})
check(_verdict("bad-json-gated").state == fg.GATED, "another form still proves a gate beside an unparsable package.json")

empty = root / "no-commits"
empty.mkdir()
_git(empty, "init", "-q", "-b", "main")
check(_verdict("no-commits").state == fg.UNKNOWN, "a repo with no commits on its default branch is unknown")

# ---- the survey: status first, then the verdict --------------------------------

registry = base / "projects.toml"
registry.write_text(
    "".join(
        f'[{n}]\ncwd_prefix = "X:/fleet/{n}"\n\n'
        for n in ("ps1", "plain", "off-branch", "not-cloned", "nested")
    )
    + '[plain-asleep]\ncwd_prefix = "X:/fleet/plain-asleep"\n\n'
    + "[global]\narchitecture_ignore = [\n  \"plain-asleep\",\n]\n",
    encoding="utf-8",
)
_repo("plain-asleep")
s = fg.survey(root, registry)
check(sorted(s.gated) == ["nested", "off-branch", "ps1"], f"gated repos: {sorted(s.gated)}")
check(s.ungated == ["plain"], f"the active repo with no gate is named: {s.ungated}")
check(s.dormant == ["plain-asleep"], f"a dormant repo is listed as skipped, not named ungated: {s.dormant}")
check(list(s.unknown) == ["not-cloned"], f"a repo with no checkout is unknown: {list(s.unknown)}")
check("plain-asleep" not in s.ungated, "dormant is never ungated")

report = fg.format_report(s)
head = report.splitlines()[0]
check("advisory" in head and "never fails" in head, f"the header says the report is advisory: {head!r}")
check("3 gated, 1 ungated, 1 dormant (skipped), 1 unknown" in report, f"the counts line: {report!r}")
check("ungated: plain" in report and "unknown: not-cloned" in report and "skipped (dormant): plain-asleep" in report,
      f"each bucket names its repos: {report!r}")

# a survey with nothing on disk reports every active repo unknown, never gated
s = fg.survey(base / "elsewhere", registry)
check(not s.gated and not s.ungated and len(s.unknown) == 5 and s.dormant == ["plain-asleep"],
      f"an empty root leaves every active repo unknown: {s}")

cli = subprocess.run(
    [sys.executable, str(ROOT / "skills" / "_lib" / "fleet_core" / "fleet_gate.py"),
     "--projects-toml", str(registry), "--root", str(root)],
    capture_output=True, text=True, encoding="utf-8",
)
check(cli.returncode == 0 and "ungated: plain" in cli.stdout,
      f"the CLI prints the report and exits 0 despite an ungated repo: {cli.returncode} {cli.stdout!r} {cli.stderr!r}")

shutil.rmtree(base, ignore_errors=True)
_h.report_and_exit("test_fleet_gate")
