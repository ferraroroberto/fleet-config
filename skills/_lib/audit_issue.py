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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

KINDS = (
    "ledger",
    "digest",
    "practices",
    "learning",
    "duplication",
    "stale",
    "claude-md-drift",
    "maintainability",
    "slop",
    "security",
    "bug",
    "documentation",
    "design-drift",
    "cert-drift",
    "context-audit",
    "context-purge",
    "sota-watch",
    "e2e-redundancy",
)

_MARKER_RE = re.compile(
    r"^[ \t]*<!--[ \t]*audit-managed:[ \t]*kind=([\w-]+)[ \t]*-->[ \t]*$", re.MULTILINE
)

# The /codebase-audit finding buckets — never the ledger/digest/practices/
# design-drift/cert-drift kinds. Used to decide whether a merged PR closed one
# of *this repo's own audit findings* (self-fix churn) vs. something else.
# `security` belongs here too even though its managed issue is *closed* on
# fix-merge (unlike the other buckets, which stay open as living backlogs): the
# self-fix check in `evaluate_repo` re-adopts a closed referenced issue by its
# `audit: security findings` title, so its own auto-fix PR is recognized as
# self-fix churn and never triggers a needless re-audit next week
# (fleet-config#361).
BUCKET_KINDS = (
    "duplication",
    "stale",
    "claude-md-drift",
    "maintainability",
    "slop",
    "security",
    "bug",
    "documentation",
)

_LEDGER_MARKER = "<!-- audit-ledger -->"
# The marker an agent hand-authoring a ledger actually tends to write: an OPEN
# comment with the data inside it, so the block stays hidden in rendered
# markdown. Both forms hide the data and nothing at authoring time
# distinguishes them, so the parser accepts either and every write normalizes
# back to the closed form (fleet-config#566 — three repos drifted, one of them
# a ledger created fresh by the very run that then failed to parse it).
_LEDGER_MARKER_OPEN = "<!-- audit-ledger"
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
    if kind == "context-purge":
        return t == "context-purge ledger"
    if kind == "sota-watch":
        return t == "sota-watch ledger"
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
    """Extract {sha, at, rubric} from the `<!-- audit-ledger` block.

    Anchored to the marker (same top-anchor philosophy as has_marker) so a
    quoted/example block elsewhere in the body can't be mistaken for the real
    one. Any missing field is None; an empty-string rubric is a legitimate
    value (repo has no CLAUDE.md) and must never be confused with None
    (unparseable/missing).

    Accepts the closed marker (`<!-- audit-ledger -->` + plain `key: value`
    lines) and the open-comment block an agent naturally writes instead
    (`<!-- audit-ledger` … `-->`) — matching on the shared prefix reads both,
    and the `-->` terminator can't match a `key: value` line. Reading only the
    closed form made a hand-authored ledger indistinguishable from a changed
    repo, buying a full Opus whole-repo audit every week forever
    (fleet-config#566). `render_ledger_body` normalizes to the closed form on
    the next write, so a drifted ledger self-heals.
    """
    # GitHub stores issue bodies with CRLF. `gh issue view -q .body` hands them
    # back LF-normalized (universal newlines on a raw stream) but `gh issue list
    # --json body` does not (the CRLF is escaped *inside* the JSON), so the same
    # ledger parsed or didn't depending on which call fed it — `\r` is
    # whitespace, so `(\S+)[ \t]*$` never matches a CRLF line. Normalize once
    # here rather than leaving a correct-looking parser that is one call-site
    # swap away from declaring all 38 ledgers unparseable.
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    idx = body.find(_LEDGER_MARKER_OPEN)
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


def render_ledger_body(sha: str, at: str, rubric: str) -> str:
    """The ONE place a ledger body is composed — no caller writes a delimiter.

    The marker is a machine contract, and a contract spelled out in agent prose
    is a contract that drifts: three repos hand-authored an unreadable variant,
    including one whose ledger was created fresh by the run that then couldn't
    read it (fleet-config#566). Enforce it in the tool, not the prose
    (project-scaffolding#202). `rubric` may legitimately be the empty string —
    a repo with no CLAUDE.md — which is why it is written unconditionally
    rather than omitted when falsy.
    """
    return (
        "Machine-readable ledger for `/codebase-audit`. Do not edit by hand — "
        "the skill upserts this on each whole-repo run. Labelled `audit-meta` "
        "so it never surfaces as actionable work.\n\n"
        f"{_LEDGER_MARKER}\n"
        f"last-audited-sha: {sha}\n"
        f"last-audited-at: {at}\n"
        f"rubric-sha: {rubric}\n"
    )


