"""Deterministic identity for audit-managed GitHub issues.

The audit + weekly skills (`/codebase-audit`, `/audit-fleet`, `/learning-log`,
`/design-sync`) keep exactly one open issue per *kind* per repo: a ledger, a
digest, the cross-fleet practices ledger, the weekly learning-log, and one per
finding bucket (incl. the web-app `design-drift` bucket). Idempotency
used to ride on LLM judgment, which slips under the unattended `claude -p` path
and spawns duplicates. This helper moves the create-vs-reuse decision into Python,
keyed on a hidden marker in the issue body, so duplication is structurally
impossible. Same principle as ``hooks/notify_complete.py``: the idempotency-
critical decision lives here, not in the model.

It does **not** decide *content* — the skill builds the (merged) body and hands it
here. This helper only answers "which issue is THE one for (repo, kind)?" and
writes to it, collapsing any strays.

Two subcommands:

  get    --repo OWNER/NAME --kind KIND
         -> prints JSON {"number": N|null, "body": "...", "duplicates": [n,...]}
         The skill reads the existing body, merges its findings, then calls upsert.

  upsert --repo OWNER/NAME --kind KIND --title T --body-file F [--label L]
         0 matches -> create · 1 -> edit · >1 -> edit lowest, close the rest as
         duplicates. Stamps the marker. Prints the canonical issue URL.

Identity is the marker `<!-- audit-managed: kind=<kind> -->`. Pre-existing issues
that predate the marker are adopted by their stable title (see ``title_matches``),
stamped on first edit — so no separate migration pass is needed.

stdlib + the `gh` CLI only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KINDS = (
    "ledger",
    "digest",
    "practices",
    "learning",
    "duplication",
    "stale",
    "claude-md-drift",
    "maintainability",
    "bug",
    "documentation",
    "design-drift",
    "cert-drift",
    "context-audit",
)

_MARKER_RE = re.compile(
    r"^[ \t]*<!--[ \t]*audit-managed:[ \t]*kind=([\w-]+)[ \t]*-->[ \t]*$", re.MULTILINE
)

# The six /codebase-audit finding buckets — never the ledger/digest/practices/
# design-drift/cert-drift kinds. Used to decide whether a merged PR closed one
# of *this repo's own audit findings* (self-fix churn) vs. something else.
BUCKET_KINDS = (
    "duplication",
    "stale",
    "claude-md-drift",
    "maintainability",
    "bug",
    "documentation",
)

_LEDGER_MARKER = "<!-- audit-ledger -->"
_LEDGER_SHA_RE = re.compile(r"^last-audited-sha:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_LEDGER_AT_RE = re.compile(r"^last-audited-at:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_LEDGER_RUBRIC_RE = re.compile(r"^rubric-sha:[ \t]*(.*?)[ \t]*$", re.MULTILINE)


# ---- pure helpers (unit-tested without gh) --------------------------------

def marker_for(kind: str) -> str:
    return f"<!-- audit-managed: kind={kind} -->"


def has_marker(body: str, kind: str) -> bool:
    """True only when the kind marker is the FIRST line of the body.

    `ensure_marker` always stamps the marker at the top, so a managed issue has
    it on line 1. Matching it *anywhere* would mis-adopt an issue that merely
    QUOTES the marker in prose or a code block (e.g. a planning issue that
    documents the ledger format) — that bug once clobbered a planning issue. The
    top-anchored match identifies the managed issue without that collision.
    """
    m = _MARKER_RE.match((body or "").lstrip())
    return bool(m) and m.group(1) == kind


def ensure_marker(body: str, kind: str) -> str:
    """Return body with exactly the correct marker as its first line.

    Strips any pre-existing audit-managed marker(s) first so re-stamping is
    idempotent and never accumulates markers.
    """
    stripped = _MARKER_RE.sub("", body or "").lstrip("\n")
    return f"{marker_for(kind)}\n\n{stripped}" if stripped else f"{marker_for(kind)}\n"


def title_matches(title: str, kind: str) -> bool:
    """Adopt pre-marker issues by their stable title."""
    t = (title or "").strip()
    if kind == "ledger":
        return t == "codebase-audit ledger"
    if kind == "digest":
        return t == "audit-fleet digest state"
    if kind == "practices":
        return t == "fleet practices ledger"
    if kind == "learning":
        return t == "learning log — fleet"
    if kind == "context-audit":
        return t == "context-audit — always-on surface"
    # bucket kinds: "audit: <kind> findings ..." (trailing count suffix tolerated)
    return re.match(r"^audit:\s*" + re.escape(kind) + r"\s+findings\b", t) is not None


def plan(issues: list[dict], kind: str) -> tuple[int | None, list[int]]:
    """Given open issues (dicts with number/title/body), decide keep + close.

    A candidate matches the marker OR the stable title. Keep the lowest number
    (the original); everything else is a stray to collapse.
    """
    candidates = sorted(
        i["number"]
        for i in issues
        if has_marker(i.get("body", ""), kind) or title_matches(i.get("title", ""), kind)
    )
    if not candidates:
        return None, []
    return candidates[0], candidates[1:]


def rubric_sha(data: bytes) -> str:
    """sha256 hex digest of the given raw bytes — the CLAUDE.md rubric fingerprint.

    Takes bytes, not decoded text: reading as text and re-encoding applies
    universal-newline translation (CRLF -> LF), which silently changes the
    hash for any CRLF-terminated file (i.e. most files on this Windows fleet)
    versus a hash taken over the file's actual on-disk bytes. Hash the bytes
    exactly as they sit on disk so this can never drift from itself.
    """
    return hashlib.sha256(data).hexdigest()


def rubric_sha_of_path(repo_path: str) -> str:
    """rubric-sha for a repo: sha256 of its CLAUDE.md, or "" if it has none.

    A missing CLAUDE.md deliberately contributes the empty string rather than
    sha256(""), so "no rubric" and "an empty rubric file" never collide.
    """
    p = Path(repo_path) / "CLAUDE.md"
    if not p.exists():
        return ""
    return rubric_sha(p.read_bytes())


def parse_ledger(body: str) -> dict:
    """Extract {sha, at, rubric} from the <!-- audit-ledger --> block.

    Anchored to the marker (same top-anchor philosophy as has_marker) so a
    quoted/example block elsewhere in the body can't be mistaken for the real
    one. Any missing field is None; an empty-string rubric is a legitimate
    value (repo has no CLAUDE.md) and must never be confused with None
    (unparseable/missing).
    """
    idx = (body or "").find(_LEDGER_MARKER)
    if idx == -1:
        return {"sha": None, "at": None, "rubric": None}
    block = body[idx:]
    sha_m = _LEDGER_SHA_RE.search(block)
    at_m = _LEDGER_AT_RE.search(block)
    rubric_m = _LEDGER_RUBRIC_RE.search(block)
    return {
        "sha": sha_m.group(1) if sha_m else None,
        "at": at_m.group(1) if at_m else None,
        "rubric": rubric_m.group(1) if rubric_m else None,
    }


def bucket_issue_numbers(issues: list[dict]) -> dict[str, int]:
    """Given an already-fetched issue list, the managed bucket-issue numbers.

    Matches by marker/title exactly like plan() does — never by label alone,
    since `bug` and `documentation` labels are also used on hand-filed issues
    that are NOT audit-managed.
    """
    result: dict[str, int] = {}
    for kind in BUCKET_KINDS:
        keep, _ = plan(issues, kind)
        if keep is not None:
            result[kind] = keep
    return result


def audit_only_churn(commit_shas: list[str], prs: list[dict], managed_numbers: set[int]) -> bool:
    """True only if every commit in range is explained by a self-fix PR.

    A commit is "explained" when it is the merge-commit of some PR in `prs`
    whose closingIssuesReferences are non-empty and entirely contained in
    `managed_numbers`. Any unexplained commit (direct push, non-squash merge,
    a PR outside the fetch window) or any PR closing something outside the
    managed set fails closed to False — this must never produce a false skip.
    """
    if not commit_shas:
        return True
    by_sha = {}
    for pr in prs:
        merge_commit = pr.get("mergeCommit") or {}
        oid = merge_commit.get("oid")
        if oid:
            by_sha[oid] = pr
    for sha in commit_shas:
        pr = by_sha.get(sha)
        if pr is None:
            return False
        refs = {r["number"] for r in (pr.get("closingIssuesReferences") or [])}
        if not refs or not refs.issubset(managed_numbers):
            return False
    return True


def ledger_decision(
    commit_count: int | None,
    stored_rubric_sha: str | None,
    current_rubric_sha: str,
    self_fix: bool = False,
) -> str:
    """The one place the skip/audit call is made. Never LLM prose again.

    AUDIT on any None input (fail open — never silently skip on bad data).
    With zero commits, the rubric decides: SKIP if unchanged, AUDIT if not
    (a rubric can't legitimately change with no commits in a clean repo, so
    a mismatch there is treated as unexplained and fails safe to AUDIT).
    With commits, the rubric is NOT checked independently: self_fix already
    means every commit — including any that edited the rubric file itself,
    e.g. fixing a claude-md-drift finding — is explained as a fix for this
    repo's own audit findings, so it's still SKIP_SELF_FIX. Only when
    self_fix is False (some commit isn't explained as a self-fix) does it
    fall to AUDIT.
    """
    if commit_count is None or stored_rubric_sha is None:
        return "AUDIT"
    if commit_count == 0:
        return "SKIP" if stored_rubric_sha == current_rubric_sha else "AUDIT"
    return "SKIP_SELF_FIX" if self_fix else "AUDIT"


# ---- gh plumbing ----------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    # Force UTF-8: issue bodies routinely contain non-ASCII (em dashes, emoji),
    # and the Windows default (cp1252) raises UnicodeDecodeError mid-read, which
    # would crash the unattended weekly run. errors="replace" never throws.
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _gh(args: list[str]) -> str:
    r = _run(args)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"gh {' '.join(args)} failed (exit {r.returncode})")
    return (r.stdout or "").strip()


def _git(args: list[str]) -> str:
    r = subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"git {' '.join(args)} failed (exit {r.returncode})")
    return (r.stdout or "").strip()


def _list_open(repo: str) -> list[dict]:
    out = _gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "300", "--json", "number,title,body",
    ])
    return json.loads(out) if out else []


def _write_tmp(body: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(body)
    f.close()
    return f.name


def _ensure_label(repo: str, label: str) -> None:
    # idempotent: a duplicate-create just fails and is ignored
    _run(["label", "create", label, "--repo", repo])


# ---- subcommands ----------------------------------------------------------

def cmd_get(repo: str, kind: str) -> None:
    keep, dupes = plan(_list_open(repo), kind)
    body = ""
    if keep is not None:
        body = _gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
    print(json.dumps({"number": keep, "body": body, "duplicates": dupes}))


def _upsert_issue(repo: str, kind: str, title: str, body: str, label: str | None) -> str:
    keep, dupes = plan(_list_open(repo), kind)
    tmp = _write_tmp(ensure_marker(body, kind))

    if label:
        _ensure_label(repo, label)

    if keep is None:
        create = ["issue", "create", "--repo", repo, "--title", title,
                  "--body-file", tmp, "--assignee", "@me"]
        if label:
            create += ["--label", label]
        url = _gh(create)
    else:
        edit = ["issue", "edit", str(keep), "--repo", repo, "--title", title, "--body-file", tmp]
        if label:
            edit += ["--add-label", label]
        _gh(edit)
        url = _gh(["issue", "view", str(keep), "--repo", repo, "--json", "url", "-q", ".url"])
        for n in dupes:
            _ensure_label(repo, "duplicate")
            _run(["issue", "edit", str(n), "--repo", repo, "--add-label", "duplicate"])
            _gh([
                "issue", "close", str(n), "--repo", repo,
                "--comment",
                f"Collapsed into #{keep} — one audit issue per type per repo "
                f"(see skills/_lib/audit_issue.py).",
            ])

    return url


def cmd_upsert(repo: str, kind: str, title: str, body: str, label: str | None) -> None:
    print(_upsert_issue(repo, kind, title, body, label))


def _fetch_merged_prs(repo: str) -> list[dict]:
    out = _gh([
        "pr", "list", "--repo", repo, "--state", "merged", "--limit", "100",
        "--json", "number,mergeCommit,closingIssuesReferences",
    ])
    return json.loads(out) if out else []


def evaluate_repo(repo: str, repo_path: str, dry_run: bool = False) -> dict:
    """The single-repo ledger-gate decision, including self-fix-churn detection.

    Reused by both the standalone `gate` CLI (one repo) and
    `fleet_audit_scan.py` (the whole fleet, one repo at a time). This is the
    ONE place that decides SKIP / AUDIT / SKIP_SELF_FIX — no LLM prose
    duplicates it anywhere else.
    """
    current_rubric = rubric_sha_of_path(repo_path)

    keep, _dupes = plan(_list_open(repo), "ledger")
    if keep is None:
        return {"decision": "AUDIT", "reason": "no-ledger"}

    body = _gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
    ledger = parse_ledger(body)
    if ledger["sha"] is None or ledger["rubric"] is None:
        return {"decision": "AUDIT", "reason": "unparseable-ledger", "ledger_issue": keep}

    commit_count = int(_git(["-C", repo_path, "rev-list", f"{ledger['sha']}..HEAD", "--count"]))
    closed_issues: list[int] = []

    if commit_count == 0:
        # Nothing landed at all. A rubric mismatch here would mean the
        # project CLAUDE.md changed with zero commits, which can't happen in
        # a clean repo — but stay fail-safe and re-audit rather than trust a
        # ledger/rubric mismatch we can't explain.
        decision = ledger_decision(commit_count, ledger["rubric"], current_rubric)
    else:
        # Deliberately do NOT short-circuit to AUDIT on a rubric mismatch
        # here: if the only commits since the last audit are self-fix PRs
        # (audit_only_churn below), one of those very fixes may have edited
        # CLAUDE.md itself (e.g. resolving a claude-md-drift finding) — that
        # is still self-fix churn, not organic drift, so it must not force a
        # full re-audit. A rubric change matters ONLY when it can't be
        # explained by the fixes themselves — captured by ledger_decision
        # falling through to AUDIT whenever self_fix comes back False.
        # --first-parent collapses each merged PR to the one commit that
        # actually landed on the mainline (the squash commit, or the merge
        # commit for a regular non-squash merge) — never the individual
        # feature-branch commits a merge pulls in as second-parent history.
        # Without this, a repo using regular "Merge pull request" merges
        # (several sister repos do — see the global CLAUDE.md's "some sister
        # projects use a local-merge flow" note) shows the feature commit as
        # an "unexplained" commit even though its own merge commit matched a
        # self-fix PR, which fails audit_only_churn closed for every such
        # repo. This makes the self-fix check work identically for
        # squash-merge and regular-merge repos.
        commit_shas = _git(
            ["-C", repo_path, "rev-list", "--first-parent", f"{ledger['sha']}..HEAD"]
        ).splitlines()
        prs = _fetch_merged_prs(repo)

        managed = set(bucket_issue_numbers(_list_open(repo)).values())
        referenced: set[int] = set()
        for pr in prs:
            referenced |= {r["number"] for r in (pr.get("closingIssuesReferences") or [])}
        for n in referenced - managed:
            try:
                view = json.loads(_gh(["issue", "view", str(n), "--repo", repo, "--json", "title,body"]))
            except SystemExit:
                continue
            if any(has_marker(view.get("body", ""), k) or title_matches(view.get("title", ""), k)
                   for k in BUCKET_KINDS):
                managed.add(n)
        closed_issues = sorted(referenced & managed)

        self_fix = audit_only_churn(commit_shas, prs, managed)
        decision = ledger_decision(commit_count, ledger["rubric"], current_rubric, self_fix=self_fix)

    result = {
        "decision": decision,
        "reason": decision.lower(),
        "ledger_issue": keep,
        "commit_count": commit_count,
        "closed_issues": closed_issues,
        "dry_run": dry_run,
    }

    if decision == "SKIP_SELF_FIX" and not dry_run:
        head_sha = _git(["-C", repo_path, "rev-parse", "HEAD"])
        today = datetime.date.today().isoformat()
        ledger_body = (
            "Machine-readable ledger for `/codebase-audit`. Do not edit by hand — "
            "the skill upserts this on each whole-repo run. Labelled `audit-meta` "
            "so it never surfaces as actionable work.\n\n"
            f"{_LEDGER_MARKER}\n"
            f"last-audited-sha: {head_sha}\n"
            f"last-audited-at: {today}\n"
            f"rubric-sha: {current_rubric}\n"
        )
        _upsert_issue(repo, "ledger", "codebase-audit ledger", ledger_body, "audit-meta")
        closed_str = ", ".join(f"#{n}" for n in closed_issues) if closed_issues else "none"
        comment = (
            f"<!-- audit-self-fix -->\n"
            f"Self-fix sweep — {today} @ {head_sha[:7]}: commits since "
            f"{ledger['sha'][:7]} closed only this repo's own audit findings "
            f"({closed_str}) — no organic change, ledger advanced without a "
            f"full re-read."
        )
        _gh(["issue", "comment", str(keep), "--repo", repo, "--body", comment])

    return result


def cmd_gate(repo: str, repo_path: str) -> None:
    print(json.dumps(evaluate_repo(repo, repo_path)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Deterministic upsert for audit-managed issues.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("--repo", required=True)
    g.add_argument("--kind", required=True, choices=KINDS)

    u = sub.add_parser("upsert")
    u.add_argument("--repo", required=True)
    u.add_argument("--kind", required=True, choices=KINDS)
    u.add_argument("--title", required=True)
    u.add_argument("--body-file", required=True)
    u.add_argument("--label", default=None)

    gt = sub.add_parser("gate")
    gt.add_argument("--repo", required=True)
    gt.add_argument("--repo-path", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "get":
        cmd_get(args.repo, args.kind)
    elif args.cmd == "upsert":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        cmd_upsert(args.repo, args.kind, args.title, body, args.label)
    elif args.cmd == "gate":
        cmd_gate(args.repo, args.repo_path)


if __name__ == "__main__":
    main()
