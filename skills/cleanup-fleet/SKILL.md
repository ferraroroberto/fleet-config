---
name: cleanup-fleet
description: Take one bucket of audit findings (a label like documentation, claude-md-drift, bug, …), gather every open issue carrying it across the E:\automation fleet, score each for complexity, and fan out a swarm of one background agent per repo — Sonnet for easy issues (full YOLO → merged), Opus for complex ones (build → stop for your review, capped at 3 Opus agents in flight via the global Opus concurrency window). The fix-half of /audit-fleet. Use when the user wants to clear a whole category of audit work in one pass — e.g. "/cleanup-fleet documentation", "/cleanup-fleet drift", "clean up all the bugs", "/cleanup-fleet docs easy".
---

# cleanup-fleet

**Goal:** `/audit-fleet` *finds* problems and files them, bucketed into six labels. This skill *fixes* one bucket across the whole fleet in a single pass. Pick a bucket → gather every open issue carrying that label → score each issue for complexity → deploy **one background sub-agent per repo**, model-sized to the work (Sonnet for easy, Opus for complex) — then aggregate the results.

**Why one agent per repo, never two:** the audit files **exactly one managed issue per (repo, bucket)**, so a bucket is naturally *at most one issue per repo*. One issue → one repo → one agent → one branch → one PR. Two agents in the same repo would collide on the working tree and produce redundant/conflicting branches, so the skill **hard-caps at one agent per repo per run** and defers any extras.

**Two execution paths, both delegating to existing skills — don't reinvent them:**

- **Easy → Sonnet → full YOLO.** The agent runs the **`/issue-yolo <N>`** flow end-to-end: branch, build, validate hard, PR, wait for CI, merge, delete branch, tray restart. No human gate. Each agent fires its own `🚀 Shipped #N — PR · <url>` ping (the per-PR link is valuable — **not** suppressed).
- **Complex → Opus → build-and-stop.** The agent runs **`/issue-start <N> now`** → build → run the verification gate → **STOP before push/PR** (the `/issue-batch` in-place contract). You validate, then ship each with `/issue-finish`.

## Arguments

`/cleanup-fleet [<bucket>] [<mode>]` — both optional, order-independent.

**Bucket** — fuzzy-matched to one of the six audit labels (case-insensitive; voice-dictation friendly):

| Says | Label |
|------|-------|
| `documentation`, `docs`, `doc` | `documentation` |
| `drift`, `claude-drift`, `cloud drift`, `claude-md-drift`, `md-drift` | `claude-md-drift` |
| `duplication`, `dupes`, `dup`, `dupe` | `duplication` |
| `stale`, `dead`, `dead-code` | `stale` |
| `maintainability`, `maint`, `slop` | `maintainability` |
| `bug`, `bugs` | `bug` |

If **no bucket** is given → run step 2's count query, then `AskUserQuestion` listing the six buckets each with its **live open-issue count**, and let the user pick.

**Mode** — `hard` (default) or `easy` / `silent`:

- **`hard`** (default) — full sweep: Sonnet issues take the easy path, Opus issues build-and-stop. The plan is **presented for approval first**.
- **`easy` / `silent`** — only the Sonnet-scored issues, fully unattended (no approval gate). Opus-scored issues are **listed but never run** ("left for a hard run"). This is the mode to run alongside `/audit-fleet` on a schedule. Safety property: easy mode can *only ever* auto-merge work that scored genuinely simple — any hard finding routes to Opus, which easy mode never executes.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh` / `git` only — no PowerShell syntax (`&`, `$env:`, here-strings). Windows paths map as `/e/automation/...`.
- **The orchestrator only does cheap, safe work:** resolve the bucket, **one** `gh search` call, score, plan, per-repo pre-flight, fan-out, aggregate. **It never edits source, commits, pushes, or merges** — every write happens inside a spawned agent.
- **Read the issue JSON directly.** Do not spawn jq / python / awk to process the `gh` output — group, score, and select model-side, exactly like `/issue-triage`.
- **One agent per repo, period.** Never spawn two agents against the same checkout.
- **Never disturb in-progress work.** A repo that is dirty or off its default branch is skipped and reported — never stashed, never force-switched.
- **Degrade, don't block** (so `easy`/`silent` can run unattended via `claude -p`): a per-repo failure is reported and skipped; only a pre-flight failure stops the whole run.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.

### 2. Resolve bucket + mode

Parse the args (order-independent): the mode token is `hard`/`easy`/`silent`; anything else is the bucket. Map the bucket through the synonym table to its canonical label. Default mode `hard`. If no bucket token was given, fetch the per-bucket counts and ask:

```
gh search issues --owner ferraroroberto --state open --include-prs=false --limit 300 \
  --json repository,number,labels
