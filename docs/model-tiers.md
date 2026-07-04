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

### Claude Code (current, primary)

| Tier | Model | Effort | Execution shape |
|---|---|---|---|
| `easy` | Sonnet | high (see caveat below) | full autonomy |
| `hard` | Sonnet | high (see caveat below) | build-and-stop for human review |
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

**Note that `easy` and `hard` resolve to the same model on Claude Code today.**
The tier split still matters — it drives execution shape (full autonomy vs.
review-gated), not model choice. That's a deliberate simplification: see the
Decision log below.

### Codex / GPT

No verified model-id or effort-level convention exists anywhere in this repo as
of this writing, and no confirmed background/scheduled skill-fan-out surface
comparable to Claude Code's `Agent` spawn is documented for Codex either.
**Fallback: serial, manual.** A Codex session running `/audit-fleet` or
`/cleanup-fleet` should work the per-repo list sequentially in the same session
rather than attempting to spawn background workers. This section gets exactly one
new row the day a real Codex background-agent surface is verified — not a
parallel doc.

### Pi / Copilot

Same as Codex: no verified model/effort convention, no known background
fan-out surface in this repo today. Serial/manual fallback, same as above.

### Antigravity

No skills surface at all today (see `README.md`'s cross-agent capability matrix
and `/config-map`). Not applicable until that changes.

## Concurrency cap, restated in tier terms

See `global-CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3": the
≤3-in-flight window is a property of **Opus's server-side burst limiter**
(anthropics/claude-code#53922), not of any tier name. On Claude Code today,
`easy` and `hard` both resolve to Sonnet — exempt from that cap and free to fan
out — so the cap currently only binds `extreme`-tier spawns. Restated generally:
**whichever tier resolves to Opus on the current host is capped at 3 concurrent;
every other tier fans out freely.**

`/audit-fleet` additionally keeps its own ≤3-wide dispatch window as a
**session-token-budget pacing default** (a reasonable checkpoint cadence for
re-evaluating live usage — see `docs/rate-gate.md`), independent of the Opus cap
above — that's a second, separate reason for the same number, not a restatement
of it.

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
