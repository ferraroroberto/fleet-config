---
name: issue-batch
description: Fan out GitHub issues to parallel background sub-agents — one per issue, in git worktrees when several hit the same repo. Each cuts a branch, builds, runs the verification gate, then STOPS for review; you run `/issue-finish` per branch, sequentially. Use after `/issue-triage` — e.g. "/issue-batch app-launcher#23 app-launcher#45 photo-ocr#12" or bare "/issue-batch 23 45 12".
---

# issue-batch

**Goal:** Take a list of selected GitHub issues, set up isolated workspaces (worktrees when needed), and spawn a parallel **background** sub-agent per issue to build + verify the change. Each sub-agent stops before pushing — you review and `/issue-finish` each branch yourself, one at a time.

Pairs with `/issue-triage` (pick what to work on) and `/issue-finish` (ship each result, sequentially).

## Execution rules

- **Read-only on GitHub** — never creates issues, pushes, opens PRs, or merges. All shipping is deferred to manual `/issue-finish` per branch.
- **Shell:** Bash tool is **Git Bash** here. No PowerShell syntax (`&`, `$env:`, here-strings) in Bash. Only `gh` and `git` are needed, both identical in Bash.
- **All git plumbing runs in the orchestrator (main conversation), not the sub-agents** — worktree creation, main-branch sync, branch cutting happen here, sequentially, *before* sub-agents launch. Sub-agents inherit a ready-to-edit workspace.
- **The post-flight dirty-tree check (step 9) also runs in the orchestrator, never the sub-agent** — it only corrects the reported status, never blocks, auto-commits, or auto-fixes.

## Arguments

A space-separated list of issue tokens. Each token is one of:

- **Explicit:** `<repo-name>#<N>` — e.g. `app-launcher#23`. Unambiguous; preferred.
- **Bare number:** `<N>` — resolved via a single `gh search issues` call. Same number in multiple repos → ask the user (AskUserQuestion) which one.

Mixed forms are fine: `/issue-batch app-launcher#23 45 photo-ocr#12`.

No tokens passed → stop: "Pass at least one issue, e.g. `/issue-batch app-launcher#23 photo-ocr#12`."

## Steps

Run in order. Step fails → print a short error and stop. **Never leave half-made worktrees or branches behind** — on failure mid-setup, undo what was done (`git worktree remove --force <path>`, `git branch -D <branch>`).

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop.
- Confirm `E:\automation\` exists (fleet root on this machine).

### 2. Parse tokens and resolve bare numbers

Split args on whitespace. Classify each:
- Contains `#` → split into `repo` + `N`; validate `N` is a positive integer.
- Else → bare `N`; collect for batch resolution.

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

- **count == 1** → **in-place mode**: sub-agent works in the primary checkout at `E:\automation\<repo>`, invoking `/issue-start <N> now` directly (handles its own branch cut).
- **count >= 2** → **worktree mode**: each issue gets its own sibling worktree. Orchestrator pre-creates the worktree and branch; sub-agent skips the worktree-incompatible parts of `/issue-start` and starts directly at the implementation step.

Print a one-line plan before any setup, e.g.:
```
Plan: 3 sub-agents across 2 repos
  app-launcher: 2 issues → worktrees (#23, #45)
  photo-ocr:    1 issue → in-place (#12)
```

### 4. Pre-flight per repo

For each repo in the plan:
- `E:\automation\<repo>` must exist, else stop.
- `git -C E:\automation\<repo> status --porcelain` must be empty, else stop with the dirty repo name — user must commit/stash before proceeding.
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

For each worktree-mode issue, use the shared concurrency helper (same one `/issue-start` uses) so the worktree gets its `.venv` junctioned from the primary and the reparse-safe teardown is owned in one place:

```
git -C E:\automation\<repo> fetch origin
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py setup-worktree E:\automation\<repo> <N> <branch>
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/active_issue.py add <printed-WORKTREE-path> <N> <branch>
```

