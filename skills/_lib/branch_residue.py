"""Report a repo's leftover branches and orphan worktree folders at teardown (fleet-config#1516).

Why this exists
----------------
Teardown deletes only the lane's own branch and, by design, reports nothing
about anyone else's (fleet-config#572), so branches from closed issues pile up.
This module judges whether one is safe to drop (a hand-run `gh pr list` /
`git diff` lookup otherwise) as a deterministic, read-only report.

`branches` only reports. The one mutating path is `branches --delete` (below),
which acts only on `landed` and `superseded` refs; no folder is ever removed here.

Classes
--------
Each ref (local from `for-each-ref`, remote from `ls-remote --heads origin`, so
a branch already gone from origin is not reported from a stale tracking ref)
gets exactly one class, evaluated cheapest-first, first hit wins:

  default      -- the default branch. Never touched.
  checked-out  -- checked out in the primary or any registered worktree.
  no-issue     -- the name is not `<type>/<N>-slug`, so there is no issue to
                  prove closed. Never `landed`.
  open-issue   -- issue N is open (protects a freshly cut, still-empty branch,
                  which is trivially an ancestor of the default branch).
  unknown      -- a fact could not be established: issue N is not an issue of
                  this repo (a PR number, another repo's), `gh` failed, the tip
                  object is missing, or the merge proof errored. A separate
                  state, never folded into `landed` or `keep`.
  unlanded     -- issue closed but the tip carries changes not in the default
                  branch (or conflicts with it), and no superseding merge
                  contains them: unmerged work.
  open-pr      -- an open PR has this head.
  landed       -- no open PR AND the tip is proven landed: by
                  `worktree_residue.head_merged` (issue closed), or by its own
                  PR into the default branch merged with this exact tip as
                  its head (any issue state: outranks open-issue, unknown).
  superseded   -- issue closed AND no open PR AND, though the tip is not
                  landed, merging it into the merge commit of a PR from a
                  *different* head that closed the issue (based on, and
                  present in, the default branch) changes nothing.

`--delete` acts on `landed` and `superseded` only: neither holds unshipped work.
The merge proof needs a closed issue too, or it would call a fresh empty branch
landed. `git branch --merged` is never used: the fleet squash-merges, so a
landed tip is never an ancestor (fleet-config#567). Local and remote tips are
judged independently.

The own-PR route covers a branch that squash-merged before the default branch
edited the same lines, so the merge proof now conflicts. GitHub records each
merged PR's head sha (`headRefOid`); when it equals the tip, nothing was pushed
after the merge, so the tip holds no work the PR did not ship. A PR based on
another branch, or with no recorded `mergedAt`, is never proof; a failed lookup
is `unknown`; one commit pushed after the merge is `unlanded`. It needs no
issue: a branch named for another repo's issue is `landed` on it alone; without
it a failed lookup stays `unknown`, an open issue `open-issue`.

`superseded` covers a branch whose issue shipped through another branch (a
`-v2` re-cut): that squash is not this tip, so `landed` cannot see it. The
proof is `head_merged(tip, M)` with M the other PR's merge commit, so the
3-way merge absorbs work interleaved before M, and edits made on the default
branch after M do not hide it. Candidates come from GitHub's closing-PR
references of the issue; a PR from this same branch, one based elsewhere, or
one whose merge commit is not on the default branch is never a candidate. A
failed lookup, a merge commit missing locally or an errored proof is
`unknown`; no candidate, or one unshipped hunk, is `unlanded`.

Orphan folders
---------------
A sibling `<repo>-wt-*` directory (any suffix) that `git worktree list` does
not register. Reported as `orphan-with-git-file | orphan-has-content |
orphan-empty | unknown`; never removed here (`chief_ops.py sweep --apply` owns
empty-shell removal).

Subcommands
-----------
  branches <repo-path> [--no-fetch]
        One line per ref:
          BRANCH=<name> WHERE=local|remote SHA=<sha7> ISSUE=<N|-> CLASS=<class> REASON=<why>
        then the orphan section (`ORPHAN=<path> CLASS=<class> REASON=<why>`,
        `ORPHANS=<n>`), `SUMMARY landed=<n> superseded=<n> unlanded=<n> keep=<n> unknown=<n>`,
        `VERDICT=complete|partial|unknown`, plus `REASON=`/`NOTE=` when a
        listing or the fetch failed. `partial` means a half could not be read
        (e.g. `ls-remote` failed), so the counts are not the whole picture.
  orphans <repo-path>
        Only the orphan section.

  branches <repo-path> --delete [--branch NAME]... [--remote] [--no-fetch]
        After the report, delete refs that are `landed` or `superseded` NOW;
        `/issue-finish` runs it as a fixed step after its own merge. Without
        `--branch` it acts on every deletable row, with it only on the named
        ones; naming any other class (the default, a checked-out branch,
        `no-issue`, `unlanded`, `unknown`, ...) is refused with its class. Each
        target is re-judged immediately before acting from a fresh tip sha,
        proof and PR lookup, so a branch that moved or gained a PR is skipped.
          local   `git branch -D` (`-d` refuses squash-merged branches).
          remote  only with `--remote`, only `origin`; the remote tip is proven
                  on its own from `ls-remote` and deleted with
                  `--force-with-lease=refs/heads/<n>:<sha> --delete`, so a push
                  after the proof is rejected and reported `STAYS=`.
        Prints `DELETED=<where>:<name> SHA=<sha> PROOF=<...>` then
        `RESTORE=<command that brings it back>`; `SKIPPED=`, `STAYS=`,
        `REFUSED=`, `FAILED=` for the rest, then
        `DELETE_SUMMARY deleted=<n> skipped=<n> failed=<n>`. Every target is
        attempted. Exits 1 when a delete failed, a named ref was not deleted,
        or the repo was unreadable.

The report exits 0 -- it reports, the caller decides. The decision core
(`classify_ref`, `branch_issue`, `summarize`) is pure; `build_report` and
`delete_landed` take the issue, open-PR, closing-PR and own-PR lookups as
injected callables so tests never touch `gh` or the network. The tip proof is
cached per sha and the `gh` and merge-tree lookups run on a small pool: serial,
a 62-ref repo took 72 s (fleet-config#1528). stdlib only.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
import worktree_residue as wr  # noqa: E402

DEFAULT = "default"
CHECKED_OUT = "checked-out"
NO_ISSUE = "no-issue"
OPEN_ISSUE = "open-issue"
UNKNOWN = "unknown"
UNLANDED = "unlanded"
OPEN_PR = "open-pr"
LANDED = "landed"
SUPERSEDED = "superseded"

DELETABLE = (LANDED, SUPERSEDED)

# Classes the summary counts as "left alone on purpose".
_KEEP = (DEFAULT, CHECKED_OUT, NO_ISSUE, OPEN_ISSUE, OPEN_PR)

VERDICT_COMPLETE = "complete"
VERDICT_PARTIAL = "partial"
VERDICT_UNKNOWN = "unknown"

# `<type>/<N>-slug`, the shape /issue-start cuts. `[A-Za-z][\w-]*` keeps
# `fix/`, `feat/`, `docs/`, `ci/` and anything similar; a bare number segment
# (`fix/12`) counts too.
_BRANCH_ISSUE_RE = re.compile(r"^[A-Za-z][\w-]*/(\d+)(?:-|$)")

GH_TIMEOUT_S = 60
PUSH_TIMEOUT_S = 120
# Few enough not to load the box (merge-tree is CPU-bound), enough to hide `gh` latency.
LOOKUP_WORKERS = 4

log = logging.getLogger("branch_residue")

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


class Ref(NamedTuple):
    name: str
    where: str  # "local" | "remote"
    sha: str


class Row(NamedTuple):
    ref: Ref
    issue: Optional[int]
    cls: str
    reason: str


class Orphan(NamedTuple):
    path: str
    cls: str
    reason: str


class Outcome(NamedTuple):
    action: str  # deleted | skipped | stays | refused | failed
    where: str
    name: str
    sha: str
    detail: str
    restore: str = ""


class Report(NamedTuple):
    rows: List[Row]
    orphans: List[Orphan]
    verdict: str
    reason: str
    notes: List[str]


# ---- pure core ------------------------------------------------------------

def branch_issue(name: str) -> Optional[int]:
    """The issue number encoded in a `<type>/<N>-slug` branch name, else None."""
    m = _BRANCH_ISSUE_RE.match(name)
    return int(m.group(1)) if m else None


def classify_ref(
    *,
    is_default: bool,
    checked_out: bool,
    issue: Optional[int],
    issue_state: Optional[str],
    tip_readable: bool,
    merged: Optional[bool],
    merged_how: str,
    open_pr: Optional[bool],
    superseded: Optional[bool] = False,
    superseded_how: str = "",
    own_pr: Optional[bool] = False,
    own_pr_how: str = "",
) -> Tuple[str, str]:
    """Pure (class, reason) for one ref. `None` means "could not establish".

    `merged` / `superseded` / `own_pr` / `open_pr` are only consulted past the
    gates that make them matter, so a caller may leave them `None` for a ref
    decided earlier.
    """
    if is_default:
        return DEFAULT, "the default branch"
    if checked_out:
        return CHECKED_OUT, "checked out in a worktree"
    if issue is None:
        return NO_ISSUE, "name carries no issue number, so there is no closed issue to prove"
    if issue_state != "closed" and own_pr:  # nothing is unshipped, so a possibly foreign issue number cannot matter
        if open_pr is None:
            return UNKNOWN, "could not establish whether an open PR has this head"
        if open_pr:
            return OPEN_PR, "an open PR has this head"
        return LANDED, f"{own_pr_how} (issue #{issue} not consulted)"
    if issue_state == "open":
        return OPEN_ISSUE, f"issue #{issue} is still open"
    if issue_state != "closed":
        return UNKNOWN, f"state of issue #{issue} not established (not an issue of this repo, or the lookup failed)"
    if not tip_readable:
        return UNKNOWN, "tip commit is not present locally, so it cannot be proven"
    if merged is None:
        return UNKNOWN, merged_how or "could not establish whether the tip is merged"
    if not merged:
        if superseded is None:
            return UNKNOWN, superseded_how or "could not establish whether another merged PR contains the tip"
        if not superseded:
            if own_pr is None:
                return UNKNOWN, own_pr_how or "could not establish whether this branch's own PR merged at this tip"
            if not own_pr:
                return UNLANDED, merged_how or "tip carries changes not in the default branch"
    if open_pr is None:
        return UNKNOWN, "could not establish whether an open PR has this head"
    if open_pr:
        return OPEN_PR, "an open PR has this head"
    if merged:
        return LANDED, f"issue #{issue} closed; {merged_how}"
    if superseded:
        return SUPERSEDED, f"issue #{issue} closed; {superseded_how}"
    return LANDED, f"issue #{issue} closed; {own_pr_how}"


def summarize(rows: Sequence[Row]) -> Dict[str, int]:
    counts = {"landed": 0, "superseded": 0, "unlanded": 0, "keep": 0, "unknown": 0}
    for row in rows:
        if row.cls == LANDED:
            counts["landed"] += 1
        elif row.cls == SUPERSEDED:
            counts["superseded"] += 1
        elif row.cls == UNLANDED:
            counts["unlanded"] += 1
        elif row.cls == UNKNOWN:
            counts["unknown"] += 1
        elif row.cls in _KEEP:
            counts["keep"] += 1
    return counts


# ---- gh lookups (injectable) ------------------------------------------------

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


# ---- git plumbing -----------------------------------------------------------

def list_local(repo: Path) -> List[Ref]:
    out = git_run.run_git_or_raise(repo, "for-each-ref", "--format=%(objectname)%09%(refname)", "refs/heads")
    refs: List[Ref] = []
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/") and sha:
            refs.append(Ref(ref[len("refs/heads/"):], "local", sha))
    return refs


def list_remote(repo: Path) -> List[Ref]:
    out = git_run.run_git_or_raise(repo, "ls-remote", "--heads", "origin")
    refs: List[Ref] = []
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/") and sha:
            refs.append(Ref(ref[len("refs/heads/"):], "remote", sha))
    return refs


def tip_readable(repo: Path, sha: str) -> bool:
    return git_run.run_git(["-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def find_orphans(primary: str, registered: Sequence[str]) -> List[Orphan]:
    """Sibling `<primary-name>-wt-*` folders that git does not register, as `worktree_residue` scans and judges them."""
    found: List[Orphan] = []
    for sib in wr.unregistered_siblings(Path(primary), registered):
        facts = wr.dir_facts(sib)
        cls, why = wr.classify_orphan(facts.has_git, None if facts.empty is None else not facts.empty)
        found.append(Orphan(str(sib), cls, why))
    return found


# ---- judgement ---------------------------------------------------------------

class _Judge:
    """Classifies refs, caching the expensive facts behind a class.

    Issue state and closing PRs per number, tip proof per sha (a local and a
    remote ref at the same tip share one `merge-tree`), own merged PRs per
    branch name, open-PR heads once.
    """

    def __init__(self, repo: Path, default_ref: str, issue_state_fn: IssueStateFn,
                 open_pr_heads_fn: OpenPrHeadsFn, closing_prs_fn: ClosingPrsFn,
                 own_prs_fn: OwnPrsFn) -> None:
        self.repo = repo
        self.default_ref = default_ref
        self.default_name = default_ref[len("origin/"):] if default_ref.startswith("origin/") else default_ref
        self._issue_state_fn = issue_state_fn
        self._open_pr_heads_fn = open_pr_heads_fn
        self._closing_prs_fn = closing_prs_fn
        self._own_prs_fn = own_prs_fn
        self._own: Dict[str, Optional[List[OwnPr]]] = {}
        self._states: Dict[int, Optional[str]] = {}
        self._closing: Dict[int, Optional[List[ClosingPr]]] = {}
        self._supersedes: Dict[Tuple[str, str, int], Tuple[Optional[bool], str]] = {}
        self._proofs: Dict[str, Tuple[bool, Optional[bool], str]] = {}
        self._pr_heads: List[Optional[Set[str]]] = []

    def state(self, n: int) -> Optional[str]:
        if n not in self._states:
            self._states[n] = self._issue_state_fn(n)
        return self._states[n]

    def proof(self, sha: str) -> Tuple[bool, Optional[bool], str]:
        """(tip readable, merged, how) for one tip."""
        if sha not in self._proofs:
            if not tip_readable(self.repo, sha):
                self._proofs[sha] = (False, None, "")
            else:
                merged, how = wr.head_merged(self.repo, sha, self.default_ref)
                self._proofs[sha] = (True, merged, how)
        return self._proofs[sha]

    def closing(self, n: int) -> Optional[List[ClosingPr]]:
        if n not in self._closing:
            self._closing[n] = self._closing_prs_fn(n)
        return self._closing[n]

    def supersede(self, name: str, sha: str, issue: int) -> Tuple[Optional[bool], str]:
        """(superseded, how) for a tip not landed on the default branch; rule in the module docstring."""
        key = (name, sha, issue)
        if key not in self._supersedes:
            self._supersedes[key] = self._supersede(name, sha, issue)
        return self._supersedes[key]

    def _supersede(self, name: str, sha: str, issue: int) -> Tuple[Optional[bool], str]:
        prs = self.closing(issue)
        if prs is None:
            return None, f"could not list the merged PRs that closed issue #{issue}"
        unknown = ""
        for pr in (p for p in prs if p.head != name and p.base == self.default_name):
            if not tip_readable(self.repo, pr.merge_sha):
                unknown = f"merge commit {pr.merge_sha[:7]} of PR #{pr.number} is not present locally"
                continue
            on_default = git_run.run_git(
                ["-C", str(self.repo), "merge-base", "--is-ancestor", pr.merge_sha, self.default_ref])
            if on_default.returncode == 1:
                continue
            if on_default.returncode != 0:
                unknown = f"could not establish whether PR #{pr.number}'s merge is on {self.default_ref}"
                continue
            contained, how = wr.head_merged(self.repo, sha, pr.merge_sha)
            if contained:
                return True, (f"tip {sha[:7]} is contained in merge {pr.merge_sha[:7]} of PR #{pr.number}"
                              f" from {pr.head} (merging it there changes nothing)")
            if contained is None:
                unknown = how
        return (None, unknown) if unknown else (False, "")

    def own_landed(self, name: str, sha: str) -> Tuple[Optional[bool], str]:
        """(own PR merged at this tip, how); rule in the module docstring."""
        if name not in self._own:
            self._own[name] = self._own_prs_fn(name)
        prs = self._own[name]
        if prs is None:
            return None, f"could not list the merged PRs whose head is {name}"
        for pr in prs:
            if pr.base == self.default_name and pr.head_sha == sha:
                return True, (f"own PR #{pr.number} merged into {pr.base} at {pr.merged_at}"
                              f" with head {sha[:7]}, the current tip")
        return False, ""

    def has_open_pr(self, name: str) -> Optional[bool]:
        if not self._pr_heads:
            self._pr_heads.append(self._open_pr_heads_fn())
        heads = self._pr_heads[0]
        return None if heads is None else name in heads

    def prefetch(self, refs: Sequence[Ref], checked_out: Set[str]) -> None:
        """Fill the issue and proof caches for `refs` on a small pool."""
        live = [r for r in refs if r.name != self.default_name and r.name not in checked_out]
        numbers = sorted({n for n in (branch_issue(r.name) for r in live) if n is not None})
        with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
            list(pool.map(self.state, numbers))
            closed = [r for r in live if self._states.get(branch_issue(r.name)) == "closed"]
            unclosed = [r for r in live if branch_issue(r.name) is not None and r not in closed]
            list(pool.map(lambda r: self.own_landed(r.name, r.sha), unclosed))
            list(pool.map(self.proof, sorted({r.sha for r in closed})))
            unmerged = [r for r in closed if self._proofs[r.sha][0] and self._proofs[r.sha][1] is False]
            list(pool.map(lambda r: self.supersede(r.name, r.sha, branch_issue(r.name)), unmerged))
            not_superseded = [r for r in unmerged if self.supersede(r.name, r.sha, branch_issue(r.name))[0] is False]
            list(pool.map(lambda r: self.own_landed(r.name, r.sha), not_superseded))

    def judge(self, ref: Ref, checked_out: Set[str]) -> Row:
        issue = branch_issue(ref.name)
        is_default = ref.name == self.default_name
        is_checked_out = ref.name in checked_out
        gated = is_default or is_checked_out or issue is None
        state = None if gated else self.state(issue)  # type: ignore[arg-type]
        readable = False
        merged: Optional[bool] = None
        how = ""
        open_pr: Optional[bool] = None
        superseded: Optional[bool] = False
        superseded_how = ""
        own_pr: Optional[bool] = False
        own_pr_how = ""
        if not gated and state != "closed":
            own_pr, own_pr_how = self.own_landed(ref.name, ref.sha)  # None (lookup failed) leaves the issue's answer standing
            if own_pr:
                open_pr = self.has_open_pr(ref.name)
        if not gated and state == "closed":
            readable, merged, how = self.proof(ref.sha)
            if readable and merged is False:
                superseded, superseded_how = self.supersede(ref.name, ref.sha, issue)  # type: ignore[arg-type]
                if superseded is False:
                    own_pr, own_pr_how = self.own_landed(ref.name, ref.sha)
            if readable and (merged or superseded or own_pr):
                open_pr = self.has_open_pr(ref.name)
        cls, why = classify_ref(
            is_default=is_default, checked_out=is_checked_out, issue=issue, issue_state=state,
            tip_readable=readable, merged=merged, merged_how=how, open_pr=open_pr,
            superseded=superseded, superseded_how=superseded_how, own_pr=own_pr, own_pr_how=own_pr_how,
        )
        return Row(ref, issue, cls, why)


# ---- report ------------------------------------------------------------------

def build_report(
    repo: Path,
    fetch: bool = True,
    issue_state_fn: Optional[IssueStateFn] = None,
    open_pr_heads_fn: Optional[OpenPrHeadsFn] = None,
    closing_prs_fn: Optional[ClosingPrsFn] = None,
    own_prs_fn: Optional[OwnPrsFn] = None,
) -> Report:
    """Real git calls + classification. Never raises."""
    try:
        porcelain = git_run.run_git_or_raise(repo, "worktree", "list", "--porcelain")
        local = list_local(repo)
    except git_run.Unreadable as exc:
        return Report([], [], VERDICT_UNKNOWN, str(exc), [])

    notes: List[str] = []
    verdict = VERDICT_COMPLETE
    if fetch:
        r = git_run.run_git(["-C", str(repo), "fetch", "origin"])
        if r.returncode != 0:
            notes.append(f"git fetch origin failed (exit {r.returncode}) -- merge proof read against the last-fetched ref")
    try:
        remote = list_remote(repo)
    except git_run.Unreadable as exc:
        remote = []
        verdict = VERDICT_PARTIAL
        notes.append(f"remote branches not listed: {exc}")

    if issue_state_fn is None or open_pr_heads_fn is None or closing_prs_fn is None or own_prs_fn is None:
        real_issue, real_prs, real_closing, real_own = make_gh_lookups(repo_slug(repo))
        issue_state_fn = issue_state_fn or real_issue
        open_pr_heads_fn = open_pr_heads_fn or real_prs
        closing_prs_fn = closing_prs_fn or real_closing
        own_prs_fn = own_prs_fn or real_own

    worktrees = wr.parse_all_worktrees(porcelain)
    checked_out = {w.branch for w in worktrees if w.branch}
    judge = _Judge(repo, git_run.resolve_default_branch_ref(repo), issue_state_fn, open_pr_heads_fn, closing_prs_fn,
                   own_prs_fn)
    refs = local + remote
    judge.prefetch(refs, checked_out)
    rows = [judge.judge(ref, checked_out) for ref in refs]

    orphans = find_orphans(worktrees[0].path, [w.path for w in worktrees]) if worktrees else []
    return Report(rows, orphans, verdict, "", notes)


def render(report: Report, include_branches: bool = True) -> List[str]:
    lines: List[str] = []
    if include_branches:
        for row in report.rows:
            r = row.ref
            lines.append(
                f"BRANCH={r.name} WHERE={r.where} SHA={r.sha[:7]} ISSUE={row.issue if row.issue is not None else '-'}"
                f" CLASS={row.cls} REASON={row.reason}"
            )
    for o in report.orphans:
        lines.append(f"ORPHAN={o.path} CLASS={o.cls} REASON={o.reason}")
    lines.append(f"ORPHANS={len(report.orphans)}")
    if include_branches:
        c = summarize(report.rows)
        lines.append(f"SUMMARY landed={c['landed']} superseded={c['superseded']} unlanded={c['unlanded']}"
                     f" keep={c['keep']} unknown={c['unknown']}")
        lines.append(f"VERDICT={report.verdict}")
    if report.reason:
        lines.append(f"REASON={report.reason}")
    for note in report.notes:
        lines.append(f"NOTE={note}")
    return lines


def orphans_report(repo: Path) -> Report:
    """Only the orphan section -- no branch listing, no fetch, no `gh`."""
    try:
        porcelain = git_run.run_git_or_raise(repo, "worktree", "list", "--porcelain")
    except git_run.Unreadable as exc:
        return Report([], [], VERDICT_UNKNOWN, str(exc), [])
    worktrees = wr.parse_all_worktrees(porcelain)
    orphans = find_orphans(worktrees[0].path, [w.path for w in worktrees]) if worktrees else []
    return Report([], orphans, VERDICT_COMPLETE, "", [])


# ---- delete --------------------------------------------------------------------

LookupsFactory = Callable[[], Lookups]


def _fresh_sha(repo: Path, name: str, where: str) -> Optional[str]:
    """The ref's tip right now, or None when it is gone or could not be read."""
    if where == "local":
        r = git_run.run_git(["-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
    else:
        r = git_run.run_git(["-C", str(repo), "ls-remote", "--heads", "origin", f"refs/heads/{name}"])
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out.split()[0] if out else None


def _restore_cmd(where: str, name: str, sha: str) -> str:
    return f"git branch {name} {sha}" if where == "local" else f"git push origin {sha}:refs/heads/{name}"


def _first_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # git's verdict line (`error:`, `fatal:`, `! [rejected]`) beats the leading `To <url>` banner.
    for ln in lines:
        if ln.startswith(("error:", "fatal:", "!")):
            return ln
    return lines[0] if lines else "no output"


def _delete_one(repo: Path, row: Row, fresh: Row) -> Outcome:
    name, where, sha = row.ref.name, row.ref.where, fresh.ref.sha
    try:
        if where == "local":
            r = git_run.run_git(["-C", str(repo), "branch", "-D", name], timeout=PUSH_TIMEOUT_S)
        else:
            # The lease pins the delete to the tip just proven: a push after the proof is rejected, not deleted.
            r = git_run.run_git(
                ["-C", str(repo), "push", "origin", f"--force-with-lease=refs/heads/{name}:{sha}", "--delete", name],
                timeout=PUSH_TIMEOUT_S,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome("failed", where, name, sha, f"git could not run: {exc}")
    if r.returncode == 0:
        return Outcome("deleted", where, name, sha, fresh.reason, _restore_cmd(where, name, sha))
    msg = _first_line(r.stderr or r.stdout)
    if where == "remote" and "stale info" in (r.stderr or ""):
        return Outcome("stays", where, name, sha, f"tip moved after the proof ({msg})")
    return Outcome("failed", where, name, sha, msg)


def delete_landed(
    repo: Path,
    report: Report,
    lookups_factory: LookupsFactory,
    names: Sequence[str] = (),
    remote: bool = False,
) -> List[Outcome]:
    """Delete the report's `landed`/`superseded` refs that still prove so. Never raises.

    `names` empty -> every deletable row; otherwise only those names, and a
    named ref of any other class is refused with its class. Each target is re-judged
    from scratch (fresh worktree list, tip sha, proof, PR lookup) right before
    it is touched; one that moved or changed class is skipped.
    """
    named = set(names)
    outcomes: List[Outcome] = []
    for name in names:
        if not any(r.ref.name == name for r in report.rows):
            outcomes.append(Outcome("refused", "-", name, "", "no such branch in the report"))

    targets: List[Row] = []
    for row in report.rows:
        if named and row.ref.name not in named:
            continue
        if row.ref.where == "remote" and not remote:
            if named:
                outcomes.append(Outcome("skipped", "remote", row.ref.name, row.ref.sha, "a remote delete needs --remote"))
            continue
        if row.cls not in DELETABLE:
            if named:
                outcomes.append(Outcome("refused", row.ref.where, row.ref.name, row.ref.sha, f"CLASS={row.cls}: {row.reason}"))
            continue
        targets.append(row)

    for row in sorted(targets, key=lambda r: r.ref.where != "local"):  # local first
        ref = row.ref
        try:
            outcomes.append(_attempt(repo, row, lookups_factory))
        except Exception as exc:  # one target's failure must not stop the rest
            outcomes.append(Outcome("failed", ref.where, ref.name, ref.sha, f"unexpected error: {exc}"))
    return outcomes


def _attempt(repo: Path, row: Row, lookups_factory: LookupsFactory) -> Outcome:
    ref = row.ref
    sha_now = _fresh_sha(repo, ref.name, ref.where)
    if sha_now is None:
        return Outcome("skipped", ref.where, ref.name, ref.sha, "gone, or its tip could not be read")
    if sha_now != ref.sha:
        return Outcome("skipped", ref.where, ref.name, sha_now, f"tip moved since the report ({ref.sha[:7]} -> {sha_now[:7]})")
    porcelain = git_run.run_git_or_raise(repo, "worktree", "list", "--porcelain")
    checked_out = {w.branch for w in wr.parse_all_worktrees(porcelain) if w.branch}
    judge = _Judge(repo, git_run.resolve_default_branch_ref(repo), *lookups_factory())
    fresh = judge.judge(Ref(ref.name, ref.where, sha_now), checked_out)
    if fresh.cls not in DELETABLE:
        return Outcome("skipped", ref.where, ref.name, sha_now, f"no longer deletable, CLASS={fresh.cls}: {fresh.reason}")
    outcome = _delete_one(repo, row, fresh)
    if outcome.action == "deleted":
        log.info("deleted %s:%s sha=%s proof=%s restore=%s", ref.where, ref.name, sha_now, fresh.reason, outcome.restore)
    else:
        log.warning("%s %s:%s sha=%s: %s", outcome.action, ref.where, ref.name, sha_now, outcome.detail)
    return outcome


def render_outcomes(outcomes: Sequence[Outcome]) -> List[str]:
    lines: List[str] = []
    for o in outcomes:
        if o.action == "deleted":
            lines.append(f"DELETED={o.where}:{o.name} SHA={o.sha} PROOF={o.detail}")
            lines.append(f"RESTORE={o.restore}")
        elif o.action == "stays":
            lines.append(f"STAYS={o.where}:{o.name} (tip moved) SHA={o.sha} REASON={o.detail}")
        else:
            lines.append(f"{o.action.upper()}={o.where}:{o.name} REASON={o.detail}")
    count = lambda a: sum(1 for o in outcomes if o.action == a)  # noqa: E731
    lines.append(
        f"DELETE_SUMMARY deleted={count('deleted')} skipped={count('skipped') + count('stays') + count('refused')}"
        f" failed={count('failed')}"
    )
    return lines


def cmd_report(repo: Path, branches: bool, fetch: bool = True) -> None:
    if not repo.exists():
        print("VERDICT=unknown")
        print(f"REASON=no such path: {repo}")
        return
    report = build_report(repo, fetch) if branches else orphans_report(repo)
    for line in render(report, include_branches=branches):
        print(line)


def cmd_delete(repo: Path, names: Sequence[str], remote: bool, fetch: bool) -> int:
    """Report, then delete; the exit code says whether the request was fully met."""
    if not repo.exists():
        print("VERDICT=unknown")
        print(f"REASON=no such path: {repo}")
        return 1
    report = build_report(repo, fetch)
    for line in render(report):
        print(line)
    if report.verdict == VERDICT_UNKNOWN:
        return 1
    outcomes = delete_landed(repo, report, lambda: make_gh_lookups(repo_slug(repo)), names, remote)
    for line in render_outcomes(outcomes):
        print(line)
    if any(o.action == "failed" for o in outcomes):
        return 1
    # An explicit request that was not carried out is not a success, even when it was refused safely.
    return 1 if names and any(o.action != "deleted" for o in outcomes) else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report a repo's leftover branches and orphan worktree folders; --delete removes landed and superseded ones."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("branches")
    b.add_argument("repo_path", type=Path)
    b.add_argument("--no-fetch", action="store_true")
    b.add_argument("--delete", action="store_true",
                   help="delete the refs that are landed or superseded right now")
    b.add_argument("--branch", action="append", default=[], metavar="NAME",
                   help="with --delete: act only on this branch (repeatable); refused unless landed or superseded")
    b.add_argument("--remote", action="store_true", help="with --delete: also delete those refs on origin")
    o = sub.add_parser("orphans")
    o.add_argument("repo_path", type=Path)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    if args.cmd == "branches":
        if (args.branch or args.remote) and not args.delete:
            ap.error("--branch and --remote only apply with --delete")
        if args.delete:
            return cmd_delete(args.repo_path, args.branch, args.remote, not args.no_fetch)
        cmd_report(args.repo_path, True, not args.no_fetch)
    else:
        cmd_report(args.repo_path, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
