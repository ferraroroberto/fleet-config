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

Subcommands (plus `gate` and `ledger-write`, documented on their functions):

  get    --repo OWNER/NAME --kind KIND
         -> prints JSON {"number": N|null, "body": "...", "duplicates": [n,...]}
         The skill reads the existing body, merges its findings, then calls upsert.

  upsert --repo OWNER/NAME --kind KIND --title T --body-file F [--label L]
         [--reopen] [--dry-run] [--verify-quotes REPO_PATH]
         0 matches -> create · 1 -> edit · >1 -> edit lowest, close the rest as
         duplicates. Stamps the marker. Prints the canonical issue URL.
         `--reopen` extends the 0-match case to the CLOSED issues before
         filing a new one — opt-in, for the one kind whose closed state is a
         meaningful "nothing outstanding" rather than "done forever".
         `--dry-run` prints the plan (`ACTION=edit|create ISSUE= DUPLICATES=`)
         and the marker-stamped body after `BODY:`, and writes nothing.
         `--verify-quotes` refuses the whole write when any finding's verbatim
         quote is not in its file (`audit_quote.py`, fleet-config#960).

         A snapshot-column bucket kind is also stamped with its recurrence
         block (fleet-config#1513, see `recurrence`).

  recurrence --repo OWNER/NAME [--json]
         Per bucket, whether the last two `<!-- audit-snapshot -->` ledger
         comments were both non-zero: `RECURRENCE=ok|unknown` then one
         `BUCKET=<kind> STATE=recurring|not-recurring|unknown` line each.
         Exit 0 ok, 2 unknown.

  structural-gate --repo-path P --base REF --issue-body-file F [--scaffold DIR]
         For a recurring bucket's cleanup branch: `STRUCTURAL=pass|fail|n/a|
         unknown` from the `Structural-check:` line(s) in the branch's
         commit bodies, with the check's `SCOPE=` (`structural.scope`).
         Exit 0 pass/n/a, 1 fail, 2 unknown.

  promote-check --name RULE --repo-pr REPO#PR --path P
         One deduped project-scaffolding issue per generalizable rule name:
         `PROMOTION=created|commented|unknown URL=` (`structural.promotion`).
         Exit 0 created/commented, 2 unknown.

  close  --repo OWNER/NAME --kind KIND --comment TEXT
         Closes the open managed issue for that kind with the comment, so an
         open issue can carry "there is live work here" as its own signal.
         Idempotent: prints `CLOSED=none` when there was nothing open.

Identity is the marker `<!-- audit-managed: kind=<kind> -->`. Pre-existing issues
that predate the marker are adopted by their stable title (see ``title_matches``),
stamped on first edit — so no separate migration pass is needed.

stdlib + the `gh` CLI only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from structural import promotion  # noqa: E402
from structural import scope as structural_scope  # noqa: E402
from structural.scope import structural_verdict  # noqa: E402
from audit_ledger.records import (  # noqa: E402
    apply_recurrence_block,
    audit_only_churn,
    BROKEN_LEDGER_REASONS as BROKEN_LEDGER_REASONS,
    bucket_issue_numbers,
    BUCKET_KINDS,
    bucket_recurrence,
    commits_since,
    default_branch_sha as default_branch_sha,
    DEFAULT_SIGNIFICANCE_THRESHOLD,
    DESIGN_REVIEW_TITLE as DESIGN_REVIEW_TITLE,
    ensure_marker,
    has_marker,
    is_snapshot as is_snapshot,
    KINDS,
    ledger_decision,
    _LEDGER_MARKER as _LEDGER_MARKER,
    marker_for as marker_for,
    _MARKER_RE as _MARKER_RE,
    normalize_ledger_body,
    parse_ledger,
    parse_snapshot as parse_snapshot,
    PERF_REVIEW_TITLE as PERF_REVIEW_TITLE,
    plan,
    pr_weight as pr_weight,
    recordable_ledger_sha,
    recurrence_at_upsert,
    recurrence_state_of,
    render_ledger_body,
    render_recurrence_block,
    rubric_sha as rubric_sha,
    rubric_sha_of_path,
    sha_is_on_default_branch as sha_is_on_default_branch,
    SNAPSHOT_COLUMNS,
    snapshot_rows,
    title_matches,
    unexplained_weighted_loc,
    _unknown,
    UNPARSEABLE_LEDGER,
    UNRESOLVABLE_BASELINE,
)

# ---- gh plumbing ----------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    # Shared plumbing: UTF-8 decoding, NO_WINDOW, and the
    # encoding/console-flash gotchas are all owned by git_run.run_gh now.
    return git_run.run_gh(args)


# A 5xx / timeout-shaped gh failure is the server or the network, not our
# request — retrying once after a short backoff clears it in practice (seen
# for pdf-to-markdown during a fleet sweep). A genuine 4xx
# (auth, not-found, bad args) is never transient, so it still fails on the
# first attempt.
_TRANSIENT_GH_RE = re.compile(
    r"(?i)(http\s*5\d\d|timed?\s*out|timeout|connection\s*reset|i/o\s*timeout|"
    r"deadline\s*exceeded|temporarily\s*unavailable|EOF)"
)
_GH_RETRY_BACKOFF_SECONDS = 2.0

# `gh ... --limit` caps, both stated bounds rather than measured ones.
# OPEN_ISSUE_FETCH_LIMIT is well above any repo's open-issue count, so the
# managed-issue lookup sees every candidate. MERGED_PR_FETCH_LIMIT is the
# self-fix-churn window: `audit_only_churn` fails closed on any PR outside it,
# so this number decides how long a repo stays audit-eligible.
OPEN_ISSUE_FETCH_LIMIT = 300
MERGED_PR_FETCH_LIMIT = 100


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
        "--limit", str(OPEN_ISSUE_FETCH_LIMIT), "--json", "number,title,body",
    ])
    return json.loads(out) if out else []


def _list_closed(repo: str, kind: str) -> list[dict]:
    """Closed issues carrying `kind`'s marker, for the one kind whose open/closed state carries meaning.

    `cleanup-deferred` is closed when a run ends with nothing deferred, so the
    *next* run that does have something to defer must find and reopen that same
    issue rather than filing a second one. Every other kind lives its whole life
    open and never consults this (fleet-config#642).

    Filtered server-side on the marker rather than taking the N most recent
    closed issues: a busy repo closes more than any fixed window within weeks, so
    the unfiltered window slid past the managed issue and a duplicate was filed
    instead of a reopen (fleet-config#1303). `plan` still re-checks each hit.
    """
    out = gh([
        "issue", "list", "--repo", repo, "--state", "closed",
        "--search", f'"audit-managed: kind={kind}" in:body',
        "--limit", "100", "--json", "number,title,body",
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

def get_managed(repo: str, kind: str) -> dict:
    """`{"number": N|None, "body": str, "duplicates": [n]}` for the managed issue of `kind`.

    The Python entry point behind `get` (fleet-config#974): a caller that
    merges a body in-process (`design_review.filing`) reads the existing one
    here instead of shelling out to its own CLI.
    """
    keep, dupes = plan(_list_open(repo), kind)
    body = ""
    if keep is not None:
        body = gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
    return {"number": keep, "body": body, "duplicates": dupes}


def cmd_get(repo: str, kind: str) -> None:
    print(json.dumps(get_managed(repo, kind)))


def upsert_issue(repo: str, kind: str, title: str, body: str, label: str | None, reopen: bool = False) -> str:
    """The public upsert (fleet-config#974) — the same create / edit / collapse-strays path as `upsert`."""
    return _upsert_issue(repo, kind, title, body, label, reopen)


def _upsert_issue(
    repo: str, kind: str, title: str, body: str, label: str | None, reopen: bool = False
) -> str:
    keep, dupes = plan(_list_open(repo), kind)
    if keep is None and reopen:
        # Opt-in, never the default: `security`'s managed issue is *closed* on
        # fix-merge by design, so a blanket reopen would resurrect it.
        keep, _closed_dupes = plan(_list_closed(repo, kind), kind)
        if keep is not None:
            gh(["issue", "reopen", str(keep), "--repo", repo])
    tmp = _write_tmp(ensure_marker(body, kind))

    # `delete=False` means nothing reclaims this but us. One weekly /audit-fleet
    # pass upserts a ledger plus up to eight bucket issues across ~39 repos, so
    # an un-unlinked temp file here is hundreds of orphaned `.md` files in
    # %TEMP% every week, forever. `finally`, not a trailing
    # unlink: `gh` raising must not be what decides whether we clean up.
    try:
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
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return url


def cmd_upsert(
    repo: str, kind: str, title: str, body: str, label: str | None, reopen: bool = False,
    dry_run: bool = False, verify_quotes: str | None = None,
) -> None:
    # A ledger is a machine contract, so it is validated and normalized here
    # rather than trusted from the caller's markdown — `--kind ledger` through
    # this path used to accept anything, which is how an unreadable block
    # reached three repos.
    if kind == "ledger":
        body = normalize_ledger_body(body)
    if verify_quotes is not None:
        # The one content check this helper runs, and only on request: a
        # finding whose verbatim quote isn't in the named file is refused
        # before anything is written. The check itself
        # lives in `audit_quote.py`.
        from audit_ledger import audit_quote
        bad = [v for v in audit_quote.check_body(body, Path(verify_quotes)) if v.status != "VERIFIED"]
        if bad:
            for v in bad:
                print(f"{v.status} {v.path}:{v.line}", file=sys.stderr)
            sys.exit(f"QUOTES=unverified count={len(bad)}: remove or fix these findings; nothing was written")
    if kind in SNAPSHOT_COLUMNS.values():
        # Stamped in the tool, not the skill prose (same principle as
        # `render_ledger_body`): every bucket upsert carries its recurrence.
        rec = upsert_recurrence(repo, kind)
        body = apply_recurrence_block(body, render_recurrence_block(rec))
    if dry_run:
        print("\n".join(dry_run_lines(repo, kind, title, body)))
        return
    print(_upsert_issue(repo, kind, title, body, label, reopen))


def _fetch_snapshot_rows(repo: str) -> tuple[Optional[list[Optional[dict]]], Optional[str]]:
    """`(rows, None)` from the repo's ledger comments, or `(None, reason)` when they can't be read."""
    try:
        keep, _dupes = plan(_list_open(repo), "ledger")
        if keep is None:
            return None, "no-ledger"
        out = gh([
            "api", f"repos/{repo}/issues/{keep}/comments", "--paginate",
            "--jq", ".[] | {created_at, body}",
        ])
    except SystemExit:
        return None, "gh-error"
    comments = [json.loads(line) for line in out.splitlines() if line.strip()]
    return snapshot_rows(comments), None


def upsert_recurrence(repo: str, kind: str) -> dict:
    rows, reason = _fetch_snapshot_rows(repo)
    if rows is None:
        return {"state": "unknown", "reason": reason}
    return recurrence_at_upsert(rows, kind)


def recurrence_lines(result: dict) -> list[str]:
    """The `recurrence` subcommand's line output — all seven buckets, every time."""
    if result["state"] != "ok":
        head = f"RECURRENCE=unknown REASON={result['reason']}"
    else:
        head = (f"RECURRENCE=ok ROWS={result['rows']} LATEST={result['latest']} "
                f"PREVIOUS={result['previous']}")
    lines = [head]
    for kind, b in result["buckets"].items():
        line = f"BUCKET={kind} STATE={b['state']}"
        if "counts" in b:
            line += f" COUNTS={b['counts'][0]},{b['counts'][1]}"
        lines.append(line)
    return lines


def cmd_recurrence(repo: str, as_json: bool) -> int:
    rows, reason = _fetch_snapshot_rows(repo)
    result = _unknown(reason) if rows is None else bucket_recurrence(rows)
    print(json.dumps(result) if as_json else "\n".join(recurrence_lines(result)))
    return 0 if result["state"] == "ok" else 2


def structural_gate(repo_path: str, base: str, issue_body: str,
                    scaffold: Path = structural_scope.DEFAULT_SCAFFOLD) -> dict:
    recurrence = recurrence_state_of(issue_body)
    if recurrence is None or recurrence == "not-recurring":
        return {"state": "n/a"}
    log = git_run.run_git(["-C", repo_path, "log", "--format=%B", f"{base}..HEAD"])
    diff = git_run.run_git(["-C", repo_path, "diff", "--name-only", f"{base}...HEAD"])
    switched_on = structural_scope.branch_switched_on(repo_path, base)
    if log.returncode != 0 or diff.returncode != 0 or switched_on is None:
        return {"state": "unknown", "reason": "git-error"}
    catalog, _reason = structural_scope.read_catalog(scaffold)
    return structural_verdict(recurrence, log.stdout or "", (diff.stdout or "").splitlines(),
                              catalog, switched_on)


def cmd_structural_gate(repo_path: str, base: str, issue_body: str,
                        scaffold: Path = structural_scope.DEFAULT_SCAFFOLD) -> int:
    v = structural_gate(repo_path, base, issue_body, scaffold)
    print(structural_scope.verdict_line(v))
    return {"pass": 0, "n/a": 0, "fail": 1}.get(v["state"], 2)


def cmd_promote_check(name: str, repo_pr: str, path: str) -> int:
    """File or extend the one scaffold promotion issue for a generalizable check (`structural.promotion`)."""
    result = promotion.promote(name, repo_pr, path, gh)
    print(promotion.result_line(result))
    return 0 if result["state"] in ("created", "commented") else 2


def dry_run_lines(repo: str, kind: str, title: str, body: str) -> list[str]:
    """What `upsert --dry-run` prints: the plan (which issue would be edited or
    created, which strays collapsed) and the marker-stamped body — the one
    read-only `gh` call is the open-issue listing. Nothing is written
    (fleet-config#974: a filing path must be provable without touching a
    real issue)."""
    keep, dupes = plan(_list_open(repo), kind)
    return [
        "DRY_RUN=1",
        f"ACTION={'edit' if keep is not None else 'create'}",
        f"ISSUE={keep if keep is not None else 'none'}",
        f"DUPLICATES={','.join(str(n) for n in dupes) or 'none'}",
        f"TITLE={title}",
        "BODY:",
        ensure_marker(body, kind),
    ]


def cmd_close(repo: str, kind: str, comment: str) -> None:
    """Close the managed issue for `kind`, letting open/closed carry the signal.

    Written for `cleanup-deferred` (fleet-config#642): a tracking issue that
    stays open saying "nothing to do" is a zombie, and it trains a reader to
    skim past exactly the issue that is supposed to catch their eye when it
    does have content. So an *open* `cleanup-deferred` issue always means
    "there is unprocessed work" -- never "the last run had nothing to say".

    Idempotent and never an error: no open managed issue for this kind means
    the state is already what the caller asked for. Prints `CLOSED=<url>` or
    `CLOSED=none`, so a caller can report which of the two actually happened
    rather than assuming.
    """
    keep, _dupes = plan(_list_open(repo), kind)
    if keep is None:
        print("CLOSED=none")
        return
    gh(["issue", "close", str(keep), "--repo", repo, "--comment", comment])
    print("CLOSED=" + gh(["issue", "view", str(keep), "--repo", repo, "--json", "url", "-q", ".url"]))


def _fetch_merged_prs(repo: str) -> list[dict]:
    out = gh([
        "pr", "list", "--repo", repo, "--state", "merged", "--limit", str(MERGED_PR_FETCH_LIMIT),
        "--json", "number,mergeCommit,closingIssuesReferences,headRefName,additions,deletions",
    ])
    return json.loads(out) if out else []


def _churn_decision(repo: str, repo_path: str, ledger: dict, commit_count: int,
                    current_rubric: str) -> tuple[str, list[int], float | None]:
    """Decide on commits since the ledger: self-fix churn, below threshold, or a re-audit.

    A rubric mismatch does not short-circuit to AUDIT here: if the only commits
    are self-fix PRs, one of them may have edited CLAUDE.md itself (resolving a
    claude-md-drift finding), which is still churn. It matters only when the
    fixes cannot explain it, which `ledger_decision` captures by falling through
    to AUDIT whenever `self_fix` is False. `--first-parent` collapses each merged
    PR to the one commit that landed on the mainline (squash or merge commit),
    so repos on regular "Merge pull request" merges match their self-fix PRs too.
    """
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

    self_fix = audit_only_churn(commit_shas, prs, managed)
    significance = None if self_fix else unexplained_weighted_loc(commit_shas, prs, managed)
    decision = ledger_decision(
        commit_count, ledger["rubric"], current_rubric, self_fix=self_fix, significance=significance
    )
    return decision, sorted(referenced & managed), significance


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
        decision, closed_issues, significance = _churn_decision(
            repo, repo_path, ledger, commit_count, current_rubric)

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
    u.add_argument(
        "--reopen", action="store_true",
        help="when no OPEN managed issue exists, reopen the closed one instead "
             "of filing a new one (cleanup-deferred; never for `security`)",
    )
    u.add_argument(
        "--dry-run", action="store_true",
        help="print the plan (edit N / create) and the would-be body; write nothing",
    )
    u.add_argument(
        "--verify-quotes", default=None, metavar="REPO_PATH",
        help="refuse (write nothing) unless every unticked, non-carried finding's "
             "Quote: is found in its file under REPO_PATH (audit_quote.py, #960)",
    )

    cl = sub.add_parser("close")
    cl.add_argument("--repo", required=True)
    cl.add_argument("--kind", required=True, choices=KINDS)
    cl.add_argument("--comment", required=True)

    gt = sub.add_parser("gate")
    gt.add_argument("--repo", required=True)
    gt.add_argument("--repo-path", required=True)

    lw = sub.add_parser("ledger-write")
    lw.add_argument("--repo", required=True)
    lw.add_argument("--repo-path", required=True)

    rc = sub.add_parser("recurrence")
    rc.add_argument("--repo", required=True)
    rc.add_argument("--json", action="store_true")

    sg = sub.add_parser("structural-gate")
    sg.add_argument("--repo-path", required=True)
    sg.add_argument("--base", required=True, help="e.g. origin/main")
    sg.add_argument("--issue-body-file", required=True)
    sg.add_argument("--scaffold", type=Path, default=structural_scope.DEFAULT_SCAFFOLD,
                    help="project-scaffolding checkout whose origin default branch holds the catalog")

    pc = sub.add_parser("promote-check")
    pc.add_argument("--name", required=True, help="the generalizable check's rule name")
    pc.add_argument("--repo-pr", required=True, help="<repo>#<pr> that merged the check")
    pc.add_argument("--path", required=True, help="the check's repo-relative path")

    args = ap.parse_args(argv)
    if args.cmd == "get":
        cmd_get(args.repo, args.kind)
    elif args.cmd == "upsert":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        cmd_upsert(args.repo, args.kind, args.title, body, args.label, args.reopen, args.dry_run,
                   args.verify_quotes)
    elif args.cmd == "close":
        cmd_close(args.repo, args.kind, args.comment)
    elif args.cmd == "gate":
        cmd_gate(args.repo, args.repo_path)
    elif args.cmd == "ledger-write":
        cmd_ledger_write(args.repo, args.repo_path)
    elif args.cmd == "recurrence":
        sys.exit(cmd_recurrence(args.repo, args.json))
    elif args.cmd == "structural-gate":
        with open(args.issue_body_file, encoding="utf-8") as fh:
            body = fh.read()
        sys.exit(cmd_structural_gate(args.repo_path, args.base, body, args.scaffold))
    elif args.cmd == "promote-check":
        sys.exit(cmd_promote_check(args.name, args.repo_pr, args.path))


if __name__ == "__main__":
    main()