Notes:
- The helper creates the **sibling** worktree `E:\automation\<repo>-wt-<N>` off latest `origin/main` on `<branch>`, then junctions the primary's `.venv` into it — so a Python repo's verification gate (`& .\.venv\Scripts\python.exe …`) resolves inside the worktree without a per-worktree reinstall.
- Do **not** hand-roll `git worktree add` + a `.venv` junction here — the helper owns both creation and the junction-strip-before-`git worktree remove` teardown (the junction footgun that wiped a real venv in fleet-config#143).
- Worktree path already exists → helper stops with a clear message (probably stale from a prior run; clean with `remove-worktree <path>` in step 9).
- The active-issue write is the worktree-mode equivalent of `/issue-start`
  step 5; in-place agents inherit it by invoking `/issue-start`. Write fails →
  immediately remove that newly-created worktree with
  `worktree_claim.py remove-worktree <path>` and stop the batch rather than
  dispatch unmarked work.

Run sequentially per repo (worktree creation modifies repo metadata; safer not to parallelize).

In-place mode: skip — `/issue-start` inside the sub-agent will cut the branch.

### 7. Fan out: spawn one background sub-agent per issue

Spawn one background sub-agent per issue (`run_in_background: true`, `subagent_type: "general-purpose"` or `"claude"`), but **bound the Opus concurrency**: these agents inherit the parent session's model, so when the parent is on **Opus**, dispatch through the global Opus concurrency window (≤3 in flight — `~/.claude/CLAUDE.md`, "Spawning sub-agents — cap concurrent Opus at 3"): launch up to 3, and each time one returns dispatch the next pending issue until the batch drains. Fewer than 3 issues → spawn that many. Parent on **Sonnet** → cap doesn't apply, spawn all in one message. Worktree pre-creation (step 6) already ran sequentially before any agent launches, so a windowed launch never races it.

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
   app-launcher it's
   `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`).
   IMPORTANT: the gate must run isolated. If it would conflict with parallel
   runs (shared port, shared file), report that you skipped it and why. The
   app-launcher gate boots its own ephemeral webapp + session host on free
   ports — safe to run in parallel.
7. Run the /e2e skill (skills/e2e/SKILL.md): it routes this branch's diff
   through the repo's own classifier and runs the proportionate slice. If
   the gate in step 6 already executed that slice, /e2e carries the result
   — no double run. Run it synchronously to completion within your turn.
