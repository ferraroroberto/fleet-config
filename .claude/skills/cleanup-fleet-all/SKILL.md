---
name: cleanup-fleet-all
description: Unattended, all-bucket sibling of /cleanup-fleet — builds, validates, and ships every open cleanup issue across all eight queued audit buckets in one overnight pass. E.g. "/cleanup-fleet-all", "clean up the whole fleet overnight", "run cleanup on all buckets unattended". Runs headless via a scheduled claude -p job.
---

# cleanup-fleet-all

**Goal:** the genuinely unattended sibling of `/cleanup-fleet` (one bucket, stops for human approval in its default `hard` mode). This walks **all eight queued** audit buckets, serially, and ships every issue with **no human review gate** — replaced by an independent validator agent, so no single agent both builds and ships its own work unchecked. (`security` is never queued — `/codebase-audit` self-heals it inline — and `cert-drift`, `/design-sync`'s other kind, is review-only, never auto-migrated; nothing here touches either.)

**Four agents per issue, never fewer:**

1. **Build** — implement the fix in a forced worktree, run the project's verification gate, then **stop** before shipping.
2. **Validate** — a fresh, independent agent with no memory of the build. Re-runs the verification gate itself and judges, leniently, whether the diff actually addresses the issue.
3. **Execute** — ships an already-validated branch: push, PR, CI, merge, tray restart.
4. **Teardown** — the terminal step of **every** lane, whatever the outcome: comments the failure reason + any WIP SHA onto the GitHub issue (non-merged lanes only), removes the worktree via `worktree_claim.py remove-worktree`, releases the claim, deletes the branch, and runs **six verification checks**. Four decide residue (worktree list, no leftover sibling directory, **this lane's own branch** gone, clean tree); two are **reported and never halt** — a stale `.git/index.lock` and a primary behind `origin/<default>` (fleet-config#534). Other lanes' leftover branches are reported too, never residue (fleet-config#572).

A failed validation retries the build **once** (feeding it the validator's feedback verbatim), for a hard cap of **2 rounds**; a second failure escalates. **Escalation is not "leave the branch for a human"** — the open GitHub issue plus the teardown agent's comment on it is the durable record; branch and worktree are torn down like any other lane (fleet-config#518).

**One bucket at a time, one repo at a time.** Lanes are strictly serial: the next repo does not start until the current lane has run all four agents and its repo is verified clean. At most one worktree exists at any instant. (Within-bucket parallelism + a teardown-free escalation path caused the incident in Notes.)

**Halt on residue.** If teardown cannot return a repo to a clean state, the run **stops there** — does not start the next lane. Lanes serial → exactly one repo affected, one command to recover.

**Never a primary checkout.** Every build/validate/execute agent forces worktree mode (`worktree_claim.py acquire … --force-worktree`) for every repo, no exceptions, no special-cased list — a running app or a live junction (e.g. `fleet-config`'s own `hooks/`+`skills/` into every `~/.claude`) is not a claim holder, so an unattended agent otherwise wins `MODE=primary` and edits files a live process is serving (fleet-config#515). Same briefs: a live-e2e guard refusal is a hard STOP — `E2E_LIVE=1` or any equivalent override is forbidden.

All retry/ship decision-making lives in **`.claude/workflows/cleanup-fleet-all.js`**, a Workflow script — not this SKILL.md, not a fourth "orchestrator" agent. Retry vs. ship vs. escalate is a fixed lookup on each agent's own schema-validated verdict (`verification`, `retryable`, `pass`, retry-round count); judgment calls happen once, inside Build/Validate, never re-litigated by whatever reads the result. See the script's header comment and `docs/model-tiers.md`.

## Arguments

`/cleanup-fleet-all [<bucket>...]` — zero or more bucket names, fuzzy-matched via the same synonym table `/cleanup-fleet` uses (`documentation`/`docs`, `claude-md-drift`/`drift`, `duplication`/`dupes`, `stale`/`dead`, `maintainability`/`maint`, `slop`/`bloat`, `bug`/`bugs`, `design-drift`/`design`).

- **No arguments** → all eight queued buckets, the intended unattended shape.
- **One or more bucket names** → restrict to just those — use this for an attended dry run of a small slice before trusting a full overnight sweep.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh`/`git` only — no PowerShell syntax. Windows paths map as `/e/automation/...`.
- **The orchestrator (this skill) only does cheap, safe work:** auth check, the issue fetch, grouping/dedupe (model-side, no jq/python), the rate-gate check, invoking the Workflow, and post-flight reporting. **It never edits source, commits, pushes, or merges** — every write happens inside an agent spawned by the workflow script.
- **Never disturb in-progress work.** A repo that is dirty or off its default branch is skipped and reported — never stashed, never force-switched. Skipped is not dropped: it is deferred, retried once after the last bucket, and recorded durably (steps 5, 7b, 8c).
- **Never background a tool call in this skill — the rule that matters most here.** Runs headless via `run-weekly.bat`'s one-shot Claude process (streamed through `claude_progress.py`), no persistent turn loop, no human, **no wake-up mechanism**: launching a command and ending the turn to "wait for it" silently kills the run — CLI exits `exit_code: 0` (false success) while nothing past that point happens (fleet-config#314, the exact failure `/audit-fleet` hit twice). Applies to the `Workflow` call in step 7 exactly as much as a backgrounded `Agent` dispatch — `Workflow` also returns immediately and notifies later. Every long-running call, **including the rate-gate wait**, must run synchronously (foreground) or poll to completion **within the same turn** (`TaskOutput` with `block: true`, re-issued in a loop; or `Monitor`'s until-loop for the rate-gate wait) — never fire-and-forget.
- **Degrade, don't block.** A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. Nothing here waits on an interactive prompt — there's nobody to answer one.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop, and the final report must print the literal line `SCHEDULED-RUN-FAILED — not authenticated, run gh auth login` (no lane is ever attempted, so this is a delivery failure, not a clean no-op — fleet-config#612).
- Confirm `E:\automation\` exists (the fleet root). Else stop, and the final report must print `SCHEDULED-RUN-FAILED — fleet root E:\automation not found`.

### 2. Resolve buckets

Parse args through the synonym table (see "Arguments"). No args → all eight queued canonical labels (`documentation`, `claude-md-drift`, `duplication`, `stale`, `maintainability`, `slop`, `bug`, `design-drift`). `security` and `cert-drift` are never in this set — `security` is self-healed inline by `/codebase-audit`, and `cert-drift` is `/design-sync`'s review-only kind (a tailnet-cert migration is never auto-applied unattended). Unrecognized tokens are ignored with a one-line note, not a hard stop.

### 3. Fetch every bucket — direct Issues API, one repo-scoped call per repo

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gh_issue_fetch.py fetch
```

**Preferred primary fetch**, not `gh search issues --owner ferraroroberto` (Search-API-backed, eventually consistent — observed reporting 23 issues open for 5+ weeks after they closed, fleet-config#623). `gh_issue_fetch.py` uses the direct Issues API, one `gh issue list --repo <owner>/<name> --state open` per repo, aggregated into the same shape. Avoids a known-bad source; **not proven immune** — step 5 still re-checks each selected issue's state before dispatch.

Read the JSON directly (no jq/python/awk — group and select model-side, same convention as `/cleanup-fleet`). For each issue, collect every label matching one of this run's resolved bucket names — **drop any row carrying `audit-meta`** (the ledger issues, never actionable), and drop any row matching none of the resolved buckets. An issue carrying more than one bucket label legitimately appears in more than one bucket's list; buckets run serially, so it's never worked on twice at once.

If the helper's stderr summary reports `ERRORS=` greater than zero, note which repos it could not read (printed as `ERROR <repo>: <reason>` lines) in the final report — those repos are simply absent from this run's candidates, not a run-wide failure.

If nothing survives for any resolved bucket: print `No open cleanup issues across the fleet 🎉` and stop. This is a legitimate empty-queue success — do **not** print the `SCHEDULED-RUN-FAILED` marker here.

### 4. Group by (bucket, repo) + enforce one issue per repo per bucket

Within each bucket, group surviving issues by `repository.name`:

- Exactly one candidate → that's the issue.
- More than one → select one, defer the rest (record for the final report). Preference: (1) the audit-managed issue (body contains `<!-- audit-managed:`), else (2) the smallest/clearest-acceptance one.

### 5. Pre-flight per selected repo

**Re-verify every selected issue's live state first, in one batch, before any per-repo check below.** Step 3's fetch isn't proven immune to every staleness source, and a run can sit for hours — an issue selected at step 4 can close for real while an earlier bucket is still running. Build one JSON array of every selected issue across every bucket (`[{"repo": ..., "number": ..., "bucket": ..., ...other fields...}, ...]` — `repo` a bare name like `"task-os"`, never `"owner/name"`: the helper prepends the owner itself, and a prefixed repo produces a doubled-owner `gh` argv that fails with a network-sounding error unrelated to the network, fleet-config#706) and pipe it through:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/issue_state_gate.py partition
```

Read the stdout JSON directly — `{"dispatch": [...], "skipped_closed": [...], "unresolved": [...]}`. **Only `dispatch` items proceed past this step.** Every `skipped_closed` item is dropped from its bucket's list and recorded in the final report as `already closed — dropped, no agent dispatched`. Every `unresolved` item is **also dropped** — the check could not establish the issue's state, so it is dropped rather than guessed as open — and recorded as `state unresolved — dropped, not dispatched (<detail>)`, distinct from both the closed and the dispatched sets. This must never collapse into "no issues found": the final report's headline must show all three counts (`dispatched`, `already-closed`, `unresolved`).

**Then gate every surviving issue on its repo's availability, in one batch.** Build a JSON array of every `dispatch` item (`[{"repo": ..., "number": ..., "bucket": ..., ...}, ...]`) and pipe it through:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/repo_preflight.py partition
```

It resolves each distinct repo exactly once (however many buckets it appears in) and runs the full per-repo check — `E:\automation\<repo>` exists · `status --porcelain` empty · HEAD on the default branch · `fetch origin` · `worktree list` shows only the primary. Read the stdout JSON directly: `{"dispatch": [...], "skipped": [...]}`. Each skipped item carries `repo_state` (`missing`/`dirty`/`off-branch`/`worktree`/`unknown`) and `skip_reason`; the stderr line gives `DISPATCH= SKIPPED_REPOS= SKIPPED_ISSUES= UNKNOWN_REPOS=`.

Only `dispatch` items proceed. **The `skipped` list is this run's deferred set** — carry it to step 7b, which re-checks it, and to step 8c, which records whatever is still unavailable. A dirty repo drops every one of its selected issues across all buckets; the helper enforces that by resolving per repo rather than per issue.

`unknown` (git unreadable) does not dispatch — an unreadable repo is not a repo proven safe to work in — but it is counted separately from a confirmed dirty tree, and the report keeps them apart. A failed `fetch origin` is recorded in the item's `note` and never changes the verdict: what makes a repo unsafe to work in is a dirty tree, a wrong branch, or someone else's worktree, not an unreachable network.

The **skip criteria are unchanged** and never soften — never stash, never force-switch, never remove a worktree you did not create. This gate only changes what the run does with the knowledge that it skipped something.

**If candidates existed and `dispatch` is empty**, every one of them was skipped: no lane will run and there is nothing to retry later. Record the deferred set through step 8c, then stop and print in the final report the literal line `SCHEDULED-RUN-FAILED — every candidate repo was skipped (<N> repos, <M> issues unprocessed), no lane ran`. This is **not** step 3's empty-queue case: there the queue was genuinely empty, which is a success; here there was real work and the run touched none of it.

**Worktrees always** — every build agent forces `MODE=worktree` and works `<repo>-wt-<N>`, never the primary checkout, for every repo (fleet-config#515). The primary is only ever read (pre-flight above) and, at teardown, checked back to clean. Lanes are serial, so a repo touched by two buckets is never touched by two agents at once, and at most one worktree exists fleet-wide at any moment.

**A build agent's handoff is a committed branch, never a dirty tree** (fleet-config#641). The build brief's STOP step forbids exactly four actions — push, PR, merge, `/issue-finish` — committing is not one of them; validate agent's first act is `git status --porcelain` on the worktree, and a dirty tree is immediate `pass: false` regardless of the otherwise-lenient default — the one rejection reason that is not a judgement call. Asserted at the boundary because otherwise invisible (execute agent's `/issue-finish` commits pending work as a safety net, so an uncommitted handoff ships fine and recurs silently): uncommitted work has no SHA for the escalation comment's WIP SHA to point at, and an escalation/crash between build and execute loses the work instead of parking it reflog-recoverable ~90 days. A build that legitimately changed nothing still leaves a clean tree — assertion is on the tree, never the commit count.

### 6. Rate-gate check

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70
```

`DECISION=PAUSE` → wait via the `Monitor` tool's until-loop pattern against the printed `WAIT_SECONDS`/`RESETS_AT` before proceeding (per the Execution rules above — this must resolve within the same turn). `OK`/`UNKNOWN` → proceed immediately.

### 7. Invoke the workflow and poll it to completion

Build `issuesByBucket` — `{ "<bucket>": [{ repo, number, title, body }, ...], ... }` — from the surviving, pre-flighted issues (step 5's skips already removed).

**Invoke with the inline `script` parameter (paste the full contents of `.claude/workflows/cleanup-fleet-all.js`), not `scriptPath`.** `scriptPath` has been observed to fail in this environment — the permission-approval layer rejects it with a false-positive "script contains control characters" error even against a byte-clean file. Read the script file fresh each invocation so edits are picked up.

```
Workflow({ script: "<full contents of .claude/workflows/cleanup-fleet-all.js>", args: { issuesByBucket } })
```

This returns a task id immediately. **Do not stop and wait for a notification** — per the Execution rules, immediately enter a blocking poll loop:

```
TaskOutput(task_id: <id>, block: true, timeout: 600000)
```

Re-issue this call (each blocks up to 10 minutes) until the returned status is `completed`, as consecutive tool calls within this same turn — this may take many calls over several hours for a full run, and that's expected. Serial lanes make a full run slower in wall-clock than the old parallel shape; that is the trade being bought, not a regression to fix.

The workflow's return value is `{ buckets: [{ bucket, results: [...], skipped? }, ...], halted }`:

- each result is `{ issue, status, round, branch, worktree, residue, residueDetail, indexLock, indexLockDetail, behindOrigin, behindOriginDetail, zombieShells?, pr?, mergeSha?, reason?, wipSha?, alreadyClosed? }` — `status` one of `merged`/`escalated`/`failed`; `residue` is `CLEAN` or `RESIDUE`. `indexLock` (`none`/`stale-cleared`/`live-held`/`unknown`) and `behindOrigin` (`current`/`fast-forwarded`/`unknown`) are **reported only** — never gate a lane, never halt the run, default `unknown` when teardown died or omitted them. `zombieShells` names a leftover directory satisfying all five zombie-pinned conditions (step 8b), not counted as residue; `foreignBranches` names local branches belonging to no lane of this run — also reported, never residue. `alreadyClosed: true` means an issue step 5's batch check let through turned out closed by the time /issue-start reached it, hours in (fleet-config#623) — lane still escalates and tears down, but teardown skips the usual "unattended lane escalated" comment (noise on an already-resolved thread). Different population from step 5's `skipped_closed`/`unresolved`, which never reach `results` — they never entered `issuesByBucket`, no lane ran for them.
- `halted` is `null` on a full run, or `{ bucket, repo, issue, status, detail, remainingInBucket }` when a lane's teardown left residue and the run stopped there. **A halted run is a loud failure, not a partial success** — report it at the top of the final summary, name the one repo, and say exactly what a human must do.

### 7b. Retry the deferred set — one pass, after the last bucket

A repo that was dirty at pre-flight has usually been committed and pushed by the time the last bucket finishes: a full run spans many hours, so step 5's verdict is stale by now. The deferred set gets exactly one more chance, as late as possible.

**Skip this step entirely if step 7's `halted` is non-null.** A halted run has left residue and must not start another lane.

Re-run the *same* gate over the deferred set — the whole array step 5 skipped, unchanged:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/repo_preflight.py partition
```

The helper holds no state between calls, so this necessarily re-establishes every fact from the live tree rather than trusting step 5's verdict — a repo that has since *become* dirty must not be dispatched on an hours-old "available".

Anything now in `dispatch` gets one retry pass: rebuild `issuesByBucket` from those issues only, and invoke the workflow a second time exactly as step 7 describes — same inline `script` parameter, same blocking `TaskOutput` poll to completion within this turn. The serial-lane invariant holds by construction: this invocation starts only after the first has fully completed, so there is still at most one worktree fleet-wide at any instant.

Merge its `buckets` results into the report under the same bucket names, marked `(retry)`. Its `halted` is handled exactly like step 7's. **One pass, never a loop** — whatever is still unavailable stays deferred and goes to step 8c.

### 8. Post-flight verification (never trust the agent's self-report)

The teardown agent already verified its own lane. This step is the independent second look — same "a check that can't establish a fact reports `unknown`, never `pass`" discipline the global CLAUDE.md requires.

**8a. Per-issue tree check.** For every issue with `status: "merged"`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → downgrade from `✅ merged` to `⚠️ merged but dirty tree — inspect <repo>` and carry the `REASON=` line.

`STATUS=UNKNOWN` → the helper has **no** verdict about that repo: render `❓ merged, tree unverified — <REASON>`, never folded into `✅` or `⚠️`. Check the path you passed first: the Git Bash backslash trap (a double-quoted Windows path loses its backslashes and never expands the loop variable) once made the helper answer `DIRTY` for five repos that were all clean (fleet-config#570). **Pass forward slashes.**

For every issue with `status: "escalated"` or `"failed"` — the teardown agent should have put the repo back on a clean default branch, so check it the same way:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → append `⚠️ post-flight: <REASON>` next to the escalation line. `STATUS=UNKNOWN` → append `❓ post-flight unverified: <REASON>` — an escalation whose tree could not be read is not an escalation whose tree is fine.

**8b. Fleet-wide residue enumeration — fail loud.** Checking only the primaries of touched repos is what let 11 worktrees slip through a run that reported `0 failed`. After all buckets finish, enumerate residue across **every repo the run touched** (Git Bash, read-only):

```
for r in <every touched repo>; do
  git -C /e/automation/$r worktree list
  ls -d /e/automation/$r-wt-* 2>/dev/null   # per-repo glob, never /e/automation/*-wt-*
  git -C /e/automation/$r branch --format='%(refname:short)'
  git -C /e/automation/$r status --porcelain
  ls -l /e/automation/$r/.git/index.lock 2>/dev/null
  git -C /e/automation/$r fetch origin && git -C /e/automation/$r rev-list --count HEAD..origin/<default>
done
```

The worktree glob is **per repo, inside the loop, and stays that way**. A fleet-wide `/e/automation/*-wt-*` makes one repo's check report another repo's in-flight worktree as residue (app-launcher#709's lane flagged home-automation's live worktree).

A dirty tree, a primary off its default branch, a second registered worktree, or a branch belonging to *this run's* lanes is **residue** — with two exceptions, the same ones the teardown agent applies.

A leftover `<repo>-wt-<N>` directory is **not** residue when all five hold, each proved by running the command: recursively empty; a real directory, not a reparse point (read the attribute bit via `powershell.exe -NoProfile -Command "(Get-Item -Force '<path>').Attributes"`); absent from `git worktree list`; `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dir_holders.py check '<path>'` prints `STATUS=CLEAR`; and `worktree_claim.py remove-worktree` was run against it and refused. (Windows keeps the process objects of exited WebKit e2e helpers alive while any handle remains — the empty shell they pin is undeletable until reboot.) Any one condition unestablished is residue: `STATUS=LIVE` (a running process names the path, printing pid + command line) and `STATUS=UNKNOWN` (probe couldn't run) are both residue. **Never ask which zombie pins which directory** — an exited process is simply absent from the process table; a `CLEAR` verdict is the whole requirement. Report qualifying shells as `🧟 zombie-pinned (not residue)` with path and probe verdict; several are expected on a host that hasn't rebooted, nothing keys on count.

That probe is **repo-agnostic on purpose** — runs from fleet-config's own venv against the Windows process table, needs nothing from the target repo. Requiring each repo's own `tests/e2e/_browser_sweep.py` (exists in **4 of 14 fleet repos**) made the condition unprovable in the other ten, guaranteeing any leftover directory halted the run (fleet-config#571); where `_browser_sweep.py` exists, running it too is welcome, but its absence proves nothing. Probe reads command lines/executable paths, so a process merely `cd`-ed into the directory with nothing naming it is invisible — hence unrunnable probe is `UNKNOWN`, not `CLEAR`.

Second exception: a **foreign branch** — a local branch belonging to no lane of this run (earlier day's lane, human session, abandoned experiment). Reported as `🌿 <repo> — foreign branch <name> (PR #N merged, diff vs default empty)`, never residue, never halts. Teardown's mandate is its own lane — no check may assert a repo-wide property teardown is forbidden to bring about (a lane once reported RESIDUE over a stale branch from an earlier lane whose PR was already merged, halting the run with 41 lanes unstarted — fleet-config#572). Judging "safe to delete" needs an empty `git diff <default>..<branch>` or the PR's merge state — **not** `git branch --merged` (reports a squash-merged branch as unmerged since the original tip is no ancestor of default — fleet-config#567). Report them; do not delete them.

Residue is never folded into a `✅`/`📋` line: it gets its own `❌ RESIDUE` block in the final summary naming the repo, the leftover path/branch, and the one-line recovery command. If a check could not run at all, report it as `❓ unknown`, never as clean.

**Neither of the last two probes is residue and neither halts anything** (fleet-config#534) — they are reported alongside it:

- **`index.lock`** — present with a **live** `git.exe` naming that repo (`powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='git.exe'\" | Select-Object ProcessId,CommandLine | Format-List"`) → report `❓ index.lock held by live git (pid N) — left alone`, touch nothing. Present with **no** live git and mtime older than 5 minutes → report by name/age as a stale lock, delete it, retry the `rev-list`/pull below. Anything unestablished → `❓ unknown`, leave in place.
- **Behind origin** — non-zero `rev-list --count HEAD..origin/<default>` fast-forwarded with `git -C /e/automation/$r pull --ff-only` (never merge/rebase/reset/`--force`), reported `⬆️ <repo> was N behind — fast-forwarded <before>→<after>`. Refused fast-forward, failed fetch, or still-locked index → `❓ unknown` with reason, not a silent pass. **Never pull over a repo this step already found dirty or off its default branch** — report as `❓ unknown` instead; mutating it destroys the evidence. A foreign branch is *not* a reason to withhold the fast-forward — gating on unrelated refs once left a healthy primary two commits behind (fleet-config#572).

`live-held` and a refused fast-forward are *unknown*-class verdicts, not passes — named spellings of "could not establish that this primary is current". Render them with `❓` and never fold them into a `✅`.

### 8c. Record whatever is still deferred — durably, outside this run's stdout

A skip that exists only in one run's stdout is invisible by the following week. Whatever step 7b could not recover goes into one tracking issue in `ferraroroberto/fleet-config`, upserted through the same marker-keyed machinery as every other managed issue, so re-running can never file a duplicate.

**Still-deferred set non-empty** — write the body to a file, then:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/fleet-config --kind cleanup-deferred --reopen --label chore \
  --title "cleanup-fleet-all deferred repos" --body-file <file>
```

`--reopen` is what makes the close-when-clear cycle idempotent: if the last run cleared the set and closed the issue, this reopens *that* issue rather than filing a second one. Creation already self-assigns (`--assignee @me`) and `chore` is the type label — maintenance, not an audit finding, which is also why `cleanup-deferred` is deliberately **not** one of `audit_issue.py`'s `BUCKET_KINDS`.

Body: one row per still-deferred repo — repo · `repo_state` · `skip_reason` · the issue numbers that went unprocessed · this run's date. Replace the body wholesale each run; it is a current-state ledger, not an append log.

**Still-deferred set empty** — close it:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py close \
  --repo ferraroroberto/fleet-config --kind cleanup-deferred \
  --comment "Run of <date> cleared the deferred set — every candidate repo was processed."
```

An **open** `cleanup-deferred` issue therefore always means *there is unprocessed work*, never *the last run had nothing to say*.

This does **not** replace the report line: step 10's skipped counts are printed on every run including the zero case. The issue carries live work; the report carries the audit trail that the check ran at all. Never collapse the two.

### 9. Notify

Per bucket, once all its issues have a final status:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind cleanup --summary "<bucket> (all-mode)" --merged <merged-count> --review <escalated-count>
```

(`--review` here means "escalated after 2 failed validation rounds," reusing the existing `--kind cleanup` semantics exactly — no code changes needed.) After every bucket has reported: fire one final roll-up call summing merged/escalated across all eight buckets, same `--kind cleanup` shape with `--summary "all buckets"`.

**`notify_complete.py` is the only sanctioned way to send these pings** — never use an MCP chat tool to pick a chat; the helper resolves it from `projects.toml`. A silent no-op with no channel configured is correct, not a bug to route around.

### 10. Final summary

```
Cleanup-fleet-all complete           (or: HALTED at <repo>#<N> — see below)
  candidates: 9 dispatched, 2 already-closed (skipped, no agent dispatched), 1 unresolved (skipped, state unknown)
  skipped: 3 repos, 11 issues unprocessed (1 repo state unknown) — 1 repo recovered on the end-of-run retry
  documentation:      3 merged, 1 escalated
    ✅ merged:   photo-ocr#44 <pr-url>, reporting#12 <pr-url>, …
    📋 escalate: app-launcher#71 — reason: <validator's last feedback>
                 torn down; findings + WIP SHA commented on the issue
  claude-md-drift:    …
  …
  deferred (extra issue, next run): grocery-shopping#9

  deferred repos (skipped at pre-flight, re-checked after the last bucket):
    ♻️ automation      — was dirty at pre-flight, clean on retry: #88, #91 processed above (retry)
    ⏸️ website         — dirty (working tree not clean) — 7 issue(s) unprocessed: #12, #14, #15, …
    ❓ local-llm-hub   — unknown (git branch --show-current failed: not a git repository) — 4 issue(s) unprocessed
    tracked in: https://github.com/ferraroroberto/fleet-config/issues/<N>

  primary hygiene (reported, nothing halted):
    🔓 home-automation — stale .git/index.lock (4h12m old, no live git) cleared, pull retried
    ❓ automation      — index.lock held by live git (pid 21884) — left alone
    ⬆️ automation      — was 11 behind origin/main, fast-forwarded a1b2c3d→e4f5a6b
    ❓ website         — behind check unknown: fetch failed (no network)
    🧟 app-launcher    — E:\automation\app-launcher-wt-709 empty + zombie-pinned (6 zombies, live=0), not residue
    🌿 pdf-to-markdown — foreign branch fix/68-harden-upload-path-handling (PR #69 merged, diff vs main empty), not residue
                         delete with: git -C E:\automation\pdf-to-markdown branch -D fix/68-harden-upload-path-handling

  ❌ RESIDUE (run halted): local-llm-hub — E:\automation\local-llm-hub-wt-451 would not delete
     <detail from the teardown agent>
     Recover: E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree E:\automation\local-llm-hub-wt-451
     Not started because of the halt: 4 issue(s) in maintainability, all of slop/bug/design-drift

SCHEDULED-RUN-FAILED — halted at local-llm-hub#N: E:\automation\local-llm-hub-wt-451 would not delete, 4 issue(s) never started

Next: escalated issues need a human — read the teardown comment on the issue and either re-run it, or close it if it doesn't warrant the work.
```

A run that halted says so on the **first** line. Never present a halted run as `complete`.

The `candidates:` line is **mandatory on every run, even when both extra counts are zero** (fleet-config#623, echoing #560/#612) — a run that silently shrank its working set at step 5 otherwise reads as "nothing to do". `already-closed` and `unresolved` are two distinct counts, never combined into one "skipped" number.

The `skipped:` line is **mandatory too, even when every count is zero** (`skipped: 0 repos, 0 issues unprocessed`). Two non-interchangeable numbers: repos skipped, and how much live work went unprocessed. Repos whose state couldn't be established are counted apart from confirmed-dirty ones. Retry recovery count belongs on this line too.

**If every candidate repo was skipped** (step 5 left `dispatch` empty while candidates existed), print the literal line `SCHEDULED-RUN-FAILED — every candidate repo was skipped (<N> repos, <M> issues unprocessed), no lane ran` — touching none of the real work found is not a clean sweep. Step 3's genuine empty-queue stop stays exempt.

**If (and only if) step 7's workflow result carries a non-null `halted`** (`{ bucket, repo, issue, status, detail, remainingInBucket }`), also print the literal line `SCHEDULED-RUN-FAILED — halted at <repo>#<issue>: <detail>, <remainingInBucket + issues in later buckets> issue(s) never started`, exactly as shown above, so `claude_progress.py` maps this run to exit 123 instead of the harness's default exit 0 (fleet-config#612 — a run that halted 1 of 9 lanes reported exit 0, green on the Jobs card). A fully successful run (`halted` null) must **not** print this line — opt-in, not a default.

### 11. Stop

No follow-up actions and no auto-launch of anything — including no retry of a halted run. A human clears the residue and decides whether to re-run.

## Hard rules

Recap of the binding constraints above — see the referenced step for full detail:

- Four agents per issue: build, validate, execute, teardown. Gate between them is deterministic workflow-script code, never another LLM call re-interpreting an already-decided verdict.
- One bucket at a time, one repo at a time. No `parallel(...)` over issues, ever. Next lane starts only after current lane's teardown reports `CLEAN`.
- Teardown runs on every lane — merged, escalated, or failed. No lane may end leaving a worktree, a branch, or a primary off its clean default branch.
- A lane that can't be returned to clean halts the run. Loud, named, with a recovery command. Never stack a second worktree on the first.
- Every agent works a forced worktree, never a primary checkout (`worktree_claim.py acquire … --force-worktree`) — a live app or live junction is not a claim holder.
- A live-e2e guard refusal is a hard STOP for every agent. `E2E_LIVE=1` or any equivalent override is forbidden.
- This skill never edits source, commits, pushes, or merges. Every write happens inside a spawned agent.
- Never disturb in-progress work. Dirty/off-default-branch repos, and repos already holding a worktree at run start, are skipped and reported — never stashed, force-switched, or torn down. Criteria never soften.
- A skipped repo is deferred, never dropped (fleet-config#642) — step 7b retry, step 8c ledger, step 10 report line. Retry re-runs the full pre-flight, never a cached verdict. One retry pass, never a loop, never when halted.
- An open `cleanup-deferred` issue always means unprocessed work; a clean run closes it with a comment (`audit_issue.py upsert --reopen` on the other path). The always-printed report line is a separate fact, never collapsed into the issue.
- A run that skipped every candidate repo prints `SCHEDULED-RUN-FAILED` — real work found and none touched, a delivery failure not a clean sweep.
- `design-drift` fixes obey `/design-sync`'s structural rule: auto-fix token/palette/spacing, **never re-author navigation or components** (reuse the vendored `project-scaffolding` snippet verbatim); an unresolvable structural finding fails validation and escalates, never auto-merges a hand-rolled rewrite. `cert-drift` is not a bucket here — never auto-applied.
- Never background a tool call and end the turn expecting a resume — including the `Workflow` call. Poll `TaskOutput` to completion within the same turn.
- Post-flight dirty-tree check runs here, in this skill, never inside a spawned agent, right before a repo's status is trusted.
- Max 2 build/validate rounds per issue. Second failure escalates — never force-merges, never silently drops the issue from the final report. Escalation means commented + torn down, not parked for later.
- Post-flight residue enumeration (step 8b) covers every touched repo, not just merged ones, fails loud. A check that can't establish a fact reports `unknown`, never clean.
- The leftover-directory glob is per repo, in both the teardown prompt and step 8b — never fleet-wide (concurrent sweeps otherwise report another repo's live worktree as this lane's residue).
- A leftover worktree directory is judged by condition, never path or count (all five hold → zombie-pinned per step 8b; any one unestablished → RESIDUE). No rule may require attributing a zombie process to a directory — `CLEAR` is the whole requirement. No condition may depend on a tool the repo might not ship — the live-holder proof (`skills/_lib/dir_holders.py`) is repo-agnostic on purpose (fleet-config#571).
- Teardown judges its own lane's mess, never the repo's — its own branch/worktree/dirty tree halts the run; foreign branches join `indexLock`/`behindOrigin`/`zombieShells` in the reported-only tier (fleet-config#572).
- A stale `index.lock` and a behind-origin primary are reported, never halting, never silently repaired — lock deleted only when no live `git.exe` holds it and it's older than 5 minutes; behind-origin fast-forwarded `--ff-only` only, never merge/rebase/reset/`--force`; a refused fast-forward is `unknown`, not a pass.
- A run that stops with lanes unprocessed must print the literal `SCHEDULED-RUN-FAILED` marker — pre-flight failure (step 1), every-candidate-repo-skipped stop (step 5), or residue halt (step 10, non-null `halted`) — so `claude_progress.py` maps the run to exit 123 instead of exit 0 (fleet-config#612). The step 3 empty-queue stop is exempt — legitimate success, never prints the marker.
- No AI attribution; no hard-wrapped issue/PR-body paragraphs (per global CLAUDE.md).

## Notes

- **Relationship to `/cleanup-fleet`:** that skill stays exactly as-is, the attended single-bucket human-gated tool — nothing here replaces it. This one is for a scheduled overnight run where nobody is watching.
- **Compose, don't reinvent:** the build agent's mechanics reuse `/issue-start <N> now`; the execute agent's mechanics reuse `/issue-finish`'s push/PR/CI/merge/tray-restart sequence — same as `/cleanup-fleet` and `/issue-finish-batch` already do.
- **Validated attended, once, against the `stale` bucket** (4 repos), all merged on the first round. Two environment bugs surfaced and were worked around: `scriptPath` invocation (step 7) and `args` arriving inside the workflow script as a JSON string rather than a parsed object — `.claude/workflows/cleanup-fleet-all.js` defensively `JSON.parse`s it when it comes through as a string. Neither is specific to this skill's content; worth re-testing if the harness changes.
- **Before trusting the full unattended schedule**, run at least one more attended pass covering a bucket with a validator rejection (to prove the retry loop, not just the happy path) before wiring `run-weekly.bat` into a scheduled job.
- **Prior incidents this shape encodes** — read the issues before relaxing any rule above: fleet-config#534 (teardown honesty: checks that passed without establishing the thing they claimed → the two reported-only probes + the by-condition zombie exception); fleet-config#518 + #515 (2026-07-31 rewrite after a within-bucket parallel fan-out left 11 stray worktrees and two primaries off `main` while reporting `0 failed` → serial lanes, the Teardown agent, residue halts, forced worktrees, step 8's fleet-wide enumeration); fleet-config#642 (skipped repos dropped rather than deferred — the same false-completeness shape as #560/#607/#612/#623 → `repo_preflight.py`, the 7b retry, the `cleanup-deferred` issue; the skip criteria themselves were correct and were left untouched); fleet-config#612 (a correct halt that never reached the process exit code → the `SCHEDULED-RUN-FAILED` marker).
