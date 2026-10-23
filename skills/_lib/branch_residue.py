"""Report a repo's leftover branches and orphan worktree folders at teardown (fleet-config#1516).

Why this exists
----------------
Teardown deletes only the lane's own branch and, by design, reports nothing
about anyone else's (fleet-config#572), so branches from closed issues pile up:
23 local and 16 remote refs in this repo at filing time. Judging whether one is
safe to drop used to be hand-run `gh pr list` / `git diff` lookups in a prompt.
This module is that judgement as a deterministic, read-only report.

`branches` only reports. The one mutating path is the explicit `branches --delete`
(fleet-config#1528, below): no teardown or scheduled skill passes it, and no folder
is ever removed here.

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
                  `--delete` will act on.

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

  branches <repo-path> --delete [--branch NAME]... [--remote] [--no-fetch]
        After the report, delete refs that are `landed` NOW. Only the owner, or
        the chief after a Board yes, runs this; no teardown or scheduled skill
        passes it. Without `--branch` it acts on every landed row, with it only
        on the named ones; naming a ref that is not `landed` (the default, a
        checked-out branch, `no-issue`, `unknown`, ...) is refused with its
        class. Each target is re-judged immediately before acting from a fresh
        tip sha, fresh proof and a fresh PR lookup, so a branch that moved or
        gained a PR since the report is skipped.
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
`delete_landed` take the issue and PR lookups as injected callables so tests
never touch `gh` or the network. The tip proof is cached per sha and the `gh`
and merge-tree lookups run on a small pool: serial, a 62-ref repo took 72 s
(fleet-config#1528). stdlib only.
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
PUSH_TIMEOUT_S = 120
# Few enough not to load the box (merge-tree is CPU-bound), enough to hide `gh` latency.
LOOKUP_WORKERS = 4

log = logging.getLogger("branch_residue")

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
    """Sibling `<primary-name>-wt-*` folders that git does not register, as `worktree_residue` scans and judges them."""
    found: List[Orphan] = []
    for sib in wr.unregistered_siblings(Path(primary), registered):
        facts = wr.dir_facts(sib)
        cls, why = wr.classify_orphan(facts.has_git, None if facts.empty is None else not facts.empty)
        found.append(Orphan(str(sib), cls, why))
    return found


# ---- judgement ---------------------------------------------------------------

class _Judge:
    """Classifies refs, caching the three expensive facts behind a class.

    Issue state per number, tip proof per sha (a local and a remote ref at the
    same tip share one `merge-tree`), open-PR heads once.
    """

    def __init__(self, repo: Path, default_ref: str, issue_state_fn: IssueStateFn,
                 open_pr_heads_fn: OpenPrHeadsFn) -> None:
        self.repo = repo
        self.default_ref = default_ref
        self.default_name = default_ref[len("origin/"):] if default_ref.startswith("origin/") else default_ref
        self._issue_state_fn = issue_state_fn
        self._open_pr_heads_fn = open_pr_heads_fn
        self._states: Dict[int, Optional[str]] = {}
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
            shas = sorted({r.sha for r in live if self._states.get(branch_issue(r.name)) == "closed"})
            list(pool.map(self.proof, shas))

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
        if not gated and state == "closed":
            readable, merged, how = self.proof(ref.sha)
            if readable and merged:
                open_pr = self.has_open_pr(ref.name)
        cls, why = classify_ref(
            is_default=is_default, checked_out=is_checked_out, issue=issue, issue_state=state,
            tip_readable=readable, merged=merged, merged_how=how, open_pr=open_pr,
        )
        return Row(ref, issue, cls, why)


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
    judge = _Judge(repo, git_run.resolve_default_branch_ref(repo), issue_state_fn, open_pr_heads_fn)
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


# ---- delete (explicit act) -----------------------------------------------------

LookupsFactory = Callable[[], Tuple[IssueStateFn, OpenPrHeadsFn]]


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
    """Delete the report's `landed` refs that are still provably landed. Never raises.

    `names` empty -> every landed row; otherwise only those names, and a named
    ref that is not landed is refused with its class. Each target is re-judged
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
        if row.cls != LANDED:
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
    issue_state_fn, open_pr_heads_fn = lookups_factory()
    judge = _Judge(repo, git_run.resolve_default_branch_ref(repo), issue_state_fn, open_pr_heads_fn)
    fresh = judge.judge(Ref(ref.name, ref.where, sha_now), checked_out)
    if fresh.cls != LANDED:
        return Outcome("skipped", ref.where, ref.name, sha_now, f"no longer landed, CLASS={fresh.cls}: {fresh.reason}")
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
        description="Report a repo's leftover branches and orphan worktree folders; --delete removes proven-landed branches."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("branches")
    b.add_argument("repo_path", type=Path)
    b.add_argument("--no-fetch", action="store_true")
    b.add_argument("--delete", action="store_true",
                   help="delete the refs that are landed right now (owner, or chief after a Board yes; never teardown)")
    b.add_argument("--branch", action="append", default=[], metavar="NAME",
                   help="with --delete: act only on this branch (repeatable); refused unless it is landed")
    b.add_argument("--remote", action="store_true", help="with --delete: also delete landed refs on origin")
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
