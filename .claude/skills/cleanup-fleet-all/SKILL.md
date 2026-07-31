---
name: cleanup-fleet-all
description: Unattended, all-bucket sibling of /cleanup-fleet — builds, validates, and ships every open cleanup issue across all eight queued audit buckets in one overnight pass, no human in the loop. Use for a fully unattended fleet-wide cleanup run — e.g. "/cleanup-fleet-all", "clean up the whole fleet overnight", "run cleanup on all buckets unattended". Runs headless via a scheduled claude -p job.
---

# cleanup-fleet-all

**Goal:** `/cleanup-fleet` processes one bucket at a time and, in its default `hard` mode, stops for a human to approve the plan and to review hard-tier work before merge. This skill is the genuinely unattended sibling: it walks **all eight queued** audit buckets, serially, and ships every issue with **no human review gate at all** — replaced by an independent validator agent instead of a human. No single agent both builds and ships its own work unchecked. (`security` is never queued — `/codebase-audit` self-heals it inline — and `cert-drift`, `/design-sync`'s other kind, is review-only, never auto-migrated; nothing here touches either.)

**Four agents per issue, never fewer:**

1. **Build** — implement the fix in a forced worktree and run the project's verification gate, then **stop** before shipping.
2. **Validate** — a fresh, independent agent with no memory of the build. Re-runs the verification gate itself and judges, leniently, whether the diff actually addresses the issue.
3. **Execute** — ships an already-validated branch: push, PR, CI, merge, tray restart.
4. **Teardown** — the terminal step of **every** lane, whatever the outcome: comments the failure reason + any WIP SHA onto the GitHub issue (non-merged lanes only), removes the worktree via `worktree_claim.py remove-worktree`, releases the claim, deletes the branch, and **verifies** the repo is back on a clean default branch with no worktree and no stray branch.

A failed validation retries the build **once** (feeding it the validator's feedback verbatim), for a hard cap of **2 rounds**; a second failure escalates the issue. **Escalation is not "leave the branch for a human"** — the open GitHub issue plus the teardown agent's comment on it *is* the durable record; the branch and worktree are torn down like any other lane (fleet-config#518).

**One bucket at a time, one repo at a time.** Buckets have always been serial; issues *within* a bucket used to fan out in parallel, and on 2026-07-30 that concurrency — multiplied by an escalation path that tore nothing down — left 11 stray worktrees, two primaries off `main`, and a fleet that had to be wiped by hand the next morning. Lanes are now strictly serial: the next repo does not start until the current lane has run all four agents and its repo is verified clean. At most one worktree exists at any instant.

**Halt on residue.** If teardown cannot return a repo to a clean state, the run **stops there** — it does not start the next lane. Because lanes are serial, exactly one repo is ever affected, so a halted run is one command to recover from; continuing is how one forgotten worktree became eleven.

**Never a primary checkout.** Every build/validate/execute agent is told to force worktree mode (`worktree_claim.py acquire … --force-worktree`) for every repo, no exceptions and no special-cased list — a *running* app (the launcher webapp, home-automation's tray) or a live junction (`fleet-config`'s own `hooks/` + `skills/` into every `~/.claude`) is not a claim holder, so an unattended agent otherwise legitimately wins `MODE=primary` and edits files a live process is serving (fleet-config#515). The same briefs state that a live-e2e guard refusal is a hard STOP: `E2E_LIVE=1` or any equivalent override is forbidden.

All of the actual retry/ship decision-making lives in **`.claude/workflows/cleanup-fleet-all.js`**, a Workflow script — not in this SKILL.md and not in a fourth "orchestrator" agent. That decision (retry vs. ship vs. escalate) is a fixed lookup on each agent's own schema-validated verdict (`verification`, `retryable`, `pass`, retry-round count) — the judgment calls themselves happen once, inside the Build and Validate agents that are positioned to make them, never re-litigated by whatever reads the result. See the workflow script's header comment and `docs/model-tiers.md` for the fuller rationale if this needs revisiting.

**Where this sits:** `/cleanup-fleet` stays the attended, single-bucket, human-gated tool — nothing here replaces it. This skill is for the specific case of a scheduled overnight run where nobody is watching.

## Arguments

`/cleanup-fleet-all [<bucket>...]` — zero or more bucket names, fuzzy-matched via the same synonym table `/cleanup-fleet` uses (`documentation`/`docs`, `claude-md-drift`/`drift`, `duplication`/`dupes`, `stale`/`dead`, `maintainability`/`maint`, `slop`/`bloat`, `bug`/`bugs`, `design-drift`/`design`).

- **No arguments** → all eight queued buckets, the intended unattended shape.
- **One or more bucket names** → restrict to just those — use this for an attended dry run of a small slice before trusting a full overnight sweep.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh`/`git` only — no PowerShell syntax. Windows paths map as `/e/automation/...`.
- **The orchestrator (this skill) only does cheap, safe work:** auth check, one `gh search`, grouping/dedupe (model-side, no jq/python), the rate-gate check, invoking the Workflow, and post-flight reporting. **It never edits source, commits, pushes, or merges** — every write happens inside an agent spawned by the workflow script.
- **Never disturb in-progress work.** A repo that is dirty or off its default branch is skipped and reported — never stashed, never force-switched.
- **Never background a tool call in this skill — this is the rule that matters most here.** This orchestrator runs headless via `run-weekly.bat`'s one-shot Claude process (streamed through `claude_progress.py`) with no persistent turn loop and no human attending it. There is no wake-up mechanism to resume the session after a turn ends, so launching a command and then ending the turn to "wait for it" silently kills the entire run: the CLI exits immediately reporting `exit_code: 0` (false success) while nothing past that point ever happened (`fleet-config#314`, the exact failure `/audit-fleet` hit twice). This applies to the `Workflow` tool call in step 5 below exactly as much as it applies to a backgrounded `Agent` dispatch — `Workflow` also returns immediately and notifies later, the same async shape. Every long-running call here — including the rate-gate wait — must run synchronously (foreground) or poll to completion **within the same turn** (e.g. `TaskOutput` with `block: true`, re-issued in a loop; or the `Monitor` tool's until-loop pattern for the rate-gate wait), never fire-and-forget.
- **Degrade, don't block.** A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. Nothing here waits on an interactive prompt — there's nobody to answer one.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.

### 2. Resolve buckets

Parse args through the synonym table (see "Arguments"). No args → all eight queued canonical labels (`documentation`, `claude-md-drift`, `duplication`, `stale`, `maintainability`, `slop`, `bug`, `design-drift`). `security` and `cert-drift` are never in this set — `security` is self-healed inline by `/codebase-audit`, and `cert-drift` is `/design-sync`'s review-only kind (a tailnet-cert migration is never auto-applied unattended). Unrecognized tokens are ignored with a one-line note, not a hard stop.

### 3. Fetch every bucket — one `gh` call

```
gh search issues --owner ferraroroberto --state open --include-prs=false --limit 300 \
  --json repository,number,title,body,labels,url
```

Read the JSON directly (no jq/python/awk — group and select model-side, same convention as `/cleanup-fleet`). For each issue, collect every label that matches one of this run's resolved bucket names — **drop any row carrying `audit-meta`** (the ledger issues, never actionable), and drop any row matching none of the resolved buckets. An issue carrying more than one bucket label legitimately appears in more than one bucket's list; that's fine, buckets run serially so it's never worked on twice at once.

If nothing survives for any resolved bucket: print `No open cleanup issues across the fleet 🎉` and stop.

### 4. Group by (bucket, repo) + enforce one issue per repo per bucket

Within each bucket, group surviving issues by `repository.name`:

- Exactly one candidate → that's the issue.
- More than one → select one, defer the rest (record for the final report). Preference: (1) the audit-managed issue (body contains `<!-- audit-managed:`), else (2) the smallest/clearest-acceptance one.

### 5. Pre-flight per selected repo

For every repo with a selected issue in any bucket:

- `E:\automation\<repo>` exists. Else skip + report.
- `git -C E:\automation\<repo> status --porcelain` empty. Else **skip + report** (never stash) — drop every one of this repo's selected issues (across all buckets) from the run.
- `git -C E:\automation\<repo> fetch origin` (once per repo, even if it has issues in multiple buckets).
- `git -C E:\automation\<repo> worktree list` — anything beyond the primary is pre-existing residue from an earlier run or a live human session. **Skip + report** that repo; never remove a worktree you did not create.

**Worktrees always** — every build agent forces `MODE=worktree` and works `<repo>-wt-<N>`, never the primary checkout, for every repo (fleet-config#515). The primary is only ever read (pre-flight above) and, at teardown, checked back to clean. Lanes are serial, so a repo touched by two buckets is never touched by two agents at once, and at most one worktree exists fleet-wide at any moment.

### 6. Rate-gate check

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70
```

`DECISION=PAUSE` → wait via the `Monitor` tool's until-loop pattern against the printed `WAIT_SECONDS`/`RESETS_AT` before proceeding (per the Execution rules above — this must resolve within the same turn). `OK`/`UNKNOWN` → proceed immediately.

### 7. Invoke the workflow and poll it to completion

Build `issuesByBucket` — `{ "<bucket>": [{ repo, number, title, body }, ...], ... }` — from the surviving, pre-flighted issues (step 5's skips already removed).

**Invoke with the inline `script` parameter (paste the full contents of `.claude/workflows/cleanup-fleet-all.js`), not `scriptPath`.** `scriptPath` has been observed to fail in this environment — the permission-approval layer rejects it with a false-positive "script contains control characters" error even against a byte-clean file (confirmed via `node --check` and a raw byte/Unicode-category scan finding nothing). Read the script file fresh each invocation so edits are picked up.

```
Workflow({ script: "<full contents of .claude/workflows/cleanup-fleet-all.js>", args: { issuesByBucket } })
```

This returns a task id immediately. **Do not stop and wait for a notification** — per the Execution rules, immediately enter a blocking poll loop:

```
TaskOutput(task_id: <id>, block: true, timeout: 600000)
```

Re-issue this call (each blocks up to 10 minutes) until the returned status is `completed`, as consecutive tool calls within this same turn — this may take many calls over several hours for a full seven-bucket run, and that's expected. Serial lanes make a full run slower in wall-clock than the old parallel shape; that is the trade being bought, not a regression to fix.

The workflow's return value is `{ buckets: [{ bucket, results: [...], skipped? }, ...], halted }`:

- each result is `{ issue, status, round, branch, worktree, residue, residueDetail, pr?, mergeSha?, reason?, wipSha? }` — `status` is one of `merged`, `escalated`, `failed`; `residue` is `CLEAN` or `RESIDUE`.
- `halted` is `null` on a full run, or `{ bucket, repo, issue, status, detail, remainingInBucket }` when a lane's teardown left residue and the run stopped there. **A halted run is a loud failure, not a partial success** — report it at the top of the final summary, name the one repo, and say exactly what a human must do.

### 8. Post-flight verification (never trust the agent's self-report)

The teardown agent already verified its own lane. This step is the independent second look — same "a check that can't establish a fact reports `unknown`, never `pass`" discipline the global CLAUDE.md requires.

**8a. Per-issue tree check.** For every issue with `status: "merged"`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → downgrade from `✅ merged` to `⚠️ merged but dirty tree — inspect <repo>` and carry the `REASON=` line.

For every issue with `status: "escalated"` or `"failed"` — the teardown agent should have put the repo back on a clean default branch, so check it the same way:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → append `⚠️ post-flight: <REASON>` next to the escalation line.

**8b. Fleet-wide residue enumeration — fail loud.** Checking only the primaries of touched repos is exactly what let 11 worktrees slip through a run that reported `0 failed`. After all buckets finish, enumerate residue across **every repo the run touched** (Git Bash, read-only):

```
for r in <every touched repo>; do
  git -C /e/automation/$r worktree list
  ls -d /e/automation/$r-wt-* 2>/dev/null
  git -C /e/automation/$r branch --format='%(refname:short)'
  git -C /e/automation/$r status --porcelain
done
```

Anything beyond `main` (or the repo's default branch), a clean tree, and a single primary worktree is **residue**. Residue is never folded into a `✅`/`📋` line: it gets its own `❌ RESIDUE` block in the final summary naming the repo, the leftover path/branch, and the one-line recovery command. If a check could not run at all, report it as `❓ unknown`, never as clean.

### 9. Notify

Per bucket, once all its issues have a final status:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind cleanup --summary "<bucket> (all-mode)" --merged <merged-count> --review <escalated-count>
```

(`--review` here means "escalated after 2 failed validation rounds," reusing the existing `--kind cleanup` semantics exactly — no code changes needed.) After every bucket has reported: fire one final roll-up call summing merged/escalated across all eight buckets, same `--kind cleanup` shape with `--summary "all buckets"`.

**`notify_complete.py` is the only sanctioned way to send these pings** — never use an MCP Slack tool to pick a channel; the helper resolves it from `projects.toml`. A silent no-op with no channel configured is correct, not a bug to route around.

### 10. Final summary

```
Cleanup-fleet-all complete           (or: HALTED at <repo>#<N> — see below)
  documentation:      3 merged, 1 escalated
    ✅ merged:   photo-ocr#44 <pr-url>, reporting#12 <pr-url>, …
    📋 escalate: app-launcher#71 — reason: <validator's last feedback>
                 torn down; findings + WIP SHA commented on the issue
  claude-md-drift:    …
  …
  skipped repos (dirty/off-branch/pre-existing worktree): website
  deferred (extra issue, next run): grocery-shopping#9

  ❌ RESIDUE (run halted): local-llm-hub — E:\automation\local-llm-hub-wt-451 would not delete
     <detail from the teardown agent>
     Recover: E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree E:\automation\local-llm-hub-wt-451
     Not started because of the halt: 4 issue(s) in maintainability, all of slop/bug/design-drift

Next: escalated issues need a human — read the teardown comment on the issue and either re-run it, or close it if it doesn't warrant the work.
```

A run that halted says so on the **first** line. Never present a halted run as `complete`.

### 11. Stop

No follow-up actions and no auto-launch of anything — including no retry of a halted run. A human clears the residue and decides whether to re-run.

## Hard rules

- **Four agents per issue: build, validate, execute, teardown. The gate between them is deterministic code in the workflow script, never another LLM call re-interpreting an already-decided verdict.**
- **One bucket at a time, one repo at a time.** No `parallel(...)` over issues, ever. The next lane starts only after the current lane's teardown reports `CLEAN`.
- **Teardown runs on every lane** — merged, escalated, or failed. No lane may end leaving a worktree, a branch, or a primary that isn't on a clean default branch.
- **A lane that can't be returned to clean halts the run.** Loud, named, with a recovery command. Never continue and stack a second worktree on top of the first.
- **Every agent works a forced worktree, never a primary checkout** (`worktree_claim.py acquire … --force-worktree`), for every repo — a live app or a live junction is not a claim holder.
- **A live-e2e guard refusal is a hard STOP for every agent.** `E2E_LIVE=1` or any equivalent override is forbidden; e2e never targets a live production instance.
- **This skill never edits source, commits, pushes, or merges.** Every write happens inside a spawned agent.
- **Never disturb in-progress work.** Dirty/off-default-branch repos, and repos that already have a worktree when the run starts, are skipped and reported — never stashed, force-switched, or torn down.
- **`design-drift` fixes obey `/design-sync`'s structural rule.** The build agent may auto-fix token/palette/spacing drift, but must **never re-author navigation or components** — reuse the vendored `project-scaffolding` snippet verbatim. A structural finding it cannot resolve by re-vendoring must fail validation and **escalate** (branch left for a human), never auto-merge a hand-rolled rewrite. `cert-drift` is not a bucket here at all — its migration is never auto-applied.
- **Never background a tool call and end the turn expecting a resume — this includes the `Workflow` call itself.** Poll `TaskOutput` to completion within the same turn.
- **Post-flight dirty-tree check runs here, in this skill, never inside a spawned agent**, right before a repo's status is trusted.
- **Max 2 build/validate rounds per issue. A second failure escalates — it never force-merges and never silently drops the issue from the final report.** Escalation means *commented on the issue and torn down*, not *branch parked for later*.
- **Post-flight residue enumeration (step 8b) covers every touched repo, not just merged ones, and fails loud.** A check that can't establish a fact reports `unknown`, never clean.
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Relationship to `/cleanup-fleet`:** that skill stays exactly as-is for attended, single-bucket, human-gated runs. This skill doesn't replace it — it's a different tool for a different situation (nobody watching, want every bucket covered).
- **Compose, don't reinvent:** the build agent's mechanics reuse `/issue-start <N> now`; the execute agent's mechanics reuse `/issue-finish`'s push/PR/CI/merge/tray-restart sequence — same as `/cleanup-fleet` and `/issue-finish-batch` already do.
- **Validated attended, once, against the `stale` bucket** (4 repos: whatsapp-radar, photo-ocr, local-llm-hub, app-launcher) — all 4 merged on the first round, no retries needed, post-flight `dirty_tree_check.py` confirmed all four trees clean on `main`. Two environment bugs surfaced and were worked around: `scriptPath` invocation (see step 7) and `args` arriving inside the workflow script as a JSON string rather than a parsed object — `.claude/workflows/cleanup-fleet-all.js` defensively `JSON.parse`s it when it comes through as a string. Neither bug is specific to this skill's content (both reproduced with trivial scripts/payloads); worth re-testing if the harness changes.
- **Before trusting the full unattended schedule**, run at least one more attended pass covering a bucket with a validator rejection (to prove the retry loop, not just the happy path) before wiring `run-weekly.bat` into a scheduled job.
- **2026-07-31 rewrite (fleet-config#518 + #515), after the 2026-07-30 run reported `35 merged / 12 escalated / 0 failed` and left the fleet unusable:** within-bucket `parallel(...)` became a serial loop; a fourth Teardown agent became the terminal step of every lane; residue halts the run; all agent briefs force worktree mode and ban live-e2e overrides; step 8 gained the fleet-wide residue enumeration. The old run's `0 failed` was the giveaway — every one of the 11 stray worktrees came from a lane the workflow considered a *success path* for reporting purposes.
