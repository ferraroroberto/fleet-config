---
name: cleanup-fleet-all
description: Unattended, all-bucket sibling of /cleanup-fleet — builds, validates, and ships every open cleanup issue across all eight queued audit buckets in one overnight pass, no human in the loop. Use for a fully unattended fleet-wide cleanup run — e.g. "/cleanup-fleet-all", "clean up the whole fleet overnight", "run cleanup on all buckets unattended". Runs headless via a scheduled claude -p job.
---

# cleanup-fleet-all

**Goal:** `/cleanup-fleet` processes one bucket at a time and, in its default `hard` mode, stops for a human to approve the plan and review hard-tier work before merge. This skill is the genuinely unattended sibling: it walks **all eight queued** audit buckets, serially, and ships every issue with **no human review gate at all** — replaced by an independent validator agent instead. No single agent both builds and ships its own work unchecked. (`security` is never queued — `/codebase-audit` self-heals it inline — and `cert-drift`, `/design-sync`'s other kind, is review-only, never auto-migrated; nothing here touches either.)

**Four agents per issue, never fewer:**

1. **Build** — implement the fix in a forced worktree and run the project's verification gate, then **stop** before shipping.
2. **Validate** — a fresh, independent agent with no memory of the build. Re-runs the verification gate itself and judges, leniently, whether the diff actually addresses the issue.
3. **Execute** — ships an already-validated branch: push, PR, CI, merge, tray restart.
4. **Teardown** — the terminal step of **every** lane, whatever the outcome: comments the failure reason + any WIP SHA onto the GitHub issue (non-merged lanes only), removes the worktree via `worktree_claim.py remove-worktree`, releases the claim, deletes the branch, and runs **six verification checks**. Four of them decide residue (worktree list, no leftover sibling directory, **this lane's own branch** gone, clean tree); two are **reported and never halt** — a stale `.git/index.lock` and a primary behind `origin/<default>` (fleet-config#534). Other lanes' leftover branches are reported too, never residue (fleet-config#572).

A failed validation retries the build **once** (feeding it the validator's feedback verbatim), for a hard cap of **2 rounds**; a second failure escalates the issue. **Escalation is not "leave the branch for a human"** — the open GitHub issue plus the teardown agent's comment on it *is* the durable record; the branch and worktree are torn down like any other lane (fleet-config#518).

**One bucket at a time, one repo at a time.** Lanes are strictly serial: the next repo does not start until the current lane has run all four agents and its repo is verified clean. At most one worktree exists at any instant. (Issues within a bucket used to fan out in parallel; that concurrency, combined with an escalation path that tore nothing down, caused the incident in Notes.)

**Halt on residue.** If teardown cannot return a repo to a clean state, the run **stops there** — it does not start the next lane. Because lanes are serial, exactly one repo is ever affected, so a halted run is one command to recover from; continuing is how one forgotten worktree became eleven.

**Never a primary checkout.** Every build/validate/execute agent is told to force worktree mode (`worktree_claim.py acquire … --force-worktree`) for every repo, no exceptions and no special-cased list — a *running* app (the launcher webapp, home-automation's tray) or a live junction (`fleet-config`'s own `hooks/` + `skills/` into every `~/.claude`) is not a claim holder, so an unattended agent otherwise legitimately wins `MODE=primary` and edits files a live process is serving (fleet-config#515). The same briefs state that a live-e2e guard refusal is a hard STOP: `E2E_LIVE=1` or any equivalent override is forbidden.

All of the actual retry/ship decision-making lives in **`.claude/workflows/cleanup-fleet-all.js`**, a Workflow script — not in this SKILL.md and not in a fourth "orchestrator" agent. That decision (retry vs. ship vs. escalate) is a fixed lookup on each agent's own schema-validated verdict (`verification`, `retryable`, `pass`, retry-round count) — the judgment calls themselves happen once, inside the Build and Validate agents that are positioned to make them, never re-litigated by whatever reads the result. See the workflow script's header comment and `docs/model-tiers.md`.

## Arguments

`/cleanup-fleet-all [<bucket>...]` — zero or more bucket names, fuzzy-matched via the same synonym table `/cleanup-fleet` uses (`documentation`/`docs`, `claude-md-drift`/`drift`, `duplication`/`dupes`, `stale`/`dead`, `maintainability`/`maint`, `slop`/`bloat`, `bug`/`bugs`, `design-drift`/`design`).

- **No arguments** → all eight queued buckets, the intended unattended shape.
- **One or more bucket names** → restrict to just those — use this for an attended dry run of a small slice before trusting a full overnight sweep.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh`/`git` only — no PowerShell syntax. Windows paths map as `/e/automation/...`.
- **The orchestrator (this skill) only does cheap, safe work:** auth check, the issue fetch, grouping/dedupe (model-side, no jq/python), the rate-gate check, invoking the Workflow, and post-flight reporting. **It never edits source, commits, pushes, or merges** — every write happens inside an agent spawned by the workflow script.
- **Never disturb in-progress work.** A repo that is dirty or off its default branch is skipped and reported — never stashed, never force-switched. Skipped is not dropped: it is deferred, retried once after the last bucket, and recorded durably (steps 5, 7b, 8c).
- **Never background a tool call in this skill — this is the rule that matters most here.** This orchestrator runs headless via `run-weekly.bat`'s one-shot Claude process (streamed through `claude_progress.py`) with no persistent turn loop and no human attending it. There is no wake-up mechanism to resume the session after a turn ends, so launching a command and then ending the turn to "wait for it" silently kills the entire run: the CLI exits immediately reporting `exit_code: 0` (false success) while nothing past that point ever happened (`fleet-config#314`, the exact failure `/audit-fleet` hit twice). This applies to the `Workflow` tool call in step 7 exactly as much as to a backgrounded `Agent` dispatch — `Workflow` also returns immediately and notifies later, the same async shape. Every long-running call here — including the rate-gate wait — must run synchronously (foreground) or poll to completion **within the same turn** (e.g. `TaskOutput` with `block: true`, re-issued in a loop; or the `Monitor` tool's until-loop pattern for the rate-gate wait), never fire-and-forget.
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

This is the **preferred primary fetch**, not `gh search issues --owner ferraroroberto`: that call is backed by GitHub's Search API, which is documented as eventually consistent and was observed reporting 23 issues as open for five-plus weeks after they had actually been closed — 46 wasted agent invocations, ~2.9M tokens, confirming already-shipped work before any code was touched (fleet-config#623). `gh_issue_fetch.py` reads the same information through the direct Issues API instead, one `gh issue list --repo <owner>/<name> --state open` per repo, aggregated into the same shape `gh search issues` returns.

**Read that as "avoids a known-bad source", not "proven immune."** The repo-scoped smoke test that motivated this was a single same-day observation, not a guarantee about every cache layer between `gh` and GitHub's backend — which is exactly why step 5 still re-checks each selected issue's state immediately before dispatch rather than treating this fetch as sufficient on its own. The two cover different failure modes and neither subsumes the other.

Read the JSON directly (no jq/python/awk — group and select model-side, same convention as `/cleanup-fleet`; the aggregation itself happens inside the purpose-built helper, not by hand-processing raw `gh` output). For each issue, collect every label that matches one of this run's resolved bucket names — **drop any row carrying `audit-meta`** (the ledger issues, never actionable), and drop any row matching none of the resolved buckets. An issue carrying more than one bucket label legitimately appears in more than one bucket's list; that's fine, buckets run serially so it's never worked on twice at once.

If the helper's stderr summary reports `ERRORS=` greater than zero, note which repos it could not read (printed as `ERROR <repo>: <reason>` lines) in the final report — those repos are simply absent from this run's candidates, not a run-wide failure.

If nothing survives for any resolved bucket: print `No open cleanup issues across the fleet 🎉` and stop. This is a legitimate empty-queue success, not a self-reported failure — the queue was checked and found empty, so do **not** print the `SCHEDULED-RUN-FAILED` marker here.

### 4. Group by (bucket, repo) + enforce one issue per repo per bucket

Within each bucket, group surviving issues by `repository.name`:

- Exactly one candidate → that's the issue.
- More than one → select one, defer the rest (record for the final report). Preference: (1) the audit-managed issue (body contains `<!-- audit-managed:`), else (2) the smallest/clearest-acceptance one.

### 5. Pre-flight per selected repo

**Re-verify every selected issue's live state first, in one batch, before any per-repo check below.** Even the direct-Issues-API fetch (step 3) is not proven immune to every staleness source (see step 3's caveat), and this run can sit for hours — an issue selected at step 4 can close for real while an earlier bucket is still running. Build one JSON array of every selected issue across every bucket (`[{"repo": ..., "number": ..., "bucket": ..., ...other fields...}, ...]`) and pipe it through:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/issue_state_gate.py partition
```

Read the stdout JSON directly — `{"dispatch": [...], "skipped_closed": [...], "unresolved": [...]}`. **Only `dispatch` items proceed past this step.** For every item in `skipped_closed`, drop it from its bucket's list and record it in the final report as `already closed — dropped, no agent dispatched`. For every item in `unresolved`, **also drop it from this run** — the check could not establish the issue's state, so it is dropped rather than guessed as open, and recorded in the final report as `state unresolved — dropped, not dispatched (<detail>)`, distinct from both the closed and the dispatched sets. This must never collapse into "no issues found" — the final report's headline must show all three counts (`dispatched`, `already-closed`, `unresolved`) so an unresolved batch is visible, not read as a quiet empty run.

**Then gate every surviving issue on its repo's availability, in one batch.** Build a JSON array of every `dispatch` item (`[{"repo": ..., "number": ..., "bucket": ..., ...}, ...]`) and pipe it through:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/repo_preflight.py partition
```

It resolves each distinct repo exactly once (however many buckets it appears in) and runs the full per-repo check — `E:\automation\<repo>` exists · `status --porcelain` empty · HEAD on the default branch · `fetch origin` · `worktree list` shows only the primary. Read the stdout JSON directly: `{"dispatch": [...], "skipped": [...]}`. Each skipped item carries `repo_state` (`missing`/`dirty`/`off-branch`/`worktree`/`unknown`) and `skip_reason`; the stderr line gives `DISPATCH= SKIPPED_REPOS= SKIPPED_ISSUES= UNKNOWN_REPOS=`.

Only `dispatch` items proceed. **The `skipped` list is this run's deferred set** — carry it to step 7b, which re-checks it, and to step 8c, which records whatever is still unavailable. A dirty repo drops every one of its selected issues across all buckets, exactly as before; the helper enforces that by resolving per repo rather than per issue.

`unknown` (git unreadable) does not dispatch — an unreadable repo is not a repo proven safe to work in — but it is counted separately from a confirmed dirty tree, and the report keeps them apart. A failed `fetch origin` is recorded in the item's `note` and never changes the verdict: what makes a repo unsafe to work in is a dirty tree, a wrong branch, or someone else's worktree, not an unreachable network.

The **skip criteria are unchanged** and never soften — never stash, never force-switch, never remove a worktree you did not create. This gate only changes what the run does with the knowledge that it skipped something.

**If candidates existed and `dispatch` is empty**, every one of them was skipped: there is nothing to retry later (a retry needs a completed run to follow) and no lane will run. Record the deferred set through step 8c, then stop and print in the final report the literal line `SCHEDULED-RUN-FAILED — every candidate repo was skipped (<N> repos, <M> issues unprocessed), no lane ran`. This is **not** step 3's empty-queue case: there the queue was checked and genuinely found empty, which is a success; here there was real work and the run touched none of it, so it must not read as a clean sweep.

**Worktrees always** — every build agent forces `MODE=worktree` and works `<repo>-wt-<N>`, never the primary checkout, for every repo (fleet-config#515). The primary is only ever read (pre-flight above) and, at teardown, checked back to clean. Lanes are serial, so a repo touched by two buckets is never touched by two agents at once, and at most one worktree exists fleet-wide at any moment.

**A build agent's handoff is a committed branch, never a dirty tree** (fleet-config#641). The build brief's STOP step forbids exactly four actions — push, PR, merge, `/issue-finish` — and committing is not one of them; the validate agent's first act is `git status --porcelain` on the worktree, and a dirty tree is an immediate `pass: false` regardless of the otherwise-lenient default. This is the one rejection reason that is not a judgement call. It is asserted at the boundary because the failure is otherwise invisible: the execute agent's `/issue-finish` commits pending work as a safety net, so an uncommitted handoff ships fine and recurs silently. It is not harmless — uncommitted work has no SHA, so the escalation comment's WIP SHA (step 8's durable record) has nothing to point at, and an escalation or crash between build and execute loses the work instead of parking it reflog-recoverable for ~90 days. A build that legitimately changed nothing still leaves a clean tree, so the assertion is on the tree, never on the commit count.

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

Re-issue this call (each blocks up to 10 minutes) until the returned status is `completed`, as consecutive tool calls within this same turn — this may take many calls over several hours for a full seven-bucket run, and that's expected. Serial lanes make a full run slower in wall-clock than the old parallel shape; that is the trade being bought, not a regression to fix.

The workflow's return value is `{ buckets: [{ bucket, results: [...], skipped? }, ...], halted }`:

- each result is `{ issue, status, round, branch, worktree, residue, residueDetail, indexLock, indexLockDetail, behindOrigin, behindOriginDetail, zombieShells?, pr?, mergeSha?, reason?, wipSha?, alreadyClosed? }` — `status` is one of `merged`, `escalated`, `failed`; `residue` is `CLEAN` or `RESIDUE`. `indexLock` (`none`/`stale-cleared`/`live-held`/`unknown`) and `behindOrigin` (`current`/`fast-forwarded`/`unknown`) are **reported only** — they never gate a lane and never halt the run, and they default to `unknown` when the teardown agent died or omitted them. `zombieShells` names any leftover directory that satisfied all five zombie-pinned conditions (step 8b) and was therefore not counted as residue; `foreignBranches` names local branches belonging to no lane of this run — also reported, also never residue. `alreadyClosed: true` means an issue that step 5's batch check let through nonetheless turned out closed by the time /issue-start reached it, hours into the run (fleet-config#623) — the lane still escalates and still tears down, but its teardown skips the usual "unattended lane escalated" comment, since posting one on an already-resolved thread is noise, not a record. Note this is a *different* population from step 5's `skipped_closed`/`unresolved`, which never reach `results` at all — they never entered `issuesByBucket`, so no build agent, let alone a full lane, ran for them.
- `halted` is `null` on a full run, or `{ bucket, repo, issue, status, detail, remainingInBucket }` when a lane's teardown left residue and the run stopped there. **A halted run is a loud failure, not a partial success** — report it at the top of the final summary, name the one repo, and say exactly what a human must do.

### 7b. Retry the deferred set — one pass, after the last bucket

A repo that was dirty at pre-flight has usually been committed and pushed by the time the last bucket finishes: a full run spans many hours, and step 5's verdict is hours stale by now. So the deferred set gets exactly one more chance, as late as possible.

**Skip this step entirely if step 7's `halted` is non-null.** A halted run has left residue and must not start another lane.

Re-run the *same* gate over the deferred set — the whole array step 5 skipped, unchanged:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/repo_preflight.py partition
```

The helper holds no state between calls, so this necessarily re-establishes every fact from the live tree rather than trusting step 5's verdict — the tree may have changed in either direction, and a repo that has since *become* dirty must not be dispatched on an hours-old "available".

Anything now in `dispatch` gets one retry pass: rebuild `issuesByBucket` from those issues only, and invoke the workflow a second time exactly as step 7 describes — same inline `script` parameter, same blocking `TaskOutput` poll to completion within this turn. The serial-lane invariant holds by construction: this invocation starts only after the first has fully completed, so there is still at most one worktree fleet-wide at any instant.

Merge its `buckets` results into the report under the same bucket names, marked `(retry)`. Its `halted` is handled exactly like step 7's. **One pass, never a loop** — whatever is still unavailable stays deferred and goes to step 8c.

### 8. Post-flight verification (never trust the agent's self-report)

The teardown agent already verified its own lane. This step is the independent second look — same "a check that can't establish a fact reports `unknown`, never `pass`" discipline the global CLAUDE.md requires.

**8a. Per-issue tree check.** For every issue with `status: "merged"`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → downgrade from `✅ merged` to `⚠️ merged but dirty tree — inspect <repo>` and carry the `REASON=` line.

`STATUS=UNKNOWN` → the helper could not read that repo at all, so it has **no** verdict about it: render `❓ merged, tree unverified — <REASON>` and never fold it into `✅` or `⚠️`. Check the path you passed first — the known occurrence was the caller-side Git Bash backslash trap (a double-quoted Windows path loses its backslashes and never expands the loop variable), and the helper answered `DIRTY` for all five touched repos, every one of which was actually clean (fleet-config#570). Pass forward slashes.

For every issue with `status: "escalated"` or `"failed"` — the teardown agent should have put the repo back on a clean default branch, so check it the same way:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
```

`STATUS=DIRTY` → append `⚠️ post-flight: <REASON>` next to the escalation line. `STATUS=UNKNOWN` → append `❓ post-flight unverified: <REASON>` — an escalation whose tree could not be read is not an escalation whose tree is fine.

**8b. Fleet-wide residue enumeration — fail loud.** Checking only the primaries of touched repos is exactly what let 11 worktrees slip through a run that reported `0 failed`. After all buckets finish, enumerate residue across **every repo the run touched** (Git Bash, read-only):

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

The worktree glob is **per repo, inside the loop, and stays that way**. A fleet-wide `/e/automation/*-wt-*` makes one repo's check report another repo's in-flight worktree as residue (it happened once — app-launcher#709's lane flagged home-automation's live worktree).

A dirty tree, a primary off its default branch, a second registered worktree, or a branch belonging to *this run's* lanes is **residue** — with two exceptions, the same ones the teardown agent applies. A leftover `<repo>-wt-<N>` directory is **not** residue when all five hold, each proved by running the command: it is recursively empty; it is a real directory, not a reparse point (read the attribute bit via `powershell.exe -NoProfile -Command "(Get-Item -Force '<path>').Attributes"`); it is absent from `git worktree list`; `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dir_holders.py check '<path>'` prints `STATUS=CLEAR`; and `worktree_claim.py remove-worktree` was run against it and refused. Windows keeps the *process objects* of exited WebKit e2e helpers alive while any handle remains, and the empty shell they pin cannot be deleted until reboot — inert, but undeletable. Any one condition unestablished is residue: `STATUS=LIVE` (a running process names the path — it prints each holder's pid and command line) and `STATUS=UNKNOWN` (the probe could not run) are both residue. **Never ask which zombie pins which directory** — an exited process is simply absent from the process table, which is precisely the case this exception exists for; a `CLEAR` verdict is the whole requirement and is sufficient. Report qualifying shells as `🧟 zombie-pinned (not residue)` with path and probe verdict; several of them are the expected state on a host that has not rebooted, so nothing keys on how many there are.

That probe is **repo-agnostic on purpose**: it runs from fleet-config's own venv against the Windows process table and needs nothing from the target repo. The requirement used to be the repo's own `tests/e2e/_browser_sweep.py`, which exists in **4 of 14 fleet repos** — so in the other ten this condition was unprovable by construction and any leftover directory was guaranteed to halt the run whatever was actually in it (fleet-config#571). Where `_browser_sweep.py` does exist it remains the better instrument for classifying leaked browser helpers and running it as well is welcome; its absence proves nothing. The probe reads command lines and executable paths, so a process merely `cd`-ed into the directory with nothing naming it is invisible — stated, not papered over, and the reason an unrunnable probe is `UNKNOWN` rather than `CLEAR`.

The second exception is a **foreign branch** — a local branch belonging to no lane of this run: an earlier day's lane, a human session, an abandoned experiment. It is reported as `🌿 <repo> — foreign branch <name> (PR #N merged, diff vs default empty)`, never residue, and it never halts. Teardown's mandate is its own lane, so no check may assert a repo-wide property teardown is forbidden to bring about; a lane that shipped perfectly once reported RESIDUE over a stale branch from an earlier lane whose PR was already merged, and halted the run with 41 lanes unstarted (fleet-config#572). Judging “is this branch safe to delete” needs an empty `git diff <default>..<branch>` or the PR's merge state — **not** `git branch --merged`, which reports a squash-merged branch as unmerged because the original tip is no ancestor of the default branch (the fleet-config#567 behaviour). Report them; do not delete them.

Residue is never folded into a `✅`/`📋` line: it gets its own `❌ RESIDUE` block in the final summary naming the repo, the leftover path/branch, and the one-line recovery command. If a check could not run at all, report it as `❓ unknown`, never as clean.

**Neither of the last two probes is residue and neither halts anything** (fleet-config#534) — they are reported alongside it:

- **`index.lock`** — present with a **live** `git.exe` naming that repo (`powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='git.exe'\" | Select-Object ProcessId,CommandLine | Format-List"`) → report `❓ index.lock held by live git (pid N) — left alone`, and touch nothing. Present with **no** live git and an mtime older than 5 minutes → report it by name and age as a stale lock, delete it, and retry the `rev-list`/pull below. Anything unestablished → `❓ unknown`, leave it in place. This is the condition that silently blocks every `git pull` and turns a "clean" primary into a stale one.
- **Behind origin** — a non-zero `rev-list --count HEAD..origin/<default>` is fast-forwarded with `git -C /e/automation/$r pull --ff-only` (never a merge, a rebase, a reset, or `--force`) and reported as `⬆️ <repo> was N behind — fast-forwarded <before>→<after>`. A refused fast-forward, a failed fetch, or a still-locked index → `❓ unknown` with the reason, not a silent pass. **Never pull over a repo this step already found dirty or off its default branch** — report the count as `❓ unknown` instead; that repo is residue and mutating it destroys the evidence. A foreign branch is *not* a reason to withhold the fast-forward: what makes a pull unsafe is a dirty tree or HEAD off the default branch, and gating on unrelated refs once left a healthy primary two commits behind (fleet-config#572).

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

An **open** `cleanup-deferred` issue therefore always means *there is unprocessed work*, never *the last run had nothing to say*. A tracking issue left open saying "nothing to do" is a zombie, and it trains a reader to skim past exactly the thing meant to catch their eye.

This does **not** replace the report line: step 10's skipped counts are printed on every run including the zero case. The issue carries live work; the report carries the audit trail that the check ran at all. Never collapse the two — "we checked and it was clean" is a fact that belongs in the report, and it is the fact whose absence started this whole class of bug.

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

The `candidates:` line is **mandatory on every run, even when both extra counts are zero** — step 5's state re-check can drop issues before any bucket runs, and a run that silently shrank its own working set reads as "nothing to do" when it actually skipped real candidates (fleet-config#623, echoing the same false-confidence shape as fleet-config#560 and #612). `already-closed` and `unresolved` are two distinct counts, never combined into one "skipped" number — an unresolved state check is not the same fact as a confirmed closure, and collapsing them back into one number is exactly the failure this line exists to prevent.

The `skipped:` line is **mandatory on every run too, even when every count is zero** (`skipped: 0 repos, 0 issues unprocessed`), for the same reason and by the same rule. It carries two numbers that are not interchangeable: how many repos were skipped, and **how much live work went unprocessed as a result** — the second is what the old single footnote line silently hid, and unlike an already-closed issue a skipped repo's issues are still work nobody has done. Repos whose state could not be established are counted apart from confirmed-dirty ones, never folded in. The retry recovery count belongs on this line as well, so a reader sees the headline number and its resolution together rather than having to reconcile two sections.

**If every candidate repo was skipped** (step 5 left `dispatch` empty while candidates existed), the report must print the literal line `SCHEDULED-RUN-FAILED — every candidate repo was skipped (<N> repos, <M> issues unprocessed), no lane ran`. A run that touched none of the real work it found is not a clean sweep, whatever the per-bucket counts read. Step 3's genuine empty-queue stop stays exempt, as it always was.

**If (and only if) step 7's workflow result carries a non-null `halted`** (`{ bucket, repo, issue, status, detail, remainingInBucket }`), the final report must also print the literal line `SCHEDULED-RUN-FAILED — halted at <repo>#<issue>: <detail>, <remainingInBucket + issues in later buckets> issue(s) never started`, exactly as shown above, so `claude_progress.py` maps this run to exit 123 instead of falling through to the harness's default exit 0 (fleet-config#612 — a run that halted 1 of 9 lanes reported exit 0 and showed green on the Jobs card). A fully successful run — `halted` is `null` — must **not** print this line; the marker is opt-in, not a default.

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
- **Never disturb in-progress work.** Dirty/off-default-branch repos, and repos that already have a worktree when the run starts, are skipped and reported — never stashed, force-switched, or torn down. The defer/retry machinery below changes only what the run does with the knowledge that it skipped something; the criteria themselves never soften.
- **A skipped repo is deferred, never dropped** (fleet-config#642). Its issues are re-checked once after the last bucket (step 7b), recorded in the `cleanup-deferred` tracking issue if still unavailable (step 8c), and counted on the mandatory `skipped:` report line (step 10) — never a single footnote that exists only in one run's stdout. **The retry re-runs the full pre-flight, never a cached verdict**: `repo_preflight.py` holds no state between calls precisely so this cannot be got wrong, and a repo that has *become* dirty since step 5 must not be dispatched on an hours-old "available". One retry pass, never a loop, and never when the run halted.
- **An open `cleanup-deferred` issue always means there is unprocessed work.** A run that ends with nothing deferred *closes* it with a comment; a run that defers something reopens-or-creates it via `audit_issue.py upsert --reopen`. The issue's open/closed state is the signal — a tracking issue left open saying "nothing to do" is a zombie and trains a reader to skim past it. The always-printed report line is a separate fact and is never collapsed into the issue.
- **A run that skipped every candidate repo prints `SCHEDULED-RUN-FAILED`.** Real work was found and none of it was touched; that is a delivery failure, not a clean sweep.
- **`design-drift` fixes obey `/design-sync`'s structural rule.** The build agent may auto-fix token/palette/spacing drift, but must **never re-author navigation or components** — reuse the vendored `project-scaffolding` snippet verbatim. A structural finding it cannot resolve by re-vendoring must fail validation and **escalate** (branch left for a human), never auto-merge a hand-rolled rewrite. `cert-drift` is not a bucket here at all — its migration is never auto-applied.
- **Never background a tool call and end the turn expecting a resume — this includes the `Workflow` call itself.** Poll `TaskOutput` to completion within the same turn.
- **Post-flight dirty-tree check runs here, in this skill, never inside a spawned agent**, right before a repo's status is trusted.
- **Max 2 build/validate rounds per issue. A second failure escalates — it never force-merges and never silently drops the issue from the final report.** Escalation means *commented on the issue and torn down*, not *branch parked for later*.
- **Post-flight residue enumeration (step 8b) covers every touched repo, not just merged ones, and fails loud.** A check that can't establish a fact reports `unknown`, never clean.
- **The leftover-directory glob is per repo, in both the teardown prompt and step 8b — never fleet-wide.** Concurrent sweeps mean `/e/automation/*-wt-*` reports another repo's live worktree as this lane's residue.
- **A leftover worktree directory is judged by condition, never by path or count.** All five hold (recursively empty, not a reparse point, git-deregistered, `dir_holders.py check` reports `STATUS=CLEAR`, `remove-worktree` was run and refused) → zombie-pinned, not residue, no halt. Any one unestablished → RESIDUE. Multiple qualifying shells are expected on a host that has not rebooted. **No rule may require attributing a zombie process to a directory** — an exited process is absent from the process table, so that attribution does not exist; a `CLEAR` verdict is the whole requirement. **And no condition may depend on a tool the repo might not ship** — the live-holder proof is repo-agnostic (`skills/_lib/dir_holders.py`, run from fleet-config's venv) precisely because requiring each repo's own `tests/e2e/_browser_sweep.py` made it unprovable in ten of fourteen repos, turning an exception into a guaranteed halt (fleet-config#571).
- **Teardown judges its own lane's mess, never the repo's.** Check 3 asserts that *this lane's* branch is gone, not that the repo has no other branches — step 5 only deletes this lane's branch and the brief forbids removing another lane's ref, so the old whole-repo form failed every subsequent lane in a repo with any pre-existing local branch. Foreign branches join `indexLock`/`behindOrigin`/`zombieShells` in the reported-only tier. This narrows *what counts as this lane's mess*; it does not lower the bar — the lane's own branch, worktree, or dirty tree still halts the run (fleet-config#572).
- **A stale `index.lock` and a behind-origin primary are reported, never halting, and never silently repaired.** The lock is deleted only when no live `git.exe` names the repo *and* it is older than 5 minutes; a live holder or an unreadable state is `unknown` and is left alone. Behind-origin is fast-forwarded with `--ff-only` only — never a merge, rebase, reset, or `--force` — and a refused fast-forward is `unknown`, not a pass.
- **A run that stops with lanes unprocessed must print the literal `SCHEDULED-RUN-FAILED` marker in its final report** — a pre-flight failure (step 1), an every-candidate-repo-skipped stop (step 5), or a residue halt (step 10, non-null `halted`) — so `claude_progress.py` maps the run to exit 123 instead of a false-success exit 0 (fleet-config#612). The step 3 empty-queue stop (`No open cleanup issues across the fleet`) is exempt: it's a legitimate success, and must never print the marker.
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Relationship to `/cleanup-fleet`:** that skill stays exactly as-is, the attended single-bucket human-gated tool — nothing here replaces it. This one is for a scheduled overnight run where nobody is watching.
- **Compose, don't reinvent:** the build agent's mechanics reuse `/issue-start <N> now`; the execute agent's mechanics reuse `/issue-finish`'s push/PR/CI/merge/tray-restart sequence — same as `/cleanup-fleet` and `/issue-finish-batch` already do.
- **Validated attended, once, against the `stale` bucket** (4 repos), all merged on the first round. Two environment bugs surfaced and were worked around: `scriptPath` invocation (see step 7) and `args` arriving inside the workflow script as a JSON string rather than a parsed object — `.claude/workflows/cleanup-fleet-all.js` defensively `JSON.parse`s it when it comes through as a string. Neither is specific to this skill's content; worth re-testing if the harness changes.
- **Before trusting the full unattended schedule**, run at least one more attended pass covering a bucket with a validator rejection (to prove the retry loop, not just the happy path) before wiring `run-weekly.bat` into a scheduled job.
- **Teardown-honesty pass (fleet-config#534).** Three defects of one class — a check that passes or fails without having established the thing it claims: a stale `.git/index.lock` silently breaking `git pull`; teardown verifying "clean" but never "current"; empty zombie-pinned shells counted as residue. The first two became the reported-only checks 5 and 6 above; the third became the by-condition zombie-pinned exception, deliberately repo-wide (`live=0`) rather than per-directory, because an exited process reports `cwd=<unreadable>` and no rule may ask for more.
- **2026-07-31 rewrite (fleet-config#518 + #515)**, after a within-bucket parallel fan-out left 11 stray worktrees and two primaries off `main` while still reporting `0 failed` — every stray came from a lane the workflow considered a *success path*. Response: within-bucket `parallel(...)` became a serial loop; a fourth Teardown agent became the terminal step of every lane; residue halts the run; all agent briefs force worktree mode and ban live-e2e overrides; step 8 gained the fleet-wide residue enumeration.
- **Skipped repos were dropped, not deferred (fleet-config#642).** A repo that was dirty, off-branch, or already held a worktree at pre-flight got one footnote line and was then forgotten — its issues not retried, not carried forward, while the run still reported itself complete. The same false-completeness shape as #560/#607/#612/#623, and self-selecting: the repos most likely to be dirty mid-run are the actively-developed ones, whose cleanup backlog grows fastest, so a weekly run could skip the same repo indefinitely. Three parts: the per-repo check became the re-runnable, stateless `skills/_lib/repo_preflight.py` (shaped after `issue_state_gate.py` — the reason that gate works is that its counts are computed by something re-runnable); one retry pass after the last bucket, which by construction re-establishes every fact rather than replaying an hours-old verdict; and a `cleanup-deferred` tracking issue whose open/closed state means "there is unprocessed work" or nothing. The skip criteria themselves were deliberately left untouched — never stashing or force-switching someone else's tree is the correct call and was never the bug.
- **False-success exit code (fleet-config#612).** The halt behaviour was already correct but never reached the process exit code — the halt path did not print the `SCHEDULED-RUN-FAILED` marker `skills/_lib/claude_progress.py` watches for, so a run that halted 1 of 9 lanes reported `exit 0` and showed green. Step 1 and step 10 now print it on any stop that leaves lanes unprocessed; the step 3 empty-queue stop is exempt.