```

Tally open issues per bucket label (drop `audit-meta` rows), then `AskUserQuestion` listing the six buckets with counts.

### 3. Fetch candidates — one `gh` call

```
gh search issues --owner ferraroroberto --state open --include-prs=false \
  --label <bucket-label> --limit 300 \
  --json repository,number,title,body,labels,url
```

Read the JSON directly. **Drop any row carrying the `audit-meta` label** — those are the per-repo `codebase-audit ledger` and the `audit-fleet digest state` issues, never actionable work. If the result is empty, print `No open <bucket> issues across the fleet 🎉` and stop.

### 4. Group by repo + enforce one-agent-per-repo

Bucket the surviving issues by `repository.name`. For each repo:

- **Exactly one candidate** → that's the issue for this repo.
- **More than one** (e.g. the audit-managed bucket issue *plus* a hand-filed one) → **select one** and **defer the rest** to keep a single branch per repo. Preference: (1) the audit-managed bucket issue (body contains `<!-- audit-managed:`) — it's the curated checklist; else (2) the smallest / clearest-acceptance one. Record the deferred issues for the plan ("caught next run").

### 5. Score each selected issue → Sonnet (easy) or Opus (complex)

Read each selected issue's title + body (for an audit bucket issue, also weigh the checklist length and the nature of its items). Two tiers, same spirit as `/issue-triage`'s S/M/L calibration collapsed to two:

- **easy → Sonnet:** narrow surface, mechanical, clear acceptance, no design decision. Doc fixes, a handful of stale-code deletions, a missing README flag, a rename, a few tightly-scoped checklist items.
- **complex → Opus:** multi-module, real design choices, a refactor, an unbounded body, or a **mixed** checklist (trivial *and* hard items together → treat the whole issue as complex; Opus absorbs the easy parts too).

When genuinely on the fence, round **up** to Opus in `hard` mode (a human will still review it) and **down**-or-defer in `easy` mode (never auto-merge something you weren't sure about).

### 6. Build + present the plan

Render one table and the headline counts:

```
/cleanup-fleet <bucket> — <mode> mode

  repo              #    title                          tier     model   path
  ----------------  ---  -----------------------------  -------  ------  -----------------
  photo-ocr         44   audit: documentation findings  easy     sonnet  YOLO → merged
  app-launcher      71   audit: documentation findings  complex  opus    build → review
  reporting         12   README missing --watch flag    easy     sonnet  YOLO → merged

  7 issues: 5 sonnet (YOLO → merged), 2 opus (build → review)
  deferred (1+ per repo): grocery-shopping#9 (hand-filed, next run)
  skipped (dirty/off-branch): website
