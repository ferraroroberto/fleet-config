"""One deterministic Python sweep of the whole fleet's audit-gate state.

Why this exists
----------------
`/audit-fleet` used to make the orchestrator LLM loop over every repo one at a
time, issuing a `git`/`gh` tool call per repo to decide skip-vs-audit (see
`audit_issue.py`'s `evaluate_repo` for the per-repo decision itself, including
self-fix-only-churn detection). That's slow, burns tool-call turns, and gives
the LLM no reason to be more careful than prose. This script does the whole
fleet in **one process**: enumerate every `ferraroroberto` repo under a root,
skip dirty/off-branch ones, sync the rest, run `evaluate_repo` per repo, and
print one JSON object bucketing every repo into `to_audit` / `unchanged` /
`self_fix` / `below_threshold` / `skipped` / `errors`. `below_threshold` is a
repo whose organic (non-self-fix) change since its last audit hasn't yet
crossed `audit_issue.py`'s weighted-LOC significance threshold — it carries
the same `significance`/`threshold` numbers `evaluate_repo` returns, so the
digest can show how close it is. The orchestrator just reads the output —
no per-repo `git`/`gh` tool calls needed for the gating step at all.

The output also carries `enumerated` (repos walked) and an `accounting` block
asserting the buckets sum back to it, so a repo can never silently drop out of
every bucket the way an unresolvable ledger baseline used to
(fleet-config#567).

CLI
---
  fleet_audit_scan.py --root <path> [--only <repo-name>] [--dry-run]

`--dry-run` is a first-class mode, not a test hook: it still fetches (so the
answer reflects the real remote state) but never runs `git pull` and never
writes to GitHub (`evaluate_repo(..., dry_run=True)` computes and reports
SKIP_SELF_FIX but skips the ledger upsert/comment). Safe to re-run any time to
preview "which repos would you audit right now."

Like `audit_issue.py`, the correctness-critical piece (`is_fleet_repo`) is
pure and unit-tested (`tests/test_fleet_audit_scan.py`) independent of the
filesystem/git/gh I/O around it. stdlib + the `git`/`gh` CLIs only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_issue  # noqa: E402
import fleet_repo_scan  # noqa: E402
import git_run  # noqa: E402

# Re-exported: `is_fleet_repo` now lives beside the crawl that uses it
# (fleet-config#561), but this module is where the unit tests and
# `design_sweep_scan` have always reached for it.
is_fleet_repo = fleet_repo_scan.is_fleet_repo


def _default_branch(repo_path: str) -> str | None:
    """Bare branch name (e.g. ``main``), no candidate probing on failure --
    routed through the shared `git_run.resolve_default_branch_ref` resolver
    with `candidates=()` to reproduce that pre-existing symbolic-ref-only
    contract (fleet-config#500)."""
    ref = git_run.resolve_default_branch_ref(Path(repo_path), candidates=(), final_fallback="")
    if not ref:
        return None
    return ref[len("origin/"):] if ref.startswith("origin/") else ref


def _current_branch(repo_path: str) -> str | None:
    try:
        return git_run.run_git_checked(["-C", repo_path, "symbolic-ref", "--short", "HEAD"])
    except SystemExit:
        return None


BUCKETS = ("to_audit", "unchanged", "self_fix", "below_threshold", "skipped", "errors")


def accounting(results: dict) -> dict:
    """Prove every enumerated repo landed in exactly one bucket.

    `enumerated` is counted at the top of the walk, before any decision; the
    buckets are counted after. A repo that falls through every branch — the
    2026-07/08 failure where an unresolvable baseline raised past all of them
    (fleet-config#567) — shows up here as a non-zero `unaccounted`, so the
    digest header's counts can never quietly fail to add up. Pure: takes the
    scan result, touches no git.
    """
    enumerated = int(results.get("enumerated", 0))
    bucketed = sum(len(results.get(b, [])) for b in BUCKETS)
    return {
        "enumerated": enumerated,
        "bucketed": bucketed,
        "unaccounted": enumerated - bucketed,
        "balanced": enumerated == bucketed,
    }


def broken_ledgers(results: dict) -> list[dict]:
    """The `to_audit` entries that are there because the ledger was unreadable.

    The plan line and the digest read this rather than re-deriving the filter
    in prose — a full whole-repo audit bought by a parse failure has to be
    visible *as* a parse failure (fleet-config#566). Pure: takes the scan
    result, touches no git.
    """
    return [e for e in results.get("to_audit", []) if e.get("reason") in audit_issue.BROKEN_LEDGER_REASONS]


def scan(root: str, only: str | None = None, dry_run: bool = False) -> dict:
    results: dict = {
        "to_audit": [], "unchanged": [], "self_fix": [], "below_threshold": [], "skipped": [], "errors": [],
        "enumerated": 0,
    }

    for d in fleet_repo_scan.iter_fleet_repos(root, only):
        name = d.name
        results["enumerated"] += 1
        repo = f"ferraroroberto/{name}"
        repo_path = str(d)

        try:
            status = git_run.run_git_checked(["-C", repo_path, "status", "--porcelain"])
        except SystemExit as exc:
            results["errors"].append({"repo": name, "reason": str(exc)})
            continue
        if status.strip():
            results["skipped"].append({"repo": name, "reason": "dirty"})
            continue

        default_branch = _default_branch(repo_path) or "main"
        current_branch = _current_branch(repo_path)
        if current_branch != default_branch:
            results["skipped"].append({"repo": name, "reason": "off-branch"})
            continue

        try:
            git_run.run_git_checked(["-C", repo_path, "fetch", "origin"])
        except SystemExit as exc:
            results["errors"].append({"repo": name, "reason": f"fetch failed: {exc}"})
            continue

        if not dry_run:
            try:
                git_run.run_git_checked(["-C", repo_path, "pull", "--ff-only"])
            except SystemExit:
                results["skipped"].append({"repo": name, "reason": "non-ff"})
                continue

        try:
            outcome = audit_issue.evaluate_repo(repo, repo_path, dry_run=dry_run)
        except SystemExit as exc:
            results["errors"].append({"repo": name, "reason": str(exc)})
            continue

        decision = outcome["decision"]
        if decision == "SKIP":
            results["unchanged"].append(name)
        elif decision == "SKIP_SELF_FIX":
            results["self_fix"].append({"repo": name, "path": repo_path, **outcome})
        elif decision == "SKIP_BELOW_THRESHOLD":
            results["below_threshold"].append({"repo": name, "path": repo_path, **outcome})
        else:
            entry = {"repo": name, "path": repo_path}
            # An AUDIT the gate was *forced* into because it couldn't read the
            # ledger must stay distinguishable from an AUDIT earned by real
            # change — it is a broken ledger to repair, not organic churn, and
            # it re-bills a full Opus pass every week until someone sees it
            # (fleet-config#566, #567).
            if outcome.get("reason") in audit_issue.BROKEN_LEDGER_REASONS:
                entry["reason"] = outcome["reason"]
                if outcome.get("baseline_sha"):
                    entry["baseline_sha"] = outcome["baseline_sha"]
                if outcome.get("ledger_issue"):
                    entry["ledger_issue"] = outcome["ledger_issue"]
            results["to_audit"].append(entry)

    results["accounting"] = accounting(results)
    return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="One-pass fleet-wide audit-gate sweep.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    print(json.dumps(scan(args.root, args.only, args.dry_run)))


if __name__ == "__main__":
    main()