def normalize_ledger_body(body: str) -> str:
    """Re-render a ledger body canonically, or raise if it can't be read.

    Every ledger write goes through here, so a body that would not parse back
    is rejected *at write time* — where it is one clear error — instead of a
    week later as a silent, permanent full-audit (fleet-config#566). A body in
    the open-comment form parses fine and comes back out closed, which is how
    an already-drifted ledger self-heals on its next write.
    """
    parsed = parse_ledger(body)
    if parsed["sha"] is None or parsed["rubric"] is None:
        raise SystemExit(
            "refusing to write an unparseable ledger body: no readable "
            f"`{_LEDGER_MARKER_OPEN}` block with `last-audited-sha` and "
            "`rubric-sha`. Don't hand-author the block — use "
            "`audit_issue.py ledger-write`."
        )
    return render_ledger_body(
        parsed["sha"], parsed["at"] or datetime.date.today().isoformat(), parsed["rubric"]
    )


# A ledger the gate cannot read is not "this repo changed" — it is a broken
# ledger, and an AUDIT bought by one costs a full Opus whole-repo pass every
# week until someone notices. Both reasons stay distinguishable from organic
# change all the way out to the sweep JSON and the plan line
# (fleet-config#566, #567).
UNPARSEABLE_LEDGER = "unparseable-ledger"

# A baseline SHA that git cannot resolve is NOT "nothing changed" and NOT a
# transient error to bury — it is a repo whose change-since-last-audit is
# unknown, and the safe answer to unknown is a full whole-repo audit
# (fleet-config#567).
UNRESOLVABLE_BASELINE = "unresolvable-baseline"

# An AUDIT carrying one of these was not earned by change — the gate simply
# could not read the ledger. `no-ledger` is deliberately absent: a repo with no
# ledger at all is a first-ever audit, not drift.
BROKEN_LEDGER_REASONS = (UNPARSEABLE_LEDGER, UNRESOLVABLE_BASELINE)