```

- **hard mode:** present this plan and **wait for explicit approval** before spawning. The user may deselect issues or retier them. Do **not** spawn until approved.
- **easy / silent mode:** print the plan to stdout (run-log record), **skip the approval gate**, and proceed with **only the Sonnet rows**. List the Opus rows as "left for a hard run" — never spawn them.

### 7. Pre-flight per selected repo

For each repo with a selected (and, in easy mode, Sonnet) issue:

- `E:\automation\<repo>` exists. Else skip + report.
- `git -C E:\automation\<repo> status --porcelain` empty. Else **skip + report** (never stash) — drop it from the run.
- `git -C E:\automation\<repo> fetch origin` (once per repo).

No worktrees: one branch per repo means each agent works the primary checkout in place (the `/issue-batch` in-place mode). `/issue-yolo` and `/issue-start now` each cut their own branch.

### 8. Fan out — one background sub-agent per selected issue

Dispatch one background sub-agent per selected issue (`run_in_background: true`, `subagent_type: "general-purpose"`, **`model` set per the score** — `model: "sonnet"` for easy, `model: "opus"` for complex), but **bound the Opus concurrency**:

- **Sonnet (easy) agents are exempt** — spawn them all at once in a single message.
- **Opus (complex) agents go through the global Opus concurrency window** (≤3 in flight — see `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3"): dispatch up to 3, and each time one returns dispatch the next pending Opus issue until the Opus queue drains. In a mixed run only Opus agents count against the window of 3; the Sonnet swarm runs alongside, uncounted. Fewer than 3 complex issues → just spawn that many.

A single-message fan-out of many Opus agents at once trips Anthropic's server-side burst limit (`Server is temporarily limiting requests · Rate limited`; ceiling 3–4 per anthropics/claude-code#53922) — the same failure that cost the 2026-06-03 `/audit-fleet` run most of its repos.

#### 8a. Sonnet / easy prompt

```
You are clearing GitHub issue #<N> in the <repo> repo, end-to-end, in YOLO mode.
Repo root: E:\automation\<repo>. You are the only agent touching this repo.

1. cd to E:\automation\<repo>.
2. Run the /issue-yolo <N> flow in full (it skips Phase 1 since the issue
   already exists): Phase 2 branch + build, Phase 3 validate HARD (the
   non-negotiable phase — do not weaken it), Phase 4 ship (PR, wait for CI
   green unless the diff is provably CI-unrelated per /issue-yolo step 7,
   merge --delete-branch, land on main, tray restart per the repo's
   CLAUDE.md), Phase 5 fire the /issue-yolo completion ping.
   KEEP Phase 5's ping — it carries this issue's PR link and must go out.
3. If validation (Phase 3) fails at any point: STOP, do not push/merge, leave
   the branch in place, and report the failure. YOLO means "no plan gate", not
   "no safety".

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Result: MERGED (<merge-sha>) | FAILED (<phase + one-line reason>)
  - PR: <url or n/a>
  - Validation: <one line — what you ran in Phase 3>
```

#### 8b. Opus / complex prompt

```
You are working GitHub issue #<N> in the <repo> repo, then STOPPING for review.
Repo root: E:\automation\<repo>. You are the only agent touching this repo.

1. cd to E:\automation\<repo>.
2. Invoke /issue-start <N> now — handles pre-flight, issue read, CLAUDE.md
   read, main sync, branch cut, hand-off to implementation in fast mode.
3. Build the change.
4. Run the project's verification gate (per its CLAUDE.md — e.g.
   `pwsh -File scripts/verify-before-ship.ps1`).
5. STOP. Do NOT push, open a PR, merge, or run /issue-finish. This issue is
   complex enough that the user validates the approach before it ships.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Files changed: <list>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - Notes: <one or two lines if anything surprising came up>

If verification FAILS, leave the branch as-is for the user to inspect — do NOT
try to "fix" the failure by guessing; just report.
```

Substitute every `<…>` placeholder with the concrete value from steps 2–7.

### 9. Confirm fan-out and stand by

