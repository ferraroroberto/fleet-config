"""Re-runnable per-repo availability gate for `/cleanup-fleet-all` (fleet-config#642).

Why this exists
----------------
`/cleanup-fleet-all` skips a repo that is dirty, off its default branch, or
already holds a worktree at pre-flight. That skip is correct and stays --
stashing or force-switching someone else's tree is the destructive move the
fleet forbids. The gap was what happened *after* the skip: the repo was
reported in one footnote line of one run's stdout and then forgotten. Its
issues were not deferred, not retried at the end of the run, and not carried
into the next one, while the run still reported itself complete.

That is the same false-completeness shape the fleet keeps hitting (#560,
#607, #612, #623): a process that could not finish something, whose report
does not distinguish that from success. The contrast inside this very skill
is instructive -- `issue_state_gate.py` produces three explicit counts
precisely so a shrunken working set cannot read as "nothing to do", while the
repo-skip path had no equivalent accounting. And a busy repo is not randomly
distributed: the repos most likely to be dirty mid-run are the ones being
actively developed, which are the ones whose cleanup backlog grows fastest,
so a weekly run could skip the same repo indefinitely.

This module is that accounting. It is deliberately shaped like
`issue_state_gate.py` -- a `partition` subcommand over a JSON working set,
with a pure decision core unit-tested independently of the `git` plumbing --
because the reason that helper works is that its counts are computed by
something re-runnable.

**Statelessness is the point, not an implementation detail.** There is no
cache to consult and no verdict to persist, so the end-of-run retry is
literally this same call over the deferred subset. The issue's constraint --
"the retry must re-run the *full* pre-flight, not a cached verdict from hours
earlier, because the tree may have changed in either direction" -- is
therefore structural rather than a line of prose an orchestrator has to
remember to obey.

A check that cannot establish a repo's state reports `unknown` (its own
state, never folded into either a pass or a confirmed skip), per global
CLAUDE.md. `unknown` does not dispatch -- an unreadable repo is not a repo
proven safe to work in -- but it is counted and reported apart from a
confirmed dirty tree.

Subcommands
-----------
  check <repo-path> [--default-branch NAME]
        Prints `STATE=<state>` and, when not `available`, `REASON=<why>`.
        Always exits 0 -- this helper reports, it never blocks.

  partition [--fleet-root DIR]
        Reads a JSON array from stdin, each element at minimum
        `{"repo": ..., "number": ...}` (any other keys -- `bucket`, `title`,
        `body` -- pass through unchanged). Resolves each distinct repo
        exactly once, however many issues it carries, and prints a JSON
        object to stdout:
          {"dispatch": [...], "skipped": [...]}
        Every element carries its original keys plus `repo_state` and, when
        skipped, `skip_reason`. Also prints a one-line summary to stderr:
          DISPATCH=<n> SKIPPED_REPOS=<n> SKIPPED_ISSUES=<n> UNKNOWN_REPOS=<n>

        An element may carry an explicit `path` to override the
        `<fleet-root>/<repo>` convention.

`git fetch origin` is run once per resolved repo as a side effect (the
orchestrator's step 5 requires it, and a later lane needs fresh remote
state), but a failed fetch never changes the verdict: what makes a repo
unsafe to work in is a dirty tree, a wrong branch, or someone else's
worktree, not an unreachable network. A fetch failure is recorded in the
item's `note` so it is visible without being conflated with a skip.

The decision logic (`classify_repo`, `partition_working_set`) is pure and
unit-tested (`tests/test_repo_preflight.py`) independent of the `git`
plumbing around it. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402

DEFAULT_FLEET_ROOT = Path("E:/automation")

# The one state that dispatches. Everything else defers.
AVAILABLE = "available"
# Confirmed-unavailable states, each naming what was actually observed.
MISSING = "missing"
DIRTY = "dirty"
OFF_BRANCH = "off-branch"
WORKTREE = "worktree"
# The state for "could not establish", never folded into either of the above.
UNKNOWN = "unknown"


class RepoFacts(NamedTuple):
    """Everything `classify_repo` is allowed to decide from.

    Gathered by `gather`, or handed in directly by the unit tests -- the
    split exists so the ordering and wording of the verdict can be pinned
    without a real repo on disk.
    """

    exists: bool
    current_branch: str
    default_branch: str
    porcelain_empty: bool
    extra_worktrees: Tuple[str, ...]


class Unreadable(Exception):
    """The repo's facts could not be established -- so there is no verdict.

    Raised rather than letting a failed `git` return an empty string that
    then reads as a fact: empty porcelain reads as *clean* and an empty
    branch name is unequal to `main`, so a repo that was never inspected
    would otherwise be dispatched as available or deferred as off-branch,
    both of them inventions (the shape `dirty_tree_check.py` documents at
    fleet-config#570).
    """


def classify_repo(facts: RepoFacts) -> Tuple[str, str]:
    """Pure verdict: no git calls, just the facts already gathered.

    Checks run in a fixed order so the reported reason is deterministic when
    a repo fails more than one of them -- a repo that is both dirty and off
    its default branch always reports `dirty`. The order follows step 5's
    own sequence (exists, tree, branch, worktrees); nothing keys on which
    one is reported, since every non-`available` state defers identically.
    """
    if not facts.exists:
        return MISSING, "no such path"
    if not facts.porcelain_empty:
        return DIRTY, "working tree not clean -- in-progress work, never stashed"
    if facts.current_branch != facts.default_branch:
        return (
            OFF_BRANCH,
            f"on {facts.current_branch}, not the default branch {facts.default_branch}",
        )
    if facts.extra_worktrees:
        return (
            WORKTREE,
            "pre-existing worktree(s) from an earlier run or a live session: "
            + ", ".join(facts.extra_worktrees),
        )
    return AVAILABLE, ""


def parse_worktree_list(porcelain: str) -> Tuple[str, ...]:
    """Every worktree past the primary, from `git worktree list --porcelain`.

    The first `worktree ` line is the primary checkout; anything after it is
    residue from an earlier run or a live human session. Returns paths in the
    order git reported them.
    """
    paths = [
        line[len("worktree ") :].strip()
        for line in porcelain.splitlines()
        if line.startswith("worktree ")
    ]
    return tuple(paths[1:])


def _run_git(repo_path: Path, *args: str) -> str:
    """Stripped stdout, or `Unreadable` -- never an empty string standing in
    for a fact. Mirrors `dirty_tree_check._run_git`; kept local rather than
    shared because the two helpers report through different vocabularies and
    a shared raiser would have to satisfy both."""
    r = git_run.run_git(["-C", str(repo_path), *args])
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        raise Unreadable(
            f"git {' '.join(args)} failed (exit {r.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return r.stdout.strip()


def detect_default_branch(repo_path: Path) -> str:
    """Bare branch name for the repo's default branch.

    `resolve_default_branch_ref` probes `origin/HEAD` first and falls back
    through the usual candidates, so `life-os`'s `master` is detected rather
    than assumed away.
    """
    ref = git_run.resolve_default_branch_ref(repo_path, final_fallback="main")
    return ref[len("origin/") :] if ref.startswith("origin/") else ref


def fetch(repo_path: Path) -> Optional[str]:
    """`git fetch origin`, once per repo. Returns None on success, else the
    reason -- never raises and never affects the verdict (see module docstring)."""
    r = git_run.run_git(["-C", str(repo_path), "fetch", "origin"])
    if r.returncode == 0:
        return None
    detail = (r.stderr or "").strip().splitlines()
    return f"git fetch origin failed (exit {r.returncode})" + (f": {detail[0]}" if detail else "")


def gather(repo_path: Path, default_branch: Optional[str] = None) -> RepoFacts:
    """Collect the live git facts `classify_repo` needs.

    Raises `Unreadable` when any of them cannot be read -- unlike
    `dirty_tree_check.gather` there is no softer fact here that may
    legitimately be absent, so a partial read is always `unknown`.
    """
    resolved_default = default_branch or detect_default_branch(repo_path)
    current_branch = _run_git(repo_path, "branch", "--show-current")
    porcelain = _run_git(repo_path, "status", "--porcelain")
    worktrees = _run_git(repo_path, "worktree", "list", "--porcelain")
    return RepoFacts(
        exists=True,
        current_branch=current_branch,
        default_branch=resolved_default,
        porcelain_empty=porcelain == "",
        extra_worktrees=parse_worktree_list(worktrees),
    )


def check(repo_path: Path, default_branch: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Real git calls + classification. Never raises -- a failed invocation
    itself becomes an `unknown` verdict with the reason attached.

    Returns (state, reason, fetch_note).
    """
    if not repo_path.exists():
        return MISSING, f"no such path: {repo_path}", None
    try:
        facts = gather(repo_path, default_branch)
    except Unreadable as exc:
        return UNKNOWN, str(exc), None
    # Only after the repo is known readable -- fetching a path that isn't a
    # repo would just produce a second, less informative failure.
    note = fetch(repo_path)
    state, reason = classify_repo(facts)
    return state, reason, note


def partition_working_set(
    issues: List[Dict[str, Any]],
    repo_lookup: Callable[[str], Tuple[str, str, Optional[str]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Pure partition over an already-resolved verdict per repo.

    `repo_lookup` is called **at most once per distinct repo**, however many
    issues that repo carries across however many buckets -- one repo cannot
    come back available for its `slop` issue and dirty for its `bug` issue in
    the same pass, and step 5's own rule is that a dirty repo drops every one
    of its selected issues across all buckets.

    Every issue lands in exactly one of the two lists. There is no third path
    by which an item reaches a dispatcher, so proving an item is absent from
    `dispatch` is sufficient to prove no lane runs for it.
    """
    verdicts: Dict[str, Tuple[str, str, Optional[str]]] = {}
    dispatch: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for item in issues:
        repo = item["repo"]
        if repo not in verdicts:
            verdicts[repo] = repo_lookup(repo)
        state, reason, note = verdicts[repo]
        annotated = {**item, "repo_state": state}
        if note:
            annotated["note"] = note
        if state == AVAILABLE:
            dispatch.append(annotated)
        else:
            skipped.append({**annotated, "skip_reason": reason})
    return {"dispatch": dispatch, "skipped": skipped}


def summarize(result: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """The counts the final report's headline is required to carry.

    `skipped_issues` is the number the report exists to surface -- how much
    live work went unprocessed -- and is deliberately not the same number as
    `skipped_repos`, which is what a reader would otherwise assume the single
    footnote line meant.
    """
    skipped_repos = {i["repo"] for i in result["skipped"]}
    unknown_repos = {i["repo"] for i in result["skipped"] if i["repo_state"] == UNKNOWN}
    return {
        "dispatch": len(result["dispatch"]),
        "skipped_repos": len(skipped_repos),
        "skipped_issues": len(result["skipped"]),
        "unknown_repos": len(unknown_repos),
    }


def cmd_check(repo_path: Path, default_branch: Optional[str]) -> None:
    state, reason, note = check(repo_path, default_branch)
    print(f"STATE={state}")
    if reason:
        print(f"REASON={reason}")
    if note:
        print(f"NOTE={note}")


def cmd_partition(fleet_root: Path) -> None:
    issues = json.loads(sys.stdin.read() or "[]")
    paths = {
        item["repo"]: Path(item["path"]) if item.get("path") else fleet_root / item["repo"]
        for item in issues
    }
    result = partition_working_set(issues, lambda repo: check(paths[repo]))
    print(json.dumps(result))
    counts = summarize(result)
    print(
        f"DISPATCH={counts['dispatch']} "
        f"SKIPPED_REPOS={counts['skipped_repos']} "
        f"SKIPPED_ISSUES={counts['skipped_issues']} "
        f"UNKNOWN_REPOS={counts['unknown_repos']}",
        file=sys.stderr,
    )


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-runnable per-repo availability gate for /cleanup-fleet-all."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("repo_path", type=Path)
    c.add_argument("--default-branch", default=None)

    p = sub.add_parser("partition")
    p.add_argument("--fleet-root", type=Path, default=DEFAULT_FLEET_ROOT)

    args = ap.parse_args(argv)
    if args.cmd == "check":
        cmd_check(args.repo_path, args.default_branch)
    elif args.cmd == "partition":
        cmd_partition(args.fleet_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
