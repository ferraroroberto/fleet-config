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

**Leftover `<repo>-wt-*` directories git no longer knows about**
(fleet-config#1082). `git worktree list` cannot see an unregistered sibling
directory, but teardown check 2 globs `<repo>-wt-*` and halts the whole run on
any hit it cannot prove inert. So a live-held leftover that already existed
before the run was invisible here and halted the run hours later, after the
first lane in that repo. Pre-flight now runs the same per-repo glob and
judges each unregistered hit read-only by the zombie-shell conditions it can
prove without touching anything: a real directory (not a reparse point),
empty, and `dir_holders.py` `STATUS=CLEAR`. Condition 3 (unregistered) is how
the hit was selected. Condition 5 (`remove-worktree` ran and refused) is
deliberately not attempted, because pre-flight never removes what the run did
not create. A directory that meets the other four cannot halt teardown anyway:
either removal succeeds or the directory is exactly the zombie shell the
exception covers. Any other hit, including `LIVE` and `UNKNOWN`, makes the
repo `leftover-dir`, and the reason names each path and its holder pids.

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
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dir_holders  # noqa: E402
import git_run  # noqa: E402
import worktree_residue  # noqa: E402

DEFAULT_FLEET_ROOT = Path("E:/automation")

# The one state that dispatches. Everything else defers.
AVAILABLE = "available"
# Confirmed-unavailable states, each naming what was actually observed.
MISSING = "missing"
DIRTY = "dirty"
OFF_BRANCH = "off-branch"
WORKTREE = "worktree"
LEFTOVER_DIR = "leftover-dir"
# The state for "could not establish", never folded into either of the above.
UNKNOWN = "unknown"


class LeftoverDir(NamedTuple):
    """One unregistered `<repo>-wt-*` sibling, as gathered by `scan_leftovers`.

    `None` in `reparse`/`empty` means the fact could not be read, which is
    never taken as a pass.
    """

    path: str
    reparse: Optional[bool]
    empty: Optional[bool]
    holders: str  # dir_holders status: "CLEAR" | "LIVE" | "UNKNOWN"
    holder_pids: Tuple[int, ...]
    probe_reason: Optional[str]


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
    leftover_dirs: Tuple[LeftoverDir, ...] = ()


# Re-exported, not redefined (fleet-config#677) -- see `git_run.Unreadable` for
# why an unreadable repo gets no verdict at all rather than a manufactured one.
# `except repo_preflight.Unreadable` at any call site still resolves here.
Unreadable = git_run.Unreadable


def judge_leftover(d: LeftoverDir) -> Optional[str]:
    """Pure: None when the directory is a provably inert shell, else why not."""
    problems = []
    if d.reparse is None:
        problems.append("attributes unreadable")
    elif d.reparse:
        problems.append("a reparse point/junction")
    elif d.empty is None:
        problems.append("contents unreadable")
    elif not d.empty:
        problems.append("not empty")
    if d.holders == "LIVE":
        problems.append("held live by pid " + ", ".join(str(p) for p in d.holder_pids))
    elif d.holders != "CLEAR":
        problems.append(f"live-holder probe {d.holders}: {d.probe_reason or 'no reason given'}")
    return "; ".join(problems) or None


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
    problems = [
        f"{d.path} ({why})"
        for d in facts.leftover_dirs
        if (why := judge_leftover(d)) is not None
    ]
    if problems:
        return (
            LEFTOVER_DIR,
            "leftover wt dir(s) not provably inert, teardown would halt on them: "
            + "; ".join(problems),
        )
    return AVAILABLE, ""


def parse_worktree_list(porcelain: str) -> Tuple[str, ...]:
    """Every worktree past the primary, from `git worktree list --porcelain`.

    The first record is the primary checkout; anything after it is residue
    from an earlier run or a live human session. Returns paths in the order
    git reported them. The parsing itself is `worktree_residue`'s, the one
    porcelain parser.
    """
    return tuple(entry.path for entry in worktree_residue.parse_worktree_porcelain(porcelain))


def _is_reparse(path: Path) -> Optional[bool]:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is None:  # non-Windows: a symlink is the nearest equivalent
        return stat.S_ISLNK(st.st_mode)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_empty(path: Path) -> Optional[bool]:
    # No top-level child means no descendant, so this is the recursive check.
    try:
        with os.scandir(path) as it:
            return next(it, None) is None
    except OSError:
        return None


def scan_leftovers(repo_path: Path, registered: Tuple[str, ...]) -> Tuple[LeftoverDir, ...]:
    """Every `<repo>-wt-*` sibling git does not list, with its read-only facts.

    The glob is per repo, exactly as teardown check 2's, never a fleet-wide
    `*-wt-*`: another repo's in-flight worktree is not this repo's leftover
    (app-launcher#709).
    """
    known = {dir_holders.normalize(p) for p in registered}
    found = []
    for hit in sorted(repo_path.parent.glob(f"{repo_path.name}-wt-*")):
        if dir_holders.normalize(str(hit)) in known:
            continue
        reparse = _is_reparse(hit)
        probe = dir_holders.probe(str(hit))
        found.append(LeftoverDir(
            path=str(hit),
            reparse=reparse,
            empty=_is_empty(hit) if reparse is False else None,
            holders=probe.status,
            holder_pids=tuple(h["pid"] for h in probe.holders),
            probe_reason=probe.reason,
        ))
    return tuple(found)


# Stripped stdout, or `Unreadable` -- never an empty string standing in for a
# fact. Shared with `dirty_tree_check` (fleet-config#677): the two copies were
# byte-identical, and the comment that used to justify keeping them apart ("the
# two helpers report through different vocabularies") described the *callers*,
# which each still render the caught exception their own way.
_run_git = git_run.run_git_or_raise


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
    extra = parse_worktree_list(worktrees)
    return RepoFacts(
        exists=True,
        current_branch=current_branch,
        default_branch=resolved_default,
        porcelain_empty=porcelain == "",
        extra_worktrees=extra,
        leftover_dirs=scan_leftovers(repo_path, extra),
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
