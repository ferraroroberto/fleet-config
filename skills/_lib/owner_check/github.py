"""Every `gh` call the owner-check package makes, behind one seam (`_gh`).

A call that fails raises `GhError`; callers turn that into the `unknown` state,
never into "nothing to do". Tests replace `_gh` and never touch the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import gh_issue_fetch  # noqa: E402
import git_run  # noqa: E402
from fleet_core import fleet_repo_scan  # noqa: E402
from owner_check import block  # noqa: E402

OWNER = gh_issue_fetch.OWNER
GH_TIMEOUT_SECONDS = 60
# A fleet-wide list is ~2 calls per repo; a few in flight keeps it under a minute
# without tripping GitHub's secondary rate limit.
LIST_WORKERS = 6


class GhError(RuntimeError):
    """A `gh` call that did not establish its answer."""


# gh's wording when a repo has GitHub Issues switched off: it holds no issues, so
# no owner items either, which is a fact, not an unreadable repo.
_ISSUES_DISABLED = "has disabled issues"


def _issue_rows(args: Sequence[str]) -> List[Dict[str, Any]]:
    """An `issue list` call's rows; a repo with Issues switched off has none."""
    try:
        return json.loads(_gh(args))
    except GhError as exc:
        if _ISSUES_DISABLED in str(exc):
            return []
        raise


def _gh(args: Sequence[str]) -> str:
    proc = git_run.run_gh(list(args), timeout=GH_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout or f"gh {args[0]} failed").strip())
    return proc.stdout


def _slug(repo: str) -> str:
    return f"{OWNER}/{repo}"


def fetch_issue(repo: str, number: int) -> Dict[str, Any]:
    """Title, body, state, labels (names), comment bodies oldest first, closing PR urls."""
    out = _gh([
        "issue", "view", str(number), "--repo", _slug(repo),
        "--json", "number,title,body,state,url,labels,comments,closedByPullRequestsReferences",
    ])
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise GhError(f"unparseable gh issue view output: {exc}") from exc
    return {
        "repo": repo,
        "number": int(data.get("number", number)),
        "title": data.get("title") or "",
        "body": data.get("body") or "",
        "state": data.get("state") or "",
        "url": data.get("url") or "",
        "labels": [str(lab.get("name")) for lab in data.get("labels") or []],
        "comments": [str(c.get("body") or "") for c in data.get("comments") or []],
        "prs": [str(p.get("url")) for p in data.get("closedByPullRequestsReferences") or [] if p.get("url")],
    }


def pr_files(repo: str, pr: int) -> List[Tuple[str, str]]:
    """`(status, path)` per changed file, from the PR itself so it works after merge."""
    out = _gh([
        "api", "--paginate", f"repos/{_slug(repo)}/pulls/{pr}/files",
        "--jq", ".[] | [.status, .filename] | @tsv",
    ])
    files = []
    for line in out.splitlines():
        if "\t" in line:
            status, path = line.split("\t", 1)
            files.append((status.strip(), path.strip()))
    return files


def pr_body(repo: str, pr: int) -> str:
    """The PR's description, where `/issue-finish` records the `VISUAL_PROOF=` line."""
    return _gh(["api", f"repos/{_slug(repo)}/pulls/{pr}", "--jq", ".body // \"\""])


def pr_url(repo: str, pr: int) -> str:
    return f"https://github.com/{_slug(repo)}/pull/{pr}"


def ensure_label(repo: str, label: str) -> None:
    color, description = block.LABEL_STYLE[label]
    _gh(["label", "create", label, "--repo", _slug(repo), "--color", color,
         "--description", description, "--force"])


