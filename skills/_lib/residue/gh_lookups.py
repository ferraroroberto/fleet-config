"""Real `gh` lookups for `branch_residue.py`, split out so the classifier stays small.

Every lookup returns None on failure, never a guess: the classifier turns a
None into its own `unknown` class (see `branch_residue.py`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

GH_TIMEOUT_S = 60

IssueStateFn = Callable[[int], Optional[str]]
OpenPrHeadsFn = Callable[[], Optional[Set[str]]]


class ClosingPr(NamedTuple):
    number: int
    head: str
    base: str
    merge_sha: str


# Merged PRs that closed issue N, or None when the lookup failed.
ClosingPrsFn = Callable[[int], Optional[List[ClosingPr]]]


class OwnPr(NamedTuple):
    number: int
    head_sha: str
    base: str
    merged_at: str


# Merged PRs whose head is branch `name`, or None when the lookup failed.
OwnPrsFn = Callable[[str], Optional[List[OwnPr]]]
Lookups = Tuple[IssueStateFn, OpenPrHeadsFn, ClosingPrsFn, OwnPrsFn]


def repo_slug(repo: Path) -> Optional[str]:
    """`owner/name` from origin's URL, or None when it is not a GitHub remote."""
    return git_run.origin_slug(repo)


def make_gh_lookups(slug: Optional[str]) -> Lookups:
    """Real `gh` lookups for `slug`. Every failure is None, never a guess."""
    cache: Dict[int, Optional[str]] = {}
    heads: List[Optional[Set[str]]] = []

    def issue_state(n: int) -> Optional[str]:
        if slug is None:
            return None
        if n not in cache:
            cache[n] = None
            try:
                # `has("pull_request")` tells an issue from a PR number, which
                # the issues endpoint also answers for.
                r = git_run.run_gh(
                    ["api", f"repos/{slug}/issues/{n}", "--jq", '[.state, has("pull_request")] | @tsv'],
                    timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if r.returncode == 0:
                parts = (r.stdout or "").strip().split("\t")
                if len(parts) == 2 and parts[1] == "false" and parts[0] in ("open", "closed"):
                    cache[n] = parts[0]
        return cache[n]

    def open_pr_heads() -> Optional[Set[str]]:
        if slug is None:
            return None
        if not heads:
            result: Optional[Set[str]] = None
            try:
                r = git_run.run_gh(
                    ["pr", "list", "-R", slug, "--state", "open", "--limit", "1000",
                     "--json", "headRefName", "--jq", ".[].headRefName"],
                    timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL,
                )
                if r.returncode == 0:
                    result = {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}
            except (OSError, subprocess.SubprocessError):
                pass
            heads.append(result)
        return heads[0]

    closing_cache: Dict[int, Optional[List[ClosingPr]]] = {}

    def closing_prs(n: int) -> Optional[List[ClosingPr]]:
        if slug is None or "/" not in slug:
            return None
        if n not in closing_cache:
            closing_cache[n] = None
            owner, name = slug.split("/", 1)
            try:
                r = git_run.run_gh(
                    ["api", "graphql", "-f", f"query={_CLOSING_PRS_QUERY}", "-F", f"owner={owner}",
                     "-F", f"name={name}", "-F", f"n={n}", "--jq", _CLOSING_PRS_JQ],
                    timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if r.returncode == 0:
                closing_cache[n] = parse_closing_prs(r.stdout or "")
        return closing_cache[n]

    own_cache: Dict[str, Optional[List[OwnPr]]] = {}

    def own_prs(name: str) -> Optional[List[OwnPr]]:
        if slug is None:
            return None
        if name not in own_cache:
            own_cache[name] = None
            try:
                r = git_run.run_gh(
                    ["pr", "list", "-R", slug, "--state", "merged", "--head", name, "--limit", "100",
                     "--json", "number,headRefOid,baseRefName,mergedAt", "--jq", _OWN_PRS_JQ],
                    timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if r.returncode == 0:
                own_cache[name] = parse_own_prs(r.stdout or "")
        return own_cache[name]

    return issue_state, open_pr_heads, closing_prs, own_prs


# `includeClosedPrs` keeps a merged PR listed; the jq keeps only MERGED rows.
_CLOSING_PRS_QUERY = (
    "query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){issue(number:$n){"
    "closedByPullRequestsReferences(first:50,includeClosedPrs:true){nodes{number state headRefName baseRefName "
    "mergeCommit{oid}}}}}}"
)
_CLOSING_PRS_JQ = (
    '.data.repository.issue.closedByPullRequestsReferences.nodes[] | select(.state == "MERGED") '
    '| [.number, .headRefName, .baseRefName, (.mergeCommit.oid // "")] | @tsv'
)


_OWN_PRS_JQ = '.[] | [.number, .headRefOid, .baseRefName, (.mergedAt // "")] | @tsv'


def parse_own_prs(text: str) -> Optional[List[OwnPr]]:
    """TSV rows from `_OWN_PRS_JQ`; one malformed row (no head sha, no merge time) makes the lookup unknown."""
    prs: List[OwnPr] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0].isdigit() or not parts[1] or not parts[3]:
            return None
        prs.append(OwnPr(int(parts[0]), parts[1], parts[2], parts[3]))
    return prs


def parse_closing_prs(text: str) -> Optional[List[ClosingPr]]:
    """TSV rows from `_CLOSING_PRS_JQ`; one malformed row makes the whole lookup unknown."""
    prs: List[ClosingPr] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0].isdigit() or not parts[3]:
            return None
        prs.append(ClosingPr(int(parts[0]), parts[1], parts[2], parts[3]))
    return prs
