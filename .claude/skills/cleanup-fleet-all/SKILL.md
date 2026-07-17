---
name: cleanup-fleet-all
description: Unattended, all-bucket sibling of /cleanup-fleet — builds, validates, and ships every open cleanup issue across all seven queued audit buckets in one overnight pass, no human in the loop. Use for a fully unattended fleet-wide cleanup run — e.g. "/cleanup-fleet-all", "clean up the whole fleet overnight", "run cleanup on all buckets unattended". Runs headless via a scheduled claude -p job.
---

# cleanup-fleet-all

**Goal:** `/cleanup-fleet` processes one bucket at a time and, in its default `hard` mode, stops for a human to approve the plan and to review hard-tier work before merge. This skill is the genuinely unattended sibling: it walks **all seven queued** audit buckets, serially, and ships every issue with **no human review gate at all** — replaced by an independent validator agent instead of a human. No single agent both builds and ships its own work unchecked. (The eighth audit kind, `security`, is never queued — `/codebase-audit` self-heals it inline; nothing here touches it.)

**Three agents per issue, never fewer:**

1. **Build** — implement the fix and run the project's verification gate, then **stop** before shipping.
2. **Validate** — a fresh, independent agent with no memory of the build. Re-runs the verification gate itself and judges, leniently, whether the diff actually addresses the issue.
3. **Execute** — ships an already-validated branch: push, PR, CI, merge, tray restart.

