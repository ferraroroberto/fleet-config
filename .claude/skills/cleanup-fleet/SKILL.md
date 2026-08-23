---
name: cleanup-fleet
description: Take one bucket of audit findings (a label like documentation, drift, or bug) and fan out one background agent per repo to fix every open issue carrying it fleet-wide. The fix-half of /audit-fleet. Use to clear a category of audit work in one pass — e.g. "/cleanup-fleet documentation", "/cleanup-fleet drift", "clean up all the bugs", "/cleanup-fleet docs easy".
---

# cleanup-fleet

**Goal:** `/audit-fleet` *finds* and files codebase findings, bucketed into seven labels, and `/design-sweep` files an eighth (`design-drift`); this skill *fixes* one bucket fleet-wide in a single pass. Pick a bucket → gather every open issue carrying that label → score each for complexity → deploy **one background sub-agent per repo**, sized via the easy/hard tier policy (`docs/model-tiers.md`) → aggregate.

**`security` is not a cleanup bucket.** `/codebase-audit`'s seven finding buckets (incl. `slop`) are all queued here for fixing; its `security` kind is the exception — self-healed inline by `/codebase-audit` itself (step 8b: redacted issue + auto-fix + auto-merge, or escalate on failure), never queued here. `/design-sync` contributes the eighth queued bucket, `design-drift` (web-app CSS/token/nav drift); its sibling `cert-drift` kind is likewise **review-only** — never queued here, since a tailnet-cert migration must never be auto-applied. This skill operates on the eight *queued* buckets below; a `security` or `cert-drift` label never appears here.

**One agent per repo, never two:** the audit files exactly one managed issue per (repo, bucket), so one issue → one repo → one agent → one branch → one PR. Two agents on one checkout collide, so the skill hard-caps at one agent per repo per run and defers extras.

**Two execution paths, both delegating to existing skills — don't reinvent them:**

- **Easy tier → full YOLO.** The agent runs the **`/issue-yolo <N>`** flow end-to-end: branch, build, validate hard, PR, wait for CI, merge, delete branch, tray restart. No human gate. Each agent fires its own `🚀 Shipped #N — PR · <url>` ping (the per-PR link is valuable — **not** suppressed).
- **Hard tier → build-and-stop.** The agent runs **`/issue-start <N> now`** → build → run the verification gate → **STOP before push/PR** (the `/issue-batch` in-place contract). Instead of a diff to review, the agent hands back a plain-English rationale summary — what it did, why, and why it believes the change is correct (see prompt 8b) — so you approve or push back on the logic, not by reading code. You then ship each with `/issue-finish`.

## Arguments

`/cleanup-fleet [<bucket>] [<mode>]` — both optional, order-independent.

**Bucket** — fuzzy-matched to one of the eight *queued* audit labels (case-insensitive; voice-dictation friendly):

| Says | Label |
|------|-------|
| `documentation`, `docs`, `doc` | `documentation` |
| `drift`, `claude-drift`, `cloud drift`, `claude-md-drift`, `md-drift` | `claude-md-drift` |
| `duplication`, `dupes`, `dup`, `dupe` | `duplication` |
| `stale`, `dead`, `dead-code` | `stale` |
| `maintainability`, `maint`, `structure` | `maintainability` |
| `slop`, `bloat`, `ai-slop` | `slop` |
| `bug`, `bugs` | `bug` |
| `design`, `design-drift`, `css`, `css-drift` | `design-drift` |

(`security` is intentionally absent — self-healed inline by `/codebase-audit`; `cert-drift` is likewise absent — it's `/design-sync`'s review-only kind, never auto-fixed here.)

If **no bucket** is given → run step 2's count query, then `AskUserQuestion` listing the eight queued buckets each with its **live open-issue count**, and let the user pick.

**Mode** — `hard` (default) or `easy` / `silent`. (This is the CLI argument, distinct from the per-issue complexity *tier* below — always read as "`hard` mode" vs. "hard-tier issue" to keep the two straight.)

