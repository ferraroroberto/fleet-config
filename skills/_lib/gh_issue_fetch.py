"""Direct-Issues-API fetch: the preferred replacement for `gh search issues
--owner ferraroroberto --state open ...` (fleet-config#623).

Why this exists
----------------
`gh search issues --owner` is backed by GitHub's Search API, which is
documented as eventually consistent and was observed reporting 23 issues as
open for five-plus weeks after they had actually been closed. This module
fetches the same information through the direct Issues REST API instead --
one `gh issue list --repo <owner>/<name> --state open` per repo, aggregated
here -- because a repo-scoped smoke test in fleet-config#623 did not exhibit
the staleness where the owner-wide search did.

Read that as "avoids a known-bad source", not as "proven immune". The
smoke test that motivated this is a single same-day observation, not a
guarantee about every cache layer between `gh` and GitHub's backend --
which is exactly why this fetch is paired with `issue_state_gate.py`'s
per-issue pre-dispatch re-check rather than treated as sufficient on its
own. The two cover different failure modes and neither subsumes the other.

Subcommand
----------
  fetch [--label LABEL] [--include-dormant] [--projects-toml PATH]
        Prints a JSON array to stdout, each element shaped like a `gh
        search issues` row (`number`, `title`, `body`, `labels`, `url`,
        `repository: {"name": ...}`) so existing callers that read
        `repository.name` need no other change. Prints a one-line summary
        to stderr: `REPOS=<n> ISSUES=<n> ERRORS=<n>`, plus one `ERROR
        <repo>: <reason>` line per repo that could not be read -- a repo
        that fails is skipped and reported, never allowed to blank the
        whole fetch (same "degrade, don't block" rule every fan-out skill
        in this repo already follows).

        A repo in `hooks/projects.toml`'s `[global] architecture_ignore` is
        dormant and is not fetched (`docs/fleet-scope.md`); a repo the list
        does not know is still fetched. Dormant repos are named on a second
        stderr line, `SKIPPED_DORMANT=<n>: <names>`, so a shrunk result is
        never silent. `--include-dormant` fetches them too and never reads
        the list. A missing list exits 2: a run never guesses its own scope.

The aggregation logic (`aggregate`) is pure and unit-tested
(`tests/repo/test_gh_issue_fetch.py`) independent of the `gh` plumbing around
it. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_core import fleet_repo_scan  # noqa: E402
import git_run  # noqa: E402

GH_TIMEOUT_SECONDS = 60
OWNER = "ferraroroberto"
ISSUE_FIELDS = "number,title,body,labels,url,createdAt,updatedAt,assignees"


def list_owner_repos() -> List[str]:
    proc = git_run.run_gh(
        ["repo", "list", OWNER, "--json", "name", "--limit", "300", "--no-archived"],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gh repo list failed").strip())
    return [row["name"] for row in json.loads(proc.stdout)]


def split_dormant(repos: List[str], dormant: Collection[str]) -> Tuple[List[str], List[str]]:
    """`(kept, skipped)`: `repos` without the dormant names, and the ones dropped, order preserved."""
    return [r for r in repos if r not in dormant], [r for r in repos if r in dormant]


def fetch_repo_issues(repo: str, label: str | None) -> List[Dict[str, Any]]:
    """Real `gh` call for one repo. Raises on failure -- the caller decides
    how to record that as a per-repo error, never as a silent empty list."""
    cmd = ["issue", "list", "--repo", f"{OWNER}/{repo}", "--state", "open", "--json", ISSUE_FIELDS]
    if label:
        cmd += ["--label", label]
    proc = git_run.run_gh(cmd, timeout=GH_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gh issue list failed").strip())
    return json.loads(proc.stdout)


def aggregate(
    repos: List[str],
    fetch_one: Callable[[str], List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """Pure aggregation over an injected per-repo fetcher: no `gh` calls of
    its own. A repo whose fetch raises is recorded in `errors` and excluded
    from the issue list -- it never blanks the whole result, and it is never
    silently missing either."""
    issues: List[Dict[str, Any]] = []
    errors: List[Tuple[str, str]] = []
    for repo in repos:
        try:
            rows = fetch_one(repo)
        except Exception as exc:  # noqa: BLE001 -- any per-repo failure degrades, never aborts
            errors.append((repo, str(exc)))
            continue
        for row in rows:
            issues.append({**row, "repository": {"name": repo}})
    return issues, errors


def cmd_fetch(label: str | None, include_dormant: bool = False, projects_toml: Path | None = None) -> None:
    repos = list_owner_repos()
    skipped: List[str] = []
    if not include_dormant:
        repos, skipped = split_dormant(repos, fleet_repo_scan.ignored_repos(projects_toml))
    issues, errors = aggregate(repos, lambda r: fetch_repo_issues(r, label))
    print(json.dumps(issues))
    print(f"REPOS={len(repos)} ISSUES={len(issues)} ERRORS={len(errors)}", file=sys.stderr)
    if skipped:
        print(f"SKIPPED_DORMANT={len(skipped)}: {', '.join(skipped)}", file=sys.stderr)
    for repo, reason in errors:
        print(f"ERROR {repo}: {reason}", file=sys.stderr)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Direct-Issues-API fetch across every ferraroroberto repo.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--label", default=None)
    f.add_argument("--include-dormant", action="store_true", help="also fetch repos switched out of fleet runs")
    f.add_argument("--projects-toml", type=Path, default=None, help="read the fleet list from another copy")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        try:
            cmd_fetch(args.label, args.include_dormant, args.projects_toml)
        except fleet_repo_scan.ProjectsTomlMissing as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