def default_branch_sha(repo_path: str) -> str | None:
    """Commit sha of the repo's default branch, or None if it can't be read.

    The *only* sha safe to record as `last-audited-sha`. Recording the working
    checkout's `HEAD` poisons the ledger whenever an audit runs off the default
    branch: the fleet pipeline squash-merges and deletes the branch, so that tip
    exists in no checkout and no remote afterwards, `rev-list <sha>..HEAD`
    fails, and the repo silently drops out of every later sweep
    (fleet-config#567 — two repos went unaudited for three weeks). A
    default-branch commit survives a squash by construction.
    """
    ref = git_run.resolve_default_branch_ref(Path(repo_path))
    r = git_run.run_git(["-C", repo_path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    sha = (r.stdout or "").strip()
    return sha if r.returncode == 0 and sha else None


def sha_is_on_default_branch(repo_path: str, sha: str) -> bool:
    """True only when `sha` is an ancestor of (or is) the default-branch tip."""
    if not sha:
        return False
    ref = git_run.resolve_default_branch_ref(Path(repo_path))
    return git_run.run_git(
        ["-C", repo_path, "merge-base", "--is-ancestor", sha, ref]
    ).returncode == 0


def recordable_ledger_sha(repo_path: str) -> str | None:
    """The sha to write into the ledger, or None when none can be verified.

    Belt and braces on top of `default_branch_sha`: re-confirm reachability
    from the default branch before handing it back, so a stale or rewritten
    ref can never be recorded. Refusing to write beats poisoning the ledger
    for weeks — a stale-but-valid baseline just means a slightly wider next
    audit, an unresolvable one means no audit at all.
    """
    sha = default_branch_sha(repo_path)
    if sha is None or not sha_is_on_default_branch(repo_path, sha):
        return None
    return sha


def commits_since(repo_path: str, sha: str) -> int | None:
    """Commits from `sha` to HEAD, or None when the range can't be resolved.

    None is a real answer — "the baseline is unreadable" — and the caller must
    route it to a full audit under its own reason. Letting the underlying
    `rev-list` raise instead lands the repo in `fleet_audit_scan`'s `errors[]`
    bucket, which is reported nowhere (fleet-config#567).
    """
    r = git_run.run_git(["-C", repo_path, "rev-list", f"{sha}..HEAD", "--count"])
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


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


PR_TYPE_WEIGHTS = {
    "feat": 1.0,
    "refactor": 1.0,
    "perf": 0.5,
    "fix": 0.3,
    "chore": 0.2,
    "docs": 0.0,
    "test": 0.0,
}

DEFAULT_SIGNIFICANCE_THRESHOLD = 1000.0


def pr_weight(branch: str) -> float:
    """Significance multiplier for a PR from its conventional branch-prefix type.

    This fleet enforces `<type>/<issue-N>-<slug>` branch naming globally, so
    the type is already present on every PR with no new convention needed.
    Fails open (weight 1.0) for any prefix outside the known low-risk types —
    an unrecognized branch never silently under-counts.
    """
    prefix = branch.split("/", 1)[0] if branch else ""
    return PR_TYPE_WEIGHTS.get(prefix, 1.0)


def unexplained_weighted_loc(commit_shas: list[str], prs: list[dict], managed_numbers: set[int]) -> float:
    """Weighted LOC of the commits NOT explained as this repo's own self-fix churn.

    Mirrors `audit_only_churn`'s per-commit self-fix check, but instead of
    failing closed to a bool, sums a significance score for every commit
    that ISN'T explained — weighted by its PR's conventional branch-type
    (`PR_TYPE_WEIGHTS`), so a docs-only or bug-fix-only commit contributes
    little or nothing while a feature or refactor contributes fully.
    Self-fix-explained commits (merge-commit of a PR whose
    closingIssuesReferences are entirely within `managed_numbers`)
    contribute nothing, regardless of type.

    A commit with no matching PR at all (a direct push — never expected in
    this fleet's PR-only workflow) has no reliable LOC data, so it fails
    open to `float("inf")`: guaranteed to cross any finite threshold rather
    than silently under-counting, exactly like `audit_only_churn`'s own
    fail-closed behavior for the same case.
    """
    by_sha = {}
    for pr in prs:
        merge_commit = pr.get("mergeCommit") or {}
        oid = merge_commit.get("oid")
        if oid:
            by_sha[oid] = pr

    total = 0.0
    for sha in commit_shas:
        pr = by_sha.get(sha)
        if pr is None:
            return float("inf")
        refs = {r["number"] for r in (pr.get("closingIssuesReferences") or [])}
        if refs and refs.issubset(managed_numbers):
            continue
        loc = pr.get("additions", 0) + pr.get("deletions", 0)
        total += loc * pr_weight(pr.get("headRefName", ""))
    return total


def ledger_decision(
    commit_count: int | None,
    stored_rubric_sha: str | None,
    current_rubric_sha: str,
    self_fix: bool = False,
    significance: float | None = None,
    threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
) -> str:
    """The one place the skip/audit call is made. Never LLM prose again.

    AUDIT on any None input (fail open — never silently skip on bad data).
    With zero commits, the rubric decides: SKIP if unchanged, AUDIT if not
    (a rubric can't legitimately change with no commits in a clean repo, so
    a mismatch there is treated as unexplained and fails safe to AUDIT).
    With commits, the rubric is NOT checked independently: self_fix already
    means every commit — including any that edited the rubric file itself,
    e.g. fixing a claude-md-drift finding — is explained as a fix for this
    repo's own audit findings, so it's still SKIP_SELF_FIX.

    When self_fix is False, `significance` (the weighted-LOC total of the
    unexplained commits — see `unexplained_weighted_loc`) decides between
    SKIP_BELOW_THRESHOLD (organic change accumulates quietly, ledger sha
    stays put so next time's check covers the same growing range) and AUDIT
    (crossed threshold — a full whole-repo audit fires and covers
    everything back to the ledger sha, so nothing is ever lost, only
    batched). `significance=None` (the caller didn't compute it) preserves
    the pre-threshold behavior: any unexplained commit means AUDIT.
    """
    if commit_count is None or stored_rubric_sha is None:
        return "AUDIT"
    if commit_count == 0:
        return "SKIP" if stored_rubric_sha == current_rubric_sha else "AUDIT"
    if self_fix:
        return "SKIP_SELF_FIX"
    if significance is not None and significance < threshold:
        return "SKIP_BELOW_THRESHOLD"
    return "AUDIT"


# ---- gh plumbing ----------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    # Force UTF-8: issue bodies routinely contain non-ASCII (em dashes, emoji),
    # and the Windows default (cp1252) raises UnicodeDecodeError mid-read, which
    # would crash the unattended weekly run. errors="replace" never throws.
    # NO_WINDOW for the same unattended run: /audit-fleet upserts dozens of
    # issues from a console-less scheduled job (fleet-config#412).
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )


# A 5xx / timeout-shaped gh failure is the server or the network, not our
# request — retrying once after a short backoff clears it in practice (seen
# for pdf-to-markdown during a fleet sweep, fleet-config#506). A genuine 4xx
# (auth, not-found, bad args) is never transient, so it still fails on the
# first attempt.
_TRANSIENT_GH_RE = re.compile(
    r"(?i)(http\s*5\d\d|timed?\s*out|timeout|connection\s*reset|i/o\s*timeout|"
    r"deadline\s*exceeded|temporarily\s*unavailable|EOF)"
)
_GH_RETRY_BACKOFF_SECONDS = 2.0


def gh(args: list[str], *, _retried: bool = False) -> str:
    """The `gh` shell-out — public (fleet-config#502): sibling helpers
    (`design_sweep_scan.py`, `fleet_audit_scan.py`) already call across
    module boundaries for the git half via `git_run.run_git_checked`; this
    is the `gh` equivalent, promoted out of the module-private `_gh` so a
    future cross-module call site has a real entry point instead of reaching
    into a name Python conventions mark internal-only."""
    r = _run(args)
    if r.returncode != 0:
        stderr = r.stderr or ""
        if not _retried and _TRANSIENT_GH_RE.search(stderr):
            time.sleep(_GH_RETRY_BACKOFF_SECONDS)
            return gh(args, _retried=True)
        sys.stderr.write(stderr)
        raise SystemExit(f"gh {' '.join(args)} failed (exit {r.returncode})")
    return (r.stdout or "").strip()


def _list_open(repo: str) -> list[dict]:
    out = gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "300", "--json", "number,title,body",
    ])
    return json.loads(out) if out else []


