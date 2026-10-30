# Skills

The skill inventory across both tiers, why the tiers are split the way they are, and the shared `skills/_lib/` primitives the issue-workflow skills are built on. One file per skill or topic under `docs/skills/`; this page is the index.

Beyond the issue-workflow trio (`/issue-add`, `/issue-start`, `/issue-finish`) and `/handoff-commit`, this repo also ships the skills below.

## Tiers and scheduling

- [Skill tiers and scheduled runs](skills/tiers-and-scheduling.md) — the three tiers, the scheduled-run adapter, stall watchdog, false-success guards, by-name invocation

## Skills

- [`/codebase-audit`](skills/codebase-audit.md) — read-only quality sweep of a resting codebase; ledger issue, skip rules, traceability, vulture dead-code pre-pass
- [`/design-sync`](skills/design-sync.md) — web-app design-conformance detector against the fleet design system
- [`/design-review`](skills/design-review.md) — rendered-design review: live measurement, rubric score, judge, ledger, report
- [`/perf-review`](skills/perf-review.md) — phone-profile load timing against versioned budgets
- [`/e2e`](skills/e2e.md) — e2e decisioning, execution and inline suite maintenance
- [`/quick`](skills/quick.md) — trunk-commit lane below the issue threshold
- [`/e2e-audit`](skills/e2e-audit.md) — on-demand e2e suite redundancy audit
- [`/docs-shots`](skills/docs-shots.md) — judgment + orchestration of a repo's visual-docs screenshots
- [`/propagate-vendored`](skills/propagate-vendored.md) — byte-for-byte re-vendor of a scaffolding component to every adopter
- [`/audit-fleet`](skills/audit-fleet.md) — scatter-gather `/codebase-audit` across every repo, one weekly digest
- [`/design-sweep`](skills/design-sweep.md) — fleet-wide unattended `/design-sync`
- [`/perf-sweep`](skills/perf-sweep.md) — weekly `/perf-review` over every web app, one digest
- [`/cleanup-fleet`](skills/cleanup-fleet.md) — fix one audit bucket across every repo, one agent per repo
- [`/cleanup-fleet-all`](skills/cleanup-fleet-all.md) — unattended all-bucket cleanup, seriality rules, deferred repos, independent-review shape
- [`/issue-finish-batch`](skills/issue-finish-batch.md) — parallel `/issue-finish` over reviewed branches
- [`/system-map`](skills/system-map.md) — regenerate the fleet architecture map
- [`/config-map`](skills/config-map.md) — regenerate the fleet config and convention map
- [`/insights-weekly`](skills/insights-weekly.md) — week-over-week diff of Claude Code's `/insights` report
- [`/learning-log`](skills/learning-log.md) — weekly journey and productivity lens over the GitHub work stream
- [`/fleet-health`](skills/fleet-health.md) — weekly hardware checkup of every fleet machine
- [`/sota-watch`](skills/sota-watch.md) — recurring state-of-the-art watch over adopted choices
- [`/fleet-10x`](skills/fleet-10x.md) — per-repo 10x-better research with an Opus synthesis
- [`/chief`](skills/chief.md) — brain of the app-launcher Board chat orchestrator
- [`/context-audit`](skills/context-audit.md) — always-on context surface audit and the context-efficiency standard
- [`/context-purge`](skills/context-purge.md) — compress CLAUDE.md and SKILL.md files, prove it with a harness
- [`/prompt-audit`](skills/prompt-audit.md) — prompt-quality lens against the vendors' current guides

## Shared primitives and gates

- [Concurrent same-repo work (claim-or-worktree)](skills/concurrent-same-repo-work.md) — `worktree_claim.py`, worktree twin, side-instance preflight, land-primary, teardown, `active_issue.py`
- [UX-conformance gate (diff-keyed design check)](skills/ux-conformance-gate.md) — `ux_surface.py`: does a diff touch a design-gated surface; `ui_proof`: the visual-proof merge gate and the per-repo `.proof/<PR>` before/after page
- [Deploy-coverage gate (merged is not shipped)](skills/deploy-coverage-gate.md) — `deploy_coverage.py`: a merged change is not a shipped change
- [Gate evidence (a green bound to the tree it ran against)](skills/gate-evidence.md) — `gate_evidence.py`: a pass bound to a content hash
- [Instruction delta (prose growth as a reported fact)](skills/instruction-delta.md) — `instruction_delta/delta.py`: net instruction-line change of a branch, in the gate and the PR body
- [Skip delta (a gate that covered less, saying so)](skills/skip-delta.md) — `skip_delta.py`: merge verification that names what it skipped
- [Design review core (rendered facts, scored from data)](skills/design-review-core.md) — `design_review/` measure, eval, judgment, ledger, filing, fleet and inventory stages
- [Box load lock (burn proofs never overlap a gate)](skills/box-load-lock.md) — `box_lock.py`: gates share, a CPU burn runs alone
- [Stranded index locks (a read that freezes a repo)](skills/stranded-index-locks.md) — `git status` stat-cache locks and the `GIT_OPTIONAL_LOCKS=0` fix
- [Sending a file to the owner (`send_file.py`)](skills/send-file.md) — any-harness half of the launcher outbox
- [Interactive capability adaptation](skills/interactive-capability-adaptation.md) — adapting shared workflows to the session's actual tools
