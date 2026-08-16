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
`self_fix` / `below_threshold` / `skipped` / `stale_lock` / `errors`.
`stale_lock` is a repo carrying a stranded `.git/index.lock` (`index_lock.py`)
— frozen against every write while every read still exits 0, so it has to be
checked for rather than inferred from the reads that keep passing
(fleet-config#667). `below_threshold` is a repo whose organic (non-self-fix)
change since its last audit hasn't yet
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
  fleet_audit_scan.py --root <path> [--only <repo-name>] [--dry-run] [--out PATH]
  fleet_audit_scan.py --root <path> [--only <repo-name>] [--dry-run] --detach [--out PATH] [--log PATH]

`--dry-run` is a first-class mode, not a test hook: it still fetches (so the
answer reflects the real remote state) but never runs `git pull` and never
writes to GitHub (`evaluate_repo(..., dry_run=True)` computes and reports
SKIP_SELF_FIX but skips the ledger upsert/comment). Safe to re-run any time to
preview "which repos would you audit right now."

`--detach` (fleet-config#609): historic sweep durations range from 345s to
1460s+ and keep growing with the fleet, well past the Bash tool's 600s
ceiling — pinning `/audit-fleet` step 2 to one synchronous call that spans the
whole sweep is what let the harness auto-background that single call and
strand a run for 10 hours with no externally observable condition for the
orchestrator to wait on (it improvised with `Monitor`/`TaskStop`, guessed
wrong, and delivered no digest). `--detach` spawns the real scan as an
independent, console-less OS process (`CREATE_NEW_PROCESS_GROUP | NO_WINDOW`,
the same survive-the-parent pattern `hooks/restart_and_verify_webapp.py`
already uses) and returns immediately, printing `LAUNCHED pid=... out=...
log=...`. The child publishes its JSON result to `out` atomically
(temp-file-then-rename) on completion, or an `{"error": ...}` payload if the
scan itself raises — so the caller has one concrete file to poll instead of
an opaque background task. `--out` alone (no `--detach`) still writes that
same sentinel from a normal synchronous run, useful for direct/manual
invocations and tests.

Like `audit_issue.py`, the correctness-critical piece (`is_fleet_repo`) is
pure and unit-tested (`tests/test_fleet_audit_scan.py`) independent of the
filesystem/git/gh I/O around it. stdlib + the `git`/`gh` CLIs only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_issue  # noqa: E402
import fleet_repo_scan  # noqa: E402
import git_run  # noqa: E402
import index_lock  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

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


BUCKETS = ("to_audit", "unchanged", "self_fix", "below_threshold", "skipped", "stale_lock", "errors")


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
        "to_audit": [], "unchanged": [], "self_fix": [], "below_threshold": [], "skipped": [],
        "stale_lock": [], "errors": [], "enumerated": 0,
    }

    for d in fleet_repo_scan.iter_fleet_repos(root, only):
        name = d.name
        results["enumerated"] += 1
        repo = f"ferraroroberto/{name}"
        repo_path = str(d)

        # Before anything reads the repo: a stranded `.git/index.lock` blocks
        # every write while leaving every read exiting 0, so `status`/`fetch`/
        # `pull` below would all pass and file this repo as healthy while it is
        # in fact frozen — nine repos spent fifteen days in exactly that state
        # (fleet-config#667). Its own bucket, never auto-deleted.
        lock = index_lock.inspect(d)
        if lock["verdict"] in index_lock.REPORTABLE_VERDICTS:
            results["stale_lock"].append({
                "repo": name, "path": repo_path, "verdict": lock["verdict"],
                "age_seconds": lock["age_seconds"], "size": lock["size"], "reason": lock["detail"],
            })
            continue
        if lock["verdict"] == "fresh":
            results["skipped"].append({"repo": name, "reason": "index-lock in flight"})
            continue
        if lock["verdict"] == "unreadable":
            results["errors"].append({"repo": name, "reason": lock["detail"]})
            continue

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


# ---- detached launch + sentinel publish (fleet-config#609) -----------------

def _default_out_dir() -> Path:
    """`E:/tmp` when it exists (this codebase's usual shared scratch location,
    e.g. the `audit-practices-ledger.md` convention in the skill docs),
    falling back to the system temp dir so this stays portable off this one
    machine (tests, a future non-Windows box)."""
    fleet_tmp = Path("E:/tmp")
    return fleet_tmp if fleet_tmp.is_dir() else Path(tempfile.gettempdir())


def default_out_path() -> Path:
    """A fresh, unique sentinel path per invocation — two overlapping runs
    (a manual retry alongside a still-live scheduled one) must never collide
    on a fixed name and silently read each other's stale result."""
    return _default_out_dir() / f"fleet-audit-scan-{os.getpid()}-{time.time_ns()}.json"


def write_result(out_path: Path, payload: dict) -> None:
    """Atomically publish `payload` as JSON to `out_path`.

    Same temp-then-rename discipline `worktree_claim.py`'s `_publish_claim`
    uses for its lock dir: a poller must never be able to observe a partially
    written file. `Path.replace` (== `os.replace`) is an atomic *overwrite* on
    Windows too — unlike `Path.rename`, which raises if the destination
    already exists — so this is safe even against a stale sentinel left by a
    prior crashed run at the same path.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f"{out_path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(out_path)


def run_and_publish(root: str, only: str | None, dry_run: bool, out_path: Path) -> dict:
    """Run the sweep and always leave a sentinel at `out_path` — success or
    error — so a poller waiting on that path never waits past a scan that
    raised. `scan()` already turns every per-repo failure into an `errors`
    bucket entry; this only guards the sweep-level exception a programming
    bug would raise, which is otherwise invisible to a detached child no one
    is watching interactively.
    """
    try:
        results = scan(root, only, dry_run)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        write_result(out_path, {"error": f"{type(exc).__name__}: {exc}"})
        raise
    print(json.dumps(results))
    write_result(out_path, results)
    return results


def launch_detached(child_argv: list[str], log_path: Path) -> int:
    """Spawn this module (re-invoked without `--detach`) as an independent,
    console-less process that outlives this call — the same
    `CREATE_NEW_PROCESS_GROUP | NO_WINDOW` pattern already used to survive a
    hook's own exit (`hooks/restart_and_verify_webapp.py`'s `_start_tray`).
    Does not wait for the child; returns its pid.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), *child_argv],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | NO_WINDOW,
            close_fds=True,
        )
    return proc.pid


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="One-pass fleet-wide audit-gate sweep.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=None,
                     help="atomically publish the JSON result to this path (in addition to stdout)")
    ap.add_argument("--detach", action="store_true",
                     help="run the sweep in a detached background process and return immediately "
                          "(fleet-config#609); prints LAUNCHED pid=... out=... log=...")
    ap.add_argument("--log", type=Path, default=None,
                     help="with --detach, redirect the child's stdout/stderr here (default: <out>.log)")
    args = ap.parse_args(argv)

    if args.detach:
        out_path = args.out or default_out_path()
        log_path = args.log or out_path.with_suffix(out_path.suffix + ".log")
        # A poller trusts "the sentinel exists" as "this run's result" — never
        # launch onto a leftover from a previous run at the same path.
        if out_path.exists():
            out_path.unlink()
        child_argv = ["--root", args.root, "--out", str(out_path)]
        if args.only:
            child_argv += ["--only", args.only]
        if args.dry_run:
            child_argv.append("--dry-run")
        pid = launch_detached(child_argv, log_path)
        print(f"LAUNCHED pid={pid} out={out_path} log={log_path}")
        return

    if args.out:
        run_and_publish(args.root, args.only, args.dry_run, args.out)
    else:
        print(json.dumps(scan(args.root, args.only, args.dry_run)))


if __name__ == "__main__":
    main()