def _edit_labels(repo: str, number: int, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
    args = ["issue", "edit", str(number), "--repo", _slug(repo)]
    for label in add:
        args += ["--add-label", label]
    for label in remove:
        args += ["--remove-label", label]
    _gh(args)


def _post_comment(repo: str, number: int, body: str) -> None:
    fd, path = tempfile.mkstemp(prefix="owner-check-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        _gh(["issue", "comment", str(number), "--repo", _slug(repo), "--body-file", path])
    finally:
        os.unlink(path)


def mark(repo: str, number: int, kind: str, body: str) -> str:
    """Create the kind's label if missing, apply it, post the block. Returns the label."""
    label = block.LABELS[kind]
    ensure_label(repo, label)
    _edit_labels(repo, number, add=[label])
    _post_comment(repo, number, body)
    return label


def clear(repo: str, number: int, present_labels: Sequence[str], body: str) -> List[str]:
    """Remove every owner-check label the issue carries and post the cleared block."""
    removed = [label for label in block.LABELS.values() if label in present_labels]
    if removed:
        _edit_labels(repo, number, remove=removed)
    _post_comment(repo, number, body)
    return removed


def closed_issues(repo: str) -> List[Dict[str, Any]]:
    """Recently closed issues with what the rule reads, for the back-fill dry run."""
    rows = _issue_rows([
        "issue", "list", "--repo", _slug(repo), "--state", "closed", "--limit", "400",
        "--json", "number,title,body,labels,closedAt,stateReason",
    ])
    for row in rows:
        row["labels"] = [str(lab.get("name")) for lab in row.get("labels") or []]
    return rows


def is_pull_request(repo: str, number: int) -> bool:
    """True when `number` is a PR; the Issues API serves both under one counter."""
    out = _gh(["api", f"repos/{_slug(repo)}/issues/{number}", "--jq", "has(\"pull_request\")"])
    return out.strip() == "true"


def _labelled(repo: str, label: str) -> List[Dict[str, Any]]:
    return _issue_rows([
        "issue", "list", "--repo", _slug(repo), "--state", "all", "--label", label,
        "--limit", "200", "--json", "number,title,state,url,labels,comments",
    ])


def _item(repo: str, row: Dict[str, Any], kind: str) -> Dict[str, Any]:
    latest = block.latest_block(str(c.get("body") or "") for c in row.get("comments") or []) or {}
    return {
        "repo": repo,
        "number": row.get("number"),
        "ref": f"{repo}#{row.get('number')}",
        "title": row.get("title") or "",
        "url": row.get("url") or "",
        "state": row.get("state") or "",
        "kind": kind,
        # A label with no block is still an item; its fields stay empty, never invented.
        "block": bool(latest) and latest.get("kind") == kind,
        "reason": latest.get("reason", "") if latest.get("kind") == kind else "",
        "try": latest.get("try", "") if latest.get("kind") == kind else "",
        "proof": latest.get("proof", []) if latest.get("kind") == kind else [],
        "since": latest.get("since", "") if latest.get("kind") == kind else "",
    }


def fleet_repo_names(projects_toml: Optional[Path] = None) -> List[str]:
    """Every live owner repo minus the dormant ones, as `gh_issue_fetch` scopes it."""
    repos = gh_issue_fetch.list_owner_repos()
    kept, _skipped = gh_issue_fetch.split_dormant(repos, fleet_repo_scan.ignored_repos(projects_toml))
    return kept


def newest_first(items: List[Dict[str, Any]]) -> None:
    """Sort in place: latest `since` first, undated items last, ties by repo and number.

    The Board's "Waiting for start" shows the most recent step on top; an item
    with no block has no date and sinks rather than being given one. Two stable
    passes, so the date's reverse order never flips the tie-break.
    """
    items.sort(key=lambda it: (it["repo"], it["number"] or 0))
    items.sort(key=lambda it: str(it.get("since") or ""), reverse=True)


def list_items(repos: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Every issue carrying an owner-check label, fleet-wide: the reader API.

    Returns `{"items": [...], "unknown": [{"repo", "reason"}]}`. A repo that
    could not be read lands in `unknown`, never silently in an empty list.
    """
    unknown: List[Dict[str, str]] = []
    if repos is None:
        try:
            repos = fleet_repo_names()
        except (RuntimeError, fleet_repo_scan.ProjectsTomlMissing) as exc:
            return {"items": [], "unknown": [{"repo": "*", "reason": str(exc)}]}
    jobs = [(repo, kind, label) for repo in repos for kind, label in block.LABELS.items()]

    def run(job: Tuple[str, str, str]) -> Tuple[str, str, Any]:
        repo, kind, label = job
        try:
            return repo, kind, _labelled(repo, label)
        except (GhError, ValueError) as exc:
            return repo, kind, exc

    items: List[Dict[str, Any]] = []
    failed = set()
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as pool:
        for repo, kind, result in pool.map(run, jobs):
            if isinstance(result, Exception):
                if repo not in failed:
                    unknown.append({"repo": repo, "reason": str(result)})
                    failed.add(repo)
                continue
            items.extend(_item(repo, row, kind) for row in result)
    newest_first(items)
    return {"items": items, "unknown": unknown}
