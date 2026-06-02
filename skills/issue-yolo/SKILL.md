---
name: issue-yolo
description: One-shot the full GitHub-issue workflow end-to-end — file the issue, cut the branch, build, validate hard, then ship (PR, CI, merge, delete branch, tray restart). Pass a number ("/issue-yolo 34") to work an existing issue without re-filing it; pass text to file a new issue first. The validation phase is non-negotiable; YOLO means "no plan gate", not "no safety".
---

# issue-yolo

**Goal:** Take a rough idea and carry it all the way to merged-and-closed in one
unbroken run — `/issue-add` → `/issue-start now` → build → **validate hard** →
`/issue-finish`. No approval pauses in between.

**YOLO means "skip the plan-approval gate", not "skip safety".** The validation
phase is what stops a broken build from reaching `main`. It is the only thing
between a fresh idea and a merge commit on a protected branch. Do not weaken
it. If validation fails at any point, **stop and report**; do not push.

Use this when:
- You've thought through the change well enough that a plan-approval gate would
  be ceremony.
- The change is bounded enough that a single validation pass can credibly cover
  it.
- You're willing to accept that the next eyeball on the work will be `main`'s.

Do **not** use this for:
- Architectural changes, cross-cutting refactors, or anything where the design
  is the hard part. Those need `/issue-start … plan`.
- Work with hard-to-reverse blast radius beyond the local app (production data,
  external API state, shared infrastructure). YOLO covers the local repo + its
  CI + the local tray, nothing more.

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

**Skip this phase if an existing issue number was passed as the argument.**
Jump directly to Phase 2 using that number.

Otherwise run the full `/issue-add` skill steps 1–8 verbatim:
1. Repo + convention context.
2. Extract the real intent.
3. Research the codebase.
4. Check for duplicates — if a clear duplicate exists, **stop**, tell the user,
   do not start work on it.
5. Decide if a question is needed — only for substantive, decision-bearing
   ambiguity that would change what gets built. The bar is higher in YOLO mode:
   if you have to ask, that's a signal the work probably shouldn't be YOLO'd.
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

The whole reason YOLO is safe enough to ship is that this phase is *not*
weakened relative to the normal flow — it is **stronger**. There is no human
checkpoint after this. Everything below must hold before Phase 4 starts.

Run **all** that apply to this project. Each is a hard gate.

**3a. Reproduction proof (for bugs).** Per the scaffolding `CLAUDE.md`'s
"While fixing" section: a bug fix needs an artefact that *demonstrates* the
fix. A failing test that now passes, a recorded console transcript showing the
old error then the new clean run, or a documented reproduction sequence
exercised before and after. "I think this fixes it" is not enough in YOLO mode
— there is no review to catch a non-fix.

**3b. Syntax / type / lint gate.** Whatever the project specifies in its
`CLAUDE.md` Verification section. Typically:
- Windows: `& .\.venv\Scripts\python.exe -m py_compile <changed files>`,
  `ruff check .` if configured.
- POSIX equivalents.
- TS / JS projects: their type-check and lint commands.

**3c. Unit + integration tests.** Project's `pytest` / `jest` / `go test` etc.
**Zero allowed failures and zero allowed skips that hide the change.** A
green-with-skips run is not green if the skip masks the area you touched.

**3d. End-to-end test suite, when one exists.** Per the scaffolding `CLAUDE.md`:
*"Boot failure is a hard failure — never `pytest.skip`. A regression suite that
skips when the app isn't up reports green on a build it never tested; that is
the exact rot this suite exists to prevent."* In YOLO mode this matters
double — if a project has a `scripts/verify-before-ship.*` gate, run **that**,
not a bare `pytest`. The gate is one command, exit-0 only.

**3e. Behavioural verification — the change actually does what it claims.**
This is the part most easily skipped and the part that matters most in YOLO
mode. Pick the smallest mode that genuinely covers the change:
- **UI change** (Streamlit, FastAPI/Flask + browser, Electron, phone webapp):
  use the **`verify` skill** to launch the app and drive the feature in a real
  browser. Headed Playwright (or Playwright MCP) so the actual feature is
  exercised, not a mock. Capture a screenshot of the working result for the PR
  body.
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

