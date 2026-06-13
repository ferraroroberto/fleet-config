---
name: issue-finish
description: Finish a GitHub issue cleanly — confirm acceptance, update docs/README, run the verification gate, push, open a PR that closes the issue, handle CI as advisory (skip the wait when the diff touches no e2e surface; proactively rerun a documented flake once), auto-merge, delete the branch, and restart the project's tray safely. Use when work on an issue branch is complete, e.g. "/issue-finish". Pairs with /issue-start.
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
  Do **not** include the `🤖 Generated with [Claude Code]` line at the bottom of the PR body.

### 5. Merge (CI is advisory — skip the wait when it adds no signal)

**CI is advisory, not a required gate.** The local verification gate (step 3) is
the contract; CI is supplementary. Its **only** signal beyond the local gate is
the **e2e suite** (the local gate skips it — it needs browsers + a live webapp),
which is also the known-flaky leg. So a diff that touches none of the e2e surface
gains nothing from waiting, and a wedged browser can block the merge for nothing.
The decision below is driven by the project's `## CI expectations` block (the
convention is `ferraroroberto/project-scaffolding#52`).

- **Read the project's `## CI expectations` block in `CLAUDE.md`.** It declares
  the workflow/job, the typical-green duration + investigate/wedged thresholds,
  the documented flaky leg, and the **e2e surface** paths. **Absent → fall back
  to the conservative behavior: always `--watch` (skip nothing).** Do not invent
  thresholds or surface paths the block doesn't state.
- **Skip-the-wait keyed on the e2e surface.** If the diff touches **none** of the
  declared e2e-surface paths and the local gate (step 3) is green → skip the
  watch and merge immediately. **State it** in the summary, e.g. `CI not awaited
  — store-only diff, no e2e surface touched`. (This generalizes the old narrow
  `*.md`-only rule: e2e is the only thing CI runs that the local gate skipped.)
- **Otherwise watch — but proactively, not passively.** Run `gh pr checks <PR>
  --watch`. The moment elapsed crosses the block's **investigate threshold**,
  stop waiting passively: inspect the run (`gh run view <run-id> --job <job>`)
  and classify **flake vs real failure**.
  - **Real failure** (test assertion, compile/lint/type error, a leg that isn't
    the documented flaky one) → stop and report. **Never rerun a real failure.**
  - **Documented flaky leg wedged** (per the block — e.g. the Playwright
    WebKit/PTY-input leg) → cancel + rerun **once** automatically, saying so
    (`cancelled wedged <leg> run, rerunning once`). If it flakes a **second**
    time → stop and surface it to the user; do not rerun again.
- **Keep-control guardrails.** Always **state** the CI decision (skip vs wait,
  plus any cancel/rerun) in the finish summary so the user can veto. Auto-rerun
  is capped at **once** and only for the *documented* flaky leg. Nothing
  force-merges: CI is advisory (no branch protection), so no `--admin` is ever
  needed — but **if a repo later marks the `e2e` check *required*** in branch
  protection, the skip-rule must **fall back to watching** (a required check
  can't be skipped without `--admin`, which is out of scope here). This skips
  only the *remote CI wait*; it never skips the verification gate in step 3.
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
- **Invoke it through a real Windows shell — never Git Bash's nested `cmd /c`.**
  Run the restart via the harness PowerShell tool, or
  `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "& '<repo>\tray.bat' --restart"`
  (forward-slash exe path, per the Git-Bash-strips-backslashes rule). Launched
  through the Bash tool as `cmd /c "tray.bat --restart"`, the batch's embedded
  `powershell.exe` tray-detection is mangled by the nested quoting: the call
  emits only the `cmd` banner, none of the batch's own `Stopping previous…`
  echoes, kills nothing, and `--restart` silently degrades to a plain start that
  **adopts the still-running old-build webapp**. Fire it non-blocking (the tray
  holds its console — a foreground launch never returns), then move to the
  bounded poll below.
- **Safety caveat — linked children.** `tray.bat --restart` does a `/T` subtree
  kill, so it is safe only for a tray whose linked-but-independent children
  (a session-host + its PTY-backed shells) are spawned **detached** and
  re-adopted on start (scaffold `docs/windows-tray.md`). For a tray that still
  hosts such children *in its subtree* (today: `app-launcher`), `--restart`
  kills the user's open Coding sessions — and your own, if you're running inside
  one. That tray's `CLAUDE.md` flags it: **confirm with the user first**, or use
  its non-destructive path (kill only the webapp port, let the tray re-adopt).
- **Fallback only** for a project with no `--restart`: kill **only** the
  specific process listening on the project's port (`Get-NetTCPConnection
  -LocalPort <port>`, stop that PID — **never** a blanket `python`/`pythonw`
  kill), then relaunch via its start script.
- Confirm the new build is live with a **bounded** poll of the project's
  version endpoint (e.g. `GET /api/version`): a **hard timeout + attempt cap**
  (≤30 s / fixed attempts), then **fail loud** — never an open-ended wait. The
  git SHA must match `HEAD` (a `/healthz` 200 is not enough — a stale process
  passes it) and the asset hash should have changed. Report that build line.
- **On a `git_sha` ≠ `HEAD` mismatch (a silent adopt-stale), stop and surface it
  to the user — do not improvise process kills.** A by-hand `taskkill`/
  `Get-NetTCPConnection` kill during recovery is exactly what the safe-restart
  rules warn against: it catches the one listener it finds, misses the orphan,
  and a mistimed single-PID kill can take the server fully down. The robust
  reclaim is the tray's job (`project-scaffolding#54` hardens `--restart` to
  reclaim and self-verify); the finisher's contract is to invoke it correctly
  and **report** a mismatch, not to hand-fix it.

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
