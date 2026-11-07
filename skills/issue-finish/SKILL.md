---
name: issue-finish
description: Finish an issue — confirm acceptance, update docs/README, run the gate, push, open a closing PR, treat CI as advisory (skip only when the recorded local gate/e2e pass still matches the tree, or no e2e surface touched; flake reruns once), auto-merge, delete branch, restart tray. Use "/issue-finish"; pairs with /issue-start.
---

# issue-finish

**Goal:** Take a finished feature branch all the way to merged-and-closed,
neatly. Invoking this skill is explicit authorization to commit, push, and merge.

## Pre-flight

Run in parallel; stop on any failure:
- `git rev-parse --is-inside-work-tree` — must be `true`.
- `git branch --show-current` — must be a feature branch, not main. On main,
  stop: "Not on a feature branch — nothing to finish."
- Derive the **issue number** from the branch name (`feat/53-...` → `53`). No
  number → ask which issue this closes.
- Read the project's `CLAUDE.md` — verification gate command, docs discipline,
  any tray/restart procedure.
- **Detect the checkout mode** (drives the merge-land + cleanup in step 5):
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py mode <repo>
  ```
  prints `primary` (shared checkout) or `worktree` (a linked `<repo>-wt-<N>`
  created by `/issue-start`'s concurrency path) — the two modes finish
  differently. **Run it from the checkout you are finishing** — it answers
  about the cwd, and `<repo>` only says which repo that cwd must belong to
  (fleet-config#652). `UNKNOWN reason=<why>` (exit 2) → stop and fix the
  cwd/argument, never guess a mode.

## Steps

### 1. Finalize the work

- `git status --porcelain` — uncommitted changes → commit now with a clear
  `type: summary` message (Git section of `CLAUDE.md`; no AI-attribution
  trailer).
- **Audit acceptance per criterion** (fleet-config#958). Code that handles a
  deliverable is not the deliverable.
  1. `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/acceptance_audit.py extract <N>`
     prints a JSON template, one entry per checkbox criterion; save it to a
     scratch file and fill every entry: `proof_location` — where the proof
     lives: `DIFF` (branch diff / gate output), `CROSS-REPO` (another repo's
     state), `EXTERNAL` (live process, device, schedule, machine-local file);
     `verdict` — `DONE` / `NOT DONE` / `UNVERIFIABLE`; `evidence` — one line;
     for `UNVERIFIABLE`, `verifier` (who/what can establish it) and
     `post_merge` (true when it can only be checked after merge).
  2. `… acceptance_audit.py tally <N> <file>` — add `--unattended` when this
     finish was invoked with `unattended` (`/issue-finish-batch`,
     `/cleanup-fleet-all`). Act on the printed `ACCEPTANCE=`:
     - `done` → proceed. `no_criteria` → judge the issue's prose; say so.
     - `not_done` → stop and say which; don't finish a partial issue.
     - `incomplete` → a criterion has no valid verdict; fill it — a gap is
       never read as met.
     - `unverifiable` → interactive: ask about each listed item one by one;
       unattended: proceed. Either way, carry each `PR_TEST_PLAN` line into
       the step-4 PR body and the step-7 summary individually — never a
       blanket "please confirm the rest".
     - `blocked` (unattended only) → an unverifiable item is not `EXTERNAL`
       and post-merge by nature; stop and report it as the blocker.
     - `unknown` → the issue could not be read; stop.

### 2. Documentation

- Update `README.md` if usage, config, or output changed.
- Do **not** create a dated `docs/YYYY-MM-DD-*.md` changelog — the PR body, the
  closed issue, and `git log` already capture it. `docs/` is for durable *design
  records*, not per-PR changelogs.
- Commit any documentation changes.

### 2b. Visual docs (`/docs-shots` sub-step — repos with a screenshot manifest only)

Deterministic discovery + diff-intersection, no LLM re-derivation:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py check <repo-root>
```

`MANIFEST=absent` → **silent no-op**, skip straight to step 3.
`MANIFEST=<path>` with an empty `STALE` → also a no-op (say so in the finish
summary: `docs-shots: no visual-docs feature touched by this diff`). A
non-empty `STALE` → read [`visual-docs.md`](E:/automation/fleet-config/skills/issue-finish/visual-docs.md)
and run its refresh flow inside this run.

### 3. Verification gate