def _write_tmp(body: str) -> str:
    # newline="" so Windows doesn't translate the body's LFs to CRLF on the way
    # to `gh` — the ledger block is a parsed machine contract, and it should not
    # acquire line endings that depend on which OS ran the audit.
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="")
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
        body = gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
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
        url = gh(create)
    else:
        edit = ["issue", "edit", str(keep), "--repo", repo, "--title", title, "--body-file", tmp]
        if label:
            edit += ["--add-label", label]
        gh(edit)
        url = gh(["issue", "view", str(keep), "--repo", repo, "--json", "url", "-q", ".url"])
        for n in dupes:
            _ensure_label(repo, "duplicate")
            _run(["issue", "edit", str(n), "--repo", repo, "--add-label", "duplicate"])
            gh([
                "issue", "close", str(n), "--repo", repo,
                "--comment",
                f"Collapsed into #{keep} — one audit issue per type per repo "
                f"(see skills/_lib/audit_issue.py).",
            ])

    return url


def cmd_upsert(repo: str, kind: str, title: str, body: str, label: str | None) -> None:
    # A ledger is a machine contract, so it is validated and normalized here
    # rather than trusted from the caller's markdown — `--kind ledger` through
    # this path used to accept anything, which is how an unreadable block
    # reached three repos (fleet-config#566).
    if kind == "ledger":
        body = normalize_ledger_body(body)
    print(_upsert_issue(repo, kind, title, body, label))


def _fetch_merged_prs(repo: str) -> list[dict]:
    out = gh([
        "pr", "list", "--repo", repo, "--state", "merged", "--limit", "100",
        "--json", "number,mergeCommit,closingIssuesReferences,headRefName,additions,deletions",
    ])
    return json.loads(out) if out else []


