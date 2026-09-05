# Fleet skill model-tier policy

## Why tiers, not model names

`/audit-fleet` and `/cleanup-fleet` used to hardcode "Sonnet" and "Opus" directly
into their orchestration text, as if those were universal concepts. The fleet is
adding Codex, Pi, and Copilot-style skill execution (and possibly Antigravity), so
the *intent* behind a delegation decision — how much capability/judgment a sub-task
needs — has to survive independent of which CLI happens to be running it. A skill
should identify the current host, then reason in **tiers**, resolving each tier to
a concrete model (and, where the host supports it, a reasoning-effort level) for
that host. This is the single source for that mapping — skills reference this
doc rather than restating a table.

## The three tiers (host-neutral)

- **`easy`** — narrow, mechanical, clear acceptance criteria, no design decision.
  Full-autonomy execution shape (e.g. `/cleanup-fleet`'s YOLO-to-merged path).
- **`hard`** — multi-module work, real design/architecture judgment, audit-grade
  reasoning, a refactor, an unbounded or mixed-complexity body. Review-gated by
  construction (build-and-stop, human approves before shipping) — **regardless of
  which model handles it**. The review gate exists because the work is
  consequential, not because the model is expensive.
- **`extreme`** — genuinely exceptional complexity. Rare by design: a well-scoped
  fleet skill should almost never resolve a sub-task to this tier.

## Concrete mapping per host

Before using a row, inspect the current session’s accepted model/effort metadata per [workflow-capabilities.md](workflow-capabilities.md). Historical CLI probes establish only that release and surface; never send an old ID or effort blindly to a native worker tool.

### Claude Code (current, primary)

| Tier | Model | Effort | Execution shape |
|---|---|---|---|
| `easy` | Sonnet | high (see caveat below) | full autonomy |
| `hard` | Opus | high (see caveat below) | build-and-stop for human review |
| `extreme` | Opus | xhigh | rare escalation; human-reviewed by construction |

**Effort caveat.** "High effort" is fleet intent, not always an enforced control.
The `Agent` tool (used by `audit-fleet`/`cleanup-fleet` for background sub-agent
spawns) exposes a `model` parameter but no per-call `effort` override — only the
top-level `claude -p --effort <level>` session flag, and the `Workflow` tool's
`agent()` `opts.effort`, can actually set reasoning effort today. So a
background-spawned sub-agent inherits whatever effort its parent session runs at;
"high" is honored at the top-level orchestrator invocation (see `run-weekly.bat`)
and awaits either a `Workflow`-based dispatch or a future `Agent`-tool `effort`
parameter to be enforceable per sub-agent.

**`easy` and `hard` now resolve to different models on Claude Code** — `easy`
stays Sonnet (cheap, full-autonomy work); `hard` is Opus again (see Decision
log below: reverted 2026-07-16, higher Opus subscription limits removed the
cost rationale for the 2026-07 change). The tier split still independently
drives execution shape (full autonomy vs. review-gated) regardless of model.

### Codex / GPT

Native Codex app/API collaboration was verified with two fresh workers on 2026-09-05; both terminal results were collected. The exact tools, model requests, slot limit and evidence are in [workflow-capabilities.md](workflow-capabilities.md#observed-bindings). This is not a blanket claim for Codex CLI or scheduled execution.

Resolve each tier from **this session's** exposed model/effort metadata, considering task complexity: easy selects an available model suitable for bounded mechanical work, hard selects available strong judgment, extreme selects the strongest suitable supported reasoning setting. Retain the tier's human-review requirement regardless of model. Do not persist a guessed permanent Astra/Luna mapping. The observed smoke requested `gpt-5.6-luna` / `low`; acceptance by the native tool is verified, provider execution is not independently attested. Inherit the parent only when appropriate and disclose controls that cannot be set. An explicit unavailable model/effort request needs clarification.

If spawn or reliable result collection is absent, use the contract's workflow-specific serial/handoff behavior. Missing fresh-review capability blocks autonomous shipping; it never permits serial self-review.

### Pi / Copilot

Interactive delegation, fresh review and model/effort controls are unknown pending the [same conformance scenarios](workflow-capabilities.md#conformance-and-evidence). Use the workflow-specific serial/handoff behavior until proven; discovery alone is not execution evidence.

### Grok (Grok Build)

Verified against grok 0.2.114 by probing the CLI itself, not from docs alone.

| Tier | Model | Effort | Execution shape |
|---|---|---|---|
| `easy` | `grok-4.5` | `low` | full autonomy |
| `hard` | `grok-4.5` | `high` | build-and-stop for human review |
| `extreme` | `grok-4.5` | `high` (ceiling) | rare escalation; human-reviewed by construction |

One model, tiered by **reasoning effort** rather than by model id — the local
install advertises a single `grok-4.5` whose menu exposes `low`/`medium`/`high`,
defaulting to `high`. Effort is settable both in the TUI and headless
(`grok -p --effort <level>`), which established the CLI's accepted effort controls for that measured release. It does not establish native worker controls on another surface.

**`hard` and `extreme` deliberately resolve to the same effort.** Grok's docs list
a canonical ladder up to `xhigh`/`max`, but this model does not advertise those
levels and the CLI **rejects them at argument-parse time** — `--effort xhigh` exits
non-zero with `unknown effort level 'xhigh'; use one of: high, medium, low`
(measured, not read). So `high` is the ceiling here, and `extreme` buys no extra
reasoning: what still separates the two tiers is the *execution shape*, which was
always the point (see "The three tiers" above — the review gate exists because the
work is consequential, not because the model is expensive). Revisit if a future
model id advertises more.

**Spawn is advertised; completed delegation remains unknown.** Grok exposes `spawn_subagent`
plus `SubagentStart`/`SubagentStop` hooks, so a scatter-gather skill is plausible
here — but no fleet skill has been driven through it, so the workflow-specific serial/handoff fallback applies until someone verifies collection and fresh context. The ≤3-concurrent-Opus cap
is a Claude-specific server-side limiter and does **not** apply to Grok.

### Antigravity

No skills surface at all today (see `cross-agent-parity.md`'s capability matrix
and `/config-map`). Not applicable until that changes.

## Concurrency cap, restated in tier terms

See `global-CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3": the
≤3-in-flight window is a property of **Opus's server-side burst limiter**
(anthropics/claude-code#53922), not of any tier name. On Claude Code today,
`easy` resolves to Sonnet — exempt from that cap and free to fan out — while
`hard` and `extreme` both resolve to Opus and bind the cap. Restated generally:
**whichever tier resolves to Opus is capped at 3 concurrent; every tier also respects the active host’s free slots and the skill’s own window.**

`/audit-fleet` additionally keeps its own ≤3-wide dispatch window as a
**session-token-budget pacing default** (a reasonable checkpoint cadence for
re-evaluating live usage — see `docs/rate-gate.md`). With `hard` back on Opus,
this window now does double duty — it's also the live Opus burst-limiter cap,
not just the pacing default — which is fine since both land on the same number;
it's a second, independent reason for that number, not a restatement of it.

## Decision log

- **2026-07 (fleet-config#250):** Claude Code's `hard` tier changed from
  Opus → **Sonnet at high effort**, on explicit user direction: Sonnet 5 is
  judged as capable as Opus 4.8 for nearly all fleet-skill work, so Opus is
  reserved for the new, rare `extreme` tier at `xhigh` effort instead. The user
  additionally confirmed `easy` should get the same Sonnet-high treatment, not
  just `hard` — so on Claude Code today the two tiers differ only in execution
  shape, not model. This is a deliberate deviation from fleet-config#250's own
  literal acceptance criterion ("hard/audit = Opus, at most 3 hard workers in
  flight") — superseded live in that issue's session, recorded here as the
  durable rationale.
- **2026-07-16:** Reverted — Claude Code's `hard` tier changed back
  Sonnet → **Opus at high effort**. Rationale: the user's subscription now
  carries higher Opus limits, removing the cost/throughput reasoning behind
  #250; for genuinely investigation/audit-grade sub-tasks (the whole point of
  `hard` tier) there's no longer a reason to default down from the stronger
  model. `easy` is untouched (still Sonnet — that tier is full-autonomy,
  mechanical work, not what prompted either change). `extreme` is untouched
  (still Opus at `xhigh` — the higher-effort, rarer escalation above `hard`).
  This re-activates the ≤3-concurrent Opus burst-limiter cap for every
  `hard`-tier spawn (see "Concurrency cap" above) — previously dormant because
  `hard` resolved to Sonnet. Same-day addition: a placeholder Codex mapping
  (`gpt-5.6`, unverified) so a `hard`/`extreme` model id exists to reference if
  Codex work ever needs one named — this does not itself establish a verified
  Codex background-fan-out surface (still serial/manual, per the Codex section
  above).
