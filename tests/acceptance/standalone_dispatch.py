"""Standalone pure-logic test-file dispatch (fleet-config#502, #505).

Split out of the former tests/run_acceptance.py god-module: concern (e) -- one
table (`_STANDALONE_UNIT_CHECKS`) pointing `_subprocess_unit_check`
(shared.py) at each focused `tests/test_*.py` file, each reported as a single
pass/fail check in the acceptance matrix so a standalone suite's own internal
check count never leaks into `run_acceptance.py`'s total. Adding a new
standalone suite is one row here -- no new wrapper function, no new import,
no new run_acceptance.py registration line (fleet-config#505).
"""
from __future__ import annotations

from typing import Tuple

# label, test_file, why-standalone rationale (kept from the pre-#505 wrapper
# docstrings, several of which cite the issue that motivated splitting the
# suite out).
_STANDALONE_UNIT_CHECKS: Tuple[Tuple[str, str, str], ...] = (
    ("audit_issue", "test_audit_issue.py",
     "the helper's marker / title-adoption / keep-close logic is testable "
     "on its own"),
    ("fleet_audit_scan", "test_fleet_audit_scan.py",
     "`is_fleet_repo` is testable on its own"),
    ("repo_preflight", "test_repo_preflight.py",
     "the per-repo availability gate -- classify_repo's ordering, the "
     "repos-vs-issues count split, `unknown` as its own state, and the "
     "statelessness that makes the end-of-run retry re-establish facts "
     "rather than replay a cached verdict -- is testable on its own "
     "against synthetic facts (fleet-config#642)"),
    ("design_sweep_scan", "test_design_sweep_scan.py",
     "the fleet-wide web-app gate -- `classify_web_app` over synthetic "
     "trees, the FastAPI-vs-Streamlit disambiguation, and the reuse of "
     "design_lint's token detection -- is testable on its own (fleet-config#180)"),
    ("worktree_claim", "test_worktree_claim.py",
     "the claim FSM -- atomic acquire, the worktree fallback when held, "
     "TTL stale-reclaim, and the sibling-path convention -- is testable "
     "on its own; the same file covers `service_probe.py`, the live-service "
     "capability land-primary's fourth guard calls (fleet-config#680)"),
    ("cleanup_fleet_all_flow", "test_cleanup_fleet_all_flow.py",
     "the cleanup-fleet-all workflow script's control flow -- strict lane "
     "seriality, teardown on every terminal path, and halt-on-residue -- is "
     "exercised against the real .js with stubbed agents, so the 2026-07-30 "
     "fan-out cannot be reintroduced silently (fleet-config#518)"),
    ("active_issue", "test_active_issue.py",
     "the helper's tolerant/pruned/concurrent JSON lifecycle and every "
     "workflow path that adds or removes a marker stay reachable from "
     "the one gate"),
    ("claude_progress", "test_claude_progress.py",
     "parser filtering/deduplication, child exit-code propagation, and "
     "the checked-in contract that every run-weekly.bat uses the shared "
     "adapter"),
    ("context_purge_check", "test_context_purge_check.py",
     "the purge's mechanical preservation rules -- marked-block "
     "byte-identity and quoted trigger survival in SKILL.md descriptions "
     "-- are testable on their own (fleet-config#287)"),
    ("context_audit", "test_context_audit.py",
     "/context-audit's skill-description cap gate -- the apostrophe regression "
     "pinned against `chief`'s real pre-fix text (29 reported vs 58 actual), "
     "the fleet-wide scan that finally sees sister repos' descriptions, and the "
     "three-state compliant/over-cap/unmeasured partition -- is testable on its "
     "own against a synthetic multi-repo tree (fleet-config#626)"),
    ("context_purge_gate", "test_context_purge_gate.py",
     "the skip-unchanged ledger's parse/render/diff core, testable "
     "without gh (fleet-config#287)"),
    ("context_purge_digest", "test_context_purge_digest.py",
     "the per-run digest's rendering layer -- the three-way "
     "probed/not-probed/not-recorded split that keeps an unknown from "
     "rendering as a zero, per-file (not per-repo) probe coverage, the "
     "cost-of-a-lost-directive ranking, partial-run banners that name the "
     "unreached repos, and validate() rejecting run data that would make the "
     "digest lie -- is testable on its own without gh or Slack "
     "(fleet-config#627)"),
    ("delivery_check_contract", "test_delivery_check_contract.py",
     "the shared digest-delivery post-condition -- /audit-fleet's "
     "pre-refactor exit codes pinned verbatim as a characterization "
     "baseline, /context-purge's strict-when-invoked-bare mode, and the "
     "rule both callers exist to hold: a delivery that cannot be "
     "ESTABLISHED exits non-zero rather than folding into success. Also "
     "exercises claude_progress's DELIVERY_NOT_CONFIRMED sentinel end to "
     "end, since a sentinel that silently stops matching turns a loud "
     "failure into a green run (fleet-config#627, #560)"),
    ("ux_surface", "test_ux_surface.py",
     "the UX-gate trigger -- `## UX surface` block parsing, brace "
     "expansion, glob->regex, and the diff intersection -- is testable "
     "on its own (fleet-config#195)"),
    ("deploy_coverage", "test_deploy_coverage.py",
     "/issue-finish's deploy-coverage gate -- the declared-component "
     "parser (fence-skipping, the four-bullet template), the path-token "
     "filter, the diff-touch matcher, and the three-state "
     "(`yes`/`no`/`unknown`) touch decision -- is testable on its own "
     "(fleet-config#459)"),
    ("e2e_test_audit", "test_e2e_test_audit.py",
     "the `/e2e-audit` skill's measurement layer -- CI-expectations "
     "e2e-surface parsing, test-dir resolution, near-duplicate-name "
     "clustering, size-outlier and coverage-gap detection -- is testable "
     "on its own (fleet-config#406)"),
    ("html_shot", "test_html_shot.py",
     "the shared headless-Chrome measure-then-shoot helper -- "
     "URL-scheme detection, file:// URL building, query appending, and "
     "the DIMS-log parser -- is testable on its own (fleet-config#96)"),
    ("docs_shots_plan", "test_docs_shots_plan.py",
     "the `/docs-shots` discovery + diff-intersection layer -- manifest "
     "discovery, source_globs matching, the unmapped-surface heuristic, "
     "and the README-marker precondition check -- is testable on its own "
     "(fleet-config#93)"),
    ("browser_verify", "test_browser_verify.py",
     "the visual-gate fallback -- iab-preferred backend selection, the "
     "browser-safety launch kwargs, the KEY_VIEWS x light/dark capture "
     "plan, and the distinct capability failures -- is testable on its "
     "own (fleet-config#351)"),
    ("cert_drift", "test_cert_drift.py",
     "the tailnet-cert drift truth table -- LAN-only stays clean, an "
     "already-migrated app stays clean, only a tailnet-reachable "
     "self-signed-only app trips -- is testable on its own (fleet-config#210)"),
    ("design_lint", "test_design_lint.py",
     "/design-sync v2's deterministic lenses -- spec frontmatter "
     "parsing, custom-prop extraction (P3/comment immunity), alias "
     "mapping, adoption ratios, contract checks, vendored byte-compare, "
     "and sibling duplicate detection -- are testable on their own "
     "(fleet-config#277)"),
    ("rate_gate", "test_rate_gate.py",
     "/audit-fleet's and /cleanup-fleet's proactive session-rate-limit "
     "gate -- OK/PAUSE/UNKNOWN decisions, staleness handling, and the "
     "wait-seconds computation from resets_at -- is testable on its own, "
     "with no real rate-limits.json touched. Replaces the retired "
     "audit_retry dead-man's-switch check. (fleet-config#261)"),
    ("chief_ops", "test_chief_ops.py",
     "the fleet chief's deterministic ops helper -- repo occupancy, the "
     "alive-worker count, the three dispatch refusals (occupied repo, "
     "at/over worker cap, unconfirmed yolo), the non-loopback-host "
     "guard, and the board-digest formatting -- is testable on its own, "
     "with no live launcher or `gh` call required (fleet-config#445); the "
     "same file covers `steer_delivery.py`, the say --verify classifier "
     "(fleet-config#680)"),
    ("chief_managed", "test_chief_managed.py",
     "the chief-managed session marker -- mark/is_managed, cross-sid "
     "isolation, and the 24h TTL prune -- is testable on its own, with "
     "no real chief-managed.json touched (fleet-config#443)"),
    ("dirty_tree_check", "test_dirty_tree_check.py",
     "the post-flight dirty-tree decision -- merged-mode expects a "
     "clean default branch, built-mode expects the reported feature "
     "branch with real evidence of work -- is testable on its own, with "
     "a real throwaway git repo (fleet-config#247)"),
    ("dir_holders", "test_dir_holders.py",
     "the repo-agnostic live-holder probe -- path matching, ancestor "
     "exclusion so the probe never reports itself, and a real spawned "
     "holder going LIVE then CLEAR -- is testable on its own, in a repo "
     "with no tests/e2e, no Playwright and no venv (fleet-config#571)"),
    ("git_run", "test_git_run.py",
     "the shared `resolve_default_branch_ref` helper -- symbolic-ref "
     "success, candidate probing, terminal fallback, and the "
     "`candidates=()` shape `dirty_tree_check.py` depends on -- is "
     "testable on its own, with a real throwaway git repo (fleet-config#485); "
     "so is `GIT_OPTIONAL_LOCKS=0`, which must be proven against real git "
     "rather than a mock because the change rests entirely on git's own "
     "semantics -- reads take no optional lock, output is unchanged, writes "
     "still take the real one (fleet-config#667)"),
    ("index_lock", "test_index_lock.py",
     "the stranded-`.git/index.lock` detector -- a pure verdict lattice "
     "whose two could-not-establish paths must not collapse into a settled "
     "answer, plus a real reproduction of the 2026-08-01 condition proving "
     "`git status` still exits 0 and reads clean while every write is "
     "frozen -- is testable on its own (fleet-config#667)"),
    ("payload_normalization", "test_payload_normalization.py",
     "the Grok camelCase -> Claude snake_case translation every hook "
     "now routes through, and -- the load-bearing half -- that a "
     "Claude-shaped payload is returned as the *identical object*, so a "
     "change that reaches the whole fleet the moment it merges cannot "
     "alter Claude behaviour (fleet-config#491)"),
    ("vendored_drift", "test_vendored_drift.py",
     "the /propagate-vendored [vendored]-manifest drift core -- "
     "manifest parsing, the hash-diff/classify local-drift-vs-behind-HEAD "
     "signals, and an end-to-end scan_fleet against a real throwaway "
     "scaffold + adopter repos -- is testable on its own (fleet-config#338)"),
    ("watchlist", "test_watchlist.py",
     "the due/fresh/delegated cadence logic and the seed watchlist's "
     "shape are testable on their own (fleet-config#393)"),
    ("e2e_route", "test_e2e_route.py",
     "the /e2e skill's deterministic front-end -- classifier/table/"
     "suite/web-surface probing, the byte-verbatim bootstrap with its "
     "refuse-on-divergence contract, and the route fail-safe (no "
     "classifier -> explicit unknown, broken classifier -> full) -- is "
     "testable on its own against synthetic trees (fleet-config#556)"),
    ("conversation_search", "test_conversation_search.py",
     "the resume-identity + search layer -- capture header round trip, the "
     "content signature surviving a resume, the index.json twin, and FTS "
     "build/query/prune -- runs against synthetic captures in a temp tree "
     "(fleet-config#586)"),
    ("fleet_private_backup", "test_fleet_private_backup.py",
     "the daily backup engine -- three-layer selection over a real git repo, "
     "junction traversal, hardlink dedup proven by st_nlink, retention, the "
     "sample verification, the zero-file regression guard, and three-state "
     "freshness -- needs real temp trees rather than mocks (fleet-config#590)"),
    ("wait_for_sentinel", "test_wait_for_sentinel.py",
     "/audit-fleet step 2's foreground blocking wait -- injected-clock pure "
     "logic, the real CLI's exit-0-only-if-found / exit-2-if-not contract, "
     "and the SKILL.md wiring that routes the orchestrator through it instead "
     "of a model-composed Monitor loop -- is testable on its own "
     "(fleet-config#609, reopened)"),
    ("issue_state_gate", "test_issue_state_gate.py",
     "/cleanup-fleet's and /cleanup-fleet-all's pre-dispatch state re-check "
     "-- gh-outcome classification into open/closed/unknown, and the "
     "partition proving a closed issue never reaches the dispatch bucket -- "
     "is testable on its own, with gh monkeypatched out (fleet-config#623)"),
    ("gh_issue_fetch", "test_gh_issue_fetch.py",
     "the direct-Issues-API fetch that replaces `gh search issues --owner` "
     "as the primary working-set source -- per-repo aggregation and the "
     "degrade-don't-block path when one repo's fetch fails -- is testable "
     "on its own, with gh monkeypatched out (fleet-config#623)"),
)