Print a single confirmation block listing every sub-agent dispatched (repo, #N, model, path) — and, if any complex issues are still queued behind the Opus window, note how many are pending. Then **stop** — do not poll, sleep, or check progress. The harness re-invokes you automatically as each background agent completes; on each Opus completion, refill the window with the next pending Opus issue (step 8) until the queue drains.

### 10. Aggregate as agents return, then the closing ping

As each sub-agent finishes, surface its report with a status mark: `✅` merged / `❌` failed for Sonnet; `📋 ready for review` / `⚠️ verification skipped` / `❌ failed` for Opus. Opus reports arrive ≤3 at a time (the window); each time one lands, dispatch the next pending Opus issue per step 8 until the complex queue is drained.

When **all** agents have returned, fire **one final** roll-up ping — the closing message for the run. The per-issue `🚀 Shipped` pings the Sonnet agents already fired are kept; this is *in addition*:

```
py C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind cleanup --summary "<bucket>" --merged <sonnet-merged-count> --review <opus-review-count>
```

(In easy mode `--review 0` — the helper drops the review clause.) Silent no-op if no Slack channel is configured; always exits 0.

Then print the final summary block:

```
Cleanup complete — <bucket> (<mode> mode)
  ✅ merged:  <repo>#<N> <pr-url>, …
  📋 review:  <repo>#<N> — cd E:\automation\<repo> && /issue-finish, …
  ❌ failed:  <repo>#<N> — <reason> (branch left for inspection)
  deferred:  <repo>#<N> (next run)

Next: validate + /issue-finish each opus branch, one at a time.
```

### 11. Stop

No follow-up actions. The user reviews + finishes each Opus branch manually with `/issue-finish`. Do **not** auto-launch `/issue-finish`.

## Hard rules

- **One agent per repo, period.** A bucket is at most one issue per repo by construction; if a repo has extras, defer them — never two agents on one checkout.
- **Sonnet path is full-YOLO-to-merged; Opus path always stops before push/PR.** Never let an Opus agent merge; never make a Sonnet agent stop early in `hard`/`easy` mode (that's what Opus is for).
- **Opus agents dispatch through the global Opus concurrency window (≤3 in flight); Sonnet agents are exempt.** Refill the window as each Opus agent returns; never a single-message fan-out of many Opus agents at once — it trips Anthropic's server-side burst rate limit (see `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3").
- **easy / silent mode never spawns Opus and never merges hard-scored work.** Opus rows are listed only. This is the unattended-safety guarantee.
- **The orchestrator never edits source, commits, pushes, or merges.** Every write happens inside a spawned agent.
- **Never disturb in-progress work.** Dirty / off-default-branch repos are skipped and reported, never stashed or force-switched.
- **Keep per-issue pings.** Sonnet agents fire their own `/issue-yolo` ping (PR link); the orchestrator's `--kind cleanup` ping is an *additional* closing roll-up, not a replacement.
- **Degrade, don't block.** A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. `easy`/`silent` must never wait on an interactive prompt.
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Where this sits:** `/codebase-audit` (per repo) and `/audit-fleet` (whole fleet) *find and file* — read-only on source. `/cleanup-fleet` *fixes* one bucket — write-capable via its agents. The split keeps both stages reviewable. `/issue-triage` remains the read-only fleet overview across *all* buckets.
- **Noise control is at execution time, by design.** The audit deliberately files generously within its materiality bar; this skill's complexity triage + (in hard mode) the approval gate are where low-value findings get deselected — so the user filters once, when deciding what to actually spend agents on, not at filing time.
- **Why compose `/issue-yolo` and `/issue-start`+gate rather than re-implement:** those skills already own the branch/build/validate/ship choreography and the per-project gate + tray logic. This skill is just the bucket selection, complexity tiering, and fan-out around them.
- **Scheduling `easy` mode:** because it degrades rather than blocks and never merges hard work, `claude -p "/cleanup-fleet documentation easy" --permission-mode bypassPermissions` is safe to run after a weekly `/audit-fleet` — the audit files the findings, the easy pass clears the mechanical ones, the rest wait for an attended `hard` run.
