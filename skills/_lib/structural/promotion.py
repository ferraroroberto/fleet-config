"""One deduped project-scaffolding issue per generalizable structural check.

A cleanup lane that merges a check it declared `Structural-scope: generalizable:
<rule-name>` has written a rule the rest of the fleet keeps needing. The
orchestrator then calls `audit_issue.py promote-check`, which lands here:

- Identity is a hidden `<!-- structural-promotion: name=<slug> -->` marker on the
  body's first line, keyed per rule name. The audit's one-issue-per-kind model
  cannot express "one issue per name", so this is its own marker.
- No match -> create, labelled `enhancement` + `structural-promotion`, self-assigned.
- An open match -> comment with the new repo, PR and check path. Never a second issue.
- A closed match -> comment only, never reopen. A completed close means the rule
  shipped, so the comment says the repo should adopt it.

A `gh` failure is `PROMOTION=unknown` with a reason, never a silent skip. The body
and comments carry the rule name, repo, PR number and check path only: no code,
no personal data. The intake contract is project-scaffolding's
`docs/conventions-ratchet.md`, "Adding a rule from a promotion issue".
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from structural.scope import slugify

SCAFFOLD_REPO = "ferraroroberto/project-scaffolding"
OWNER = "ferraroroberto"
LABEL = "structural-promotion"
INTAKE_DOC = (
    "https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/conventions-ratchet.md"
    "#adding-a-rule-from-a-promotion-issue"
)
# The scaffold carries a few hundred issues; every promotion issue fits well inside this.
LIST_LIMIT = 500

_MARKER_RE = re.compile(r"^[ \t]*<!--[ \t]*structural-promotion:[ \t]*name=([a-z0-9-]+)[ \t]*-->[ \t]*$")
_REPO_PR_RE = re.compile(r"^(?:([\w.-]+)/)?([\w.-]+)#(\d+)$")


def marker(slug: str) -> str:
    return f"<!-- structural-promotion: name={slug} -->"


def marker_name(body: str) -> Optional[str]:
    """The slug in the body's first non-blank line, when that line is the marker."""
    lines = [ln for ln in (body or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    m = _MARKER_RE.match(lines[0]) if lines else None
    return m.group(1) if m else None


def parse_repo_pr(repo_pr: str) -> tuple[str, int]:
    """`("ferraroroberto/<repo>", <pr>)` from `<repo>#<pr>` or `<owner>/<repo>#<pr>`; ValueError otherwise."""
    m = _REPO_PR_RE.match((repo_pr or "").strip())
    if not m:
        raise ValueError(f"--repo-pr must be <repo>#<pr>, got {repo_pr!r}")
    return f"{m.group(1) or OWNER}/{m.group(2)}", int(m.group(3))


def plan(issues: list[dict], slug: str) -> dict:
    """`{"action": "create"}`, or `{"action": "comment", "number", "closed", "completed"}`.

    Among several issues carrying the marker, the lowest-numbered open one wins,
    then the lowest-numbered closed one: a second issue is never created for a name.
    """
    matches = sorted((i for i in issues if marker_name(i.get("body", "")) == slug), key=lambda i: i["number"])
    if not matches:
        return {"action": "create"}
    open_ = [i for i in matches if str(i.get("state", "")).upper() == "OPEN"]
    target = (open_ or matches)[0]
    closed = not open_
    return {
        "action": "comment", "number": target["number"], "closed": closed,
        "completed": closed and str(target.get("stateReason") or "").upper() == "COMPLETED",
    }


def render_title(slug: str) -> str:
    return f"structural-promotion: {slug}"


def render_body(slug: str, repo: str, pr: int, path: str) -> str:
    return (
        f"{marker(slug)}\n"
        f"A cleanup lane merged a structural check and declared it generalizable. This issue "
        f"tracks promoting it to a scaffold ratchet rule, worked under [the intake contract]({INTAKE_DOC}).\n\n"
        f"**Rule name:** `{slug}`\n\n"
        f"| Repo | Pull request | Check path |\n|---|---|---|\n"
        f"| {repo} | {repo}#{pr} | `{path}` |\n"
    )


def render_comment(repo: str, pr: int, path: str, target: dict) -> str:
    line = f"Also written in {repo}#{pr}, check path `{path}`."
    if target["completed"]:
        return f"{line} This rule has shipped in the scaffold, so {repo} should adopt it and drop its local check."
    if target["closed"]:
        return f"{line} Recorded only: this issue is closed and stays closed."
    return line


def promote(name: str, repo_pr: str, path: str, gh: Callable[[list[str]], str]) -> dict:
    """`{"state": "created"|"commented"|"unknown", "url"?, "reason"?}`; `gh` raises SystemExit on failure."""
    slug = slugify(name)
    if not slug:
        return {"state": "unknown", "reason": f"rule name {name!r} has no kebab-case form"}
    try:
        repo, pr = parse_repo_pr(repo_pr)
    except ValueError as exc:
        return {"state": "unknown", "reason": str(exc)}
    try:
        listed = json.loads(gh(["issue", "list", "--repo", SCAFFOLD_REPO, "--label", LABEL, "--state", "all",
                                "--limit", str(LIST_LIMIT), "--json", "number,state,stateReason,body,url"]) or "[]")
        step = plan(listed, slug)
        if step["action"] == "create":
            url = gh(["issue", "create", "--repo", SCAFFOLD_REPO, "--title", render_title(slug),
                      "--body", render_body(slug, repo, pr, path),
                      "--label", "enhancement", "--label", LABEL, "--assignee", "@me"])
            return {"state": "created", "url": url}
        url = gh(["issue", "comment", str(step["number"]), "--repo", SCAFFOLD_REPO,
                  "--body", render_comment(repo, pr, path, step)])
        return {"state": "commented", "url": url}
    except (SystemExit, ValueError) as exc:
        return {"state": "unknown", "reason": f"gh failed: {exc}"}


def result_line(result: dict) -> str:
    line = f"PROMOTION={result['state']} URL={result.get('url') or 'none'}"
    return line + (f" REASON={result['reason']}" if "reason" in result else "")
