---
name: docs-shots
description: Judgment + orchestration for a repo's visual-docs screenshots — decides which features a diff touched (or takes named/all features standalone), proposes the stale set, and on your OK drives the repo's own capture engine + README regen. Never captures without asking. No-op unless docs/screenshots/manifest.json exists. E.g. "/docs-shots", "/docs-shots reporting newsletter", "refresh the visual docs for what changed".
---

# docs-shots

**Goal:** decide *whether* and *which* features of a repo's visual docs need fresh screenshots, then drive the repo's own deterministic capture engine — never silently, always **propose-then-capture** (fleet-config#93).

**Judgment + orchestration only.** The mechanism — Playwright capture, fail-safe masking, the manifest, the README generator — lives per-app, pinned to the shipped reference implementation: `content-management`'s `config/doc_capture/` (`content-management#110`, PR #162). This skill never screenshots anything itself: it decides, asks the human, then calls the engine.

**Generic, no project hardcoded.** Any repo adopts by shipping an engine matching the contract below at `docs/screenshots/manifest.json` + `config/doc_capture`; `content-management` is the only adopter. Wiring any other project is out of scope (fleet-config#93).

## The engine contract (pinned to content-management#110's shipped shape)

- **Manifest**: `docs/screenshots/manifest.json` — `features.<name>` = `{title, description, source_globs[], reach, wait, mask[] (REQUIRED), input_hash, captured_at, files[]}` (`input_hash`/`captured_at`/`files` are engine-maintained).
- **CLI**: `& <repo>/.venv/Scripts/python.exe -m config.doc_capture <cmd> [flags]` from the repo root:
  - `capture [--only NAME]... [--force] [--headed] [--base-url URL]` — idempotent on an input-hash of the matched `source_globs` files + capture config; `--force` overrides.
  - `readme` — regenerate the README block between `<!-- docs-shots:start -->` / `<!-- docs-shots:end -->` (hard-fails if the markers are missing — check for them **before** calling this).
  - `all [same capture flags]` — capture then regenerate README in one call. **Prefer this** over `capture` + `readme`.
  - Exit 0 on a normal run, including skipped-unmasked/skipped-unchanged (warnings, not failures). Non-zero only for a genuine setup failure: app unreachable at `--base-url`, or missing README markers.
- **PNG naming**: `docs/screenshots/<feature>-desktop.png` — stable, no timestamp, so git diffs stay clean.
- **Fail-safe masking is entirely engine-owned.** The engine itself refuses a feature with no `mask` entries — `plan_features` returns `ACTION_SKIP_UNMASKED`. This skill only **surfaces** that warning; guarding or re-implementing it is never its job.

## Discovery — activate only if the repo has opted in

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py discover <repo-root>
```

Prints `MANIFEST=<path>|absent` and, when present, `FEATURES=<csv>`. `MANIFEST=absent` → this skill (and the `/issue-finish` sub-step) is a **silent no-op**. Never create a manifest yourself — that is the project's own pilot setup work.

## Two entry points

- **Standalone `/docs-shots [feature ...]`** — a deliberate refresh. No argument → propose **every** manifest feature (the engine no-ops anything unchanged, but the human still approves the run). Named arguments (`/docs-shots reporting newsletter`) → propose just those.
- **Inside `/issue-finish` Step 2** — after the README/docs update, the **diff-intersection judgment routine**: which manifest features does *this PR's diff* touch, keyed off `source_globs`. No-op when the repo has no manifest or the diff touches no feature.

## Steps (standalone invocation)

1. **Pre-flight + discovery.** Run `discover`. `MANIFEST=absent` → stop: `<repo> has no docs/screenshots/manifest.json — nothing to refresh.` File nothing, run nothing.
2. **Build the proposal.** No feature args → every name in `FEATURES` (comma-separated). Named args → exactly those; any name not in `FEATURES` → stop with a one-line error listing the valid names.
3. **Propose-then-capture gate.** Show the proposed features (title + one-line description from the manifest) and **wait for the user's explicit OK** before running anything. A hard gate, not a formality — never invoke the engine unasked.
4. **Invoke the engine.** On approval, from the repo root, through **that repo's own `.venv`**:

   ```
   & <repo>/.venv/Scripts/python.exe -m config.doc_capture all --only <feature1> --only <feature2> ...
   ```

   Repeat `--only` per feature; omit it entirely when the user approved a full refresh with no named args. Capture stdout — it carries the per-feature verdicts (captured / skip-unchanged / skip-unmasked) for step 5. Engine exits non-zero → stop and report its actual error (app unreachable, missing README markers); never retry, never guess a fix.
5. **Report.**
   - **Captured**: feature name + output PNG path.
   - **Skipped — unchanged**: informational.
   - **Skipped — unmasked**: **flag prominently** — "needs a `mask` entry in the manifest before it can ever be captured" — the one outcome needing human follow-up.
   - README regenerated: yes/no (the engine no-ops if the block was already current).
   - Never commit, push, or restart — leave the tree dirty for review, like `/design-sync`'s default mode.

## Steps (the `/issue-finish` Step 2 sub-step)

Wired into `skills/issue-finish/SKILL.md` Step 2 (Documentation), after the README/docs update and before the verification gate:

1. One command computes discovery + the diff-intersection:

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/docs_shots_plan.py check <repo-root> [--base <ref>]
   ```

   Prints `MANIFEST=<path>|absent`, `STALE=<feature:file|file,...;...>|unknown`, `UNMAPPED=<csv>|unknown`, `README_MARKERS=yes|no`. `--base` defaults to the repo's main branch — omit it unless `/issue-finish` diffs against something else.
2. `MANIFEST=absent` → no-op, nothing to report.
3. `STALE` — the stale set (parse `feature:file|file;feature:file` — `;` separates features, `|` separates that feature's matched files), with the matched files as the reason ("touched `app/tab_newsletter.py`").
4. `UNMAPPED` — changed files under a manifest-covered directory that match no feature. **Surface them for the user to add a manifest entry (including a `mask` config) — never guess one yourself.** A report-only flag, not a blocker for the rest of `/issue-finish`.
5. Empty stale set → no-op; say so in the finish summary (`docs-shots: no visual-docs feature touched by this diff`). `STALE=unknown` / `UNMAPPED=unknown` is **not** the empty set — the diff itself failed (bad base ref, unreadable repo), so nothing is known either way (fleet-config#681). Report it as unknown, name the base ref, and ask the user rather than proceed as if nothing were stale.
6. Non-empty stale set → **the same propose-then-capture gate as standalone**: show the stale set + reasons and wait for the user's OK **inside the same `/issue-finish` run** (screenshot capture touches a running app and produces its own diff — global `CLAUDE.md` "ask before assuming" applies). On approval, run standalone step 4 (`all --only <stale features>`) and fold its report into the finish summary. On decline, note it was skipped and move on — never block the rest of `/issue-finish` over a declined refresh.

## Hard rules

- **Never invoke the engine without the user's explicit OK** — propose, wait, then act, in both entry points, every time.
- **Never re-implement the mask fail-safe**, and **never guess a manifest entry** (an unmapped file, with its required `mask` selectors, is the human's to configure).
- **Never edit `manifest.json` or the README block by hand** — only the engine's `capture`/`readme`/`all` touch them.
- **Never commit, push, or restart** on the skill's own initiative — standalone leaves the tree dirty for review; the sub-step folds into `/issue-finish`'s own commit/push flow.
- **No AI attribution; no hard-wrapped issue-body paragraphs** where output lands in an issue/PR (global `CLAUDE.md`).

## Notes

- Design + acceptance criteria: fleet-config#93. Engine design + shipped contract: `content-management#110` / PR #162 (`ferraroroberto/content-management`).
- `skills/_lib/docs_shots_plan.py` owns the pure discovery + diff-intersection logic (unit-tested, `tests/audit/test_docs_shots_plan.py`), reusing `ux_surface.py`'s `matches_any` glob-matching.
- Today's engine invocation (`python -m config.doc_capture`) is the fleet convention because there is exactly one adopter. If a second app adopts a differently-pathed engine, extend the discovery to read an explicit declaration (e.g. a manifest `engine` field) rather than assuming every future adopter's module lives at the same path — don't build that generalization before a second real caller exists.
