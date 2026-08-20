---
name: issue-finish
description: Finish an issue — confirm acceptance, update docs/README, run the verification gate, push, open a closing PR, treat CI as advisory (skip when local e2e + pytest are green this session or no e2e surface touched; flake reruns once), auto-merge, delete branch, restart tray. Use "/issue-finish"; pairs with /issue-start.
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
- **Detect the checkout mode** (drives the merge-land + cleanup in step 5):
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py mode <repo>
  ```
  prints `primary` (work in the shared checkout) or `worktree` (a linked
  `<repo>-wt-<N>` created by `/issue-start`'s concurrency path). Remember which;
  the two modes finish differently. **Run it from the checkout you are
  finishing** — it answers about the cwd, and `<repo>` only says which repo that
  cwd must belong to (fleet-config#652). A `UNKNOWN reason=<why>` line (exit 2)
  means it could not tell: stop and fix the cwd/argument, never guess a mode.

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
- Do **not** create a dated `docs/YYYY-MM-DD-*.md` changelog — the PR body, the
  closed issue, and `git log` already capture what was done. `docs/` is reserved
  for durable *design records* a future reader will re-open (architecture,
  testing strategy), not per-PR changelogs.
- Commit any documentation changes.

### 2b. Visual docs (`/docs-shots` sub-step — repos with a screenshot manifest only)

Deterministic discovery + diff-intersection, no LLM re-derivation:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py check <repo-root>
```

`MANIFEST=absent` → this step is a **silent no-op**, most repos every time —
skip straight to step 3. `MANIFEST=<path>` with an empty `STALE` → also a
no-op (say so in the finish summary: `docs-shots: no visual-docs feature
touched by this diff`). A non-empty `STALE` → run the full `/docs-shots`
judgment + propose-then-capture flow (`skills/docs-shots/SKILL.md`) **inside
this run**: show the stale set + reasons, wait for the user's explicit OK,
and on approval invoke the repo's own doc-capture engine for just those
features, fold the result into the finish summary. Also surface any
`UNMAPPED` changed files for the user to add a manifest entry for — never
guess one. On decline, note the refresh was skipped and continue to step 3 —
never block the finish over it.

### 3. Verification gate

Run the gate the project's `CLAUDE.md` specifies (e.g.
`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`).
It must exit 0. Do not proceed on a red gate. If the project has no checker, say
so explicitly — never claim tests passed when there are none.

### 3b. UX-conformance gate (web-app UX diffs only)