**3f. Sanity sweep for unintended damage.** `git diff main...HEAD` and
read it. Anything outside the change's stated scope is a red flag — either
revert that hunk or stop and ask. Look especially for: dependency bumps you
didn't intend, removed tests, weakened assertions, suppressed warnings,
silently-broadened exception handlers, `.gitignore` edits.

**3g. Self-critique pass.** Take 30 seconds to ask "what would a senior,
perfectionist reviewer reject?" — per scaffolding `CLAUDE.md`'s Senior-dev
check. Fix anything obvious *now*. The reviewer in this run is you.

**If anything in 3a–3g fails — stop.** Report the failure, leave the branch
in place, let the user inspect. Do **not** continue to Phase 4. Do **not**
soft-pass with caveats. A YOLO run that ships a half-broken change defeats
the whole structure.

### Phase 4 — Ship (`/issue-finish` flow)

Only reachable on a fully-green Phase 3. Run the full `/issue-finish` skill:
1. Re-confirm every acceptance point on the issue is actually met.
2. Update `README.md` if usage / config / output changed. Do not write a
   dated `docs/YYYY-MM-DD-*.md` file — per the project doc-discipline
   sections, the PR + issue + `git log` are the changelog.
3. Run the project's verification gate (e.g. `scripts/verify-before-ship.ps1`)
   as a final atomic pass/fail. Already-run sub-pieces in Phase 3 don't
   substitute for the consolidated gate.
4. Commit any remaining work with a conventional `type:` message (no
   `Co-Authored-By: Claude` trailer).
5. `git push -u origin <branch>`.
6. `gh pr create` — body with **Summary**, **Validation** (concretely what
   you ran in Phase 3 and what its outputs were), and `Closes #<N>`.
7. `gh pr checks <PR> --watch` — green only. CI red → **stop**, do not merge.
8. `gh pr merge <PR> --merge --delete-branch`. Land on main locally
   (`git checkout main && git pull --ff-only`). Confirm the issue auto-closed.
9. Tray restart per project `CLAUDE.md` if a tray exists — surgically (kill
   the project port's PID only, never blanket `pythonw`).

**Do not fire `/issue-finish`'s own Slack ping (its step 8) during this phase** —
Phase 5 sends a single `--kind yolo` ping instead, so the run produces exactly
one completion notification, not two.

### Phase 5 — Final report

Single concise summary:
- Issue number + title + URL
- Branch name + merge commit SHA
- Validation: which Phase 3 gates ran and their results (one line each)
- PR URL
- Build line from the version endpoint (if the project has one)
- Live tray status (if applicable)

Then fire the single completion ping with the deterministic helper — canonical
format, real PR title + URL from `gh`:

```
py C:/Users/rober/.claude/hooks/notify_complete.py --kind yolo --issue <N> --pr <PR> --pr-url <PR_URL>
```

`<PR_URL>` is the full PR URL (e.g. `https://github.com/owner/repo/pull/31`) —
pass the URL you already have from `gh pr create` or `gh pr view`. This makes
the title/URL lookup CWD-independent so it works correctly from subagent
contexts where the shell's working directory may differ from the project root.

Silent no-op if no channel is configured; always exits 0, so it can never block
or delay the finish.

## Notes on safety

- The "approval gate" you're skipping is the plan-mode pause where the user
  would normally vet the *approach* before code is written. The validation
  gate you're not skipping is what proves the code *works*. These are
  different gates; do not conflate them.
- If you find yourself wanting to weaken Phase 3 to keep the run moving,
  you are not doing YOLO any more — you are doing something else. Stop and
  ask the user.
- If a project has a tray running an older version while you ship, the tray
  restart at the end is what makes "merged" mean "live". A YOLO run that
  merges but leaves the tray on the previous build is not finished.
