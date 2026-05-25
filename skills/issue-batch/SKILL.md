---
name: issue-batch
description: Fan out a batch of GitHub issues to parallel background sub-agents — one agent per issue, with git worktrees when multiple issues hit the same repo so the agents don't collide. Each sub-agent cuts the branch, builds the change, runs the verification gate, then STOPS for review. You run `/issue-finish` yourself per branch, sequentially. Use after `/issue-triage` when you want to pick a handful of issues and work on them in parallel — e.g. "/issue-batch app-launcher#23 app-launcher#45 photo-ocr#12" or bare "/issue-batch 23 45 12".
---

# issue-batch

**Goal:** Take a list of selected GitHub issues, set up isolated workspaces (worktrees when needed), and spawn a parallel **background** sub-agent per issue to build + verify the change. Each sub-agent stops before pushing — you review and `/issue-finish` each branch yourself, one at a time.

Pairs with `/issue-triage` (pick what to work on) and `/issue-finish` (ship each result, sequentially).

## Execution rules

- **Read-only on GitHub.** This skill never creates issues, pushes, opens PRs, or merges. All shipping is deferred to manual `/issue-finish` per branch.
- **Shell:** Bash tool is **Git Bash** on this machine. Do not use PowerShell syntax (`&`, `$env:`, here-strings) in Bash. The only shell commands this skill needs are `gh` and `git`, both of which work identically in Bash.
- **All git plumbing runs in the orchestrator (main conversation), not the sub-agents.** Worktree creation, main-branch sync, and branch cutting happen here — sequentially and safely — *before* sub-agents launch. Sub-agents inherit a ready-to-edit workspace.

## Arguments

A space-separated list of issue tokens. Each token is one of:

- **Explicit:** `<repo-name>#<N>` — e.g. `app-launcher#23`. Unambiguous; preferred.
- **Bare number:** `<N>` — resolved via a single `gh search issues` call. If the same number exists in multiple repos, ask the user (AskUserQuestion) which one they meant.

Mixed forms are fine: `/issue-batch app-launcher#23 45 photo-ocr#12`.

If no tokens are passed, stop and say "Pass at least one issue, e.g. `/issue-batch app-launcher#23 photo-ocr#12`."

## Steps

Run in order. If a step fails, print a short error and stop. **Never leave half-made worktrees or branches behind** — on failure mid-setup, undo what was done so far (`git worktree remove --force <path>`, `git branch -D <branch>`).

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop.
- Confirm `E:\automation\` exists (it's the fleet root on this machine).

### 2. Parse tokens and resolve bare numbers

Split args on whitespace. Classify each:
- Contains `#` → split into `repo` + `N`. Validate `N` is a positive integer.
- Else → treat as bare `N`; collect for batch resolution.

If any bare numbers exist, run **once**:

```
gh search issues --owner ferraroroberto --state open --include-prs=false --limit 300 \
  --json repository,number,title,labels
```

For each bare `N`:
- Match against the result on `number == N`.
- 0 matches → stop: "No open issue #N in any ferraroroberto repo."
- 1 match → resolved.
- 2+ matches → AskUserQuestion listing each `repo#N — <title>` and let the user pick.

Output of this step: a list of `(repo, N, title, labels)` tuples.

### 3. Group and decide isolation mode

Group tuples by repo. For each repo, count the selected issues.

- **count == 1** → **in-place mode**: sub-agent works in the primary checkout at `E:\automation\<repo>`. The sub-agent will invoke `/issue-start <N> now` directly, which handles its own branch cut.
- **count >= 2** → **worktree mode**: each issue gets its own sibling worktree. The orchestrator pre-creates the worktree and branch; the sub-agent skips the worktree-incompatible parts of `/issue-start` and starts directly at the implementation step.

Print a one-line plan before any setup, e.g.:
```
Plan: 3 sub-agents across 2 repos
  app-launcher: 2 issues → worktrees (#23, #45)
  photo-ocr:    1 issue → in-place (#12)
```

### 4. Pre-flight per repo

For each repo in the plan:
- `E:\automation\<repo>` must exist. Else stop.
- `git -C E:\automation\<repo> status --porcelain` must be empty. Else stop with the dirty repo name — the user must commit/stash before proceeding.
- `git -C E:\automation\<repo> fetch origin` (once per repo, sequentially).

### 5. Compute branch names

For every selected issue, derive the branch name using the **same convention as `/issue-start` step 5**:

- Prefix:
  - `bug` label → `fix/`
  - `documentation` label → `docs/`
  - title or labels indicate CI/build (`ci`, `chore` involving CI/workflow) → `ci/`
  - otherwise → `feat/`
- Slug: lowercase the title, keep alphanumerics, collapse the rest to single hyphens, trim to ~4 words.
- Branch: `<prefix>/<N>-<slug>`.

Example: bug #23 titled "WS handshake retry fails on reconnect" → `fix/23-ws-handshake-retry-fails`.

### 6. Create worktrees (worktree mode only)

For each worktree-mode issue:

```
git -C E:\automation\<repo> worktree add E:\automation\<repo>-wt-<N> -b <branch> origin/main
```