When the diff touches the web app's UX, confirm it still conforms to the fleet
design system **and** isn't visually broken — *before* the PR, so a drift-fix
commit lands in it. Convention + contract: `project-scaffolding#83`. The trigger
is deterministic, not a judgment call:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/ux_surface.py check .
```

- `SPEC_APPLIES=no` (non-web repo / Streamlit spike) **or** `TOUCHED=no` → the
  gate is a no-op. **State it** in the step-7 summary (`no UX surface touched`)
  and go to step 4. This is the common case and costs nothing.
- `TOUCHED=yes` → run both legs against the files in `MATCHED`:
  - **Token check (fix-now).** Compare the touched CSS custom properties (light
    **and** dark) and the nav contract to `~/.claude/design.md` +
    `design.dark.md`, and **fix material drift in this branch now**, committing
    it — do *not* file-and-defer a `design-drift` issue (that is `/design-sync`'s
    job for the periodic sweep; this gate's job is to not *introduce* drift).
    Materiality bar: a wrong canvas color, a missing dark theme, a hand-rolled
    nav, or a broken layout is a blocker; a 1-unit radius nitpick is not.
  - **Visual check (in-session only — never attach the image).** Launch the
    feature-branch working tree and look at the touched view via the `verify`
    skill (with `ux-full`, every `KEY_VIEWS` entry, not just the touched one).
    Inspect the render against the spec — nav pill, layout, palette. **The
    screenshot is for your eyes in this session only:** save it to a local
    scratch path, never commit it, and **never attach it to the PR body, an
    issue, or a comment.** Assume every repo is public — an uploaded UI
    screenshot is an information breach. Put a **text-only** conformance line in
    the PR instead (e.g. `Visual: touched view renders per spec — nav pill,
    layout, palette conform`).
    - **Browser-backend preflight (Codex — no live `iab`).** Before the drive,
      pick the backend deterministically: prefer the in-app Browser (`iab`) when
      `agent.browsers.list()` includes it; when it returns `[]`, fall back to
      installed Playwright with real Chrome — `iab` absence is **not** a reason
      to skip the visual leg (fleet-config#351). Get the plan (backend, venv,
      `channel="chrome"` launch kwargs honoring the browser-safety contract,
      and the `KEY_VIEWS` × {light, dark} capture list) from:
      ```
      E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/browser_verify.py plan . --base-url <app-root> --iab-available <yes|no>
      ```
      A missing Playwright, missing Chrome, unreachable app, or exhausted
      profile-lock each report distinctly (never one generic error) — report
      the one you observed, never the whole legend. Those codes, background
      and recovery: `docs/codex-browser.md`. On Claude Code the `verify` skill's
      MCP browser is the `iab`-equivalent path — the fallback is for hosts with
      no live in-app backend.

**Overrides** (words in the finish invocation): `ux`/`design` forces the gate
even if `TOUCHED=no`; `no-ux` skips it; `ux-full` checks every `KEY_VIEWS`
entry. Always **state** the gate decision (ran / skipped / `ux-full`, plus any
drift fixed) in the step-7 summary so the user can veto.

### 3c. E2e leg (delegated to `/e2e`)

Run the **`/e2e` skill** (`skills/e2e/SKILL.md`) — the evaluation is
mandatory before any PR; the execution is proportionate. It probes the repo,
routes the branch diff through the repo's own `classify_e2e.py`
(bootstrapping it on first contact — self-healing adoption), runs the routed
slice (`skip` / `static` / `full`), and applies its inline suite maintenance
(delete-with-the-feature, qualifying additions, table upkeep) on this branch.
Integration rules:

- If step 3's gate already executed the routed e2e slice this session (the
  scaffold-shaped `verify-before-ship` gates route internally), `/e2e`
  carries that result — no double run.
- A **FAIL** from the slice stops the finish exactly like a red gate.
- Echo `/e2e`'s report block into the step-7 summary — the tier + reason
  always appear there, even when the outcome is `skip` or `n/a`.

### 4. Push and open the PR

- `git push -u origin <branch>`.
- `gh pr create` with a body containing: a short **Summary**, a **Validation**
  line (what gate ran and its result), and `Closes #<N>` so the issue
  auto-closes on merge. Match the PR-body style of recent merged PRs in the repo.
  Do **not** include the `🤖 Generated with [Claude Code]` line at the bottom of the PR body.

### 5. Merge (CI is advisory — skip the wait when it adds no signal)

**CI is advisory, not a required gate.** The local verification gate (step 3) is
the contract. CI's **only** signal beyond it is the **e2e suite** (when the local
gate doesn't run it — that needs browsers + a live webapp), which is also the
known-flaky leg. So waiting adds nothing for a diff touching no e2e surface, nor
for one whose e2e coverage was already proven **locally, in this session** — a
wedged remote browser blocks the merge for nothing either way. The decision below
is driven by the project's `## CI expectations` block (convention:
`ferraroroberto/project-scaffolding#52`).

- **Read the project's `## CI expectations` block in `CLAUDE.md`.** It declares
  the workflow/job, the typical-green duration + investigate/wedged thresholds,
  the documented flaky leg, and the **e2e surface** paths. **Absent → fall back
  to the conservative behavior: always `--watch` (skip nothing)**, subject to
  the local-e2e-proof rule below. Do not invent thresholds or surface paths the
  block doesn't state.
- **Skip-the-wait when step 3c's `/e2e` run already proved it.** If step 3's
  gate is green **and** step 3c executed (or carried from the gate) a passing
  `full`-tier e2e run this session → skip the watch and merge immediately,
  **regardless of whether the diff touches declared e2e-surface paths** —
  CI's only signal beyond the local gate has already been produced locally.
  **State it** in the summary, e.g. `CI not awaited — /e2e full slice green
  this session`.