- **`hard` mode** (default) — full sweep: easy-tier issues take the YOLO path, hard-tier issues build-and-stop for review. The plan is **presented for approval first**.
- **`easy` / `silent` mode** — only the easy-tier issues, fully unattended (no approval gate). Hard-tier issues are **listed but never run** ("left for a hard run"). This is the mode to run alongside `/audit-fleet` on a schedule. Safety property: easy mode can *only ever* auto-merge work that scored genuinely simple — any hard-tier finding is never executed in this mode.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh` / `git` only — no PowerShell syntax (`&`, `$env:`, here-strings). Windows paths map as `/e/automation/...`.
- **The orchestrator only does cheap, safe work:** resolve the bucket, **one** issue-fetch call, score, plan, per-repo pre-flight, fan-out, aggregate. **It never edits source, commits, pushes, or merges** — every write happens inside a spawned agent.
- **Read the issue JSON directly.** Do not spawn jq / python / awk to process the output — group, score, and select model-side, exactly like `/issue-triage`.
- **One agent per repo, period.** Never spawn two agents against the same checkout.
- **Never disturb in-progress work.** A repo that is dirty or off its default branch is skipped and reported — never stashed, never force-switched.
- **Degrade, don't block** (so `easy`/`silent` can run unattended via `claude -p`): a per-repo failure is reported and skipped; only a pre-flight failure stops the whole run.
- **In `easy`/`silent` mode, never background-and-wait.** An attended `hard`-mode run is a normal top-level Claude Code session, and the harness *does* re-invoke it as each background sub-agent completes (step 9's "stop and stand by" is correct there). But `easy`/`silent` mode is designed to run headless via `claude -p` (see the Notes' scheduling example) — a headless run has **no** wake-up mechanism at all, so stopping to "wait for the harness" in that mode silently kills the run: the CLI exits `0` immediately and every dispatched agent gets killed at the background-task ceiling with nothing collected (`fleet-config#506`, `fleet-config#314`). In `easy`/`silent` mode, step 9 must instead block on `TaskOutput` (`block: true`) for every in-flight agent within the same turn, re-issuing on timeout, and never end the turn until the selected-issue list is fully drained.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.

### 2. Resolve bucket + mode

Parse the args (order-independent): the mode token is `hard`/`easy`/`silent`; anything else is the bucket. Map the bucket through the synonym table to its canonical label. Default mode `hard`. If no bucket token was given, fetch the per-bucket counts and ask:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gh_issue_fetch.py fetch
```

Tally open issues per bucket label (drop `audit-meta` rows; a `security` or `cert-drift` row should never appear — neither is queued here — but drop them too if one somehow exists), then `AskUserQuestion` listing the eight queued buckets with counts.

### 3. Fetch candidates — direct Issues API, one repo-scoped call per repo

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gh_issue_fetch.py fetch --label <bucket-label>
```

This is the **preferred primary fetch**, not `gh search issues --owner ferraroroberto`: that call is backed by GitHub's Search API, which is documented as eventually consistent and was observed reporting 23 issues as open for five-plus weeks after they had actually been closed — 46 wasted agent invocations, ~2.9M tokens confirming already-shipped work (fleet-config#623). `gh_issue_fetch.py` reads the same information through the direct Issues API instead, one `gh issue list --repo <owner>/<name> --state open` per repo. **Read that as "avoids a known-bad source," not "proven immune"** — the repo-scoped smoke test that motivated this was a single same-day observation, not a guarantee about every cache layer between `gh` and GitHub's backend. That's why step 8 still re-checks each selected issue's state immediately before dispatch rather than treating this fetch as sufficient on its own; the two cover different failure modes and neither subsumes the other.

Read the JSON directly. **Drop any row carrying the `audit-meta` label** — those are the per-repo `codebase-audit ledger` and the `audit-fleet digest state` issues, never actionable work. If the result is empty, print `No open <bucket> issues across the fleet 🎉` and stop. If the helper's stderr summary reports any `ERROR <repo>: <reason>` lines, note them in the eventual plan — those repos are simply absent from this run's candidates, not a run-wide failure.

### 4. Group by repo + enforce one-agent-per-repo

Bucket the surviving issues by `repository.name`. For each repo:

