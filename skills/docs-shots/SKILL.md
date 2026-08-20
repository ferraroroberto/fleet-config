---
name: docs-shots
description: Judgment + orchestration for a repo's visual-docs screenshots — decides which features a diff touched (or takes named/all features standalone), proposes the stale set, and on your OK drives the repo's own capture engine + README regen. Never captures without asking. No-op unless docs/screenshots/manifest.json exists. E.g. "/docs-shots", "/docs-shots reporting newsletter", "refresh the visual docs for what changed".
---

# docs-shots

**Goal:** Decide *whether* and *which* features of a repo's visual
documentation need fresh screenshots, then drive the repo's own deterministic
capture engine — never silently, always **propose-then-capture** (fleet-config#93).

**This skill is judgment + orchestration only.** The deterministic mechanism —
Playwright capture, fail-safe masking, the manifest, the README generator —
lives per-app, pinned to the shipped reference implementation:
`content-management`'s `config/doc_capture/` (`content-management#110`,
shipped in PR #162). This skill never screenshots anything itself; it decides
what to ask the engine to do, asks the human first, then calls it.

**Generic, no project hardcoded.** Any repo can adopt this by shipping an
engine matching the contract below at `docs/screenshots/manifest.json` +
`config/doc_capture` — today `content-management` is the only adopter. Wiring
it into any other project is explicitly out of scope (fleet-config#93).

## The engine contract (pinned to content-management#110's shipped shape)

- **Manifest**: `docs/screenshots/manifest.json` — `features.<name>` =
  `{title, description, source_globs[], reach, wait, mask[] (REQUIRED),
  input_hash, captured_at, files[]}` (engine-maintained fields:
  `input_hash`/`captured_at`/`files`). A feature with no `mask` entries is
  **refused by the engine itself** — never this skill's job to guard.
- **CLI**: `& <repo>/.venv/Scripts/python.exe -m config.doc_capture <cmd> [flags]`
  from the repo root:
  - `capture [--only NAME]... [--force] [--headed] [--base-url URL]` —
    capture stale/named features (idempotent on an input-hash of the matched
    `source_globs` files + capture config; `--force` overrides).
  - `readme` — regenerate the README block between
    `<!-- docs-shots:start -->` / `<!-- docs-shots:end -->` (hard-fails if
    those markers are missing — check for them **before** calling this).
  - `all [same capture flags]` — capture then regenerate the README in one
    call. **Prefer this** over calling `capture` + `readme` separately.
  - Exit 0 on a normal run (including one where every requested feature was
    skipped-unmasked or skipped-unchanged — those are warnings, not
    failures). Non-zero only for a genuine setup failure: the app unreachable
    at `--base-url`, or missing README markers.
- **PNG naming**: `docs/screenshots/<feature>-desktop.png` (stable — no
  timestamp in the filename, so git diffs stay clean).
- **Fail-safe masking is entirely engine-owned.** `plan_features` inside the
  engine refuses (loud warning, `ACTION_SKIP_UNMASKED`) any feature with no
  `mask` selectors — this skill's job is to **surface** that warning
  distinctly in its own report, not to re-implement the guard.

## Discovery — activate only if the repo has opted in

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py discover <repo-root>
```

Prints `MANIFEST=<path>|absent` and, when present, `FEATURES=<csv>`.
`MANIFEST=absent` → this skill (and the `/issue-finish` sub-step, below) is a
**silent no-op**. Never create a manifest yourself — that's the per-project
pilot's own setup work.

## Two entry points

- **Standalone `/docs-shots [feature ...]`** — a deliberate refresh. No
  argument → propose **every** manifest feature (engine idempotency no-ops
  anything genuinely unchanged, but the human still approves the run). Named
  arguments (`/docs-shots reporting newsletter`) → propose just those.
- **Inside `/issue-finish` Step 2** (wired below) — after the README/docs
  update, run the **diff-intersection judgment routine**: which manifest
  features does *this PR's diff* touch, keyed off `source_globs`. No-op when
  the repo has no manifest or the diff touches no feature.

## Steps (standalone invocation)

### 1. Pre-flight + discovery

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py discover <repo-root>
```

`MANIFEST=absent` → stop: `<repo> has no docs/screenshots/manifest.json —
nothing to refresh.` File nothing, run nothing.

### 2. Build the proposal

- No feature args → propose every name in `FEATURES` (comma-separated).
- Named args → propose exactly those (stop with a one-line error listing the
  valid `FEATURES` names if any argument isn't one of them).

### 3. Propose-then-capture gate

Show the proposed feature list (title + one-line description from the
manifest) and **wait for the user's explicit OK** before running anything.
This is a hard gate, not a formality — never invoke the engine unasked.

### 4. Invoke the engine

On approval, from the repo root, through **that repo's own `.venv`**:

```
& <repo>/.venv/Scripts/python.exe -m config.doc_capture all --only <feature1> --only <feature2> ...
```

(repeat `--only` per feature; omit `--only` entirely to run every feature
when the user approved a full refresh with no named args). Capture stdout —
it's the source of the per-feature verdicts (captured / skip-unchanged /
skip-unmasked) for step 5.

If the engine exits non-zero, stop and report its actual error (app
unreachable, missing README markers) — never retry, never guess a fix.

### 5. Report

- **Captured**: feature name + output PNG path.
- **Skipped — unchanged**: informational, not a problem.
- **Skipped — unmasked**: **flag prominently** — "needs a `mask` entry in
  the manifest before it can ever be captured" — this is the one outcome
  that needs a human follow-up action, not just a note.
- README regenerated: yes/no (the engine no-ops if the block was already
  current).
- Never commit, push, or restart — the working tree is left dirty for the
  user to review, exactly like `/design-sync`'s default mode.

## Steps (the `/issue-finish` Step 2 sub-step)

Wired into `skills/issue-finish/SKILL.md` Step 2 (Documentation), after the
README/docs update and before the verification gate:

1. One command computes discovery + the diff-intersection together:

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py check <repo-root> [--base <ref>]
   ```

   Prints `MANIFEST=<path>|absent`, `STALE=<feature:file|file,...;...>|unknown`,
   `UNMAPPED=<csv>|unknown`, `README_MARKERS=yes|no`. `--base` defaults to the repo's
   main branch — omit it unless `/issue-finish` is diffing against something
   else.
2. `MANIFEST=absent` → no-op, nothing to report.
3. `STALE` — the stale set (parse `feature:file|file;feature:file` — `;`
   separates features, `|` separates that feature's matched files) with the
   matched files as the reason ("touched `app/tab_newsletter.py`").
4. `UNMAPPED` — changed files under a manifest-covered directory that match
   no feature. **Surface these for the user to add a manifest entry
   (including a `mask` config) — never guess one yourself.** This is a
   report-only flag, not a blocker for the rest of `/issue-finish`.
5. Empty stale set → no-op, say so in the finish summary
   (`docs-shots: no visual-docs feature touched by this diff`).
   `STALE=unknown` / `UNMAPPED=unknown` is **not** the empty set — the diff
   itself failed (bad base ref, unreadable repo), so nothing is known either
   way (fleet-config#681). Report it as unknown, name the base ref, and ask
   the user rather than proceeding as if nothing were stale.
6. Non-empty stale set → **propose-then-capture, same gate as standalone**:
   show the stale set + reasons, wait for the user's OK, **inside the same
   `/issue-finish` run** (this is exactly the kind of decision the global
   `CLAUDE.md` "ask before assuming" rule calls for — screenshot capture
   touches a running app and produces a diff of its own). On approval, run
   step 4 above (`all --only <stale features>`) and fold its report into the
   finish summary. On decline, note it was skipped and move on — never block
   the rest of `/issue-finish` over a declined docs-shots refresh.

## Hard rules

- **Never invoke the engine without the user's explicit OK.** Propose, wait,
  then act — in both entry points, every time.
- **Never re-implement the mask fail-safe.** The engine already refuses an
  unmasked feature; this skill only has to report that verdict clearly.
- **Never guess a manifest entry.** An unmapped changed file is reported for
  the human to configure (including its required `mask` selectors) — never
  authored by this skill.
- **Never edit `manifest.json` or the README block by hand.** Only the
  engine's own `capture`/`readme`/`all` commands touch those files.
- **Never commit, push, or restart** on the skill's own initiative — the
  working tree is left dirty for review (standalone), or folds into
  `/issue-finish`'s own existing commit/push flow (the sub-step).
- **No AI attribution; no hard-wrapped issue-body paragraphs** where this
  skill's output lands in an issue/PR (per global `CLAUDE.md`).

## Notes

- Design + acceptance criteria: fleet-config#93. Engine design + shipped
  contract: `content-management#110` / PR #162
  (`ferraroroberto/content-management`).
- `skills/_lib/docs_shots_plan.py` owns the pure discovery + diff-
  intersection logic (unit-tested, `tests/test_docs_shots_plan.py`), reusing
  `ux_surface.py`'s glob-matching (`matches_any`) rather than reimplementing
  it.
- Today's engine invocation (`python -m config.doc_capture`) is the fleet
  convention because there is exactly one adopter. If a second app adopts a
  differently-pathed engine, extend the discovery to read an explicit
  declaration (e.g. a manifest `engine` field) rather than assuming every
  future adopter's module lives at the same path — don't build that
  generalization before a second real caller exists.
