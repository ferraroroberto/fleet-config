"""The pure half of `audit_issue.py`: managed-issue identity, the ledger, snapshots and churn.

Markers and titles that identify a managed issue, the ledger body's parse and
render, the snapshot/recurrence tables, and the audit-or-skip decision. No
`gh` here -- only read-only `git` through `git_run` -- so `audit_issue.py`
keeps every network call (and the seams its tests swap) and re-exports these
names for its callers.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Iterator, Optional

import git_run

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
    "design-review",
    "perf-review",
    "cert-drift",
    "context-audit",
    "context-purge",
    "context-purge-digest",
    "sota-watch",
    "prompt-audit",
    "prompt-drift",
    "e2e-redundancy",
    "cleanup-deferred",
)

_MARKER_RE = re.compile(
    r"^[ \t]*<!--[ \t]*audit-managed:[ \t]*kind=([\w-]+)[ \t]*-->[ \t]*$", re.MULTILINE
)

# The one managed `/design-review` issue per repo: rendered
# findings keyed by rubric id in an app repo; the spec- or scaffold-owned list
# in fleet-config / project-scaffolding. Stable title, no count suffix.
DESIGN_REVIEW_TITLE = "design-review: rendered findings"

# The one managed `/perf-review` issue per repo: phone-load
# budgets vs measured, a hand-curated Fixes checklist, a run log.
PERF_REVIEW_TITLE = "perf-review: phone-load findings"

# The /codebase-audit finding buckets — never the ledger/digest/practices/
# design-drift/cert-drift kinds. Used to decide whether a merged PR closed one
# of *this repo's own audit findings* (self-fix churn) vs. something else.
# `security` belongs here too even though its managed issue is *closed* on
# fix-merge (unlike the other buckets, which stay open as living backlogs): the
# self-fix check in `evaluate_repo` re-adopts a closed referenced issue by its
# `audit: security findings` title, so its own auto-fix PR is recognized as
# self-fix churn and never triggers a needless re-audit next week.
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

# `<!-- audit-snapshot -->` column -> managed bucket kind.
# `sec` and `total` are deliberately absent: security is self-healed and closed
# on merge, never a cleanup bucket, and `total` is a sum. Bucket kinds with no
# column (`security`, and non-audit cleanup kinds) have no recurrence to report.
SNAPSHOT_COLUMNS = {
    "dup": "duplication",
    "stale": "stale",
    "drift": "claude-md-drift",
    "maint": "maintainability",
    "slop": "slop",
    "bug": "bug",
    "doc": "documentation",
}

_SNAPSHOT_MARKER = "<!-- audit-snapshot -->"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RECURRENCE_BLOCK_RE = re.compile(
    r"^[ \t]*<!--[ \t]*audit-recurrence:[^\n]*-->[ \t]*(?:\n>[^\n]*)*\n*", re.MULTILINE
)
_RECURRENCE_STATE_RE = re.compile(r"<!--[ \t]*audit-recurrence:[ \t]*state=([\w-]+)")

_LEDGER_MARKER = "<!-- audit-ledger -->"
# The marker an agent hand-authoring a ledger actually tends to write: an OPEN
# comment with the data inside it, so the block stays hidden in rendered
# markdown. Both forms hide the data and nothing at authoring time
# distinguishes them, so the parser accepts either and every write normalizes
# back to the closed form (three repos once drifted, one of them
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
    if kind == "prompt-audit":
        return t == "prompt-audit ledger"
    if kind == "cleanup-deferred":
        return t == "cleanup-fleet-all deferred repos"
    if kind == "design-review":
        return t == DESIGN_REVIEW_TITLE
    if kind == "perf-review":
        return t == PERF_REVIEW_TITLE
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


# ---- bucket recurrence ---------------------------------
# A bucket that is non-zero in two consecutive audit snapshots keeps regrowing,
# so its cleanup must ship a structural check (lint rule, static test, ratchet)
# or say why it can't. Snapshots are counts only, so recurrence is per bucket,
# never per finding. `unknown` is its own state and is never folded into
# `not-recurring` — a window we couldn't read is not a clean bill.

def is_snapshot(body: str) -> bool:
    """True only when the snapshot marker is the first line (top-anchored, like `has_marker`)."""
    return (body or "").replace("\r\n", "\n").lstrip().startswith(_SNAPSHOT_MARKER)


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_snapshot(body: str) -> Optional[dict]:
    """`{"run", "sha", "counts": {kind: int}}` from a snapshot comment, or None if malformed.

    Cells map by header name, not position, so a reordered table still reads.
    Valid only when `run` is an ISO date and every bucket column is a
    non-negative integer; anything else is None — never a guessed zero.
    """
    if not is_snapshot(body):
        return None
    lines = [ln for ln in (body or "").replace("\r\n", "\n").split("\n") if ln.strip().startswith("|")]
    if len(lines) < 3:
        return None
    header = _table_cells(lines[0])
    data = _table_cells(lines[2])
    if len(header) != len(data):
        return None
    row = dict(zip(header, data))
    run = row.get("run", "")
    if not _ISO_DATE_RE.match(run):
        return None
    counts: dict[str, int] = {}
    for col, kind in SNAPSHOT_COLUMNS.items():
        cell = row.get(col)
        if cell is None or not cell.isdigit():
            return None
        counts[kind] = int(cell)
    return {"run": run, "sha": row.get("sha", ""), "counts": counts}


def snapshot_rows(comments: list[dict]) -> list[Optional[dict]]:
    """The ledger's snapshot rows, oldest first; a malformed row stays in place as None.

    Ordered by (run date, comment `created_at`) so two audits on one day still
    count as two rows. A malformed row has no run date and sorts by its post date.
    """
    keyed = []
    for c in comments:
        body = c.get("body") or ""
        if not is_snapshot(body):
            continue
        created = c.get("created_at") or ""
        row = parse_snapshot(body)
        keyed.append(((row["run"] if row else created[:10]), created, row))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [row for _run, _created, row in keyed]


def _unknown(reason: str) -> dict:
    return {
        "state": "unknown",
        "reason": reason,
        "buckets": {kind: {"state": "unknown"} for kind in SNAPSHOT_COLUMNS.values()},
    }


def bucket_recurrence(rows: list[Optional[dict]]) -> dict:
    """Recurrence per bucket over the last two snapshot rows.

    Never skips back past a malformed row to an older valid one: that would
    compare two runs that weren't consecutive.
    """
    if len(rows) < 2:
        return _unknown("insufficient-history")
    previous, latest = rows[-2], rows[-1]
    if previous is None or latest is None:
        return _unknown("malformed-row")
    buckets = {}
    for kind in SNAPSHOT_COLUMNS.values():
        a, b = previous["counts"][kind], latest["counts"][kind]
        buckets[kind] = {"state": "recurring" if a > 0 and b > 0 else "not-recurring", "counts": [a, b]}
    return {
        "state": "ok",
        "rows": len(rows),
        "latest": f"{latest['run']}@{latest['sha']}",
        "previous": f"{previous['run']}@{previous['sha']}",
        "buckets": buckets,
    }


def recurrence_at_upsert(rows: list[Optional[dict]], kind: str) -> dict:
    """The recurrence state for a bucket issue being upserted by `/codebase-audit` step 8.

    Step 8 upserts *before* step 9 posts this run's snapshot, and a bucket is
    only upserted when it is non-empty this run. So the window is (latest
    posted snapshot, this run), and once step 9 posts, it is exactly the last
    two rows `bucket_recurrence` reads — the stamp and the subcommand agree.
    """
    if not rows:
        return {"state": "unknown", "reason": "insufficient-history"}
    latest = rows[-1]
    if latest is None:
        return {"state": "unknown", "reason": "malformed-row"}
    if latest["counts"][kind] > 0:
        return {"state": "recurring", "since": latest["run"]}
    return {"state": "not-recurring"}


def render_recurrence_block(rec: dict) -> Optional[str]:
    """The one managed recurrence block, or None when the bucket isn't recurring."""
    if rec["state"] == "recurring":
        return (
            f"<!-- audit-recurrence: state=recurring since={rec['since']} -->\n"
            "> **Recurring: structural check required.** This bucket had findings in two "
            "consecutive audits. A cleanup PR must also ship one structural check that would "
            "have caught this class, or record `not encodable: <why>`."
        )
    if rec["state"] == "unknown":
        return (
            f"<!-- audit-recurrence: state=unknown reason={rec['reason']} -->\n"
            f"> **Recurrence unknown.** Recurrence could not be established "
            f"({rec['reason']}), so no structural check is required yet."
        )
    return None


