---
name: issue-finish-batch
description: Ship a set of already-built, already-reviewed issue branches in parallel — fan out one background Sonnet agent per branch, each running the full /issue-finish flow one-shot (push, PR, CI-advisory, merge, delete branch, tray restart), reporting back only when it hits a genuine blocker a human must resolve. The parallel-finish step after /cleanup-fleet or /issue-batch. Use when you've reviewed several branches and want them all shipped without the serial slog — e.g. "/issue-finish-batch app-launcher#71 reporting#12", bare "/issue-finish-batch 71 12", or just "finish them all" after a fan-out run.
---

# issue-finish-batch

**Goal:** `/cleanup-fleet` and `/issue-batch` *build and stop* — they leave one reviewed branch per issue and tell you to run `/issue-finish` on each **manually and sequentially**. When you've reviewed those branches and you're happy, finishing each is purely mechanical (push, PR, CI-as-advisory, merge, delete branch, land on main, tray restart) and needs neither Opus nor the main session's serial attention. This skill fans out **one background Sonnet agent per branch**, each running the existing `/issue-finish` flow end-to-end one-shot, and reports back to you **only** when an agent hits a genuine blocker it cannot resolve on its own.

**This is user-triggered, never automatic.** `/cleanup-fleet`'s rule that the orchestrator never *auto*-launches `/issue-finish` is unchanged — you invoke this skill explicitly when you've decided the reviewed branches are ready to ship.

