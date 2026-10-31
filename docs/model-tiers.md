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
| `easy` | Sonnet | medium | full autonomy |
| `hard` | Opus | medium by default, high where verification matters | build-and-stop for human review |
| `extreme` | Opus | xhigh | rare escalation; human-reviewed by construction |

**Picking a chief lane's model and effort.** Opus at medium is the default for a dispatched lane. Raise it to high for a bug fix in existing code, cross-repo work, security, data handling, or device-sensitive UI such as the iOS nav. Use xhigh only for a lane expected to run over 30 minutes; max stays off. Sonnet at medium takes the mechanical work: re-vendoring, propagation waves, docs and easy bugs. `chief_ops.py dispatch` carries both: `--model opus|sonnet` and `--effort low|medium|high|xhigh`.

**Effort reaches a worker only through a control that carries it**: `claude -p --effort` (the `run-weekly.bat` launchers), the `Workflow` tool's `agent()` `opts.effort`, the `Agent` tool's `effort` parameter, and the dispatch `--effort` flag once the launcher honours it. A worker spawned without one inherits its parent's effort, or the launcher's persisted per-model effort for a dispatched lane.

### Codex / GPT

Native Codex app/API collaboration was verified with two fresh workers on 2026-09-05; both terminal results were collected. The exact tools, model requests, slot limit and evidence are in [workflow-capabilities.md](workflow-capabilities.md#observed-bindings). This is not a blanket claim for Codex CLI or scheduled execution.

The opt-in fleet policy in `codex/` resolves native roles explicitly: `easy` uses Luna at xhigh effort; `normal` uses Terra at high effort; `hard` uses Sol at high effort. No rollout/thinking budget is configured, so a turn uses the model's native limits. Luna's context capacity is not configurable by the role: the installed Codex catalog reports 272k default / 872k maximum, not 1M. Astra remains available only through an explicit human choice. Skills choose a role, while explicit per-spawn model and effort overrides retain Codex precedence. Retain the tier's human-review requirement regardless of model.

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

**Chief dispatch: `easy` tier only.** The fleet chief may hand an `easy`-tier lane to Grok Build with `chief_ops.py dispatch <repo> <N> --model grok`, which posts the launcher's `grok:grok-4.5`; the launcher applies the Coding tab's Grok effort and permission mode. `hard` and `extreme` lanes stay on Claude or Codex until Grok delegation and fresh review are proven, because those tiers depend on a review shape no Grok lane has been driven through.

**Chief dispatch to Codex.** `chief_ops.py dispatch <repo> <N> --model luna` names the family, never a version: the slug (`codex:<id>`) is read from the launcher's live Codex model catalog, so a new Luna release needs no edit here. A family the catalog does not offer, or an unreadable catalog, is refused with `REFUSED=` and nothing spawns. Only Luna resolves, because Codex on a ChatGPT account rejects the other families.

On grok 1.0.70 the Claude-compat reach (`install.ps1 -VerifyGrokCompat`'s checks) passes, the fleet's `PreToolUse` guards refuse in a Grok session, and a chief-managed Grok session writes `turn-end` events to the chief inbox. Its `session-end` event is intermittent and `needs-input` is unobserved; [session-state-capability-matrix.md](session-state-capability-matrix.md) has the per-event result.

**Folder trust stalls a lane in a new repo.** An interactive Grok session opened in a folder it has never trusted stops at "Do you trust the contents of this directory?" before reading its prompt, and until trust is granted Grok also holds back the global `~/.claude/settings.json` hooks. Trust is pre-grantable: run `grok --trust` once in the repo's primary checkout (recorded in `~/.grok/trusted_folders.toml`), or launch with `--trust`. On 1.0.70 the flag is accepted but missing from `grok --help`. A grant covers the repo's subdirectories but not a sibling `<repo>-wt-<N>` worktree, which is a separate folder. A dispatched lane launches in the primary checkout, so trust is decided there at session start. Whether it still holds after `/issue-start` moves the lane into a worktree has not been observed live. Headless `grok -p` counts the folder as trusted for user-scope hooks and never prompts. Before dispatching Grok to a repo, confirm the repo's primary checkout is trusted.

### Antigravity

No skills surface at all today (see `cross-agent-parity.md`'s capability matrix
and `/config-map`). Not applicable until that changes.

## Concurrency cap, restated in tier terms

See `global-CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3": the
≤3-in-flight window is a property of **Opus's server-side burst limiter**,
not of any tier name. On Claude Code today,
`easy` resolves to Sonnet — exempt from that cap and free to fan out — while
`hard` and `extreme` both resolve to Opus and bind the cap. Restated generally:
**whichever tier resolves to Opus is capped at 3 concurrent; every tier also respects the active host’s free slots and the skill’s own window.**

`/audit-fleet` additionally keeps its own ≤3-wide dispatch window as a
**session-token-budget pacing default** (a reasonable checkpoint cadence for
re-evaluating live usage — see `docs/rate-gate.md`). Because `hard` resolves to Opus,
this window does double duty — it's also the live Opus burst-limiter cap,
not just the pacing default — which is fine since both land on the same number;
it's a second, independent reason for that number, not a restatement of it.

## Why the Claude Code mapping is what it is

- **Medium is Anthropic's own starting point.** Claude Code ships Opus 5.5 as the default model, and Opus 5.5, Sonnet 5.5 and Haiku 5.5 all default to medium effort. The same page says Opus 5.5 at medium matches or exceeds Opus 5 at high on coding and knowledge-work evals. Its level guide: medium is "day-to-day engineering with a clear scope", high is "work where verification matters or edge cases are likely", and max "may show diminishing returns and is prone to overthinking".
- **High is where verification matters.** That wording is why bug fixes, cross-repo, security, data and device UI run at high: fleet lanes merge without a human reviewing each step, so those are where a missed edge case costs most.
- **xhigh is for long lanes.** The API effort guide reserves it for long-running agentic and coding tasks over 30 minutes.
- **Sonnet at medium fits mechanical work.** Anthropic advises Sonnet 5.5 start at medium for well-specified agentic coding. Sonnet is also exempt from the Opus burst cap, so propagation waves fan out freely.
- **Effort is recalibrated per model.** A level name does not buy the same thinking on a new model, and the API guide says to run an effort sweep on your own evals rather than carry settings over. Re-check this table when a model ships, against gate pass rate and tokens on past fleet issues.
- **Community evidence is thin and agrees.** One published run of Opus 5.5 on three small tasks found medium, high and xhigh all passed the hidden edge cases, at roughly $1.69, $2.30 and $4.99; no substantial dissent turned up.

Sources: [Claude Code model config](https://code.claude.com/docs/en/model-config), [API effort guide](https://platform.claude.com/docs/en/build-with-claude/effort), [Opus 5.5 prompting guide](https://platform.claude.com/docs/en/build-with-claude/prompting-claude-opus-5-5), [effort-level comparison run](https://www.dsebastien.net/claude-code-effort-levels/).