- **Otherwise, skip-the-wait keyed on the `/e2e` routing.** If step 3c routed
  `skip` (the classifier — or the judgment fail-safe — positively cleared
  every changed path of browser impact) and the local gate is green → skip
  the watch and merge immediately. Same for a green `static` slice: the
  repo's own `[e2e]` table declared the smoke target sufficient for those
  paths. **State it**, e.g. `CI not awaited — /e2e routed skip: docs-only
  diff`. Never re-derive the surface match by eye — the routing decision is
  step 3c's.
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
- **Merge — the flag depends on the checkout mode** (from pre-flight):
  - **Primary checkout:** `gh pr merge <PR> --merge --delete-branch` — merge
    commit; branch deleted on both remote and local.
  - **Linked worktree:** `gh pr merge <PR> --merge` — **no `--delete-branch`**.
    From inside a worktree that flag fails its *local* half every time
    (`'main' is already used by worktree at <primary>`, 6 for 6 on 2026-08-16):
    `gh` tries to check out the default branch to delete the ref, and the
    primary holds it. The remote merge still succeeds, so the failure is
    cosmetic — but each lane then improvised its own recovery. Delete the refs
    explicitly instead, after the landing step below:
    ```
    git push origin --delete <branch>
    git -C <repo> branch -D <branch>
    ```
    **Gate the local delete on an ancestry check** — confirm the tip is already
    in the default branch before deleting anything: `git -C <repo> branch
    --merged origin/<default>` lists it, or `git -C <repo> diff --quiet
    origin/<default> <branch>` is clean. Use `-D`, not `-d`: a **squash** merge
    rewrites the SHA, so `-d`'s own merged-check fails on a branch that is
    genuinely merged. The ancestry check is what makes `-D` safe — never skip it
    and never `-D` a branch whose tip you haven't confirmed landed.