Notes:
- Worktree path convention: **sibling** to the repo root, named `<repo>-wt-<N>`. Stable, easy to spot, easy to `git worktree remove` later.
- The branch is cut off `origin/main` (latest remote), not local `main` — so the worktree doesn't depend on the primary checkout's branch state.
- If a worktree path already exists, stop with a clear message (probably stale from a prior run; user runs `git worktree remove --force <path>` to clean up).

Run these sequentially per repo (worktree creation modifies repo metadata; safer not to parallelize).

In-place mode: skip — `/issue-start` inside the sub-agent will cut the branch.

### 7. Fan out: spawn one background sub-agent per issue

Spawn all sub-agents in a **single message** with multiple parallel `Agent` tool calls, each with `run_in_background: true`. Use `subagent_type: "general-purpose"` (or `"claude"`).

Two prompt templates — pick by isolation mode:

#### 7a. Worktree-mode prompt

```
You are working on GitHub issue #<N> in the <repo> repo on branch <branch>.
You are in an isolated git worktree at: <wt-path>

Setup is already done — the worktree exists and you are on the correct
feature branch cut off latest origin/main. Do NOT cut a new branch, do NOT
checkout main, do NOT pull (the primary worktree owns main).

Workflow (mirrors /issue-start steps 3 + 6, plus verification):

1. cd to <wt-path>.
2. Read the issue: `gh issue view <N> --json number,title,body,labels`.
3. Read <repo>'s CLAUDE.md and README.md for conventions and the
   verification gate command.
4. Investigate the codebase as needed.
5. Build the change. Fast mode (no plan-approval gate) — the user has
   already approved this batch.
6. Run the project's verification gate (per its CLAUDE.md — for
   app-launcher it's `pwsh -File scripts/verify-before-ship.ps1`).
   IMPORTANT: the gate must run isolated. If the gate would conflict with
   parallel runs (shared port, shared file), report that you skipped it
   and why. The app-launcher gate boots its own ephemeral webapp + session
   host on free ports — that one is safe to run in parallel.
7. STOP. Do not push, do not open a PR, do not run /issue-finish.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Worktree: <wt-path>
  - Files changed: <list>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - Notes: <one or two lines if anything surprising came up>

If verification FAILS, leave the worktree as-is for the user to inspect —
do NOT try to "fix" the failure by guessing; just report.
```

#### 7b. In-place-mode prompt

```
You are working on GitHub issue #<N> in the <repo> repo.
Repo root: E:\automation\<repo>
You are the only sub-agent touching this repo right now, so the primary
checkout is yours — no worktree needed.

Workflow:

1. cd to E:\automation\<repo>.
2. Invoke the /issue-start skill with: `/issue-start <N> now`
   - This handles: pre-flight, issue read, CLAUDE.md read, main sync,
     branch cut, and the hand-off to implementation in fast mode.
3. Build the change.
4. Run the project's verification gate (per its CLAUDE.md).
5. STOP. Do not push, do not open a PR, do not run /issue-finish.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch-name-as-cut-by-issue-start>
  - Worktree: (in-place — primary checkout at E:\automation\<repo>)
  - Files changed: <list>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - Notes: <one or two lines if anything surprising came up>

If verification FAILS, leave the branch as-is for the user to inspect —
do NOT try to "fix" the failure by guessing; just report.
```

Substitute every `<…>` placeholder with the concrete value computed in steps 2–6.

### 8. Confirm fan-out and stand by

After spawning, print a single confirmation block listing every sub-agent dispatched, e.g.:

```
Dispatched 3 background sub-agents:
  • app-launcher#23 → worktree E:\automation\app-launcher-wt-23 on fix/23-ws-handshake-retry
  • app-launcher#45 → worktree E:\automation\app-launcher-wt-45 on feat/45-cache-hygiene
  • photo-ocr#12   → in-place on feat/12-vision-model-swap

I'll report each result here as the agents complete. You don't need to wait —
ask me anything else in the meantime.
```

Then stop. Do not poll, do not sleep, do not check on progress — the harness re-invokes you automatically when each background agent completes.

### 9. Report each completion (as agents return)

As each background sub-agent finishes, surface its report verbatim in the chat with a short header (`✅` if verification passed, `⚠️` if skipped, `❌` if failed).

After **all** agents have returned, finish with one summary block:

```
All <N> sub-agents complete.
  ✅ <repo>#<N> ready for review — `cd <path> && /issue-finish`
  ✅ <repo>#<N> ready for review — `cd <path> && /issue-finish`
  ❌ <repo>#<N> verification failed — inspect <path>

Next: review each branch and run /issue-finish one at a time
(sequential merges avoid CI pile-up and tray-restart races).
Remember: after `/issue-finish` merges a worktree-mode branch,
clean up with `git worktree remove <wt-path>` (run from the
primary checkout, not from inside the worktree).
```

### 10. Stop

No follow-up actions. The user reviews + finishes each branch manually with `/issue-finish`. Do **not** auto-launch `/issue-finish`.