**Why Sonnet, all at once:** finishing a reviewed branch is mechanical, so Sonnet is the right tier — and Sonnet sub-agents are **exempt from the global Opus concurrency cap of 3** (see `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3"). So the whole batch fans out in a single message, no window. Only an explicit Opus override (`opus` token) re-imposes the ≤3-in-flight window.

**Why one agent per branch, never two on a checkout:** each agent operates a working tree (the primary checkout, or a worktree path for `/issue-batch` worktree-mode branches). Two agents on the same checkout collide. One issue → one branch → one agent → one merge.

## Arguments

`/issue-finish-batch [<issue/branch list>] [<model>]`

- **Issue/branch list** — space-separated, order-independent. Each token is either `<repo>#<N>` (e.g. `app-launcher#71`), a bare issue number `<N>` (single-repo case — resolved against the current repo), or a branch name. Resolves to one `(repo, issue, branch, worktree-path?)` per token in step 2.
- **Model token** — `sonnet` (default, omit it) or `opus` to override the tier (rare — only if the finishes are unexpectedly involved). With `opus` the global ≤3 Opus window applies.
- **No list given** → if the immediately-preceding turn was a `/cleanup-fleet` or `/issue-batch` fan-out, offer its build-and-stop branches as the candidate set and confirm; otherwise ask which branches to finish and stop.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. Use plain `gh` / `git` only — no PowerShell syntax. Windows paths map as `/e/automation/...`.
- **The orchestrator only does cheap, safe work:** resolve each token to its repo + branch, per-branch pre-flight, fan-out, aggregate. **It never edits source, commits, pushes, or merges** — every write happens inside a spawned agent's `/issue-finish` run.
- **One agent per branch, period.** Never two agents against the same checkout/worktree.
- **The branch is already built and reviewed.** Agents run `/issue-finish` only — they do **not** re-build, re-design, or "improve" the change.
- **Blocker-only escalation.** An agent reports `BLOCKED` and stops on a genuine blocker (merge conflict, CI red on a diff that *does* touch e2e surface, verification-gate failure). It must **not** guess-fix, weaken the gate, or force anything. Everything else just ships and reports `MERGED`.
- **Degrade, don't block.** A per-branch failure is reported and skipped; only a pre-flight failure stops the whole run.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."
- Parse the args: pull the model token (`sonnet`/`opus`) if present; everything else is the issue/branch list. Default model Sonnet. Empty list → handle per "No list given" above.

### 2. Resolve each token to (repo, issue, branch, path)

For each token, determine:

- **repo** — from the `<repo>#<N>` prefix, or the current repo for a bare number, or by locating the branch's repo.
- **branch** — discover it: `git -C E:\automation\<repo> branch --list "*<N>-*"` (the `<prefix>/<N>-<slug>` cut by the build step). If multiple match, prefer the one whose tip is ahead of `origin/main`. If none, **report and skip** that token — there is nothing built to finish.
- **path** — the primary checkout `E:\automation\<repo>` unless the branch lives in a worktree (`git -C E:\automation\<repo> worktree list` shows it elsewhere — the `/issue-batch` worktree-mode case); then use the worktree path.
- **issue number** — from the token or the branch slug.

### 3. Pre-flight per branch

For each resolved branch:

- The repo/worktree path exists. Else skip + report.
- The branch exists and is ahead of `origin/main` (has commits to ship). Else skip + report ("nothing to finish").
- `git -C <path> fetch origin` (once per repo).

Do **not** check out branches or touch working trees here — each agent does that inside its own run.

### 4. Confirm the set + fan out

Print the resolved set (repo, #N, branch, path, model) and — unless invoked from a just-confirmed fan-out — get a one-line go-ahead. Then dispatch **one background sub-agent per branch** (`run_in_background: true`, `subagent_type: "general-purpose"`, `model: "sonnet"` by default):

- **Sonnet (default):** spawn them **all at once** in a single message — Sonnet is exempt from the Opus cap.
- **Opus override:** dispatch through the global ≤3-in-flight Opus window — launch up to 3, refill as each returns, until the queue drains.

#### Agent prompt

```
You are SHIPPING an already-built, already-reviewed GitHub issue branch with /issue-finish.
Repo: <repo>. Working tree: <path>. Branch: <branch>. Issue: #<N>.
You are the only agent touching this checkout.

1. cd to <path>. Confirm you are on <branch> (git checkout <branch> if needed);
   the change is already built and reviewed — do NOT re-build, redesign, or
   "improve" it.
2. Run the /issue-finish flow in full: re-confirm the issue's acceptance points,
   update README/docs only if usage/config/output changed, run the project's
   verification gate (per its CLAUDE.md), commit any doc edits with a
   conventional message (no AI-attribution trailer), push, open a PR whose body
   ends with "Closes #<N>", handle CI as advisory (skip the wait only when the
   diff is provably CI-unrelated; rerun a single documented flake once), merge
   with --merge --delete-branch, land on main, and restart the project's tray
   per its CLAUDE.md if it has one.
3. Fire /issue-finish's own completion ping (✅ Done #<N> … — PR merged) — KEEP
   it, it carries this branch's PR link. notify_complete.py is the ONLY
   sanctioned way to send it: do NOT use any MCP Slack tool (search/send/etc.)
   to find a channel or post the ping — the helper resolves the channel from
   projects.toml; choosing one yourself is a security violation and may post to
   the wrong channel.
4. If you hit a genuine blocker — merge conflict, CI red on a diff that DOES
   touch e2e surface, or the verification gate fails — STOP. Do NOT guess-fix,
   weaken the gate, or force the merge. Leave the branch in place and report
   BLOCKED with a one-line reason.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Result: MERGED (<merge-sha>) | BLOCKED (<one-line reason — needs human>)
  - PR: <url or n/a>
```

Substitute every `<…>` with the concrete value from steps 2–3.

### 5. Confirm fan-out and stand by

Print a single confirmation block listing every agent dispatched (repo, #N, branch, model). For an Opus override, note how many are queued behind the window. Then **stop** — do not poll or sleep. The harness re-invokes you as each agent completes; on each Opus completion (override only) refill the window with the next pending branch.

### 6. Aggregate, then the closing ping

As each agent returns, surface its report with a status mark: `✅ merged` / `❌ blocked`. The per-issue `✅ Done` pings the finishers already fired are kept — this is *in addition*.

When **all** agents have returned, fire **one final** roll-up ping:

```
py C:/Users/rober/.claude/hooks/notify_complete.py \
  --kind finish-batch --merged <merged-count> --blocked <blocked-count>
```

(A `0`/empty `--blocked` drops the clause.) Silent no-op if no Slack channel is configured; always exits 0.

**`notify_complete.py` is the ONLY sanctioned way to send this roll-up ping — do NOT use any MCP Slack tool (search/send/etc.) to find a channel or post the ping.** The helper resolves the destination channel deterministically from `projects.toml`; picking a channel yourself is both a security violation (an agent-inferred external write destination) and wrong (it may post to the wrong channel). A silent no-op when no channel is configured is the correct outcome — do not "fix" it by reaching for Slack tools.

Then print the final summary block:

```
Finish-batch complete
  ✅ merged:   <repo>#<N> <pr-url>, …
  ❌ blocked:  <repo>#<N> — <reason> · cd E:\automation\<repo> && /issue-finish

Next: resolve each blocked branch, then re-run /issue-finish-batch on it (or /issue-finish manually).
```

### 7. Stop

No follow-up actions. Blocked branches are left in place for you to inspect and finish manually.

## Hard rules

- **One agent per branch/checkout, period.** Never two against the same working tree or worktree.
- **Agents finish only — never re-build.** The branch is reviewed; this skill ships it.
- **Sonnet by default, fanned out all at once; Opus only on explicit override (then the ≤3 window applies).** Per `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3" — Sonnet is exempt.
- **Blocker-only escalation.** Agents ship the happy path silently and report; they escalate (BLOCKED, leave branch) only on a real blocker, never guess-fixing or weakening the gate.
- **Keep per-issue pings.** Each `/issue-finish` fires its own `✅ Done` ping (PR link); the `--kind finish-batch` roll-up is an *additional* closing aggregate, not a replacement. (Per `~/.claude/CLAUDE.md`, "keep per-item pings with aggregate".)
- **The orchestrator never edits source, commits, pushes, or merges.** Every write happens inside a spawned agent.
- **User-triggered, never automatic.** This does not change `/cleanup-fleet`'s rule that the orchestrator never auto-launches `/issue-finish`.
- **Degrade, don't block.** A per-branch failure is reported and skipped; only a pre-flight failure stops the whole run.
- **No AI attribution; no hard-wrapped issue/PR-body paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Where this sits:** `/cleanup-fleet` and `/issue-batch` *build and stop* (one reviewed branch per issue); `/issue-finish-batch` *ships* a set of those reviewed branches in parallel. It composes `/issue-finish` exactly — it owns only the branch resolution, fan-out, and aggregate, never the ship choreography itself.
- **Worktree branches:** `/issue-batch` uses worktrees when several issues hit one repo. Step 2 detects the worktree path and points the agent at it; the agent's `/issue-finish` run handles the merge, and worktree cleanup (`git worktree remove`) is done from the primary checkout afterwards per `/issue-batch`'s closing note.
- **Why compose `/issue-finish` rather than re-implement:** it already owns the acceptance/gate/CI-advisory/merge/tray choreography per project. This skill is just the parallel-finish wrapper around it.
