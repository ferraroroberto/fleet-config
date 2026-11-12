"""`ui_proof gate`: may `gh pr merge` go ahead for this PR? The merge hook's one question.

`check` judges the checkout it is pointed at, so a merge run from a checkout on
`main` (or with `-R` from another repo) would diff nothing and pass. The gate
first finds the tree that holds the PR: the checkout of `-R owner/name` (the
cwd's own, else the sibling fleet checkout of that name), then the worktree on
the PR's head branch. That tree is judged exactly as `check --pr` judges it.
When no local worktree has the branch, the PR's own file list from `gh` takes
the diff's place, and the page must be in this checkout's or the primary's
`.proof/`.

A repo with no design-gated UX surface answers `n/a` before any `gh` call, so
a non-UX merge pays one CLAUDE.md read. A fact the gate cannot establish (no
local checkout, `gh` failing, no PR for the branch) is `GATE=unknown`, exit 3,
never a pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import git_run
import ux_surface

from ui_proof import proof

# skills/_lib/ui_proof/merge_gate.py -> the folder that holds every fleet checkout.
FLEET_ROOT = Path(__file__).resolve().parents[4]
# Two of these at most per merge, inside the hook's 50 s bound on the whole gate.
_GH_TIMEOUT_S = 20
# A PR named by URL carries its own repo, which outranks the cwd's.
_PR_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/\d+")

PrInfo = dict
FetchPr = Callable[[str, str], Optional[PrInfo]]
FetchFiles = Callable[[int, str], Optional[List[str]]]


def fetch_pr(selector: str, slug: str) -> Optional[PrInfo]:
    """`number`, `headRefName`, `baseRefName` of the PR `selector` names in `slug`, or None."""
    res = git_run.run_gh(["pr", "view", selector, "-R", slug, "--json", "number,headRefName,baseRefName"],
                         timeout=_GH_TIMEOUT_S)
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("number"), int) else None


def fetch_files(number: int, slug: str) -> Optional[List[str]]:
    """Every path the PR changes (`gh pr diff --name-only`, which is not capped at 100), or None."""
    res = git_run.run_gh(["pr", "diff", str(number), "-R", slug, "--name-only"], timeout=_GH_TIMEOUT_S)
    if res.returncode != 0:
        return None
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _toplevel(path: Path) -> Optional[Path]:
    res = git_run.run_git(["-C", str(path), "rev-parse", "--show-toplevel"])
    return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None


def resolve_checkout(cwd: Path, slug: Optional[str]) -> Tuple[Optional[Path], Optional[str], str]:
    """`(checkout, owner/name, why-not)` for the repo the merge targets."""
    here = _toplevel(cwd) if cwd.is_dir() else None
    here_slug = git_run.origin_slug(here) if here else None
    if not slug:
        if here is None or here_slug is None:
            return None, None, f"{cwd} is not a checkout with a GitHub origin, and no -R names the repo"
        return here, here_slug, ""
    if here is not None and (here_slug or "").lower() == slug.lower():
        return here, here_slug, ""
    sibling = FLEET_ROOT / slug.split("/", 1)[-1]
    top = _toplevel(sibling) if sibling.is_dir() else None
    if top is not None and (git_run.origin_slug(top) or "").lower() == slug.lower():
        return top, slug, ""
    return None, slug, f"no local checkout of {slug} (looked in the cwd and {sibling})"


def branch_checkout(repo: Path, branch: str) -> Optional[Path]:
    """The worktree of `repo` that has `branch` checked out, if any."""
    res = git_run.run_git(["-C", str(repo), "worktree", "list", "--porcelain"])
    if res.returncode != 0:
        return None
    path: Optional[str] = None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.strip() == f"branch refs/heads/{branch}" and path:
            return Path(path)
    return None


def _unknown(reason: str) -> Tuple[int, List[str]]:
    return 3, ["GATE=unknown", f"REASON={reason}"]


def run(cwd: Path, selector: Optional[str], slug: Optional[str],
        get_pr: FetchPr = fetch_pr, get_files: FetchFiles = fetch_files) -> Tuple[int, List[str]]:
    """`(exit code, KEY=value lines)`: 0 merge may go ahead, 1 refused, 3 unknown."""
    url = _PR_URL_RE.search(selector or "")
    if url and not slug:
        slug = url.group(1)
    repo, slug, why = resolve_checkout(cwd, slug)
    if repo is None or slug is None:
        return _unknown(why)
    if not ux_surface.trigger(repo, diff=False).applies:
        return 0, ["GATE=pass", "VISUAL_PROOF=n/a", f"REPO={slug}", "DETAIL=no design-gated UX surface"]
    if not selector:
        selector = git_run.run_git(["-C", str(repo), "branch", "--show-current"]).stdout.strip()
        if not selector:
            return _unknown(f"no PR named and {repo} has no current branch")
    info = get_pr(selector, slug)
    if info is None:
        return _unknown(f"`gh pr view {selector} -R {slug}` failed")
    number, head = info["number"], str(info.get("headRefName") or "")
    base = f"origin/{info['baseRefName']}" if info.get("baseRefName") else None
    tree = branch_checkout(repo, head) if head else None
    if tree is not None:
        verdict = proof.evaluate(tree, base, number)
        where = f"the {head} checkout"
    else:
        files = get_files(number, slug)
        if files is None:
            return _unknown(f"`gh pr diff {number} -R {slug} --name-only` failed")
        verdict = proof.evaluate(repo, None, number, changed=files)
        where = f"the PR's file list ({head or 'branch'} is not checked out here)"
    lines = ["GATE=" + ("refuse" if verdict.blocks else "pass"), f"PR={slug}#{number}",
             f"VISUAL_PROOF={verdict.state}", f"TOUCHED={verdict.touched}", f"PROOF_PAGE={verdict.page}",
             "NEED=" + " | ".join(verdict.need), f"DETAIL=judged {where}; {verdict.detail}"]
    return (1 if verdict.blocks else 0), lines