def evaluate_repo(repo: str, repo_path: str, dry_run: bool = False) -> dict:
    """The single-repo ledger-gate decision, including self-fix-churn detection.

    Reused by both the standalone `gate` CLI (one repo) and
    `fleet_audit_scan.py` (the whole fleet, one repo at a time). This is the
    ONE place that decides SKIP / AUDIT / SKIP_SELF_FIX / SKIP_BELOW_THRESHOLD
    — no LLM prose duplicates it anywhere else.
    """
    current_rubric = rubric_sha_of_path(repo_path)

    keep, _dupes = plan(_list_open(repo), "ledger")
    if keep is None:
        return {"decision": "AUDIT", "reason": "no-ledger"}

    body = gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
    ledger = parse_ledger(body)
    if ledger["sha"] is None or ledger["rubric"] is None:
        return {"decision": "AUDIT", "reason": UNPARSEABLE_LEDGER, "ledger_issue": keep}

    commit_count = commits_since(repo_path, ledger["sha"])
    if commit_count is None:
        # The recorded baseline resolves to nothing in this checkout (the
        # classic cause: a squash-merged, deleted feature-branch tip). Audit
        # the whole repo — the safe answer — and say *why*, so this never
        # again looks like ordinary organic change or vanishes into errors[].
        return {
            "decision": "AUDIT",
            "reason": UNRESOLVABLE_BASELINE,
            "ledger_issue": keep,
            "baseline_sha": ledger["sha"],
        }
    closed_issues: list[int] = []
    significance: float | None = None

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
        commit_shas = git_run.run_git_checked(
            ["-C", repo_path, "rev-list", "--first-parent", f"{ledger['sha']}..HEAD"]
        ).splitlines()
        prs = _fetch_merged_prs(repo)

        managed = set(bucket_issue_numbers(_list_open(repo)).values())
        referenced: set[int] = set()
        for pr in prs:
            referenced |= {r["number"] for r in (pr.get("closingIssuesReferences") or [])}
        for n in referenced - managed:
            try:
                view = json.loads(gh(["issue", "view", str(n), "--repo", repo, "--json", "title,body"]))
            except SystemExit:
                continue
            if any(has_marker(view.get("body", ""), k) or title_matches(view.get("title", ""), k)
                   for k in BUCKET_KINDS):
                managed.add(n)
        closed_issues = sorted(referenced & managed)

        self_fix = audit_only_churn(commit_shas, prs, managed)
        if not self_fix:
            significance = unexplained_weighted_loc(commit_shas, prs, managed)
        decision = ledger_decision(
            commit_count, ledger["rubric"], current_rubric, self_fix=self_fix, significance=significance
        )

    result = {
        "decision": decision,
        "reason": decision.lower(),
        "ledger_issue": keep,
        "commit_count": commit_count,
        "closed_issues": closed_issues,
        "dry_run": dry_run,
        "significance": significance,
        "threshold": DEFAULT_SIGNIFICANCE_THRESHOLD if significance is not None else None,
    }

    if decision == "SKIP_SELF_FIX" and not dry_run:
        head_sha = recordable_ledger_sha(repo_path)
        if head_sha is None:
            # Never write a sha we could not verify — a poisoned ledger costs
            # weeks of missed audits, a refused write costs one wider audit.
            result["ledger_write"] = "refused-unverifiable-sha"
            return result
        today = datetime.date.today().isoformat()
        ledger_body = render_ledger_body(head_sha, today, current_rubric)
        _upsert_issue(repo, "ledger", "codebase-audit ledger", ledger_body, "audit-meta")
        closed_str = ", ".join(f"#{n}" for n in closed_issues) if closed_issues else "none"
        comment = (
            f"<!-- audit-self-fix -->\n"
            f"Self-fix sweep — {today} @ {head_sha[:7]}: commits since "
            f"{ledger['sha'][:7]} closed only this repo's own audit findings "
            f"({closed_str}) — no organic change, ledger advanced without a "
            f"full re-read."
        )
        gh(["issue", "comment", str(keep), "--repo", repo, "--body", comment])

    return result


def cmd_gate(repo: str, repo_path: str) -> None:
    print(json.dumps(evaluate_repo(repo, repo_path)))


def cmd_ledger_write(repo: str, repo_path: str) -> None:
    """Compose *and* upsert the ledger — `/codebase-audit` step 9, entire.

    Deliberately the only ledger-writing entry point: the sha comes from
    `recordable_ledger_sha` (squash-proof, fleet-config#567) and the block from
    `render_ledger_body` (unhand-authorable, fleet-config#566). A subcommand
    that merely handed back the sha would have left the agent composing the
    block itself, which is the bug.
    """
    sha = recordable_ledger_sha(repo_path)
    if sha is None:
        raise SystemExit(
            f"no verifiable default-branch commit in {repo_path} — "
            "refusing to record an unreachable ledger sha"
        )
    body = render_ledger_body(sha, datetime.date.today().isoformat(), rubric_sha_of_path(repo_path))
    print(_upsert_issue(repo, "ledger", "codebase-audit ledger", body, "audit-meta"))


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

    lw = sub.add_parser("ledger-write")
    lw.add_argument("--repo", required=True)
    lw.add_argument("--repo-path", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "get":
        cmd_get(args.repo, args.kind)
    elif args.cmd == "upsert":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        cmd_upsert(args.repo, args.kind, args.title, body, args.label)
    elif args.cmd == "gate":
        cmd_gate(args.repo, args.repo_path)
    elif args.cmd == "ledger-write":
        cmd_ledger_write(args.repo, args.repo_path)


if __name__ == "__main__":
    main()