- **Exactly one candidate** → that's the issue for this repo.
- **More than one** (e.g. the audit-managed bucket issue *plus* a hand-filed one) → **select one** and **defer the rest** to keep a single branch per repo. Preference: (1) the audit-managed bucket issue (body contains `<!-- audit-managed:`) — it's the curated checklist; else (2) the smallest / clearest-acceptance one. Record the deferred issues for the plan ("caught next run").

### 5. Score each selected issue → easy tier or hard tier

Read each selected issue's title + body (for an audit bucket issue, also weigh the checklist length and the nature of its items). Two tiers, same spirit as `/issue-triage`'s S/M/L calibration collapsed to two (see `docs/model-tiers.md` for how each tier resolves to a model):

- **easy tier:** narrow surface, mechanical, clear acceptance, no design decision. Doc fixes, a handful of stale-code deletions, a missing README flag, a rename, a few tightly-scoped checklist items.
- **hard tier:** multi-module, real design choices, a refactor, an unbounded body, or a **mixed** checklist (trivial *and* hard items together → treat the whole issue as hard-tier; it absorbs the easy parts too).
- **`design-drift` specifically:** pure token/palette/spacing drift is easy-tier; any **structural** finding — a hand-rolled nav, a forked or re-authored vendored component, a layout rewrite — is hard-tier. `/design-sync`'s rule is *never re-author nav/components* (reuse the vendored snippet, don't rewrite), so a `design-drift` issue carrying one is worked build-and-stop for review, never auto-merged.

