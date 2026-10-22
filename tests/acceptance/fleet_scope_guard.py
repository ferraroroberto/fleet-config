"""Fleet-scope guard: every fleet enumeration route obeys the dormant switch.

A fleet-wide route must drop the repos in `hooks/projects.toml`'s
`[global] architecture_ignore` (`docs/fleet-scope.md`) or be recorded as
deliberately unfiltered. This scan reads the runtime trees (`hooks/`,
`skills/`, `.claude/skills/`: `.py` files and `SKILL.md`) for each route
pattern in `PATTERNS`. A hit passes when

- its file handles the status (a `STATUS_MARKERS` name appears in it), for the
  patterns that marker can vouch for, or
- its file and pattern are in `fleet_scope_allowlist.json` with a reason.

An allow-list entry that no longer matches anything fails too: a stale entry
is drift, and a missing reason is no reason. `tests/` is exempt because its
fixtures legitimately contain these strings.

The scan is textual on purpose. A missed pattern is a silent hole, so it errs
noisy and lets the allow-list carry the reasoned exceptions.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from acceptance.shared import REPO, _Checker

ALLOWLIST_PATH = Path(__file__).resolve().parent / "fleet_scope_allowlist.json"

# Runtime trees only; `skills/synced` is Anthropic's account-skill sync landing
# in the junctioned `skills/`, not ours.
SCAN_DIRS = ("hooks", "skills", ".claude/skills")
SCAN_EXCLUDE = ("skills/synced",)

# A file naming one of these handles the status itself: it filters, reports
# what it dropped, or exposes the opt-in flag.
STATUS_MARKERS = re.compile(
    r"\b(?:ignored_repos|split_dormant|list_status|get_status|SKIPPED_DORMANT)\b|include[_-]dormant"
)

# Characters either side of an `.iterdir()` hit searched for a `.git` test.
GIT_TEST_WINDOW = 10


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: "re.Pattern[str]"
    comment: str
    # True: a STATUS_MARKERS name in the same file clears a hit.
    # False: only an allow-list entry does (the pattern is itself an opt-out).
    marker_clears: bool = True
    # Only a hit with this regex within GIT_TEST_WINDOW lines counts.
    near: Optional["re.Pattern[str]"] = None


PATTERNS: Tuple[Pattern, ...] = (
    # fleet_repo_tables() is unfiltered by design: it answers what a repo
    # declares, so a new caller must say why a dormant repo belongs in its answer.
    Pattern(
        "fleet_repo_tables",
        re.compile(r"(?<!def )\bfleet_repo_tables\("),
        "declaration lookup, not filtered by the switch",
    ),
    # fleet_repos() and iter_fleet_repos() drop dormant repos by default; only
    # an explicit include_ignored=True keeps them. Nested parens (a call in an
    # argument) are covered one level deep.
    Pattern(
        "include_ignored",
        re.compile(r"\b(?:iter_)?fleet_repos\((?:[^()]|\([^()]*\))*?include_ignored\s*=\s*True"),
        "explicit opt-out of the switch on a filtering route",
        marker_clears=False,
    ),
    # Owner-wide repo listing, as a command line or as an argv list.
    Pattern(
        "gh_repo_list",
        re.compile(r"gh repo list|[\"']repo[\"']\s*,\s*[\"']list[\"']"),
        "owner-wide repo listing; gh_issue_fetch.py is the filtered issue route",
    ),
    # Owner-wide issue search, as a command line or as an argv list.
    Pattern(
        "gh_search_issues",
        re.compile(r"gh search issues[^\n]*--owner|[\"']search[\"']\s*,\s*[\"']issues[\"']"),
        "owner-wide issue search; gh_issue_fetch.py is the filtered issue route",
    ),
    # A directory walk next to a .git test is a fleet-root crawl.
    Pattern(
        "iterdir_git",
        re.compile(r"\.iterdir\(\)"),
        "ad hoc crawl of a directory of repos",
        near=re.compile(r"[\"']\.git[\"']"),
    ),
)
PATTERN_NAMES = {p.name for p in PATTERNS}

FIX_HINT = (
    "use fleet_repos() / iter_fleet_repos() / ignored_repos() so dormant repos are dropped, "
    "or add an entry to fleet_scope_allowlist.json with a reason"
)


def _files(root: Path) -> List[Path]:
    """Every `.py` and `SKILL.md` under the scanned trees of `root`."""
    found: List[Path] = []
    for rel in SCAN_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not (path.name == "SKILL.md" or path.suffix == ".py"):
                continue
            label = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or label.startswith(tuple(f"{d}/" for d in SCAN_EXCLUDE)):
                continue
            if path.is_file():
                found.append(path)
    return found


def _hits(pattern: Pattern, text: str) -> List[int]:
    """1-based line numbers where `pattern` matches in `text`."""
    lines = text.splitlines()
    out: List[int] = []
    for match in pattern.regex.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if pattern.near is not None:
            lo, hi = max(0, line - 1 - GIT_TEST_WINDOW), line + GIT_TEST_WINDOW
            if not any(pattern.near.search(row) for row in lines[lo:hi]):
                continue
        out.append(line)
    return out


def _allow_index(allowlist: Sequence[dict]) -> Tuple[Dict[Tuple[str, str], str], List[str]]:
    """`{(file, pattern): reason}` plus a problem line per malformed entry."""
    index: Dict[Tuple[str, str], str] = {}
    problems: List[str] = []
    for n, entry in enumerate(allowlist, 1):
        name, files = entry.get("pattern"), entry.get("files")
        reason = str(entry.get("reason", "")).strip()
        if name not in PATTERN_NAMES:
            problems.append(f"allow-list entry {n}: unknown pattern {name!r} (known: {sorted(PATTERN_NAMES)})")
        elif not reason:
            problems.append(f"allow-list entry {n} ({name}): no reason")
        elif not isinstance(files, list) or not files:
            problems.append(f"allow-list entry {n} ({name}): no files")
        else:
            for rel in files:
                index[(rel, name)] = reason
    return index, problems


def scan(root: Path, allowlist: Sequence[dict]) -> List[str]:
    """Every violation under `root`, one line each: unlisted hits first, then
    malformed or stale allow-list entries."""
    index, problems = _allow_index(allowlist)
    matched: set = set()
    violations: List[str] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        handles_status = bool(STATUS_MARKERS.search(text))
        for pattern in PATTERNS:
            lines = _hits(pattern, text)
            if not lines:
                continue
            if (rel, pattern.name) in index:
                matched.add((rel, pattern.name))
                continue
            if pattern.marker_clears and handles_status:
                continue
            violations.extend(f"{rel}:{n}: {pattern.name} ({pattern.comment}) -- {FIX_HINT}" for n in lines)
    stale = [
        f"{rel}: allow-list entry for {name} matches nothing -- remove it"
        for rel, name in sorted(index)
        if (rel, name) not in matched
    ]
    return violations + problems + stale


def load_allowlist(path: Path = ALLOWLIST_PATH) -> List[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_cases(check: _Checker) -> None:
    """The guard's own behaviour on throwaway trees: each case adds a file to
    an otherwise empty tree and names the outcome it must produce."""
    route = 'import subprocess\nsubprocess.run(["gh", "repo", "list", "owner"])\n'

    def run(files: Dict[str, str], allowlist: Sequence[dict] = ()) -> List[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            return scan(root, allowlist)

    got = run({"skills/_lib/new_route.py": route})
    check("fleet_scope: a new `gh repo list` call fails, naming file, line and pattern",
          len(got) == 1 and got[0].startswith("skills/_lib/new_route.py:2: gh_repo_list"), "\n".join(got))

    entry = {"pattern": "gh_repo_list", "files": ["skills/_lib/new_route.py"], "reason": "reads history"}
    got = run({"skills/_lib/new_route.py": route}, [entry])
    check("fleet_scope: the same call with an allow-list entry passes", got == [], "\n".join(got))

    got = run({"skills/_lib/new_route.py": "x = 1\n"}, [entry])
    check("fleet_scope: an allow-list entry that matches nothing fails as stale",
          len(got) == 1 and "matches nothing" in got[0], "\n".join(got))

    got = run({}, [entry])
    check("fleet_scope: an allow-list entry for a missing file fails as stale",
          len(got) == 1 and "matches nothing" in got[0], "\n".join(got))

    got = run({"skills/_lib/a.py": route}, [{**entry, "files": ["skills/_lib/a.py"], "reason": " "}])
    check("fleet_scope: an allow-list entry without a reason fails", any("no reason" in g for g in got), "\n".join(got))

    got = run({}, [{**entry, "pattern": "gh_repo_lst"}])
    check("fleet_scope: an allow-list entry with an unknown pattern fails",
          any("unknown pattern" in g for g in got), "\n".join(got))

    got = run({"skills/_lib/a.py": "fleet_repo_scan.iter_fleet_repos(root, include_ignored=True)\n"})
    check("fleet_scope: iter_fleet_repos(include_ignored=True) with no entry fails",
          len(got) == 1 and ":1: include_ignored" in got[0], "\n".join(got))

    got = run({"skills/_lib/a.py": "x = fleet_repos(\n    toml,\n    include_ignored=True,\n)\n"})
    check("fleet_scope: a multi-line include_ignored=True call fails",
          len(got) == 1 and ":1: include_ignored" in got[0], "\n".join(got))

    got = run({"skills/_lib/a.py": "for d in iter_fleet_repos(root):\n    pass\nrepos = fleet_repos()\n"})
    check("fleet_scope: the filtering routes called with their defaults pass", got == [], "\n".join(got))

    got = run({"skills/_lib/a.py": "tables = fleet_repo_scan.fleet_repo_tables()\n"})
    check("fleet_scope: an unfiltered fleet_repo_tables() call fails",
          len(got) == 1 and "fleet_repo_tables" in got[0], "\n".join(got))

    got = run({"skills/_lib/a.py": "tables = fleet_repo_tables()\ndormant = ignored_repos()\n"})
    check("fleet_scope: a file that reads ignored_repos() clears the unfiltered route", got == [], "\n".join(got))

    got = run({"skills/_lib/a.py": "x = fleet_repos(include_ignored=True)\ndormant = ignored_repos()\n"})
    check("fleet_scope: a status marker does not clear an include_ignored opt-out", len(got) == 1, "\n".join(got))

    got = run({".claude/skills/new/SKILL.md": "Run `gh repo list ferraroroberto` and loop.\n"})
    check("fleet_scope: a raw `gh repo list` instruction in a SKILL.md fails",
          len(got) == 1 and got[0].startswith(".claude/skills/new/SKILL.md:1: gh_repo_list"), "\n".join(got))

    got = run({".claude/skills/new/SKILL.md": "Run `gh search issues --owner x --state open`.\n"})
    check("fleet_scope: a raw `gh search issues --owner` instruction fails",
          len(got) == 1 and "gh_search_issues" in got[0], "\n".join(got))

    got = run({".claude/skills/new/SKILL.md": "Run `gh repo list x`; dormant repos are named on SKIPPED_DORMANT=.\n"})
    check("fleet_scope: a SKILL.md that documents the dormant handling passes", got == [], "\n".join(got))

    crawl = 'for d in root.iterdir():\n    if (d / ".git").is_dir():\n        pass\n'
    got = run({"skills/_lib/a.py": crawl})
    check("fleet_scope: an iterdir() crawl with a .git test fails",
          len(got) == 1 and ":1: iterdir_git" in got[0], "\n".join(got))

    got = run({"skills/_lib/a.py": "for d in lock_dir.iterdir():\n    pass\n" + "\n" * 30 + 'x = ".git"\n'})
    check("fleet_scope: an iterdir() with no .git test nearby passes", got == [], "\n".join(got))

    got = run({"tests/test_x.py": route, "skills/synced/x/SKILL.md": "gh repo list\n"})
    check("fleet_scope: tests/ and skills/synced are not scanned", got == [], "\n".join(got))


def _fleet_scope_guard_check() -> Tuple[int, int]:
    check = _Checker()
    _fixture_cases(check)
    violations = scan(REPO, load_allowlist())
    check("fleet_scope: every fleet enumeration route obeys the switch or is allow-listed",
          not violations, "\n".join(violations))
    return check.failures, check.total