- **Land + clean up, by checkout mode** (from pre-flight):
  - **Primary checkout:** before switching, guard against landing this merge on
    top of a tree that isn't this session's to touch (fleet-config#473 — the
    claim system routes a *second* session into a worktree, but never re-checks
    who holds the claim at the moment something actually runs `git checkout
    <main>` here):
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py assert-owner <repo> <N>
    ```
    `ASSERT_OWNER=pass` (tree clean, claim free or owned by `<N>`) → proceed.
    `ASSERT_OWNER=refuse: <reason>` (dirty tree, or another issue's claim is
    live) → **stop immediately, do not checkout or pull** — surface the refusal
    reason to the user rather than improvising a recovery. Only on a pass:
    `git checkout <main>` then `git pull --ff-only` to land the merge locally,
    then release the concurrency claim so the next session can own the primary:
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py release <repo>
    ```
    **Verify the release** — this is a hard acceptance step, not optional prose:
    a finisher that is not the acquiring session (build-and-stop → separate
    finisher, `/cleanup-fleet`, `/issue-finish-batch`) or an abbreviated finish
    must not silently skip it and leak the claim until the 8h TTL
    (fleet-config#174). Immediately run:
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py status <repo>
    ```
    and confirm it prints `CLAIM=free`. If it still shows `CLAIM=held`, the
    release did not take — re-run `release <repo>` and re-check before reporting
    the finish done.
  - **Linked worktree:** do **not** `git checkout <main>` — the primary checkout
    may belong to another live session. That caution stays, but it does **not**
    mean leaving the primary behind: `cd` out to the primary repo path
    (`<repo>`), remove this worktree, then **land the primary** (fleet-config#647).
    1. Remove the worktree (the helper strips the `.venv` junction *before*
       `git worktree remove`, so the primary's real venv is never touched):
       ```
       E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree <repo>-wt-<N>
       ```
    2. **Land the primary, or say plainly that you didn't.** A merged PR is not
       a deployed fix: until the primary tree fast-forwards, the merged change
       is not live for anyone working there — and in `fleet-config`
       specifically, `hooks/` and `skills/` reach `~/.claude` through junctions
       rooted at the primary, so the change does nothing *fleet-wide*:
       ```
       E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py land-primary <repo> <N>
       ```
       It applies the same guard as `assert-owner` (clean tree, claim free or
       owned by `<N>`) plus "already on the default branch", then `pull
       --ff-only` and a `rev-list --count HEAD..origin/<default>` check. It
       prints exactly one line either way:
       - `PRIMARY=live behind=0` — the merge is live locally.
       - `PRIMARY=stale reason=<why>` — it could not establish that. **Do not
         improvise, do not stash, do not force, do not `git checkout`.**
         Reporting stale *is* the correct outcome; recovering a dirty or
         claimed primary is never this lane's job. `reason=live process
         serving this tree (…)` is that same correct outcome by design
         (fleet-config#665): a repo declaring `tray_cmd` runs a server out of
         its checkout, so fast-forwarding it would serve one UI out of two
         commits. The named restart is the remedy — nothing here is broken.
       **Put that line in the finish summary, verbatim, next to the merge
       result** — never absent, never implied by silence. "Merged" and "live"
       are two facts and the summary carries both (the fleet rule that a check
       which cannot establish a fact reports its own state rather than passing).
    3. **`fleet-config` only — prove the junction is serving the merge.** When
       the diff touched `hooks/` or `skills/`, landing the primary is what
       *deploys*. Read a changed file back through its `~/.claude/...` path
       (e.g. `C:/Users/rober/.claude/skills/issue-finish/SKILL.md`) and confirm
       it carries the edit. An artefact check — never infer it from the
       junction's existence. Same class of check as the `#199`/`#459`
       deploy-coverage gate.

    A worktree session holds no primary claim, so there is nothing to release.
- Confirm the issue closed (`gh issue view <N>` → `CLOSED`). If it didn't
  auto-close, close it manually with a comment referencing the merge commit.
- Clear the issue's Fleet Board marker now that the merge is authoritative:
  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/active_issue.py remove <repo> <N>
  ```
  This runs **after** a successful merge, never when verification or CI stops
  the finish early — a failed finish is still in flight and must remain marked.
  The remove is idempotent (`ACTIVE_ISSUE=absent` is success), but a helper
  error is not: retry once and stop if it still cannot update the shared file.

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
  `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "& '<repo>/tray.bat' --restart"`
  (forward-slash exe path, per the Git-Bash-strips-backslashes rule). Launched
  through the Bash tool as `cmd /c "tray.bat --restart"`, Git Bash/MSYS rewrites
  `/c` to `C:/`; `cmd.exe` opens an interactive prompt and the batch/helper never
  runs — it emits only the `cmd` banner, none of the batch's own `Stopping
  previous…` echoes, and leaves the old build untouched. Fire the PowerShell
  invocation non-blocking (the tray holds its console — a foreground launch
  never returns), then move to the bounded poll below.
- **Safety caveat — linked children.** `tray.bat --restart` does a `/T` subtree
  kill, so it is safe only for a tray whose linked-but-independent children
  (a session-host + its PTY-backed shells) are spawned **detached** and
  re-adopted on start (scaffold `docs/windows-tray.md`). Read the target repo's
  `CLAUDE.md` to know which case you're in — don't assume by app name. A tray
  that declares its linked children detach-compliant (e.g. `app-launcher`, per
  `project-scaffolding#35`: its `:8446` session-host is spawned via `cmd /c
  start` and re-adopted, so `--restart` preserves open Coding sessions and is
  safe even from inside one) is fine to restart. A tray that still hosts such
  children *in its subtree* — or is silent on the point — must be treated as
  unsafe: `--restart` would kill the user's open Coding sessions, and your own
  if you're inside one, so **confirm with the user first**, or use its
  non-destructive path (kill only the webapp port, let the tray re-adopt).
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
  rules above warn against, and a mistimed single-PID kill can take the server
  fully down. The robust reclaim is the tray's job (`project-scaffolding#54`
  hardens `--restart` to reclaim and self-verify); the finisher's contract is to
  invoke it correctly and **report** a mismatch, not to hand-fix it.

If the project has no tray, skip this step.

### 6b. Deploy-coverage check (repos with declared not-fully-covered components only)

