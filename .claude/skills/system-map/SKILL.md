---
name: system-map
description: Regenerate the fleet architecture map (crawl every repo under E:\automation, render to architecture/system-map.png) and post the refreshed image to Slack. Use when the user wants to refresh or see the system architecture diagram — e.g. "/system-map", "update the architecture map", "regenerate the system diagram". Also runs unattended on a weekly schedule.
---

# system-map

**Goal:** Keep one always-current, shareable picture of the whole personal fleet. Crawl the fleet, reconcile it against the written architecture, render the visual, commit when it changed, and drop the fresh image in Slack — every run, on-demand or scheduled.

**The map is self-describing: each repo declares its own card in a root `.fleet.toml`, and `.claude/skills/system-map/build_data.py` aggregates those into `architecture/fleet.data.js`** (`window.FLEET = { …strict JSON… };`, the *generated* file the renderer reads). The hand-maintained input is `architecture/fleet.residual.json` — the non-repo structure (access/edge/compute/external/principles), every repo's fallback card in curated order, and an `_adopted` registry of repos that MUST carry a `.fleet.toml`. The visual (`architecture/system-map.html`) is a pure renderer that reads the generated `fleet.data.js`; `architecture/ARCHITECTURE.md` is the human-readable narrative that must agree with it. The acceptance matrix (`tests/run_acceptance.py`) fails loud if the fleet, the data file, the per-repo `.fleet.toml`s, and the doc ever drift apart — so keeping them in sync is enforced, not hoped for.

**Designed for unattended runs.** A weekly job invokes it via
`claude -p "/system-map" --permission-mode bypassPermissions` from the
`fleet-config` repo. Every step must degrade gracefully, never block on a prompt.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never leak hardware specs.** The render always forces `?placeholders=1`, so the committed PNG shows `<model> · <NN> GB` placeholders even though a local `system-map.local.js` exists. Do not put real specs into `ARCHITECTURE.md`, the `DATA` object, or the commit. (See `architecture/README.md`.)
- **Keep the residual and `ARCHITECTURE.md` in lockstep.** Any project add/remove/edit happens in `architecture/fleet.residual.json` (or the repo's `.fleet.toml`) *and* `ARCHITECTURE.md` in the same run, then regenerate `fleet.data.js` with `build_data.py`. Never hand-edit `fleet.data.js`.
- **Don't disturb in-progress work.** Only touch `architecture/` and only commit those paths.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Load the sources

- `hooks/projects.toml` → the fleet: every `[<name>]` table's bare name is a repo; the `[global] architecture_ignore` array lists repos to exclude (vendored/legacy/out-of-scope). The fleet set = all repo names − `architecture_ignore`.
- each repo's `<cwd_prefix>/.fleet.toml` → that repo's self-declared card (authoritative when present). Schema in `architecture/README.md`.
- `architecture/fleet.residual.json` → the hand-maintained input: non-repo structure (access/edge/compute/external/principles) + fallback cards (curated order) + the `_adopted` registry.
- `architecture/fleet.data.js` → the **generated** map data (`window.FLEET`); never hand-edit it.
- `architecture/ARCHITECTURE.md` → the current layer assignment + prose descriptions.

### 2. Reconcile the fleet, then regenerate

Compute the difference between the fleet set (step 1) and the projects currently represented in the map:

- **New repo** (in the fleet, absent from the map): prefer its own `<cwd_prefix>/.fleet.toml` — if present, the card comes from there automatically. If it has none, read its `README.md` (first paragraph) and `CLAUDE.md`, write **one concise sentence** in the existing card voice, assign a layer (default **working — pipelines** unless it is plainly a *shared* enabling tool used by more than one app), and add a fallback card to `architecture/fleet.residual.json` (the matching array: `enabling` / `web` / `pipe`; set `"repo"` when the display `nm` differs). Also add it to `ARCHITECTURE.md`. (Ideally the repo then adopts a `.fleet.toml` via the standard fan-out so the central fallback can be dropped.)
- **Departed repo** (in the map, no longer in the fleet, or newly in `architecture_ignore`): remove it from `fleet.residual.json`, `ARCHITECTURE.md`, and its `_adopted` entry if any.
- **Otherwise**: no content change — proceed to regenerate (specs/date may still refresh).

Keep edits minimal and in the existing card voice. Don't restructure layers or rewrite untouched cards.

Then regenerate the data file and validate:

```
py .claude/skills/system-map/build_data.py     # residual + per-repo .fleet.toml → fleet.data.js
py tests/run_acceptance.py
```

The `system_map:` checks fail loud if the fleet, `fleet.data.js`, the per-repo `.fleet.toml`s, and `ARCHITECTURE.md` disagree (a forgotten repo, a stale entry, an adopted repo that lost its `.fleet.toml`, a malformed declaration, or a doc that omits a mapped repo). Fix any failure before rendering.

### 3. Render the visual

```
py .claude/skills/system-map/render.py
```

This measures the page and screenshots `architecture/system-map.png` at 2× with placeholders forced. On a render failure it prints the real Chrome/console error — fix the `DATA`/HTML and re-run (the page logs a single `DIMS w h` line on success).

### 4. Compute the week-over-week change line

Before committing (so `HEAD` still points at the previous run), capture the
one-line "what changed" summary for the Slack post:

```
py .claude/skills/system-map/whatchanged.py
```

This diffs the freshly-reconciled working `architecture/fleet.data.js` against
the previously-committed one (`git show HEAD:…`) and prints a single line —
`+whatsapp-radar, −suna, 3 repos updated` (added/removed repos named, in-place
edits counted). A no-op week prints `no fleet changes`; the very first run (no
prior snapshot) prints `baseline`. Keep this string for step 6.

### 5. Commit when the map changed

```
git status --porcelain architecture/
```

If nothing under `architecture/` changed, **skip the commit** (idempotent — a no-op week makes no commit). If it did:

```
git add architecture/
git commit -m "docs: refresh system map (<YYYY-MM-DD>)"
```

If the current branch is `main` (the scheduled unattended case), also `git push`. On a feature branch, leave pushing to the normal PR/`issue-finish` flow.

### 6. Post the image to Slack (every run)

Post the refreshed map, folding in the change line from step 4 so the recurring run reads as alive. This is **activity-log** traffic, so route it with `--category log` (the helper resolves the `#log` channel from `hooks/projects.toml` — never hardcode a channel id):

```
py hooks/slack_notify.py --category log \
   --file architecture/system-map.png \
   --title "Roberto's System — architecture" \
   --text "🛠️ Fleet architecture map — refreshed <YYYY-MM-DD>. <change line from step 4>."
```

Always post — on-demand *and* scheduled — so the fresh picture lands on the phone. The helper never raises; a missing token just logs and exits non-zero.

### 7. Report

Print: the change line from step 4, projects added/removed (if any), whether a commit was made (and pushed), and the Slack post result. Keep it to a few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly:

```
claude -p "/system-map" --permission-mode bypassPermissions
```

cwd = `E:/automation/fleet-config`. Same executor as every other scheduled job; the skill handles render + commit-if-changed + Slack itself. (Alternatively a scheduled cloud agent invoking the same line.)
