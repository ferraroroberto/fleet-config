"""Standalone pure-logic test-file dispatch (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (e) --
one-line wrappers that each point `_subprocess_unit_check` (shared.py) at one
focused `tests/test_*.py` file and report it as a single pass/fail check in
the acceptance matrix, so a standalone suite's own internal check count never
leaks into `run_acceptance.py`'s total.
"""
from __future__ import annotations

from typing import Tuple

from acceptance.shared import _subprocess_unit_check


def _audit_issue_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/audit_issue.py's pure-logic tests as a subprocess.

    Kept standalone (not inlined here) so the helper's marker / title-adoption /
    keep-close logic is testable on its own, and reachable from the one gate.
    """
    return _subprocess_unit_check("audit_issue", "test_audit_issue.py")


def _fleet_audit_scan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/fleet_audit_scan.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue) so `is_fleet_repo` is testable on its
    own and reachable from the one gate.
    """
    return _subprocess_unit_check("fleet_audit_scan", "test_fleet_audit_scan.py")


def _design_sweep_scan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/design_sweep_scan.py's pure-logic tests as a subprocess.

    Standalone (like test_fleet_audit_scan / test_cert_drift) so the fleet-wide
    web-app gate — `classify_web_app` over synthetic trees, the FastAPI-vs-
    Streamlit disambiguation, and the reuse of design_lint's token detection —
    is testable on its own and reachable from the one gate. (fleet-config#180)
    """
    return _subprocess_unit_check("design_sweep_scan", "test_design_sweep_scan.py")


def _worktree_claim_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/worktree_claim.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue) so the claim FSM — atomic acquire, the
    worktree fallback when held, TTL stale-reclaim, and the sibling-path
    convention — is testable on its own and reachable from the one gate.
    """
    return _subprocess_unit_check("worktree_claim", "test_worktree_claim.py")


def _active_issue_unit_check() -> Tuple[int, int]:
    """Run active-issue state + workflow wiring tests as a subprocess.

    The helper's tolerant/pruned/concurrent JSON lifecycle and every workflow
    path that adds or removes a marker stay reachable from the one gate.
    """
    return _subprocess_unit_check("active_issue", "test_active_issue.py")


def _claude_progress_unit_check() -> Tuple[int, int]:
    """Run the scheduled Claude stream adapter's focused tests.

    Covers parser filtering/deduplication, child exit-code propagation, and the
    checked-in contract that every run-weekly.bat uses the shared adapter.
    """
    return _subprocess_unit_check("claude_progress", "test_claude_progress.py")


def _context_purge_check_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/context-purge/check.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue / test_ux_surface) so the purge's
    mechanical preservation rules — marked-block byte-identity and quoted
    trigger survival in SKILL.md descriptions — are testable on their own and
    reachable from the one gate. (fleet-config#287)
    """
    return _subprocess_unit_check("context_purge_check", "test_context_purge_check.py")


def _context_purge_gate_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/context-purge/gate.py's pure-logic tests as a subprocess.

    The skip-unchanged ledger's parse/render/diff core, testable without gh —
    same standalone pattern as test_context_purge_check. (fleet-config#287)
    """
    return _subprocess_unit_check("context_purge_gate", "test_context_purge_gate.py")


def _ux_surface_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/ux_surface.py's pure-logic tests as a subprocess.

    Standalone (like test_audit_issue / test_worktree_claim) so the UX-gate
    trigger — `## UX surface` block parsing, brace expansion, glob→regex, and
    the diff intersection — is testable on its own and reachable from the one
    gate. (fleet-config#195)
    """
    return _subprocess_unit_check("ux_surface", "test_ux_surface.py")


def _deploy_coverage_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/deploy_coverage.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so /issue-finish's deploy-coverage gate —
    the declared-component parser (fence-skipping, the four-bullet template),
    the path-token filter, the diff-touch matcher, and the three-state
    (`yes`/`no`/`unknown`) touch decision — is testable on its own and
    reachable from the one gate. (fleet-config#459)
    """
    return _subprocess_unit_check("deploy_coverage", "test_deploy_coverage.py")


def _e2e_test_audit_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/e2e_test_audit.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the `/e2e-audit` skill's measurement
    layer — CI-expectations e2e-surface parsing, test-dir resolution, near-
    duplicate-name clustering, size-outlier and coverage-gap detection — is
    testable on its own and reachable from the one gate. (fleet-config#406)
    """
    return _subprocess_unit_check("e2e_test_audit", "test_e2e_test_audit.py")


def _html_shot_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/html_shot.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the shared headless-Chrome
    measure-then-shoot helper — URL-scheme detection, file:// URL building,
    query appending, and the DIMS-log parser — is testable on its own and
    reachable from the one gate. (fleet-config#96)
    """
    return _subprocess_unit_check("html_shot", "test_html_shot.py")


def _docs_shots_plan_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/docs_shots_plan.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the `/docs-shots` discovery +
    diff-intersection layer — manifest discovery, source_globs matching, the
    unmapped-surface heuristic, and the README-marker precondition check — is
    testable on its own and reachable from the one gate. (fleet-config#93)
    """
    return _subprocess_unit_check("docs_shots_plan", "test_docs_shots_plan.py")


def _browser_verify_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/browser_verify.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the visual-gate fallback — iab-preferred
    backend selection, the browser-safety launch kwargs, the KEY_VIEWS x
    light/dark capture plan, and the distinct capability failures — is testable
    on its own and reachable from the one gate. (fleet-config#351)
    """
    return _subprocess_unit_check("browser_verify", "test_browser_verify.py")


def _cert_drift_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/cert_drift.py's pure-logic tests as a subprocess.

    Standalone (like test_ux_surface) so the tailnet-cert drift truth table —
    LAN-only stays clean, an already-migrated app stays clean, only a
    tailnet-reachable self-signed-only app trips — is testable on its own and
    reachable from the one gate. (fleet-config#210)
    """
    return _subprocess_unit_check("cert_drift", "test_cert_drift.py")


def _design_lint_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/design_lint.py's pure-logic tests as a subprocess.

    Standalone (like test_cert_drift) so /design-sync v2's deterministic
    lenses — spec frontmatter parsing, custom-prop extraction (P3/comment
    immunity), alias mapping, adoption ratios, contract checks, vendored
    byte-compare, and sibling duplicate detection — are testable on their own
    and reachable from the one gate. (fleet-config#277)
    """
    return _subprocess_unit_check("design_lint", "test_design_lint.py")


def _rate_gate_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/rate_gate.py's pure-logic tests as a subprocess.

    Standalone (like test_cert_drift) so /audit-fleet's and /cleanup-fleet's
    proactive session-rate-limit gate — OK/PAUSE/UNKNOWN decisions, staleness
    handling, and the wait-seconds computation from resets_at — is testable on
    its own, with no real rate-limits.json touched, and reachable from the one
    gate. Replaces the retired audit_retry dead-man's-switch check. (fleet-config#261)
    """
    return _subprocess_unit_check("rate_gate", "test_rate_gate.py")


def _chief_ops_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/chief_ops.py's pure-logic tests as a subprocess.

    Standalone (like test_rate_gate) so the fleet chief's deterministic
    ops helper -- repo occupancy, the alive-worker count, the three
    dispatch refusals (occupied repo, at/over worker cap, unconfirmed
    yolo), the non-loopback-host guard, and the board-digest formatting --
    is testable on its own, with no live launcher or `gh` call required,
    and reachable from the one gate. (fleet-config#445)
    """
    return _subprocess_unit_check("chief_ops", "test_chief_ops.py")


def _chief_managed_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/chief_managed.py's pure-logic tests as a subprocess.

    Standalone (like test_chief_ops) so the chief-managed session marker --
    mark/is_managed, cross-sid isolation, and the 24h TTL prune -- is
    testable on its own, with no real chief-managed.json touched, and
    reachable from the one gate. (fleet-config#443)
    """
    return _subprocess_unit_check("chief_managed", "test_chief_managed.py")


def _dirty_tree_check_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/dirty_tree_check.py's pure-logic tests as a subprocess.

    Standalone (like test_rate_gate) so the post-flight dirty-tree decision --
    merged-mode expects a clean default branch, built-mode expects the reported
    feature branch with real evidence of work -- is testable on its own, with a
    real throwaway git repo, and reachable from the one gate. (fleet-config#247)
    """
    return _subprocess_unit_check("dirty_tree_check", "test_dirty_tree_check.py")


def _git_run_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/git_run.py's pure-logic tests as a subprocess.

    Standalone (like test_dirty_tree_check) so the shared
    `resolve_default_branch_ref` helper -- symbolic-ref success, candidate
    probing, terminal fallback, and the `candidates=()` shape
    `dirty_tree_check.py` depends on -- is testable on its own, with a real
    throwaway git repo, and reachable from the one gate. (fleet-config#485)
    """
    return _subprocess_unit_check("git_run", "test_git_run.py")


def _payload_normalization_unit_check() -> Tuple[int, int]:
    """Run hooks/_lib.py's foreign-harness payload normalization tests.

    Covers the Grok camelCase -> Claude snake_case translation every hook now
    routes through, and -- the load-bearing half -- asserts a Claude-shaped
    payload is returned as the *identical object*, so a change that reaches the
    whole fleet the moment it merges cannot alter Claude behaviour.
    (fleet-config#491)
    """
    return _subprocess_unit_check("payload_normalization", "test_payload_normalization.py")


def _vendored_drift_unit_check() -> Tuple[int, int]:
    """Run skills/_lib/vendored_drift.py's pure-logic tests as a subprocess.

    Standalone (like test_dirty_tree_check) so the /propagate-vendored
    [vendored]-manifest drift core -- manifest parsing, the hash-diff/classify
    local-drift-vs-behind-HEAD signals, and an end-to-end scan_fleet against a
    real throwaway scaffold + adopter repos -- is testable on its own and
    reachable from the one gate. (fleet-config#338)
    """
    return _subprocess_unit_check("vendored_drift", "test_vendored_drift.py")


def _watchlist_unit_check() -> Tuple[int, int]:
    """Run .claude/skills/sota-watch/watchlist.py's pure-logic tests.

    Standalone file, same pattern as the other helpers, so the due/fresh/
    delegated cadence logic and the seed watchlist's shape are testable on
    their own and reachable from the one gate. (fleet-config#393)
    """
    return _subprocess_unit_check("watchlist", "test_watchlist.py")


