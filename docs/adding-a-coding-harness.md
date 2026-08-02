# Adding a coding harness to the fleet

Onboarding a new CLI coding agent — Codex, Pi, Copilot, Grok, whatever is next —
has been rediscovered from scratch every time, by reading the previous one's
diff. This is that procedure written down: every touchpoint, in dependency
order, with the facts that must be **probed** rather than assumed and the
verification each leg actually needs.

Scope: the `fleet-config` half (context, skills, hooks, Board state, tiers) plus
the `app-launcher` half (launch, render, restart). Read it end to end before
starting — several later steps change what you'd do in the earlier ones.

## The rule that outranks the checklist

**Probe the CLI; do not port the last harness's assumptions.** Every harness so
far has broken at least one thing the previous one established, and the failures
are silent by nature — a hook that no-ops, a state row that never appears, a
guard that reports a block it did not perform. Two concrete examples from
onboarding Grok (fleet-config#491), both of which contradicted the issue that
commissioned the work:

- The issue specified a `~/.grok/rules/` link for the global instructions.
  Unnecessary — Grok's Claude-compat layer already loads `~/.claude/CLAUDE.md`
  (confirmed: 8,720 tokens, `compatibilityStatus: "enabled"`). Building it would
  have added a second, drifting copy of the fleet's most important file.
- The issue predicted the compat shim would write Board rows misattributed to
  Claude. It wrote *nothing* — the event-name mismatch made it inert. The real
  defect was elsewhere and worse: six of seven safety guards fired and silently
  allowed commands they block for Claude.

Neither would have been caught by reading docs, and neither matched the
hypothesis. Budget for a probing session before you budget for an
implementation one.

## Step 0 — Probe the harness and write down what you measured

Do this first, in a throwaway session, and record the answers. Every later step
depends on them. Most CLIs ship an introspection command (Grok:
`grok inspect --json`; use it, it beats guessing) and bundled docs on disk
(`~/.grok/docs/`) that are more current than anything published.

Establish, empirically:

| Question | Why it decides something | How to answer it |
|---|---|---|
| Where does it read **user-scope instructions**? | Whether `global-CLAUDE.md` needs a new link or already arrives | Introspection command; check whether it already reads `~/.claude/` |
| Which **skills directories** does it scan? | Whether `skills/` needs a new junction | Introspection; look for `~/.claude/skills` or `~/.agents/skills` in the resolved list |
| Does it have a **Claude/Cursor compatibility layer**, and is it on by default? | May mean *zero* new wiring — or that the fleet's hooks are already running there unnoticed | Introspection + config reference |
| What is its **hook vocabulary**? | Which Board states it can prove | Its hooks doc; map onto `UserPromptSubmit` / `Stop` / `SessionEnd` / idle |
| What is the exact **stdin payload shape**? | Key names *and* value casing both matter | Its hooks doc, then verify against a live event |
| How does a hook **refuse** a tool call? | Exit code, stdout JSON, or both — and what it does with the codes it does not recognise | Its hooks doc's exit-code table, then verify live |
| Can a hook **substitute tool input/output**, or only observe/block? | Decides whether the context filter (and any future rewriting hook) can run there at all — Claude/Codex: `updatedInput`; Copilot: `modifiedArgs`; agy: `overwrite`; Pi: mutable `tool_call` input / modifiable `tool_result`; Grok: **neither** — allow/deny only | Its hooks doc's response-field list, then verify a real substitution live (an emitted field the harness ignores fails silently — the Grok lesson) |
| Does it fire turn-boundary hooks in **headless/print mode**? | Whether you can verify without a human at a TUI | Run it headless with debug logging |
| Which **models / effort levels** does it accept? | The `docs/model-tiers.md` row | Introspection; then actually pass each level and see which are rejected |
| Does it **self-name** sessions? Fullscreen/alt-screen? | app-launcher's renderer and title logic | ConPTY probe (see app-launcher's own notes) |

Record what you could not establish as **unknown**, not as "probably fine". A
harness fact you guessed becomes a silent failure two steps later.

### The compatibility-layer trap

Grok, Cursor, and friends increasingly ship "read the other guy's config"
shims, on by default. That is a real shortcut — but it means **the fleet's hooks
may already be running inside the new harness before you have wired anything**,
which is strictly more dangerous than not being wired at all: the hooks appear
healthy in the harness's own UI while doing nothing.