def apply_recurrence_block(body: str, block: Optional[str]) -> str:
    """Replace the recurrence block idempotently; `ensure_marker` later puts the identity marker above it.

    Any old block is removed wherever it sits, and the new one (if any) leads
    the body, so the identity marker stays on line 1 and `has_marker` still matches.
    """
    rest = _RECURRENCE_BLOCK_RE.sub("", _MARKER_RE.sub("", body or "")).lstrip("\n")
    if block is None:
        return rest
    return f"{block}\n\n{rest}" if rest else f"{block}\n"


def recurrence_state_of(body: str) -> Optional[str]:
    """The stamped recurrence state, read only from the top of a managed body (after the identity marker)."""
    rest = _MARKER_RE.sub("", (body or "").replace("\r\n", "\n"), count=1).lstrip()
    m = _RECURRENCE_STATE_RE.match(rest)
    return m.group(1) if m else None


# A ledger the gate cannot read is not "this repo changed" — it is a broken
# ledger, and an AUDIT bought by one costs a full Opus whole-repo pass every
# week until someone notices. Both reasons stay distinguishable from organic
# change all the way out to the sweep JSON and the plan line.
UNPARSEABLE_LEDGER = "unparseable-ledger"

# A baseline SHA that git cannot resolve is NOT "nothing changed" and NOT a
# transient error to bury — it is a repo whose change-since-last-audit is
# unknown, and the safe answer to unknown is a full whole-repo audit.
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


