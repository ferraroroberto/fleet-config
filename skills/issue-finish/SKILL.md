---
name: issue-finish
description: Finish a GitHub issue cleanly — confirm acceptance, update docs/README, run the verification gate, push, open a PR that closes the issue, wait for CI (skipped when the change is provably CI-unrelated), auto-merge, delete the branch, and restart the project's tray safely. Use when work on an issue branch is complete, e.g. "/issue-finish". Pairs with /issue-start.
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

### 5. Merge (wait for CI unless provably unrelated)

- **Classify the diff first.** It is **CI-unrelated** only if *every* changed
  file is one CI never executes — `*.md`, `docs/`, `LICENSE`, images/assets, or
  pure code-comment edits — **AND** `.github/workflows/` contains no job that
  targets them (no markdownlint, link-checker, docs/site build). Actually read
  the workflow files to confirm — never assume.
- **If CI-unrelated:** skip the watch and merge immediately (next bullet). State
  it in the summary, e.g. `CI not awaited — docs-only change, no docs CI job.`
  If the merge is rejected because a branch-protection *required* check is still
  pending/failing, fall back to `--watch` and proceed as below.
- **Otherwise** (any source/test/config/dependency/build touch, or any doubt
  about what the workflows cover): `gh pr checks <PR> --watch` — wait for all
  required checks to go green. If a check fails, stop and report — don't merge
  red. This skips only the *remote CI wait*; it never skips the verification
  gate in step 2, which always runs.
- `gh pr merge <PR> --merge --delete-branch` — merge commit; branch deleted on
  both remote and local.
- `git checkout <main>` then `git pull --ff-only` to land the merge locally.
- Confirm the issue closed (`gh issue view <N>` → `CLOSED`). If it didn't
  auto-close, close it manually with a comment referencing the merge commit.

### 6. Restart the tray (only if the project runs one)

If the project's `CLAUDE.md` describes a tray or long-running local process,
follow that procedure **exactly**. The non-negotiables:
- **Prefer the deterministic restart.** If the project ships a `tray.bat`
  with a `--restart` flag (the canonical orphan-proof reclaim-then-start —
  every fleet tray has one), run **`tray.bat --restart`** and nothing else.
  That single command does the subtree kill + per-`.venv` port reclaim + start
  atomically. **Do not** hand-roll a `Get-NetTCPConnection`/`taskkill` kill:
  a by-hand kill only catches the one listener it finds and misses the orphan
  the reclaim sweep exists to kill, then re-runs a start-only script.
- **Fallback only** for a project with no `--restart`: kill **only** the
  specific process listening on the project's port (`Get-NetTCPConnection
  -LocalPort <port>`, stop that PID — **never** a blanket `python`/`pythonw`
  kill), then relaunch via its start script.
- Confirm the new build is live via the project's version/health endpoint
  (e.g. `GET /api/version`): the git SHA should match `HEAD` and the asset hash
  should have changed. Report that build line.

If the project has no tray, skip this step.

### 7. Report

Summarize: issue closed, PR merged, branch deleted, docs updated (or why not),
gate result, and the live build line.

### 8. Slack notification

After the summary, fire the completion ping with the deterministic helper. It
resolves the channel/user from `projects.toml` and emits the one canonical
format. Run:

```
py C:/Users/rober/.claude/hooks/notify_complete.py --kind finish --issue <N> --pr <PR> --pr-url <PR_URL>
```

`<PR_URL>` is the full PR URL (e.g. `https://github.com/owner/repo/pull/31`) —
pass the URL you already have from `gh pr create` or `gh pr view`. This makes
the title/URL lookup CWD-independent so it works correctly from subagent
contexts where the shell's working directory may differ from the project root.
If no channel is configured it's a silent no-op, and it always exits 0, so a
notification failure can never block or delay anything.