So the first thing to check is not "what do I need to add" but "what is already
running, and does it actually work there". Concretely, for each blocking guard,
drive it with the new harness's payload shape and confirm it still blocks. A
one-page A/B table (guard × harness-shape → blocked/allowed) is the cheapest
possible artefact and the one that found the whole of #491.

## Step 1 — Context: the global instructions

`global-CLAUDE.md` is the single source; every agent gets a link, never a copy
(`install.ps1`'s `$Items` table). Add a home constant and a link entry **only if
step 0 showed the harness cannot already reach `~/.claude/CLAUDE.md`**.

- Codex → `~/.codex/AGENTS.md`, Pi → `~/.pi/agent/AGENTS.md`,
  Copilot → `~/.copilot/copilot-instructions.md` — all real symlinks.
- Grok → **nothing added.** Its `[compat.claude] agents` cell reads
  `~/.claude/CLAUDE.md` directly.

A second copy of this file is a bug, not redundancy: it drifts, and the drift is
invisible until an agent follows a stale rule.

## Step 2 — Skills

Same principle. The fleet's `skills/` tree is junctioned into each agent's
auto-scanned directory; all agents use the same `SKILL.md` format, so a junction
is the entire port — there is no per-agent translation.

- `~/.agents/skills` serves Codex **and** Pi; Copilot needs its own
  `~/.copilot/skills`; Grok needs nothing (it scans both `~/.claude/skills` and
  `~/.agents/skills`, and dedupes by name).
- Verify by listing what the harness actually resolved, not by checking the link
  exists. For Grok that was 14 user-tier skills plus 13 project-tier — proof the
  junction resolves *through* the compat layer.
- Fleet-only skills under `.claude/skills/` are project-scoped by design and
  reach a harness only when its cwd is this repo. That is intentional
  (fleet-config#161), not a gap to fix.

## Step 3 — Hooks and the payload contract

The expensive step, and the one with the silent failure modes.

**Wiring.** If the harness reads `~/.claude/settings.json` (compat), you may need
no new wiring at all. Otherwise give it its own entry file in its own dialect —
Codex has `codex-hooks.json`, invoking hook modules directly (no PowerShell
shim); Claude routes through `run-hook.ps1`. Never write backslashes into a
command string that Claude will route through Git Bash.

**The reported tool name does not tell you the execution shell.** A harness may
name its shell tool `Bash` and still execute the command under PowerShell —
Codex does exactly this on Windows, and the context filter's Bash-form wrap died
there with a PowerShell ParserError until the wrap was keyed to the *invoking
harness*, not the tool name (fleet-config#541, the codex probe). Any hook that
emits a command string the harness will re-parse must determine the real shell
empirically: substitute a probe command whose parse differs by dialect and see
which shell ran it.

**Translation, not duplication.** When a harness's payload differs in shape,
normalize it at `hooks/_lib.read_stdin_json()` — the one entry point every hook
already shares — rather than teaching each hook a second dialect or shipping a
parallel adapter per hook. `normalize_payload()` is the working example.
Two properties are non-negotiable:

- **Strict pass-through for the existing shape.** `hooks/` is junctioned live
  into `~/.claude/hooks`, so a merge is fleet-wide the instant it lands, against
  running sessions. A Claude-shaped payload must return the *same object*; assert
  it in a test, not in a comment.
- **Carry the harness identity forward**, so `session_state` can attribute the
  row. Precedence is `APP_LAUNCHER_AGENT` → payload hint → `default_agent` →
  `claude`; the launcher-injected value always wins (fleet-config#345).

**Check every axis the payload feeds, not just the ones you changed.** Grok's
envelope differed on three at once: key casing (`toolName`), event-value casing
(`pre_tool_use`), and tool *identity* (`run_terminal_command`). Fixing two of
three still leaves guards inert.

**Where the harnesses' tool models genuinely differ, widen rather than guess.**
Claude splits `Bash` and `PowerShell`; Grok has one shell tool that can run
either. `safe_kill_guard` discriminates by shell to avoid false-positiving on an
echoed kill string, so a single shell tool is marked *ambiguous* and both rule
sets apply. A false block is recoverable; a missed blanket kill is not — and a
check that cannot establish a fact must say so rather than fold it into the
passing state.

**Confirm how the harness receives a refusal.** This is the leg most likely to
look finished and not be. Claude blocks on exit 2 + stderr. Grok nominally
accepts exit 2, but live it recorded our 2 as `1` and fails *open* on anything
that is not 2 — so the guard printed its refusal and the command ran. Its
documented escape hatch (a `deny` decision on **stdout**, honored regardless of
exit code) is what `_lib.block()` now emits for a Grok payload, gated on the
detected agent so Claude's stdout stays empty.

> **A guard that reports a block it did not perform is worse than no guard.** It
> is the fleet's recurring bug class — the confident wrong answer — in its most
> dangerous form. Verify a real refusal, live, in the real harness.

## Step 4 — Board state and the capability matrix

`hooks/session_state.py` is the sole writer of `sessions-state.json`. Map the
harness's lifecycle events onto `working` / `needs-you` / row-removal, then add a
column to [`session-state-capability-matrix.md`](session-state-capability-matrix.md).

- Prefer reusing `session_state.py` through normalization over writing a fourth
  adapter. Codex and Pi have adapter modules because their *entry* differs; Grok
  needed none, because Claude's wiring already reached it.
- **Never fabricate a state the harness cannot prove.** A gap degrades to the
  Board's existing `unknown`; that is a supported outcome, and an honest
  `unknown` beats a plausible guess. Grok's `idle` cell is marked unproven even
  though Grok exposes a `Notification` event, because the classifier behind it
  was never verified against Grok's payload.
- Watch for **duplicate or out-of-order lifecycle events**. Grok fires a second,
  observe-only `Stop` *after* `SessionEnd`; taken literally it resurrects the row
  that was just deleted and strands a dead session on the Board. Only a live run
  reveals this ordering.

## Step 5 — Model tiers

Add a host section to [`model-tiers.md`](model-tiers.md) mapping
`easy`/`hard`/`extreme` onto real model ids and effort levels. Two rules:

- **Pass every level to the CLI and see which are rejected.** Grok's docs list a
  canonical ladder through `xhigh`/`max`; the shipped model rejects both at
  argument-parse time. A table asserting an unaccepted level is a table that
  breaks the first scheduled run that uses it.
- Where the ceiling collapses two tiers onto one effort, say so and explain what
  still separates them — the tiers encode *execution shape* (review-gated or
  not), not just model spend.

Also state whether the harness has a verified background fan-out surface. If it
does not, the serial/manual fallback applies. The ≤3-concurrent cap is a
Claude-specific server-side limiter and does not transfer.

## Step 6 — app-launcher

The launcher half (`ferraroroberto/app-launcher`): an `src/agents.py` entry,
brand icon, flag builder, launch-router dispatch, README. Probe rather than
assume the fullscreen/alt-screen class and the self-naming behaviour — both
drive the renderer, and both differ per harness.

If the launcher injects `APP_LAUNCHER_AGENT`, confirm the value it injects
matches the string the capability matrix and `session_state` use. Note also that
the session-host scrubs inherited agent-marker env vars from hosted sessions, so
a hook must read them from its own process rather than assume inheritance.

## Step 7 — Verify, then write it down

Per leg, in the real harness, not in a test harness:

- [ ] Global instructions load (introspection reports the file and its size)
- [ ] Skills resolve (introspection lists them with their source paths)
- [ ] A representative **blocking guard actually refuses a command live**
- [ ] The Board row appears with the **correct agent**, and is removed on exit
- [ ] A session started **outside** App Launcher is attributed to the harness,
      never to Claude
- [ ] `tests/run_acceptance.py` covers the new payload shape, and those cases
      **fail against pre-fix code** — prove it by stashing the fix
- [ ] Pre-existing failures are distinguished from new ones by running the gate
      on a clean tree

Then update `README.md`, this document, the capability matrix, `model-tiers.md`,
and `docs/architecture.mmd` in the **same PR** — the anti-staleness contract this
repo applies to `.fleet.toml` applies here too.

## Cost note

Onboarding a harness through a compat layer is nearly free in wiring and *not*
free at runtime: every fleet hook that fires costs a PowerShell + Python spawn.
A measured Grok tool call ran eight `PreToolUse` hooks at roughly 650–1400 ms
each. That is the same cost Claude pays and is not a regression — but before the
payload fix it bought nothing at all. Worth knowing before adding the seventh
harness; reducing it is its own piece of work, not part of onboarding.