A failed validation retries the build **once** (feeding it the validator's feedback verbatim), for a hard cap of **2 rounds**; a second failure escalates the issue — the branch is left as-is for a human to look at later via `/issue-finish`, never force-merged.

All of the actual retry/ship decision-making lives in **`.claude/workflows/cleanup-fleet-all.js`**, a Workflow script — not in this SKILL.md and not in a fourth "orchestrator" agent. That decision (retry vs. ship vs. escalate) is a fixed lookup on each agent's own schema-validated verdict (`verification`, `retryable`, `pass`, retry-round count) — the judgment calls themselves happen once, inside the Build and Validate agents that are positioned to make them, never re-litigated by whatever reads the result. See the workflow script's header comment and `docs/model-tiers.md` for the fuller rationale if this needs revisiting.

**Where this sits:** `/cleanup-fleet` stays the attended, single-bucket, human-gated tool — nothing here replaces it. This skill is for the specific case of a scheduled overnight run where nobody is watching.

## Arguments

`/cleanup-fleet-all [<bucket>...]` — zero or more bucket names, fuzzy-matched via the same synonym table `/cleanup-fleet` uses (`documentation`/`docs`, `claude-md-drift`/`drift`, `duplication`/`dupes`, `stale`/`dead`, `maintainability`/`maint`, `slop`/`bloat`, `bug`/`bugs`).

- **No arguments** → all seven queued buckets, the intended unattended shape.
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

Parse args through the synonym table (see "Arguments"). No args → all seven queued canonical labels (`documentation`, `claude-md-drift`, `duplication`, `stale`, `maintainability`, `slop`, `bug`). `security` is never in this set — it's self-healed inline by `/codebase-audit`, never queued. Unrecognized tokens are ignored with a one-line note, not a hard stop.

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

No worktrees — each selected issue's build agent works its repo's primary checkout in place, same as `/cleanup-fleet`'s in-place mode. Since buckets run serially, a repo touched by two buckets is never touched by two agents at once.

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

Re-issue this call (each blocks up to 10 minutes) until the returned status is `completed`, as consecutive tool calls within this same turn — this may take many calls over several hours for a full seven-bucket run, and that's expected. The workflow's own return value is an array of `{ bucket, results: [{ issue, status, round, branch, pr?, mergeSha?, reason? }, ...] }` — `status` is one of `merged`, `escalated`, `failed`.

### 8. Post-flight verification (never trust the agent's self-report)

For every issue with `status: "merged"`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → downgrade from `✅ merged` to `⚠️ merged but dirty tree — inspect <repo>` and carry the `REASON=` line.

For every issue with `status: "escalated"` (branch left built but not shipped):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode built --expect-branch <branch>
```

`STATUS=DIRTY` → append `⚠️ post-flight: <REASON>` next to the escalation line. This check only reports — it never blocks, auto-commits, or auto-fixes, and a per-repo failure never stops aggregating the rest.

### 9. Notify

Per bucket, once all its issues have a final status:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind cleanup --summary "<bucket> (all-mode)" --merged <merged-count> --review <escalated-count>
```

(`--review` here means "escalated after 2 failed validation rounds," reusing the existing `--kind cleanup` semantics exactly — no code changes needed.) After every bucket has reported: fire one final roll-up call summing merged/escalated across all seven buckets, same `--kind cleanup` shape with `--summary "all buckets"`.

**`notify_complete.py` is the only sanctioned way to send these pings** — never use an MCP Slack tool to pick a channel; the helper resolves it from `projects.toml`. A silent no-op with no channel configured is correct, not a bug to route around.

### 10. Final summary

```
Cleanup-fleet-all complete
  documentation:      3 merged, 1 escalated
    ✅ merged:   photo-ocr#44 <pr-url>, reporting#12 <pr-url>, …
    ⚠️ escalate: app-launcher#71 — cd E:\automation\app-launcher && /issue-finish
                 branch fix/71-x left in place, reason: <validator's last feedback>
  claude-md-drift:    …
  …
  skipped repos (dirty/off-branch): website
  deferred (extra issue, next run): grocery-shopping#9

Next: escalated issues need a human — inspect the branch and either fix + /issue-finish, or close the issue if it doesn't warrant it.
```

### 11. Stop

No follow-up actions and no auto-launch of anything. A human deals with escalated branches on their own schedule.

## Hard rules

- **Three agents per issue: build, validate, execute. The gate between them is deterministic code in the workflow script, never a fourth LLM call re-interpreting an already-decided verdict.**
- **This skill never edits source, commits, pushes, or merges.** Every write happens inside a spawned agent.
- **Never disturb in-progress work.** Dirty/off-default-branch repos are skipped and reported, never stashed or force-switched.
- **Never background a tool call and end the turn expecting a resume — this includes the `Workflow` call itself.** Poll `TaskOutput` to completion within the same turn.
- **Post-flight dirty-tree check runs here, in this skill, never inside a spawned agent**, right before a repo's status is trusted.
- **Max 2 build/validate rounds per issue. A second failure escalates — it never force-merges and never silently drops the issue from the final report.**
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Relationship to `/cleanup-fleet`:** that skill stays exactly as-is for attended, single-bucket, human-gated runs. This skill doesn't replace it — it's a different tool for a different situation (nobody watching, want every bucket covered).
- **Compose, don't reinvent:** the build agent's mechanics reuse `/issue-start <N> now`; the execute agent's mechanics reuse `/issue-finish`'s push/PR/CI/merge/tray-restart sequence — same as `/cleanup-fleet` and `/issue-finish-batch` already do.
- **Validated attended, once, against the `stale` bucket** (4 repos: whatsapp-radar, photo-ocr, local-llm-hub, app-launcher) — all 4 merged on the first round, no retries needed, post-flight `dirty_tree_check.py` confirmed all four trees clean on `main`. Two environment bugs surfaced and were worked around: `scriptPath` invocation (see step 7) and `args` arriving inside the workflow script as a JSON string rather than a parsed object — `.claude/workflows/cleanup-fleet-all.js` defensively `JSON.parse`s it when it comes through as a string. Neither bug is specific to this skill's content (both reproduced with trivial scripts/payloads); worth re-testing if the harness changes.
- **Before trusting the full unattended schedule**, run at least one more attended pass covering a bucket with a validator rejection (to prove the retry loop, not just the happy path) before wiring `run-weekly.bat` into a scheduled job.