def _unexplained_commits(
    commit_shas: list[str], prs: list[dict], managed_numbers: set[int]
) -> Iterator[Optional[dict]]:
    """Yield one entry per commit that is NOT explained as this repo's own
    self-fix churn: the commit's PR, or `None` when no PR merged it at all.

    A commit is "explained" when it is the merge-commit of some PR in `prs`
    whose closingIssuesReferences are non-empty and entirely contained in
    `managed_numbers`. Both callers need exactly this walk and differ only in
    what they do with the result — `audit_only_churn` asks whether the sequence
    is empty, `unexplained_weighted_loc` sums a score over it — so the self-fix
    rule is defined once and can only change in one place (fleet-config#677).
    """
    by_sha = {}
    for pr in prs:
        merge_commit = pr.get("mergeCommit") or {}
        oid = merge_commit.get("oid")
        if oid:
            by_sha[oid] = pr
    for sha in commit_shas:
        pr = by_sha.get(sha)
        if pr is None:
            yield None
            continue
        refs = {r["number"] for r in (pr.get("closingIssuesReferences") or [])}
        if not refs or not refs.issubset(managed_numbers):
            yield pr


def audit_only_churn(commit_shas: list[str], prs: list[dict], managed_numbers: set[int]) -> bool:
    """True only if every commit in range is explained by a self-fix PR.

    Any unexplained commit (direct push, non-squash merge, a PR outside the
    fetch window) or any PR closing something outside the managed set fails
    closed to False — this must never produce a false skip. An empty range is
    vacuously True.
    """
    # `any(True for _ in ...)`, not `any(...)`: the generator yields `None` for
    # a commit with no PR at all, which is falsy — a bare `any` would read that
    # unexplained commit as explained, the exact false skip this guards against.
    return not any(True for _ in _unexplained_commits(commit_shas, prs, managed_numbers))


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

    Walks the same `_unexplained_commits` sequence `audit_only_churn` does —
    one definition of the self-fix rule — but instead of failing closed to a
    bool, sums a significance score over it, weighted by each PR's conventional
    branch-type (`PR_TYPE_WEIGHTS`), so a docs-only or bug-fix-only commit
    contributes little or nothing while a feature or refactor contributes
    fully. Self-fix-explained commits never reach this loop at all, so they
    contribute nothing regardless of type.

    A commit with no matching PR at all (a direct push — never expected in
    this fleet's PR-only workflow) has no reliable LOC data, so it fails
    open to `float("inf")`: guaranteed to cross any finite threshold rather
    than silently under-counting, exactly like `audit_only_churn`'s own
    fail-closed behavior for the same case.
    """
    total = 0.0
    for pr in _unexplained_commits(commit_shas, prs, managed_numbers):
        if pr is None:
            return float("inf")
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