8. Ready-to-validate handoff (worktree mode): do NOT restart the shared
   tray/webapp — the primary checkout may be serving it live, only one build
   can be live at a time. If the repo has a web surface, include in your
   report the one-line command (from the repo's README/CLAUDE.md) that
   launches THIS worktree's app for validation. No web surface → report
   `Validate: n/a`.
9. Commit your work on the branch — git add the files you changed and commit
   (conventional `type: subject` message, no AI-attribution trailer). Your
   handoff artefact is a committed branch, not a dirty working tree:
   uncommitted work has no SHA, so anything that goes wrong before the user
   runs /issue-finish loses it outright (fleet-config#641). Changed nothing?
   Commit nothing and say so — a clean tree with no new commits is a valid
   report, a dirty tree never is.
10. STOP. Do not push, do not open a PR, do not run /issue-finish. "Do not
    ship" does not mean "do not commit": step 9 is required, only the three
    actions named here are forbidden.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch>
  - Worktree: <wt-path>
  - Files changed: <list>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - E2e: <tier> (<reason>) — PASS / FAIL / skip / n/a
  - Validate: <launch command for this worktree | n/a (no web surface)>
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
5. Run the /e2e skill (skills/e2e/SKILL.md): proportionate e2e for this
   branch's diff; if the gate already executed the routed slice, /e2e
   carries the result. Run it synchronously to completion within your turn.
6. Ready-to-validate handoff (in-place mode): if the repo's CLAUDE.md
   declares a long-lived app and an unattended-safe restart recipe (e.g.
   `tray.bat --restart` with detach-compliant children), run it, confirm the
   new build with the bounded build-identity poll, and report the URL. If
   the recipe requires confirmation, or the repo is silent on restart
   safety, do NOT restart — report the exact restart command for the user
   instead. No web surface → report `Validate: n/a`.
7. Commit your work on the branch — git add the files you changed and git
   commit them (conventional `type: subject` message, no AI-attribution
   trailer). Your handoff artefact is a committed branch, not a dirty working
   tree: uncommitted work has no SHA, so anything that goes wrong before the
   user runs /issue-finish loses it outright (fleet-config#641). Changed
   nothing? Commit nothing and say so — a clean tree with no new commits is a
   valid report, a dirty tree never is.
8. STOP. Do not push, do not open a PR, do not run /issue-finish. "Do not
   ship" does not mean "do not commit": step 7 is required, and only the
   three actions named here are forbidden.

Report back, in this exact shape:
  - Issue: <repo>#<N> — <title>
  - Branch: <branch-name-as-cut-by-issue-start>
  - Worktree: (in-place — primary checkout at E:\automation\<repo>)
  - Files changed: <list>
  - Verification: PASS / SKIPPED (<reason>) / FAIL (<short reason>)
  - E2e: <tier> (<reason>) — PASS / FAIL / skip / n/a
  - Validate: <live URL | restart command | n/a (no web surface)>
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

Then stop. Do not poll, do not sleep, do not check on progress — the harness re-invokes you automatically when each background agent completes. On Opus, when a completion frees a window slot, dispatch the next pending issue (step 7) until the batch drains.

### 9. Report each completion (as agents return)

**Before surfacing a completion as verified, run the post-flight dirty-tree check yourself — never trust the agent's self-reported `Files changed:`/`Verification:` lines alone.** Every issue-batch sub-agent is build-and-stop (never merges), so this is always `--mode built`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dirty_tree_check.py check <path> --mode built --expect-branch <branch>
```

(`<path>` is the worktree path in worktree mode, or `E:\automation\<repo>` in-place.) `STATUS=DIRTY` → keep the agent's verification mark but append an explicit `⚠️ post-flight: <REASON>` note (catches HEAD unexpectedly back on the default branch, a branch mismatch, or the agent reporting changed files it never actually saved). `STATUS=UNKNOWN` → the helper could not read that repo, no verdict: mark it `❓ tree unverified — <REASON>`, never fold into a pass or a fail (fleet-config#570). This check only reports; never blocks the run, never auto-commits, never auto-fixes.

As each background sub-agent finishes, surface its report verbatim in the chat with a short header (`✅` if verification passed, `⚠️` if skipped or post-flight flagged something, `❌` if failed).

After **all** agents have returned, fire the batch-complete ping with the deterministic helper (canonical format, resolves channel/user from `projects.toml`):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind batch --passed <pass> --total <total>
```

If no chat is configured it's a silent no-op; it always exits 0, so a notification failure can't block or delay anything.

Then finish with one summary block:

```
All <N> sub-agents complete.
  ✅ <repo>#<N> ready for review — `cd <path> && /issue-finish`
  ✅ <repo>#<N> ready for review — `cd <path> && /issue-finish`
  ⚠️ <repo>#<N> ready for review, post-flight: <reason> — inspect before `/issue-finish`
  ❌ <repo>#<N> verification failed — inspect <path>

Next: review each branch, then ship — either `/issue-finish-batch <branches>`
to fan out parallel Sonnet finishers once you're happy with several, or
`/issue-finish` one at a time (sequential merges avoid CI pile-up and
tray-restart races). `/issue-finish` removes a worktree-mode branch's worktree
for you (it detects the linked worktree and runs the reparse-safe teardown). To
clean one up by hand, run from the primary checkout, never from inside the
worktree: `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py
remove-worktree <wt-path>`.
```

### 10. Stop

No follow-up actions. The user reviews each branch, then ships — via `/issue-finish-batch <branches>` (parallel Sonnet finishers, blocker-only escalation) or `/issue-finish` per branch (manual fallback). Do **not** auto-launch either.