When genuinely on the fence, round **up** to hard-tier in `hard` mode (a human will still review it) and **down**-or-defer in `easy`/`silent` mode (never auto-merge something you weren't sure about).

### 6. Build + present the plan

Render one table and the headline counts:

```
/cleanup-fleet <bucket> — <mode> mode

  repo              #    title                          tier     model   path
  ----------------  ---  -----------------------------  -------  ------  -----------------
  photo-ocr         44   audit: documentation findings  easy     sonnet  YOLO → merged
  app-launcher      71   audit: documentation findings  hard     opus    build → review
  reporting         12   README missing --watch flag    easy     sonnet  YOLO → merged

  7 issues: 5 easy-tier (YOLO → merged), 2 hard-tier (build → review)
  deferred (1+ per repo): grocery-shopping#9 (hand-filed, next run)
  skipped (dirty/off-branch): website
```

The `model` column shows `sonnet` for easy-tier and `opus` for hard-tier on Claude Code today — the tier split drives both *execution shape* (full-autonomy vs. review-gated) and model choice; see `docs/model-tiers.md`.

- **`hard` mode:** present this plan and **wait for explicit approval** before spawning. The user may deselect issues or retier them. Do **not** spawn until approved.
- **`easy`/`silent` mode:** print the plan to stdout (run-log record), **skip the approval gate**, and proceed with **only the easy-tier rows**. List the hard-tier rows as "left for a hard run" — never spawn them.

### 7. Pre-flight per selected repo

For each repo with a selected (and, in `easy`/`silent` mode, easy-tier) issue:

- `E:\automation\<repo>` exists. Else skip + report.
- `git -C E:\automation\<repo> status --porcelain` empty. Else **skip + report** (never stash) — drop it from the run.
- `git -C E:\automation\<repo> fetch origin` (once per repo).
- `git -C E:\automation\<repo> worktree list` — anything beyond the primary is pre-existing residue or a live human session. **Skip + report**; never remove a worktree you did not create.

**Worktrees always** (fleet-config#515): every dispatched agent forces `MODE=worktree` and works `<repo>-wt-<N>`, never the primary checkout, for every repo. A *running* app or a live junction is not a claim holder, so `MODE=primary` on an unattended dispatch means editing files a live process is serving. Easy-tier agents tear their worktree down themselves (ship or fail); hard-tier agents leave theirs standing for the human's `/issue-finish`, and report its path.

### 8. Fan out — one background sub-agent per selected issue

**Re-verify state first, in one batch.** Even step 3's direct-Issues-API fetch is
not proven immune to every staleness source (see step 3's caveat), and `hard`
mode's approval wait can itself take long enough for an issue to close in the
meantime. Build one JSON array of every still-selected issue (`[{"repo": ...,
"number": ..., ...other fields...}, ...]` — `repo` a bare name like `"task-os"`,
never `"owner/name"`: the helper prepends the owner itself, and a prefixed repo
produces a doubled-owner `gh` argv that fails with a network-sounding error
unrelated to the network, fleet-config#706) and pipe it through:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/issue_state_gate.py partition
```

Read the stdout JSON directly — `{"dispatch": [...], "skipped_closed": [...],
"unresolved": [...]}`. **Only `dispatch` items get a sub-agent below.** For
every `skipped_closed` item, drop it and report `already closed — dropped, no
agent dispatched` in the run's summary. For every `unresolved` item — the check
could not establish open vs. closed (network error, rate limit) — **also drop
it from this run**, never guessed as open, and report `state unresolved —
dropped, not dispatched (<detail>)`, kept as its own category, never merged
into either the closed count or the dispatched count. The run's final summary
must show all three counts (`dispatched` / `already-closed` / `unresolved`)
even when the latter two are zero — a run that silently shrank its own working
set reads as "nothing to do," which is the same false-confidence shape this
issue exists to fix (fleet-config#623, #560, #612).

Before the mass easy-tier dispatch below, call
`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70`
once. `DECISION=PAUSE` → wait via the `Monitor` tool's until-loop pattern against
the printed `WAIT_SECONDS`/`RESETS_AT` before firing the batch (see
`docs/rate-gate.md`); `OK`/`UNKNOWN` → proceed immediately.

Dispatch one background sub-agent per selected issue (`run_in_background: true`, `subagent_type: "general-purpose"`, **`model` resolved from the tier** — `model: "sonnet"` for easy tier, `model: "opus"` for hard tier on Claude Code today, see `docs/model-tiers.md`), but **bound whichever tier resolves to Opus on the current host**:

- **Easy-tier agents are exempt** — spawn them all at once in a single message (after the rate-gate check above).
- **Any tier that resolves to Opus on the current host goes through the global Opus concurrency window** (≤3 in flight — `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3"): dispatch up to 3, refill as each returns until the queue drains. On Claude Code today **hard-tier resolves to Opus, so this window is live** for every hard-tier dispatch, not just a future `extreme`-tier escalation (`docs/model-tiers.md`). A single-message fan-out of many Opus agents trips Anthropic's burst limiter (ceiling 3–4, anthropics/claude-code#53922).

#### 8a. Easy-tier prompt

```
You are clearing GitHub issue #<N> in the <repo> repo, end-to-end, in YOLO mode.
Repo root: E:\automation\<repo>. You are the only agent touching this repo.

HARD RULES — both are live-incident scars, never work around them:
 - Never work the primary checkout. Build in an isolated sibling worktree,
   always, for every repo — a RUNNING app (the launcher webapp, a tray) or a
   live junction is not a claim holder, so an unattended agent otherwise wins
   MODE=primary and edits files a live process is serving (fleet-config#515).
   Force it, then cd into the printed WORKTREE= path before anything else:
     E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire E:\automation\<repo> --issue <N> --force-worktree
     E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py setup-worktree E:\automation\<repo> <N> <branch>
 - A live-e2e guard refusal is a hard STOP. Report it and stop; setting
   E2E_LIVE=1 or any equivalent override is FORBIDDEN. e2e never targets a
   live production instance.

1. Force worktree mode as above and cd into the worktree.
2. Run the /issue-yolo <N> flow in full (it skips Phase 1 since the issue
   already exists): Phase 2 branch + build, Phase 3 validate HARD (the
   non-negotiable phase — do not weaken it), Phase 4 ship (PR, wait for CI
   green unless the diff is provably CI-unrelated per /issue-yolo step 7,
   merge and land per /issue-yolo step 8's WORKTREE branch — you forced
   worktree mode in step 1, so: no --delete-branch, no `git checkout main`,
   `remove-worktree` then `worktree_claim.py land-primary <repo> <N>`, then
   delete the refs explicitly; report its PRIMARY= line, tray restart per the
   repo's CLAUDE.md), Phase 5 fire the /issue-yolo completion ping.
   KEEP Phase 5's ping — it carries this issue's PR link and must go out.
   notify_complete.py is the ONLY sanctioned way to send it: do NOT use any MCP
   Slack tool (search/send/etc.) to find a channel or post the ping — the helper
   resolves the channel from projects.toml; choosing one yourself is a security
   violation and may post to the wrong channel.
3. If validation (Phase 3) fails at any point: STOP, do not push/merge, and
   report the failure. YOLO means "no plan gate", not "no safety". Then clean
   up after yourself — the open issue is the durable record, the branch is not
   (fleet-config#518):
     a. gh issue comment <N> --repo ferraroroberto/<repo> with the failure
        reason and, if the branch has commits ahead of the default branch, its
        HEAD SHA plus a note that it is reflog-recoverable for ~90 days.
     b. Tear the worktree down via the helper, never by hand (rm -rf follows
        the .venv junction and destroys the primary's real venv):
          E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree <worktree-path>
        then `release` the claim and delete the local branch.
     c. Verify: `git -C E:\automation\<repo> worktree list` shows the primary
        only, `git branch` shows the default branch only, tree clean. Report
        honestly if it does not — never claim clean you could not confirm.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Result: MERGED (<merge-sha>) | FAILED (<phase + one-line reason>)
  - PR: <url or n/a>
  - Validation: <one line — what you ran in Phase 3>
```

#### 8b. Hard-tier prompt

```
You are working GitHub issue #<N> in the <repo> repo, then STOPPING for review.
Repo root: E:\automation\<repo>. You are the only agent touching this repo.

HARD RULES — both are live-incident scars, never work around them:
 - Never work the primary checkout. Build in an isolated sibling worktree,
   always, for every repo (fleet-config#515 — a RUNNING app or a live junction
   is not a claim holder). Force it, then cd into the printed WORKTREE= path:
     E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire E:\automation\<repo> --issue <N> --force-worktree
     E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py setup-worktree E:\automation\<repo> <N> <branch>
   Report the worktree path — the user needs it to run /issue-finish there.
 - A live-e2e guard refusal is a hard STOP. Report it and stop; setting
   E2E_LIVE=1 or any equivalent override is FORBIDDEN.

1. Force worktree mode as above and cd into the worktree.
2. Invoke /issue-start <N> now — handles pre-flight, issue read, CLAUDE.md
   read, main sync, branch cut, hand-off to implementation in fast mode.
3. Build the change.
4. Run the project's verification gate (per its CLAUDE.md — e.g.
   `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`).
5. Commit your work on the branch — git add the files you changed and git
   commit them (conventional `type: subject` message, no AI-attribution
   trailer). Your handoff artefact is a committed branch, not a dirty working
   tree: uncommitted work has no SHA, so a crash or an escalation before the
   user ships it loses it outright instead of parking it recoverably in the
   reflog (fleet-config#641). Changed nothing? Commit nothing and say so — a
   clean tree with no new commits is a valid report, a dirty tree never is.
6. STOP. Do NOT push, open a PR, merge, or run /issue-finish. "Do not ship"
   does not mean "do not commit": step 5 is required, and only the four
   actions named here are forbidden. This issue is hard-tier enough that the
   user validates the approach before it ships. The user will NOT read the
   diff — they review your summary below, so make it count.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - What I did & why: <2-4 plain-English sentences aimed at someone who will
    NOT read the diff — the decision you made and the reasoning behind it, not
    a code walkthrough>
  - Why I believe this is correct: <1-2 sentences — what gives confidence:
    what you tested/verified, what edge cases you considered>

If verification FAILS, leave the branch as-is for the user to inspect — do NOT
try to "fix" the failure by guessing; just report.
```

Substitute every `<…>` placeholder with the concrete value from steps 2–7.

### 9. Confirm fan-out, then either stand by (attended) or poll in-turn (unattended)

Print a single confirmation block listing every sub-agent dispatched (repo, #N, model, path) — and, if any hard-tier issues are still queued behind the Opus window (see step 8), note how many are pending.

- **`hard` mode (attended, a normal top-level session):** then **stop** — do not poll, sleep, or check progress. The harness re-invokes you automatically as each background agent completes; on each Opus-tier completion, refill that window with the next pending issue (step 8) until the queue drains.
- **`easy`/`silent` mode (designed to also run headless via `claude -p`):** a headless run has no re-invocation to rely on, so instead **block on `TaskOutput` (`block: true`) for every in-flight agent within this same turn**, re-issuing on timeout; refill the window as each returns (step 8); never end the turn until every selected issue has a final status (`fleet-config#506`).

### 10. Aggregate as agents return, then the closing ping

**Before marking any repo complete, run the post-flight dirty-tree check yourself — never trust the agent's self-report.** The agent that reports the result is the same actor that might have forgotten a commit or left something dirty, so this runs in the orchestrator:

- **Easy-tier (reported `MERGED`):**
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode merged
  ```
  `STATUS=DIRTY` → downgrade the status from `✅ merged` to `⚠️ merged but dirty tree — inspect <repo>` and carry the `REASON=` line, instead of trusting `Result: MERGED`. `STATUS=UNKNOWN` → the helper could not read that repo, so it has no verdict: mark it `❓ tree unverified — <REASON>` and never fold it into a pass or a fail (fleet-config#570).
- **Hard-tier (build-and-stop):**
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check E:\automation\<repo> --mode built --expect-branch <branch>
  ```
  `STATUS=DIRTY` → keep the `📋 ready for review` mark but append an explicit `⚠️ post-flight: <REASON>` line right next to it, distinct from the rationale summary — this catches HEAD silently back on `main`, a branch mismatch, or the agent reporting changes it never actually saved. `STATUS=UNKNOWN` → the helper could not read that repo, so it has no verdict: mark it `❓ tree unverified — <REASON>` and never fold it into a pass or a fail (fleet-config#570).

This check only reports — it never blocks the run, never auto-commits, and never auto-fixes. A per-repo failure never stops the aggregation of the rest.

As each sub-agent finishes, surface its report with a status mark: `✅` merged / `❌` failed for easy-tier; `📋 ready for review` / `⚠️ verification skipped` / `❌ failed` for hard-tier. **For every hard-tier report, surface its "What I did & why" and "Why I believe this is correct" text directly, right next to the `📋 ready for review` line** — the user's approve/ship decision is made from that summary, not by opening the diff, so don't just point at a branch name.

When **all** agents have returned, fire **one final** roll-up ping — the closing message for the run. The per-issue `🚀 Shipped` pings the easy-tier agents already fired are kept; this is *in addition*:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind cleanup --summary "<bucket>" --merged <easy-tier-merged-count> --review <hard-tier-review-count>
```

(In easy mode `--review 0` — the helper drops the review clause.) Silent no-op if no Slack channel is configured; always exits 0.

**`notify_complete.py` is the ONLY sanctioned way to send this roll-up ping — do NOT use any MCP Slack tool (search/send/etc.) to find a channel or post the ping.** The helper resolves the destination channel deterministically from `projects.toml`; picking one yourself is both a security violation (an agent-inferred external write destination) and wrong (it may post to the wrong channel). A silent no-op when no channel is configured is correct — do not "fix" it by reaching for Slack tools.

Then print the final summary block, with each hard-tier review row carrying its rationale summary inline, and any post-flight dirty-tree finding called out as its own line rather than folded silently into a clean-looking status:

```
Cleanup complete — <bucket> (<mode> mode)
  candidates: 6 dispatched, 1 already-closed (skipped, no agent dispatched), 0 unresolved
  ✅ merged:  <repo>#<N> <pr-url>, …
  ⚠️ merged but dirty tree — inspect <repo> (<reason>)
  📋 review:  <repo>#<N> — cd <repo>-wt-<N> && /issue-finish
              What I did & why: <the agent's summary, verbatim>
              Why correct: <the agent's confidence summary, verbatim>
              ⚠️ post-flight: <reason, only when the check flagged this repo>
              …
  ❌ failed:  <repo>#<N> — <reason> (commented on the issue; branch + worktree torn down)
  ❌ RESIDUE: <repo> — <what survived teardown> → <one-line recovery command>
  deferred:  <repo>#<N> (next run)

Next: read each review row's summary above, then /issue-finish the ones you approve.
```

The `candidates:` line is mandatory even when the extra two counts are zero — step 8's state re-check can drop candidates before any agent is dispatched, and a summary that silently shrinks its own working set reads as "nothing to do" (fleet-config#623). `already-closed` and `unresolved` are always two separate counts, never combined into one "skipped" number.

### 11. Stop

No follow-up actions. The user reads each hard-tier row's rationale summary (not the diff) and decides, then ships the ones they approve. Two ways, user's choice — **never auto-launch either**:

- **`/issue-finish-batch <branches>`** — once happy with several branches, fan out one background finisher per branch (all at once — this is easy-tier work itself, exempt from the Opus cap), each running `/issue-finish` one-shot and reporting back only on a genuine blocker. The parallel path.
- **`/issue-finish`** per branch, one at a time — the always-available manual fallback.

Do **not** auto-launch either: the batch finish is user-triggered, exactly like the manual one.

## Hard rules

- **One agent per repo, period.** A bucket is at most one issue per repo by construction; if a repo has extras, defer them — never two agents on one checkout.
- **Easy-tier path is full-YOLO-to-merged; hard-tier path always stops before push/PR.** Never let a hard-tier agent merge; never make an easy-tier agent stop early in `hard`/`easy` mode (that's what the hard tier is for).
- **Whichever tier resolves to Opus on the current host dispatches through the global Opus concurrency window (≤3 in flight); every other tier is exempt.** On Claude Code today hard-tier resolves to Opus. Refill the window as each capped agent returns; never a single-message fan-out of many Opus agents at once — it trips Anthropic's server-side burst rate limit (see `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3").
- **`easy`/`silent` mode never spawns hard-tier work and never merges hard-scored work.** Hard-tier rows are listed only. This is the unattended-safety guarantee.
- **Hard-tier review is by rationale summary, not diff.** Prompt 8b's agent must return "What I did & why" / "Why I believe this is correct"; the orchestrator surfaces both verbatim next to every `📋 ready for review` row (steps 9-10) — that's what the user reviews, not the code.
- **The orchestrator never edits source, commits, pushes, or merges.** Every write happens inside a spawned agent.
- **Never disturb in-progress work.** Dirty / off-default-branch repos are skipped and reported, never stashed or force-switched.
- **Post-flight dirty-tree check runs in the orchestrator, never the sub-agent, right before a repo is marked complete (step 10).** It only corrects the reported status (downgrades `✅`/`📋` on a mismatch) — it never blocks, auto-commits, or auto-fixes.
- **Keep per-issue pings.** Easy-tier agents fire their own `/issue-yolo` ping (PR link); the orchestrator's `--kind cleanup` ping is an *additional* closing roll-up, not a replacement.
- **Degrade, don't block.** A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. `easy`/`silent` must never wait on an interactive prompt.
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Where this sits:** `/codebase-audit` / `/audit-fleet` find and file (read-only on source); `/cleanup-fleet` fixes one bucket (write-capable via its agents); `/issue-triage` stays the read-only overview across all buckets.
- **Noise control starts at filing time, not here** — `/codebase-audit`'s materiality bar (fleet-config#251) gates what becomes a checklist item; this skill's triage and approval gate are about execution *shape* and safety, not deselecting noise the audit shouldn't have filed.
- **Compose `/issue-yolo` and `/issue-start`+gate rather than re-implement** — they own the branch/build/validate/ship choreography; this skill is just bucket selection, tiering, and fan-out.
- **Scheduling `easy` mode:** it degrades rather than blocks and never merges hard work, so `claude -p "/cleanup-fleet documentation easy" --permission-mode bypassPermissions` is safe after a weekly `/audit-fleet` — the easy pass clears the mechanical findings, the rest wait for an attended `hard` run.
