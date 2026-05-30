---
name: issue-finish
description: Finish a GitHub issue cleanly — confirm acceptance, update docs/README, run the verification gate, push, open a PR that closes the issue, wait for CI, auto-merge, delete the branch, and restart the project's tray safely. Use when work on an issue branch is complete, e.g. "/issue-finish". Pairs with /issue-start.
---

# issue-finish

**Goal:** Take a finished feature branch all the way to merged-and-closed,
neatly. Invoking this skill is explicit authorization to commit, push, and merge.

## Pre-flight

Run in parallel; stop on any failure:
- `git rev-parse --is-inside-work-tree` — must be `true`.
- `git branch --show-current` — must be a feature branch, not the main branch.
  If on main, stop: "Not on a feature branch — nothing to finish."
- Derive the **issue number** from the branch name (`feat/53-...` → `53`).
  If the branch carries no number, ask which issue this closes.
- Read the project's `CLAUDE.md` — verification gate command, docs discipline,
  any tray/restart procedure.

## Steps

### 1. Finalize the work

- `git status --porcelain` — if there are uncommitted changes, commit them now
  with a clear `type: summary` message (follow the Git section of `CLAUDE.md`;
  no AI-attribution trailer).
- Re-read the issue (`gh issue view <N>`) and confirm every acceptance point is
  actually met. If something is unmet, stop and say so — don't finish a partial
  issue.

### 2. Documentation

- Update `README.md` if usage, config, or output changed.
- Do **not** create a dated `docs/YYYY-MM-DD-*.md` changelog. The PR body, the
  closed issue, and `git log` already capture "what was done, files modified,
  validation run" — a third copy in `docs/` is busywork that ages badly. `docs/`
  is reserved for durable *design records* a future reader will actually
  re-open (architecture, testing strategy, etc.), not per-PR changelogs.
- Commit any documentation changes.

### 3. Verification gate

Run the gate the project's `CLAUDE.md` specifies (e.g.
`pwsh -File scripts/verify-before-ship.ps1`). It must exit 0. Do not proceed on
a red gate. If the project has no checker, say so explicitly — never claim tests
passed when there are none.

### 4. Push and open the PR

- `git push -u origin <branch>`.
- `gh pr create` with a body containing: a short **Summary**, a **Validation**
  line (what gate ran and its result), and `Closes #<N>` so the issue
  auto-closes on merge. Match the PR-body style of recent merged PRs in the repo.

### 5. Wait for CI, then merge

- `gh pr checks <PR> --watch` — wait for all required checks to go green. If a
  check fails, stop and report — don't merge red.
- `gh pr merge <PR> --merge --delete-branch` — merge commit; branch deleted on
  both remote and local.
- `git checkout <main>` then `git pull --ff-only` to land the merge locally.
- Confirm the issue closed (`gh issue view <N>` → `CLOSED`). If it didn't
  auto-close, close it manually with a comment referencing the merge commit.

### 6. Restart the tray (only if the project runs one)

If the project's `CLAUDE.md` describes a tray or long-running local process,
follow that procedure **exactly**. The non-negotiables:
- Kill **only** the specific process listening on the project's port — find it
  with `Get-NetTCPConnection -LocalPort <port>` and stop that PID. **Never** a
  blanket `python`/`pythonw` kill: sister apps and other services must survive.
- Relaunch via the project's start script (e.g. `tray.bat`) — these are usually
  start-only and won't restart a live instance, so kill first, then relaunch.
- Confirm the new build is live via the project's version endpoint (e.g.
  `GET /api/version`): the git SHA should match `HEAD` and the asset hash should
  have changed. Report that build line.

If the project has no tray, skip this step.

### 7. Report

Summarize: issue closed, PR merged, branch deleted, docs updated (or why not),
gate result, and the live build line.

### 8. Slack notification

After the summary, fire a completion ping so the user knows the work is done
while away from the terminal.

Read `slack_notify_channel` and `slack_notify_user` from the `[global]` table
in `~/.claude/hooks/projects.toml`. If the channel key is absent, skip silently.
If both are present, run:

```
py C:/Users/rober/.claude/hooks/slack_notify.py --channel <channel> --text "<@<user>> ✅ Done: #<N> <title> — PR merged · <pr-url>"
```

Never let a notification failure block or delay anything — if the command
errors, log the error and continue.