A merged PR and a restarted process only prove the component the restart
actually touched is live — not every runtime component the repo owns.
Convention + declared shape: `project-scaffolding#199`/`#200`; reference
implementation: `app-launcher#615`. The trigger is deterministic:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/deploy_coverage.py check .
```

- `DECLARED=no` → **no-op**, the common case for ~every repo today (a repo
  with no `## <component>` block carrying a `liveness signal:` bullet in its
  `CLAUDE.md`). Skip straight to step 7 and say nothing — this must never get
  slower or noisier for a repo that hasn't declared anything.
- `DECLARED=yes` with every component `TOUCHED=no` → still a no-op, but
  **state it** in the step-7 summary (`deploy-coverage: N component(s)
  declared, none touched by this diff`).
- `DECLARED=yes` with a component `TOUCHED=yes` **or** `TOUCHED=unknown` (no
  parseable path in its declaration — treat this as touched, never as a
  silent "no": a flow that can't tell whether it was touched must not assume
  it wasn't) → check that component's printed `LIVENESS` field against the
  actual running target. Where step 6 already polled a build-identity
  endpoint (e.g. `GET /api/version`) as part of the restart, read the
  matching sub-block out of that same response — don't issue a second
  request. Three outcomes, and only the wording changes; **never** block,
  hang, or prompt for confirmation, and never skip closing the issue or
  merging over this check:
  - **Live** (the field confirms the loaded build matches current `HEAD`) →
    step 7 states `confirmed live: <component>`.
  - **Stale** (the field says otherwise) → step 7 states **`merged but not
    yet live: <component> — requires <UPDATE_CMD>`**, never "shipped".
  - **Unresolvable** (endpoint unreachable, no matching field, or no local
    restart happened at all to poll from) → step 7 states **`unknown:
    <LIVENESS> could not be checked`** — never assume fine.
- Do **not** invoke `UPDATE_CMD` yourself. Where the declaration says it's
  confirmation-gated or destructive (e.g. a manual session-host restart that
  would kill live PTYs), it is a human/operator action only — report what's
  needed, don't attempt it as a side effect of finishing an issue.

### 7. Report

Summarize: issue closed, PR merged, branch deleted, docs updated (or why not),
gate result, the UX-conformance gate decision (ran / skipped / `ux-full`, plus
any drift fixed — step 3b), the `/e2e` report block (source, tier + reason,
result, maintenance — step 3c), the deploy-coverage decision (n/a / not touched /
confirmed live / merged but not yet live / unknown — step 6b), and the live
build line. **Worktree mode also carries step 5's `PRIMARY=` line verbatim** —
`PRIMARY=live behind=0` or `PRIMARY=stale reason=<why>` — right next to the
merge result. Merged and live are two facts; a summary that reports only the
first is reporting a deploy it never established.

Then append the **work-summary** — the file/LOC shape of what shipped — by
running the deterministic helper and echoing its output verbatim into the
report:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/work_summary.py --pr <PR_URL>
```

It prints the roll-up (`📊 +N −M · K files` + new/changed/deleted buckets) and a
churn-sorted per-file table (status · file · + · −) that renders here in chat. No
LLM, all from `gh`; it prints nothing on any `gh` error, so just skip the block
if it comes back empty. The same roll-up rides the Slack ping in step 8 — don't
re-assemble it by hand.

### 8. Slack notification

After the summary, fire the completion ping with the deterministic helper. It
resolves the channel/user from `projects.toml` and emits the one canonical
format. Run:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind finish --issue <N> --pr <PR> --pr-url <PR_URL>
```

`<PR_URL>` is the full PR URL (e.g. `https://github.com/owner/repo/pull/31`) —
pass the URL you already have from `gh pr create` or `gh pr view`. This makes
the title/URL lookup CWD-independent so it works from subagent contexts where
the shell's working directory may differ from the project root. If no channel is
configured it's a silent no-op, and it always exits 0, so a notification failure
can never block or delay anything.

**`notify_complete.py` is the ONLY sanctioned way to send this ping — do NOT use
any MCP Slack tool (search/send/etc.) to find a channel or post the ping.**
Picking a channel yourself is both a security violation (an agent-inferred
external write destination) and wrong (it may post to the wrong channel). A
silent no-op because no channel is configured is the correct outcome — do not
"fix" it by reaching for Slack tools.
