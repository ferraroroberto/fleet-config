"""Report a repo's leftover branches and orphan worktree folders at teardown (fleet-config#1516).

Why this exists
----------------
Teardown deletes only the lane's own branch and, by design, reports nothing
about anyone else's (fleet-config#572), so branches from closed issues pile up:
23 local and 16 remote refs in this repo at filing time. Judging whether one is
safe to drop used to be hand-run `gh pr list` / `git diff` lookups in a prompt.
This module is that judgement as a deterministic, read-only report.

It reports. It has no delete path: nothing here runs `git branch -D`, `git push
--delete`, or removes a folder.

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
                  branch (or conflicts with it): unmerged work.
  open-pr      -- an open PR has this head.
  landed       -- issue closed AND the tip is proven landed by
                  `worktree_residue.head_merged` AND no open PR. The only class
                  that could ever be deleted, and only by a later explicit act.

`landed` needs both facts. The closed issue alone says nothing about the
content; the merge proof alone would call a fresh empty branch landed. Neither
is inferred from absence. `git branch --merged` is never used: the fleet
squash-merges, so a landed tip is never an ancestor (fleet-config#567).
Local and remote tips are judged independently -- one can be ahead of the other.

Orphan folders
---------------
A sibling `<repo>-wt-*` directory (any suffix, not only digits) that
`git worktree list` does not register. Reported as
`orphan-with-git-file | orphan-has-content | orphan-empty | unknown`; never
removed here (`chief_ops.py sweep --apply` owns empty-shell removal).

Subcommands
-----------
  branches <repo-path> [--no-fetch]
        One line per ref:
          BRANCH=<name> WHERE=local|remote SHA=<sha7> ISSUE=<N|-> CLASS=<class> REASON=<why>
        then the orphan section (`ORPHAN=<path> CLASS=<class> REASON=<why>`,
        `ORPHANS=<n>`), `SUMMARY landed=<n> unlanded=<n> keep=<n> unknown=<n>`,
        `VERDICT=complete|partial|unknown`, plus `REASON=`/`NOTE=` when a
        listing or the fetch failed. `partial` means a half could not be read
        (e.g. `ls-remote` failed), so the counts are not the whole picture.
  orphans <repo-path>
        Only the orphan section.

Always exits 0 -- it reports, the caller decides. The decision core
(`classify_ref`, `branch_issue`, `summarize`) is pure; `build_report` takes the
issue and PR lookups as injected callables so tests never touch `gh` or the
network. stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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

# Classes the summary counts as "left alone on purpose".
_KEEP = (DEFAULT, CHECKED_OUT, NO_ISSUE, OPEN_ISSUE, OPEN_PR)

VERDICT_COMPLETE = "complete"
VERDICT_PARTIAL = "partial"
VERDICT_UNKNOWN = "unknown"

# `<type>/<N>-slug`, the shape /issue-start cuts. `[A-Za-z][\w-]*` keeps
# `fix/`, `feat/`, `docs/`, `ci/` and anything similar; a bare number segment
# (`fix/12`) counts too.
_BRANCH_ISSUE_RE = re.compile(r"^[A-Za-z][\w-]*/(\d+)(?:-|$)")
_SLUG_RE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

GH_TIMEOUT_S = 60

IssueStateFn = Callable[[int], Optional[str]]
OpenPrHeadsFn = Callable[[], Optional[Set[str]]]


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
) -> Tuple[str, str]:
    """Pure (class, reason) for one ref. `None` means "could not establish".

    `merged` / `open_pr` are only consulted past the gates that make them
    matter, so a caller may leave them `None` for a ref decided earlier.
    """
    if is_default:
        return DEFAULT, "the default branch"
    if checked_out:
        return CHECKED_OUT, "checked out in a worktree"
    if issue is None:
        return NO_ISSUE, "name carries no issue number, so there is no closed issue to prove"
    if issue_state == "open":
        return OPEN_ISSUE, f"issue #{issue} is still open"
    if issue_state != "closed":
        return UNKNOWN, f"state of issue #{issue} not established (not an issue of this repo, or the lookup failed)"
    if not tip_readable:
        return UNKNOWN, "tip commit is not present locally, so it cannot be proven"
    if merged is None:
        return UNKNOWN, merged_how or "could not establish whether the tip is merged"
    if not merged:
        return UNLANDED, merged_how or "tip carries changes not in the default branch"
    if open_pr is None:
        return UNKNOWN, "could not establish whether an open PR has this head"
    if open_pr:
        return OPEN_PR, "an open PR has this head"
    return LANDED, f"issue #{issue} closed; {merged_how}"


def summarize(rows: Sequence[Row]) -> Dict[str, int]:
    counts = {"landed": 0, "unlanded": 0, "keep": 0, "unknown": 0}
    for row in rows:
        if row.cls == LANDED:
            counts["landed"] += 1
        elif row.cls == UNLANDED:
            counts["unlanded"] += 1
        elif row.cls == UNKNOWN:
            counts["unknown"] += 1
        elif row.cls in _KEEP:
            counts["keep"] += 1
    return counts


def classify_orphan(has_git: bool, has_entries: Optional[bool]) -> Tuple[str, str]:
    if has_git:
        return "orphan-with-git-file", "has a .git entry but git does not register it as a worktree"
    if has_entries is None:
        return UNKNOWN, "could not read the folder"
    if has_entries:
        return "orphan-has-content", "not a registered worktree, and not empty"
    return "orphan-empty", "not a registered worktree, and empty"


# ---- gh lookups (injectable) ------------------------------------------------

def repo_slug(repo: Path) -> Optional[str]:
    """`owner/name` from origin's URL, or None when it is not a GitHub remote."""
    r = git_run.run_git(["-C", str(repo), "remote", "get-url", "origin"])
    if r.returncode != 0:
        return None
    m = _SLUG_RE.search((r.stdout or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def make_gh_lookups(slug: Optional[str]) -> Tuple[IssueStateFn, OpenPrHeadsFn]:
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

    return issue_state, open_pr_heads


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
    """Sibling `<primary-name>-wt-*` folders that git does not register."""
    primary_path = Path(primary)
    prefix = primary_path.name.lower() + "-wt-"
    known = {wr.normalize_path(p) for p in registered}
    found: List[Orphan] = []
    try:
        siblings = sorted(primary_path.parent.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found
    for sib in siblings:
        if not sib.name.lower().startswith(prefix) or not sib.is_dir():
            continue
        if wr.normalize_path(str(sib)) in known:
            continue
        has_git = (sib / ".git").exists()
        try:
            # Top level only -- never descend: a `.venv` junction inside must not be walked.
            with os.scandir(sib) as it:
                has_entries: Optional[bool] = any(True for _ in it)
        except OSError:
            has_entries = None
        cls, why = classify_orphan(has_git, has_entries)
        found.append(Orphan(str(sib), cls, why))
    return found


# ---- report ------------------------------------------------------------------

def build_report(
    repo: Path,
    fetch: bool = True,
    issue_state_fn: Optional[IssueStateFn] = None,
    open_pr_heads_fn: Optional[OpenPrHeadsFn] = None,
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

    if issue_state_fn is None or open_pr_heads_fn is None:
        real_issue, real_prs = make_gh_lookups(repo_slug(repo))
        issue_state_fn = issue_state_fn or real_issue
        open_pr_heads_fn = open_pr_heads_fn or real_prs

    worktrees = wr.parse_all_worktrees(porcelain)
    checked_out = {w.branch for w in worktrees if w.branch}
    default_ref = git_run.resolve_default_branch_ref(repo)
    default_name = default_ref[len("origin/"):] if default_ref.startswith("origin/") else default_ref

    pr_heads_box: List[Optional[Set[str]]] = []

    def has_open_pr(name: str) -> Optional[bool]:
        if not pr_heads_box:
            pr_heads_box.append(open_pr_heads_fn())
        return None if pr_heads_box[0] is None else name in pr_heads_box[0]

    rows: List[Row] = []
    for ref in local + remote:
        issue = branch_issue(ref.name)
        is_default = ref.name == default_name
        is_checked_out = ref.name in checked_out
        gated = is_default or is_checked_out or issue is None
        state = None if gated else issue_state_fn(issue)  # type: ignore[arg-type]
        readable = merged = open_pr = None
        how = ""
        if not gated and state == "closed":
            readable = tip_readable(repo, ref.sha)
            if readable:
                merged, how = wr.head_merged(repo, ref.sha, default_ref)
                if merged:
                    open_pr = has_open_pr(ref.name)
        cls, why = classify_ref(
            is_default=is_default, checked_out=is_checked_out, issue=issue, issue_state=state,
            tip_readable=bool(readable), merged=merged, merged_how=how, open_pr=open_pr,
        )
        rows.append(Row(ref, issue, cls, why))

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
        lines.append(f"SUMMARY landed={c['landed']} unlanded={c['unlanded']} keep={c['keep']} unknown={c['unknown']}")
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


def cmd_report(repo: Path, branches: bool, fetch: bool = True) -> None:
    if not repo.exists():
        print("VERDICT=unknown")
        print(f"REASON=no such path: {repo}")
        return
    report = build_report(repo, fetch) if branches else orphans_report(repo)
    for line in render(report, include_branches=branches):
        print(line)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report a repo's leftover branches and orphan worktree folders (read-only)."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("branches")
    b.add_argument("repo_path", type=Path)
    b.add_argument("--no-fetch", action="store_true")
    o = sub.add_parser("orphans")
    o.add_argument("repo_path", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "branches":
        cmd_report(args.repo_path, True, not args.no_fetch)
    else:
        cmd_report(args.repo_path, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
