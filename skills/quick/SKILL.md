---
name: quick
description: Trunk-based lane for changes below the issue threshold — one capped, verified, conventional commit pushed directly to the default branch, no issue and no PR. Explicit invocation is the authorization (global-CLAUDE.md carve-out); hard scope caps with auto-escalation to the issue workflow. E.g. "/quick fix the button color", "/quick typo in the README", "commit this quickly".
---

# quick

**Goal:** ship a genuinely trivial change — a one-line fix, a color tweak, a
typo — as **one verified conventional commit straight onto the default
branch**, skipping the issue + branch + PR ceremony that buys nothing at this
size (nothing to plan, no second reviewer, a single commit is already a clean
revert unit). This is the sanctioned exception to the global "never commit to
`main` directly" hard rule: **invoking `/quick` is the explicit
authorization**, and the caps below are the guardrail that keeps the
exception honest.

The known failure mode of quick-lanes is scope creep — "quick" changes that
were secretly medium. The escalation rule is therefore the most important
rule in this skill.

## Arguments

- `/quick <description of the change>` → make the change and ship it.
- Bare `/quick` with a tiny change already sitting in the working tree →
  ratify-and-ship that change (the common "I just tweaked a line in
  conversation" case). Tree dirty with anything *beyond* the intended change
  → stop and say so.
- Nothing to do and nothing described → ask once and stop.

## Eligibility — check BEFORE touching anything

All must hold, else **escalate** (see below) instead of proceeding:

- **One logical change.** Not two small things bundled.
- **Size cap:** ≤2 files, ~≤20 changed lines, **no new files**.
- **No decision-bearing surface:** no API/schema/config-shape changes, no
  dependency changes, no behavior redesign — zero choices a reviewer might
  reasonably contest.
- **No design questions open.** If you're weighing alternatives, it isn't
  `/quick`.

**Escalation is a first-class outcome, not a failure:** say "this outgrew
`/quick`" and hand off to `/issue-add` (or `/issue-yolo` when the user wants
it shipped in one run). If work already exists on the ephemeral branch, keep
it there for the issue flow to adopt — never grind on toward main.

## Steps

### 1. Pre-flight

From the repo root, all must pass:

- On the **default branch**, working tree clean (except, in ratify mode, the
  intended change itself).
- Concurrency claim free:
  `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py status .`
  → must print `CLAIM=free`. Held → stop; another session owns this repo.
- `git pull --ff-only` (not fast-forwardable → stop and report).

### 2. Ephemeral branch — edits never happen on `main`

`git checkout -b quick/<short-slug>` (in ratify mode the pending change rides
along). All edits happen here — this keeps the branch-before-edit guard
satisfied in launcher-dispatched sessions and means an escalation or a red
gate leaves `main` untouched by construction.

### 3. Make the change

Exactly the described change, nothing else. No drive-by cleanups — a
tempting adjacent fix is its own `/quick` (or an issue).

### 4. Verify — proportionate, never skipped

- Run the repo's **verification gate** (its `CLAUDE.md` Verification
  section). No gate declared → run the language-level minimum on the touched
  files (byte-compile / lint) and **say** the repo has no gate.
- Run the **`/e2e` skill** evaluation — for a cosmetic diff it routes
  `static`/`skip` in seconds; that proportionality is what makes this lane
  affordable. A `full`-tier routing on a supposedly-trivial change is itself
  an escalation signal.
- **Red anything → stop.** Nothing lands on main; report, leave the branch
  for inspection.

### 5. Cap re-check on the real diff

`git diff main --stat` — over the size caps, or any new file → **escalate**
(step 0's rule). The pre-check estimated; this measures.

### 6. Land

```
git add <the files> && git commit -m "<type>: <subject>"   # body: one *why* line
git checkout <default-branch> && git merge --ff-only quick/<slug>
git push && git branch -d quick/<slug>
```

A plain commit lands on main — no merge commit, no PR. If the push is
rejected (branch protection requiring PRs, non-ff) → do not force anything;
fall back to pushing the branch and opening a normal PR, saying so.

### 7. Deploy + hand off ready to validate

- Repo declares a tray/long-lived process → run its safe restart recipe and
  the bounded build-identity check, exactly as `/issue-finish` step 6 does;
  hand the URL. No web surface → skip silently.
- Repo has CI on default-branch pushes and the diff touches its surface →
  glance at the run result (advisory, same spirit as `/issue-finish` step 5);
  don't wait on CI a `skip`-tier diff can't affect.
- If the change relates to an existing open issue, paste the commit SHA there
  as a comment (the conventions' direct-commit close rule) — but finding one
  is not required.

### 8. Report

One block: `sha · <type>: <subject> · gate: <result> · e2e: <tier> ·
validate: <URL | n/a>`.

## Hard rules

- **The caps are not negotiable and the escalation rule beats momentum** —
  when in doubt, it's an issue.
- **Verification always runs** (gate + `/e2e` evaluation). "Main is always
  shippable" is the invariant this lane must preserve; the ceremony was
  removed, not the safety.
- **Never force-push, never bypass branch protection, never use `/quick`
  twice to sneak a medium change in as two smalls.**
- **One `/quick` at a time per repo** — the claim check is the lock.
- Conventional commit message with the *why*; no AI-attribution trailer.

## Notes

- Decision record: fleet-config#558. The three-lane boundary: `/quick` =
  cosmetic/one-liner, zero decisions; `/issue-yolo` = real bounded work;
  `/issue-start plan` = design-bearing.
- The global-CLAUDE.md Hard rules carve-out and this SKILL.md were landed in
  the same PR — the rule and its exception live together deliberately.
