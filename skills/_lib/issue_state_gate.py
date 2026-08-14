"""Pre-dispatch issue-state gate for fleet-wide cleanup fan-out (fleet-config#623).

Why this exists
----------------
`gh search issues --owner ferraroroberto --state open ...` is backed by
GitHub's Search API, which is documented as eventually consistent and was
observed reporting 23 issues as open for five-plus weeks after they had
actually been closed via a merged PR. `/cleanup-fleet-all` dispatched all
four agents (build/validate/execute/teardown, minus teardown when build
detects closure) against every one of them before any code was touched --
46 wasted agent invocations, ~2.9M tokens, confirming already-shipped work.

This module is the fix: one direct `gh issue view --json state` per
candidate issue, run by the orchestrator immediately before it builds the
argument it hands to a dispatch mechanism (the `Workflow` tool for
`/cleanup-fleet-all`, background `Agent` calls for `/cleanup-fleet`) --
never folded into the same eventually-consistent source that produced the
stale list in the first place.

A check that cannot establish state is its own outcome, never a passing
one (global CLAUDE.md's "any check that can fail to establish a fact must
report that as its own state" -- this fleet has hit the collapsed-into-a-
passing-state failure shape before, #560, #612, and this issue). Concretely:
a network error, a rate limit, or an unreadable repo produces `unknown`,
sorted into its own bucket, dispatched exactly like a run that surfaced
zero results -- never silently dropped, never silently treated as open.

Subcommands
-----------
  check <repo> <number>
        Prints `STATE=open|closed|unknown` and, when not `open`,
        `DETAIL=<reason>`. Always exits 0 -- this helper reports, it never
        blocks.

  partition
        Reads a JSON array from stdin, each element at minimum
        `{"repo": ..., "number": ...}` (any other keys pass through
        unchanged). Runs one `check` per element and prints a JSON object
        to stdout:
          {"dispatch": [...], "skipped_closed": [...], "unresolved": [...]}
        Each element in the output carries its original keys plus `state`
        and `detail`. Also prints a one-line summary to stderr:
          DISPATCH=<n> SKIPPED_CLOSED=<n> UNRESOLVED=<n>

The decision logic (`classify_state`, `partition_working_set`) is pure and
unit-tested (`tests/test_issue_state_gate.py`) independent of the `gh`
plumbing around it. stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

GH_TIMEOUT_SECONDS = 30


def classify_state(returncode: int, stdout: str, stderr: str) -> Tuple[str, str]:
    """Pure classification of a `gh issue view --json state` invocation's result.

    Returns (state, detail) where state is "open" | "closed" | "unknown".
    `detail` is empty for "open"/"closed" and a human-readable reason for
    "unknown" -- never invented, always traceable to what `gh` actually said.
    """
    if returncode != 0:
        reason = (stderr or stdout or "gh exited non-zero with no output").strip()
        return "unknown", f"gh issue view failed: {reason}"
    try:
        payload = json.loads(stdout)
        state = payload["state"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "unknown", f"could not parse gh output: {stdout.strip()!r}"
    if state == "OPEN":
        return "open", ""
    if state == "CLOSED":
        return "closed", ""
    return "unknown", f"unrecognized state {state!r}"


def check(repo: str, number: str) -> Tuple[str, str]:
    """Real `gh` call + classification. Never raises -- a failed invocation
    itself becomes an "unknown" verdict with the reason attached."""
    try:
        proc = subprocess.run(
            ["gh", "issue", "view", str(number), "--repo", f"ferraroroberto/{repo}", "--json", "state"],
            capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, creationflags=NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "unknown", f"gh invocation failed: {exc}"
    return classify_state(proc.returncode, proc.stdout, proc.stderr)


def partition_working_set(
    issues: List[Dict[str, Any]],
    state_lookup: Callable[[str, str], Tuple[str, str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Pure partition: no `gh` calls of its own, just the classification
    `state_lookup` already produced. Every issue lands in exactly one of
    three buckets -- there is no fourth path by which an item reaches a
    dispatcher, so proving an item is absent from `dispatch` is sufficient
    to prove no agent is ever spawned for it.
    """
    dispatch: List[Dict[str, Any]] = []
    skipped_closed: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    for item in issues:
        state, detail = state_lookup(item["repo"], item["number"])
        annotated = {**item, "state": state, "detail": detail}
        if state == "open":
            dispatch.append(annotated)
        elif state == "closed":
            skipped_closed.append(annotated)
        else:
            unresolved.append(annotated)
    return {"dispatch": dispatch, "skipped_closed": skipped_closed, "unresolved": unresolved}


def cmd_check(repo: str, number: str) -> None:
    state, detail = check(repo, number)
    print(f"STATE={state}")
    if detail:
        print(f"DETAIL={detail}")


def cmd_partition() -> None:
    issues = json.loads(sys.stdin.read() or "[]")
    result = partition_working_set(issues, check)
    print(json.dumps(result))
    print(
        f"DISPATCH={len(result['dispatch'])} "
        f"SKIPPED_CLOSED={len(result['skipped_closed'])} "
        f"UNRESOLVED={len(result['unresolved'])}",
        file=sys.stderr,
    )


def main(argv: List[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["check"] and len(argv) == 3:
        cmd_check(argv[1], argv[2])
        return 0
    if argv[:1] == ["partition"] and len(argv) == 1:
        cmd_partition()
        return 0
    print("usage: issue_state_gate.py check <repo> <number> | partition", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
