---
name: issue-start
description: Start work on a GitHub issue — pick the issue, sync the main branch, cut a feature branch, load project context, then either present a plan (for enhancements) or build straight away (for bugs/chores). Use when beginning a new issue, e.g. "/issue-start 35", "/issue-start next", "/issue-start", "/issue-start 35 now" (force fast), or "/issue-start 35 plan" (force plan). Pairs with /issue-finish.
---

# issue-start

**Goal:** Get cleanly onto a fresh feature branch for one GitHub issue, then hand
off to the implementation. This skill sets up — it does **not** implement.

## Arguments

- A number (`/issue-start 35`) → that issue.
- No argument, `next`, or anything non-numeric → **pick mode**: list open issues
  and let me choose. Never auto-pick.
- The word `now` anywhere in the args (`/issue-start 35 now`, `/issue-start now`)
  → **force fast mode**: skip the plan-approval gate regardless of label.
- The word `plan` anywhere in the args → **force plan mode**: present a plan
  and wait for approval regardless of label.

Without `now`/`plan`, the mode is chosen from the issue's type label (step 6).

## Steps

Run in order. If a step fails, print a short error and stop — don't leave a
half-made branch behind.

### 1. Pre-flight

Run in parallel:
- `git rev-parse --is-inside-work-tree` — must be `true`, else stop:
  "Not inside a git repository."
- `git status --porcelain` — must be empty. If the working tree is dirty, stop:
  "Uncommitted changes — commit, stash, or discard them before starting a new
  issue." Never switch branches over dirty state.
- `git branch --show-current` — if already on a `feat/`/`fix/`/`ci/`/`docs/`
  branch, warn that another issue looks in-flight and ask whether to continue.

### 2. Choose the issue

- **Number given:** `gh issue view <N>`. If it fails or the issue is closed,
  stop and say so.
- **Pick mode:** `gh issue list --state open --json number,title,labels` and
  present the open issues with the AskUserQuestion tool (number + title). Never
  auto-pick. **Exclude any issue labelled `audit-meta`** (the `/codebase-audit`
  ledger — not actionable work); filter it out model-side rather than adding a
  `gh` query qualifier.

### 3. Read the issue and project conventions

In parallel:
- `gh issue view <N> --json number,title,body,labels` — read the whole issue.
- Read the project's `CLAUDE.md` (and `README.md` if present) — conventions,
  layout, verification gate.

### 4. Sync the main branch

- Detect the main branch: `git symbolic-ref refs/remotes/origin/HEAD` → strip
  `origin/`; fall back to `main`.
- `git checkout <main>` then `git pull --ff-only`. If the pull is not a
  fast-forward, stop and report — don't merge or rebase blindly.

### 5. Cut the feature branch

- Prefix: `fix/` if the issue carries a `bug` label, else `feat/` (use `ci/` or
  `docs/` when the issue is plainly that kind of work).
- Slug: lowercase the issue title, keep alphanumerics, collapse the rest to
  single hyphens, trim to ~4 words.
- Branch name: `<prefix>/<N>-<slug>` — e.g. `feat/35-running-session-rename`.
- `git checkout -b <branch>`. Report the branch name.

### 6. Hand off to work

Investigate the codebase for what the issue needs and decide on an approach.
Pick the mode:

- **Forced by args:** `now` → fast mode; `plan` → plan mode. Forced mode wins.
- **Otherwise from the issue's type label:**
  - `bug`, `chore`, `documentation` → **fast mode**. The work is usually small
    and the right shape is obvious from the issue + the code. Just build it.
  - `enhancement` → **plan mode**. New features deserve a plan-approval gate
    because shape decisions are expensive to undo.
  - No type label or unknown label → **plan mode** (safe default).

In **fast mode**: think the approach through, then go straight to implementing.
Do **not** enter plan mode and do **not** wait for approval. Only pause to ask
a question if there is genuine, expensive, or hard-to-undo ambiguity.

In **plan mode**: present an implementation plan and wait for approval, per
the project's plan-mode default in `CLAUDE.md`. Resolve real ambiguity with
questions first.

When the work, validation, and review are done, finish with `/issue-finish`.

### 7. Notify when control returns to the user

At the point where the ball is back in the user's court — the **plan is
presented for approval** (plan mode), or the **fast-mode build is complete and
ready to validate** — fire the completion ping so they can act from their phone:

```
py C:/Users/rober/.claude/hooks/notify_complete.py --kind start --issue <N> --summary "<one concise line: the single next action>"
```

The `--summary` is the only free-form part — keep it to one short imperative
line (e.g. `review the diff, then /issue-finish` or `approve the plan to
proceed`). The helper resolves the channel/user, pulls the issue title + link
from `gh`, and emits the canonical format. Silent no-op if no channel is
configured; always exits 0. Skip it only if the
work ran straight through to `/issue-finish` without ever pausing for the user
(that flow fires its own ping).
