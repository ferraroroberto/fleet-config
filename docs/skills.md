# Skills

The skill inventory across both tiers, why the tiers are split the way they are, and the shared `skills/_lib/` primitives the issue-workflow skills are built on. One file per skill or topic under `docs/skills/`; this page is the index, and it lists every skill directory under `skills/` and `.claude/skills/` (the acceptance run's `docs_index` check fails on a skill with no entry, or an entry with no skill).

## Linking shared docs from a skill

A skill runs from any repo and is loaded through a junction in each agent's skills dir, so a `../../docs/...` link resolves against the junction and a bare `docs/...` link against the working repo — neither reaches this repo's `docs/`. Skill markdown links shared docs by the absolute `E:/automation/fleet-config/docs/...` path, the same convention its `.venv` commands use; the acceptance run's `skill doc links` check fails a relative or dead link into `docs/`.

A skill's main `SKILL.md` holds what every invocation needs. Material only some branches reach (a mode, a repo shape, a failure path) lives in a sibling `skills/<name>/<topic>.md`, linked from a pointer that names its branch by the absolute `E:/automation/fleet-config/skills/...` path. The same check fails a dead absolute link. `/issue-finish` is split this way, and `/context-purge`'s compression contract carries the test and the `check.py --moved-to` proof that a split lost nothing.

## Tiers and scheduling

- [Skill tiers and scheduled runs](skills/tiers-and-scheduling.md) — the three tiers, the scheduled-run adapter, stall watchdog, false-success guards, by-name invocation

## Issue workflow

The trio and `/handoff-commit` are specified by their own `SKILL.md`, whose always-on description is the spec.

- [`/issue-add`](../skills/issue-add/SKILL.md) — turn a rough idea or transcript into a well-formed, labelled, self-assigned issue
- [`/issue-start`](../skills/issue-start/SKILL.md) — claim the repo, sync main, cut the branch or worktree, then plan or build by type label
- [`/issue-finish`](../skills/issue-finish/SKILL.md) — acceptance, docs, gate, PR, merge, branch teardown and tray restart
- [`/issue-yolo`](skills/issue-yolo.md) — the whole workflow in one run, no plan gate, hard validation before ship
- [`/issue-batch`](skills/issue-batch.md) — one background build-and-stop sub-agent per issue, shipped later by `/issue-finish`
- [`/handoff-commit`](../skills/handoff-commit/SKILL.md) — a copy-paste prompt handing one commit to another LLM for a sister repo

## Skills

- [`/screen`](skills/screen.md) — attach recent screenshots by count or by minutes and answer the question about them
- [`/codebase-audit`](skills/codebase-audit.md) — read-only quality sweep of a resting codebase; ledger issue, skip rules, traceability, vulture dead-code pre-pass
- [`/design-sync`](skills/design-sync.md) — web-app design-conformance detector against the fleet design system
- [`/design-review`](skills/design-review.md) — rendered-design review: live measurement, rubric score, judge, ledger, report
- [`/perf-review`](skills/perf-review.md) — phone-profile load timing against versioned budgets
- [`/e2e`](skills/e2e.md) — e2e decisioning, execution and inline suite maintenance
- [`/quick`](skills/quick.md) — trunk-commit lane below the issue threshold
- [`/retro`](skills/retro.md) — one session's mistakes sorted into mechanical (file a check) or judgement (file a rule), filed via `/issue-add` on approval
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
- [`/inspiration-scan`](skills/inspiration-scan.md) — daily digest of ideas from the sources the owner follows
- [`/fleet-10x`](skills/fleet-10x.md) — per-repo 10x-better research with an Opus synthesis
- [`/chief`](skills/chief.md) — brain of the app-launcher Board chat orchestrator
- [`/context-audit`](skills/context-audit.md) — always-on context surface audit and the context-efficiency standard
- [`/context-purge`](skills/context-purge.md) — compress CLAUDE.md and SKILL.md files, prove it with a harness
- [`/prompt-audit`](skills/prompt-audit.md) — prompt-quality lens against the vendors' current guides

## Shared primitives and gates

- [Concurrent same-repo work (claim-or-worktree)](skills/concurrent-same-repo-work.md) — `worktree_claim.py`, worktree twin, side-instance preflight, land-primary, teardown, `active_issue.py`
- [UX-conformance gate (diff-keyed design check)](skills/ux-conformance-gate.md) — `ux_surface.py`: does a diff touch a design-gated surface; `ui_proof`: the visual-proof merge gate and the per-repo `.proof/<PR>` before/after page
- [Deploy-coverage gate (merged is not shipped)](skills/deploy-coverage-gate.md) — `deploy_coverage.py`: a merged change is not a shipped change
- [Owner checks (work that needs Roberto, marked by code)](owner-check.md) — `owner_check/`: the rule, the `needs-validation` / `needs-owner` labels and comment block, the `/issue-add` · `/issue-start` · `/issue-finish` call points, back-fill
- [Gate evidence (a green bound to the tree it ran against)](skills/gate-evidence.md) — `gate_evidence.py`: a pass bound to a content hash
- [Instruction delta (prose growth as a reported fact)](skills/instruction-delta.md) — `instruction_delta/delta.py`: net instruction-line change of a branch, in the gate and the PR body
- [PR-body check (closing refs, images, attribution, ticked boxes, merge danger)](skills/pr-body-check.md) — `pr_body/check.py`: `/issue-finish` refuses a PR body that breaks a rule, naming the fix
- [Debug tags (temporary debug output never merges)](skills/debug-tags.md) — `debug_tags/check.py`: a branch that still adds a tagged debug line is refused before the PR
- [Skip delta (a gate that covered less, saying so)](skills/skip-delta.md) — `skip_delta.py`: merge verification that names what it skipped
- [Design review core (rendered facts, scored from data)](skills/design-review-core.md) — `design_review/` measure, eval, judgment, ledger, filing, fleet and inventory stages
- [Box load lock (burn proofs never overlap a gate)](skills/box-load-lock.md) — `box_lock.py`: gates share, a CPU burn runs alone
- [Stranded index locks (a read that freezes a repo)](skills/stranded-index-locks.md) — `git status` stat-cache locks and the `GIT_OPTIONAL_LOCKS=0` fix
- [Sending a file to the owner (`send_file.py`)](skills/send-file.md) — any-harness half of the launcher outbox
- [Interactive capability adaptation](skills/interactive-capability-adaptation.md) — adapting shared workflows to the session's actual tools