Run the gate the project's `CLAUDE.md` specifies (e.g.
`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`)
**through the evidence helper**, so its result is bound to the tree it ran
against (fleet-config#957):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gate_evidence.py run --label gate -- <gate command argv>
```

It exits with the gate's own code; must exit 0. Do not proceed on a red gate.
No checker → say so explicitly, never claim tests passed when there are none.
A structure-ratchet failure on root layout or placement is fixed by moving the
file to where `docs/layout.md` says, never by adding an `extra_roots` entry.
Every later "is the gate still green?" question — steps 3c and 5 — is
answered by `gate_evidence.py check --label gate`, never by memory:
`FRESH` (the recorded pass matches the current tree), `STALE` (the tree
changed since — e.g. a 3b drift-fix commit, `/e2e` suite upkeep),
`MISSING`, or `UNKNOWN` (hash not computable). Anything but `FRESH` → re-run
the gate once through the helper; a last line `EVIDENCE=not_recorded` means
the gate itself changed the tree (the paths are named) — watch CI rather than
loop.

**Bug issues (`bug` label) need red-first evidence** (fleet-config#1915): the new
regression test must be seen failing on the pre-fix tree, and the helper records
that failure bound to the tree it ran on. If the branch has no such record yet,
`git stash` the fix (leave the new test in the tree), run
`gate_evidence.py run --label red -- <the new test's command>`, see it exit
non-zero, then `git stash pop`; `gate_evidence.py check-red` prints `RED` once the
record is on a different tree than the fix. A bug with no automatable test skips
this with a PR-body line `**Red-first:** exempt · <reason>` (step 4).

### 3b. UX-conformance gate (web-app UX diffs only)

When the diff touches the web app's UX, confirm it still conforms to the fleet
design system **and** isn't visually broken — *before* the PR, so a drift-fix
commit lands in it. Convention + contract: `project-scaffolding#83`. Trigger is
deterministic, not a judgment call:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/ux_surface.py check .
```

- `SPEC_APPLIES=no` (non-web repo / Streamlit spike) **or** `TOUCHED=no` → gate
  is a no-op. **State it** in the step-7 summary (`no UX surface touched`) and
  go to step 4 — the common case, costs nothing.
- `TOUCHED=unknown` → the diff itself could not be taken — **not** evidence the
  surface was untouched (fleet-config#681). Treat as `yes`: run both legs
  against every `KEY_VIEWS` entry, say `UX surface: unknown (diff failed) —
  gate run in full` in the step-7 summary.
- `TOUCHED=yes` → run both legs against the files in `MATCHED`.

**`TOUCHED=yes`, `TOUCHED=unknown` or a forcing override** → read
[`ux-conformance.md`](E:/automation/fleet-config/skills/issue-finish/ux-conformance.md)
now: it holds both legs (token check, visual proof, visual check).

**Overrides** (words in the finish invocation): `ux`/`design` forces the gate
even if `TOUCHED=no`; `no-ux` skips it; `ux-full` checks every `KEY_VIEWS`
entry. Always **state** the gate decision (ran / skipped / `ux-full`, plus any
drift fixed) in the step-7 summary so the user can veto.

### 3c. E2e leg (delegated to `/e2e`)

Run the **`/e2e` skill** (`skills/e2e/SKILL.md`) — evaluation is mandatory
before any PR; execution is proportionate. It probes the repo, routes the
branch diff through the repo's own `classify_e2e.py` (bootstrapping it on first
contact), runs the routed slice (`skip` / `static` / `full` / `surface`), and applies its
inline suite maintenance (delete-with-the-feature, qualifying additions, table
upkeep) on this branch. Integration rules:

- If step 3's gate already executed the routed e2e slice (the
  scaffold-shaped `verify-before-ship` gates route internally) **and**
  `gate_evidence.py check --label gate` prints `FRESH`, `/e2e` carries that
  result — no double run. Any other answer → run the slice.
- A **FAIL** from the slice stops the finish exactly like a red gate.
- Echo `/e2e`'s report block into the step-7 summary — the tier + reason
  always appear there, even when the outcome is `skip` or `n/a`.

### 4. Push and open the PR

- `git push -u origin <branch>`.
- `gh pr create` with a body containing: a short **Summary**, a **Validation**
  line (what gate ran and its result), and `Closes #<N>` so the issue
  auto-closes on merge. Step 1's `PR_TEST_PLAN` lines go in as unticked test-plan
  boxes, one per unverifiable criterion, naming its verifier.
  If the diff **untracks a file** (`git rm --cached`, a rename to `.sample`, a `.gitignore` line over a tracked path), say so in the Summary, naming each path: every checkout that pulls it loses its live copy unless its fast-forward goes through `untrack_guard.py` (fleet-config#1086).
  Paste `skills/_lib/instruction_delta/delta.py`'s `INSTRUCTION_DELTA=` line verbatim into the Summary.
  A `bug` PR names the confirmed cause: `**Cause:** <the hypothesis the repro proved>`.
  A `bug` PR carries the step-3 red-first record, or `**Red-first:** exempt · <reason>` for a bug with no automatable test.
  Add `**Merge danger:** two-way door · blast radius: <word>` (one-way appends ` · <reason>`).
  `skills/_lib/debug_tags/check.py .` must pass before `gh pr create`: each `FOUND=` line is leftover debug output to delete; `NOT_CONFIRMED` stops the finish.
  Match the PR-body style of recent merged PRs in the repo.
  No `🤖 Generated with [Claude Code]` line, session link or AI trailer; no image or screenshot in a public repo's PR.
  Tick a test-plan box only for something observed: the box must match a `DONE` verdict from step 1 or name the gate while `gate_evidence.py check --label gate` is `FRESH`.
- **PR-body check**, right after `gh pr create`/`gh pr edit`: `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/pr_body/check.py --pr <PR> --issue <N> --verdicts <step-1 verdicts file>`
  (`--allow-images` only when the project's `CLAUDE.md` declares the repo private). `PR_BODY=pass` → continue.
  `refused` → each `REFUSED <rule>: <fix>` line names what to change; `gh pr edit` the body and re-run until it passes.
  `not_confirmed` (exit 3) → the PR, verdicts file or gate record could not be read; stop and say so, never read it as a pass.
  Rules: [pr-body-check](E:/automation/fleet-config/docs/skills/pr-body-check.md).

### 5. Merge (CI is advisory — skip the wait when it adds no signal)

**CI is advisory, not a required gate.** The local verification gate (step 3)
is the contract. CI's **only** signal beyond it is the **e2e suite** (when the
local gate doesn't run it — needs browsers + a live webapp), also the
known-flaky leg — waiting adds nothing once that signal exists locally.
Decision below is driven by the project's `## CI expectations` block
(convention: `ferraroroberto/project-scaffolding#52`).

- **Read the project's `## CI expectations` block in `CLAUDE.md`.** It declares
  the workflow/job, the typical-green duration + investigate/wedged thresholds,
  the documented flaky leg, and the **e2e surface** paths. **Absent → fall back
  to the conservative behavior: always `--watch` (skip nothing)**, subject to
  the local-e2e-proof rule below. Do not invent thresholds or surface paths the
  block doesn't state.
- **Sync with the default branch before the evidence check.** The squash lands
  on whatever `origin/<default>` is *now*, so a gate that passed on a behind
  branch proved a tree the merge never produces. `git fetch origin`, then
  `git rev-list --count HEAD..origin/<default>` (`<default>` per
  `git symbolic-ref refs/remotes/origin/HEAD`). `0` → `BEHIND=0`, nothing else
  runs. `n > 0` → `git merge origin/<default>` (never rebase), `git push`,
  `BEHIND=<n> merged`; the tree changed, so the check below prints `STALE` and
  its single re-run covers the merged tree. A conflict → `git merge --abort`,
  stop and report; never resolve it unreviewed.
- **Evidence first.** Every skip below requires
  `gate_evidence.py check --label gate` to print `FRESH` *now*, just before
  the merge. `STALE` / `MISSING` / `UNKNOWN` → re-run the gate once through
  the helper, else watch; never skip the wait on remembered green.
- **Skip-the-wait when step 3c's `/e2e` run already proved it.** If the gate
  is `FRESH` **and** a passing `full`-tier e2e run is `FRESH` too — `check
  --label e2e-full` (recorded by `/e2e`), or the gate itself ran the full
  slice → skip the watch and merge immediately,
  **regardless of whether the diff touches declared e2e-surface paths**.
  **State it**, e.g. `CI not awaited — /e2e full slice FRESH for this tree`.
- **Otherwise, skip-the-wait keyed on the `/e2e` routing.** If step 3c routed
  `skip` (classifier or judgment fail-safe positively cleared every changed
  path of browser impact) and the local gate is `FRESH` → skip the watch and
  merge immediately. Same for a `static` or `surface` slice whose `e2e-<tier>` evidence is `FRESH`: the repo's
  own `[e2e]` table declared that target sufficient for those paths. **State it**,
  e.g. `CI not awaited — /e2e routed skip: docs-only diff`. Never re-derive the
  surface match by eye — the routing decision is step 3c's.
- **Otherwise watch — but proactively, not passively.** Read
  [`ci-watch.md`](E:/automation/fleet-config/skills/issue-finish/ci-watch.md)
  for the watch command, the flake-vs-real classification and the rerun-once rule.
- **Keep-control guardrails.** Always **state** the CI decision (skip vs wait,
  plus any cancel/rerun) in the finish summary so the user can veto. Auto-rerun
  is capped at **once** and only for the *documented* flaky leg. Nothing
  force-merges: CI is advisory (no branch protection), so no `--admin` is ever
  needed — but **if a repo later marks the `e2e` check *required*** in branch
  protection, the skip-rule must **fall back to watching** (`--admin` is out of
  scope). This skips only the *remote CI wait*; never the step-3 gate.
- **Merge, then land + clean up, by checkout mode** (from pre-flight). Read the
  one file for your mode and follow it to its end before the bullets below:
  - **Primary checkout** → [`land-primary.md`](E:/automation/fleet-config/skills/issue-finish/land-primary.md)
    (squash merge with `--delete-branch`, `assert-owner`, fast-forward, release
    and verify the claim).
  - **Linked worktree** → [`land-worktree.md`](E:/automation/fleet-config/skills/issue-finish/land-worktree.md)
    (squash merge without `--delete-branch`, landed-check ref deletes, remove
    the worktree, `land-primary`, its `PRIMARY=` line).
- Confirm the issue closed (`gh issue view <N>` → `CLOSED`). If it didn't
  auto-close, close it manually with a comment referencing the merge commit.
- **Owner check** ([owner-check](E:/automation/fleet-config/docs/owner-check.md)), after the close:
  `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/owner_check/cli.py finish <repo>#<N> --pr <PR> --verified yes|scheduled|no [--gap <class>:<criterion> …] --try "<what Roberto does>" [--proof <URL> …]`.
  Prove first: before `no` for anything visual or behavioural, run the gate, the original repro, a screenshot
  baseline or visual proof, rendered geometry and an API probe; a criterion they show is ticked with the
  evidence. `yes` when the repro or acceptance re-ran and passed in this run; `scheduled` when all of it did
  except a criterion only a later scheduled run can show (the chief reads it); `no` only with one `--gap` per
  criterion no such proof can show, class `device`, `owner-account`, `physical`, `third-party` or `taste`.
  Exit 2 → fix the refused flag (`--verified`, `--gap` or `--try`) and re-run. Step 7 carries its `OWNER_CHECK=` line
  verbatim; `unknown` is its own state, never reported as none.
- Clear the issue's Fleet Board marker now that the merge is authoritative:
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/active_issue.py remove <repo> <N>
  ```
  This runs **after** a successful merge, never when verification or CI stops
  the finish early — a failed finish is still in flight and must remain marked.
  The remove is idempotent (`ACTIVE_ISSUE=absent` is success), but a helper
  error is not: retry once and stop if it still cannot update the shared file.
- **Delete the repo's proven-landed and superseded branches, report the rest**,
  from the primary repo path, after the landing above — one call, non-blocking,
  no question to anyone:
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/branch_residue.py branches E:/automation/<repo> --delete --remote
  ```
  **The rule (the same one `/cleanup-fleet-all` states):** a ref classed `landed`
  (no open PR, tip proven merged with a closed issue, or equal to the recorded head of
  its own PR merged into the default branch whatever the issue says) or `superseded` (closed issue, no
  open PR, tip contained in the merge of another branch's PR that closed the issue)
  holds no unshipped work, so any run deletes it, local and on `origin`. The
  helper re-judges each target from a fresh tip, proof and PR lookup right before
  deleting it and skips anything that moved or changed class. It never touches
  the default branch, a checked-out branch, or an `open-issue`, `open-pr`,
  `no-issue`, `unlanded` or `unknown` ref.
  Put its result in the step-7 summary: every `DELETED=` line (with its
  `RESTORE=`), then `branch residue: N unlanded, K unknown` from the `SUMMARY` line,
  naming each `unlanded` ref, plus each `ORPHAN=` line (a `<repo>-wt-*` folder git
  no longer registers). `unlanded` refs stay for a human, raised as one batched
  question per repo, never one per branch. `unknown` is its own state (a fact was
  not established), never folded into another count. If the helper could not run,
  or printed `VERDICT=unknown`/`partial`, say `branch residue: not established` —
  never `0`. `FAILED=` lines are reported, never retried by hand; no folder is
  removed here. The landed check that gates your own branch's `-D` (the
  `git diff --quiet` test above) is unchanged.

### 6. Restart the tray (only if the project runs one)

If the project's `CLAUDE.md` describes a tray or long-running local process,
follow that procedure **exactly**. The non-negotiables: read
[`tray-restart.md`](E:/automation/fleet-config/skills/issue-finish/tray-restart.md)
before running any restart.

If the project has no tray, skip this step.

### 6b. Deploy-coverage check (repos with declared not-fully-covered components only)

After step 6, **read [`deploy-coverage.md`](E:/automation/fleet-config/skills/issue-finish/deploy-coverage.md) and run its `deploy_coverage.py check .`**: `DECLARED=no` is a silent no-op, any other result qualifies the step-7 summary as confirmed live, merged but not yet live, or unknown — never blocking the merge.

### 7. Report

Summarize: issue closed, PR merged, branch deleted, docs updated (or why not),
the step-5 `BEHIND=` line (`BEHIND=0` or `BEHIND=<n> merged`), gate result
with its evidence state at merge time (`gate: FRESH` / re-run /
`STALE → CI watched`), the step-1 `ACCEPTANCE=` state with each unverifiable criterion
listed individually (verifier named), the UX-conformance gate decision (ran / skipped / `ux-full`, plus
any drift fixed, the `VISUAL_PROOF=` line and the proof page `SENT=` line — step 3b), the `/e2e` report block (source, tier + reason,
result, maintenance — step 3c), the deploy-coverage decision (n/a / not
touched / confirmed live / merged but not yet live / unknown — step 6b),
the live build line, the step-4 `Merge danger:` line, and the step-5 `OWNER_CHECK=` line. **Worktree mode also carries step 5's `PRIMARY=` line
verbatim** — `PRIMARY=live behind=0` or `PRIMARY=stale reason=<why>` — right
next to the merge result, with any `RESTORED_UNTRACKED=`/`KEPT_ASIDE=` line
(both modes; a `KEPT_ASIDE` file is only in its stash dir and needs a human). Merged and live are two facts; a summary reporting
only the first is reporting a deploy it never established.

Then append the **work-summary** — the file/LOC shape of what shipped — by
running the deterministic helper and pasting its output verbatim into the reply
as plain markdown — **never inside a code fence** (a chat renderer shows a fenced
table as literal `|` text; it renders only outside one):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/work_summary.py --pr <PR_URL>
```

It prints the roll-up (`📊 +N −M · K files` + new/changed/deleted buckets) and a
churn-sorted per-file table (status · file · + · −). No LLM, all from `gh`; it
prints nothing on any `gh` error, so skip the block if it comes back empty. The
same roll-up rides the Telegram ping in step 8 — don't re-assemble it by hand.

### 8. Telegram notification

After the summary, fire the completion ping with the deterministic helper. It
resolves the chat from `projects.toml` and emits the one canonical format. Run:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind finish --issue <N> --pr <PR> --pr-url <PR_URL>
```

`<PR_URL>` is the full PR URL (e.g. `https://github.com/owner/repo/pull/31`) —
pass the URL already on hand from `gh pr create`/`gh pr view`; keeps the
title/URL lookup CWD-independent for subagent contexts where the shell's
working directory may differ from the project root. No chat configured →
silent no-op, always exits 0 — a notification failure can never block or
delay anything.

**`notify_complete.py` is the ONLY sanctioned way to send this ping — never use an MCP chat tool to find a chat or post it, and a silent no-op is correct, not a bug to route around.** Why: [notify-ping](E:/automation/fleet-config/docs/notify-ping.md).
