---
name: issue-yolo
description: One-shot the GitHub-issue workflow end-to-end — file the issue, cut the branch, build, validate hard (including an independent fresh-agent review), then ship (PR, CI, merge, delete branch, tray restart). Pass a number ("/issue-yolo 34") to work an existing issue; pass text to file one first. YOLO means "no plan gate", not "no safety".
---

# issue-yolo

**Capability preflight:** read [workflow-capabilities](../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional Claude examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** idea → merged-and-closed in one unbroken run — `/issue-add` →
`/issue-start now` → build → **validate hard** → `/issue-finish`. No approval
pauses in between.

**YOLO means "skip the plan-approval gate", not "skip safety".** The validation
phase (Phase 3) is the only thing between a fresh idea and a merge commit on a
protected branch — do not weaken it. Validation fails at any point → **stop and
report**; do not push.

Use when: the approach is already thought through (a plan gate would be
ceremony); the change is bounded enough for one validation pass to credibly
cover it; you accept the next eyeball on the work is `main`'s.

**Do not** use for: architectural changes / cross-cutting refactors / anything
where design is the hard part — use `/issue-start … plan` instead; work with
hard-to-reverse blast radius beyond the local app (production data, external
API state, shared infrastructure) — YOLO covers the local repo + its CI + the
local tray, nothing more.

## Argument

- **Number** (`/issue-yolo 34`) — existing issue. Skip Phase 1 entirely; use
  that issue number for Phases 2–5. Verify it is open via `gh issue view <N>`;
  stop if it is closed or not found.
- **Text / transcript** — raw idea. Run Phase 1 to file the issue first, then
  continue.
- **Nothing** — ask once and stop.

## Steps

Run in order. Any failure stops the whole run — no partial finish.

### Phase 1 — File the issue (`/issue-add` flow)

**Skip this phase if an existing issue number was passed as the argument** —
jump directly to Phase 2 using that number.

Otherwise run the full `/issue-add` skill steps 1–8 verbatim:
1. Repo + convention context.
2. Extract the real intent.
3. Research the codebase.
4. Check for duplicates — clear duplicate exists → **stop**, tell the user, do
   not start work on it.
5. Decide if a question is needed — only for substantive, decision-bearing
   ambiguity that would change what gets built. Bar is higher in YOLO mode:
   having to ask is a signal the work probably shouldn't be YOLO'd.
6. Draft the issue (canonical title style, body proportionate to the work).
7. Apply one canonical type label.
8. Create the issue via `gh issue create … --assignee @me`.

Report the new issue number + URL inline and proceed to Phase 2 on the same
turn. Do **not** stop.

### Phase 2 — Branch + build (`/issue-start now` flow)

Run the `/issue-start <N> now` flow:
- Pre-flight: must be in a git repo, working tree must be clean (commit/stash
  any unrelated dirt first or stop), warn if already on a feature branch.
- Sync the main branch: detect main (`git symbolic-ref refs/remotes/origin/HEAD`,
  fall back to `main`), `git checkout main`, `git pull --ff-only`.
- Cut the branch: prefix from label (`fix/` for `bug`, `feat/` for
  `enhancement`, `chore/`, `docs/`); slug from the title; name
  `<prefix>/<N>-<slug>`.
- Build the change end-to-end. Forced fast mode regardless of the issue's
  label — no plan-approval gate.

### Phase 3 — Validate hard *(the non-negotiable phase)*

**Stronger** than the normal flow, not weaker — no human checkpoint after this.
Everything below must hold before Phase 4 starts. Run **all** that apply to
this project; each is a hard gate.

**3a. Reproduction proof (for bugs).** Per the scaffolding `CLAUDE.md`'s
"While fixing" section: a bug fix needs an artefact that *demonstrates* the
fix — a failing test that now passes, a recorded console transcript (old error
→ new clean run), or a documented repro sequence exercised before and after.
"I think this fixes it" is not enough — there is no review to catch a non-fix.

**3b. Syntax / type / lint gate.** Whatever the project specifies in its
`CLAUDE.md` Verification section. Typically:
- Windows: `& .\.venv\Scripts\python.exe -m py_compile <changed files>`,
  `ruff check .` if configured.
- POSIX equivalents.
- TS / JS projects: their type-check and lint commands.

**3c. Unit + integration tests.** Project's `pytest` / `jest` / `go test` etc.
**Zero allowed failures and zero allowed skips that hide the change** — a
green-with-skips run is not green if the skip masks the area you touched.

**3d. E2e leg — delegated to the `/e2e` skill.** Run `/e2e`
(`skills/e2e/SKILL.md`): routes the branch diff through the repo's own
`classify_e2e.py` (bootstrapping it on first contact), runs the proportionate
slice (`skip` / `static` / `full`), applies its inline suite maintenance on
this branch. If the project has a `scripts/verify-before-ship.*` gate, run
**that** first (one command, exit-0 only) — `/e2e` carries the gate's e2e
result instead of re-running the same slice. Boot failure is a hard failure —
never `pytest.skip` (a suite that skips when the app isn't up reports green on
a build it never tested). A FAIL from the routed slice stops the run.

**3e. Behavioural verification — the change actually does what it claims.**
Pick the smallest mode that genuinely covers the change:
- **UI change** (Streamlit, FastAPI/Flask + browser, Electron, phone webapp):
  use the **`verify` skill** to launch the app and drive the feature in a real
  browser. Headed Playwright (or Playwright MCP) so the actual feature is
  exercised, not a mock. **Inspect the screenshot in-session only** — save to a
  local scratch path; **never attach it to the PR body, an issue, or a
  comment** (assume every repo is public — a screenshot is an information
  breach). Text-only result line in the PR instead.
  **Browser-backend preflight (Codex — no live `iab`):** same preflight as
  `/issue-finish` step 3b (backend selection between `iab` and installed
  Playwright, the `browser_verify.py plan` invocation, its four distinct
  failure codes) — see that step, don't restate here.
  When this is a **web-app UX diff**, also run the **design-conformance gate**
  (`project-scaffolding#83`):

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/ux_surface.py check .
  ```

  If `SPEC_APPLIES=yes` and `TOUCHED=yes` (or `TOUCHED=unknown` — the diff
  failed, not evidence the surface was untouched, fleet-config#681): (a)
  **token check, fix-now** — compare the touched CSS custom properties (light
  **and** dark) and the nav contract to `~/.claude/design.md` +
  `design.dark.md`, fix material drift in-branch now (don't file-and-defer;
  that's `/design-sync`'s periodic job); (b) apply the **design-conformance
  lens** to the screenshot just taken (nav pill, layout, palette per spec).
  Overrides: `ux`/`design` force it, `no-ux` skips it, `ux-full` checks every
  `KEY_VIEWS`. `SPEC_APPLIES=no` / `TOUCHED=no` → nothing to do here.
- **CLI change:** invoke the CLI with realistic arguments against realistic
  input. Show the actual output. Don't trust that "the function returns X" —
  show the binary printing X.
- **Library change:** add a probe call from the project root (`-m
  scratch.foo`) that exercises the new surface against real inputs, not
  fixtures. Print the result.
- **Background / scheduled work:** trigger it once manually and observe the
  side effect it's supposed to produce.

If the project has its own `run`-style skill, prefer that — it knows how to
launch the app. Fall back to the global `verify` skill otherwise.

**3f. Sanity sweep for unintended damage.** `git diff main...HEAD`, read it.
Anything outside the change's stated scope is a red flag — revert that hunk or
stop and ask. Watch for: unintended dependency bumps, removed tests, weakened
assertions, suppressed warnings, silently-broadened exception handlers,
`.gitignore` edits.

**3g. Self-critique pass.** 30 seconds: "what would a senior, perfectionist
reviewer reject?" (scaffolding `CLAUDE.md` Senior-dev check). Fix anything
obvious *now* — the reviewer in this run is you.

**If anything in 3a–3g fails — stop.** Report the failure, leave the branch
in place, let the user inspect. Do **not** continue to 3h or Phase 4. Do
**not** soft-pass with caveats. A YOLO run that ships a half-broken change
defeats the whole structure.

**3h. Independent review — a fresh agent with no memory of the build.** Only
reached once 3a–3g are all green. Self-review (3a–3g) is done by the same
context that wrote the code — it cannot be the last checkpoint before a merge
with no human in the loop. Per the fleet's
[independent-review-gate convention](../../docs/independent-review-gate.md)
(fleet-config#408), with **stop-and-report** failure handling (not
`cleanup-fleet-all.js`'s retry-then-escalate — this run is interactive, a human
is already present to decide on a retry).

Spawn a genuinely fresh independent context through the capability contract. Claude: fresh `Agent`/`Task` type, never fork; Codex native collaboration: `fork_turns: "none"`. If no fresh spawn exists, stop at review-ready and request an independent human or separately launched reviewer; serial self-review does not satisfy this gate. Brief it with only the issue number, branch name and repo path, plus these review duties; collect and validate its terminal verdict before shipping:

1. **Fetch the issue's acceptance criteria itself** — `gh issue view <N>` —
   never trust this run's own restatement of them.
2. **Read the diff** against the base branch — `git diff <main>...<branch>`.
3. **Independently re-run the project's verification gate** — never trust
   Phase 3's report of PASS; a fresh `PASS` from a fresh run is the only
   trustworthy signal.
4. **Judge** whether the diff plausibly and reasonably satisfies the fetched
   acceptance criteria **and** conforms to the repo's own `CLAUDE.md`
   conventions — not just "did the gate pass". Lenient by default (per
   `docs/independent-review-gate.md`): fail only on something a human reviewer
   would actually reject (gate genuinely fails, diff doesn't touch what the
   issue asked for, obvious bug) — never style preference.
5. Report a schema-validated verdict: `pass: boolean`, `feedback: string`
   (always filled in, briefly even on a pass).

**On `pass: false` — stop and report.** Surface the reviewer's `feedback`
verbatim, leave the branch as-is, let the user decide retry / adjust scope /
abandon. **Do not** auto-retry the build and do **not** continue to Phase 4 on
a rejected verdict — that silent-second-round shape is `cleanup-fleet-all.js`'s
job, not this one.

### Phase 4 — Ship (delegates to `/issue-finish`)

Only reachable on a fully-green Phase 3 (3a–3h, including the independent
review's `pass: true`). Once there, run the full **`/issue-finish` skill**
(`skills/issue-finish/SKILL.md`, steps 1 through 8) verbatim — acceptance
re-confirmation, README + `/docs-shots` visual-docs (step 2b), the
consolidated verification gate, `/e2e` (step 3c), push + PR, the
CI-advisory/checkout-mode-aware merge and land, the tray restart, and the
deploy-coverage liveness check (step 6b). Delegate rather than restate: a prior
version duplicated these as its own nine steps and the copy drifted stale,
silently skipping step 2b and 6b on every YOLO run touching either surface
(fleet-config#728). Delegating means every future `/issue-finish` step reaches
this flow for free, no second copy to fall out of sync.

Three YOLO-specific deltas on top of the delegated steps:

- **`/issue-finish` step 4's PR body carries Phase 3's validation, not just
  the gate result** — the **Validation** section names what Phase 3 ran
  (including the reproduction proof for a bug fix and the 3h independent
  review's verdict), not only the consolidated verification gate.
- **Step 3b (UX-conformance) is already satisfied by Phase 3e above** — do not
  re-run `ux_surface.py check` or re-screenshot in this phase; any drift was
  already fixed and the text-only conformance line already belongs in the PR
  body from step 4.
- **Skip `/issue-finish`'s own step 8 Telegram ping** — Phase 5 below sends a
  single `--kind yolo` ping instead, so the run produces exactly one
  completion notification, not two.

### Phase 5 — Final report

Single concise summary:
- Issue number + title + URL
- Branch name + merge commit SHA
- Validation: which Phase 3 gates ran and their results (one line each),
  **plus the 3h independent-review verdict** (`pass: true` + a one-line
  summary of the reviewer's `feedback`)
- PR URL
- Build line from the version endpoint (if the project has one)
- Live tray status (if applicable)
- **Work-summary** — run `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/work_summary.py --pr
  <PR_URL>` and echo its output verbatim: the roll-up (`📊 +N −M · K files` +
  new/changed/deleted buckets) and the churn-sorted per-file table. Deterministic
  from `gh`, no LLM; prints nothing on a `gh` error, so skip it if empty. The
  same roll-up rides the Phase 5 ping below — don't hand-assemble it.

Then fire the single completion ping with the deterministic helper — canonical
format, real PR title + URL from `gh`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind yolo --issue <N> --pr <PR> --pr-url <PR_URL>
```

`<PR_URL>` is the full PR URL (e.g. `https://github.com/owner/repo/pull/31`) —
pass the URL already on hand from `gh pr create`/`gh pr view`; keeps the
title/URL lookup CWD-independent for subagent contexts.

Silent no-op if no chat is configured; always exits 0 — never blocks or delays
the finish.

**`notify_complete.py` is the ONLY sanctioned way to send this ping — do NOT use
any MCP chat tool (search/send/etc.) to find a chat or post the ping.** The
helper resolves the destination chat deterministically from `projects.toml`;
picking a chat yourself is both a security violation (an agent-inferred
external write destination) and wrong (may post to the wrong chat). A silent
no-op is the correct outcome — don't "fix" it with MCP chat tools.

## Notes on safety

- The "approval gate" skipped is the plan-mode pause vetting *approach* before
  code is written. The validation gate NOT skipped is what proves the code
  *works*. Different gates — don't conflate them.
- Tempted to weaken Phase 3 to keep the run moving? That's not YOLO any more.
  Stop and ask the user.
- The tray restart at the end is what makes "merged" mean "live" for a project
  running an older build while you ship. A YOLO run that merges but leaves the
  tray on the previous build is not finished.
