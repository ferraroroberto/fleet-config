---
name: system-map
description: Regenerate the fleet architecture map — crawl every repo under E:\automation (from hooks/projects.toml, minus the architecture_ignore list), reconcile it against the layered source-of-truth doc (architecture/ARCHITECTURE.md) and the visual's data (architecture/system-map.html), render the light, horizontal, Janis-style infographic to architecture/system-map.png with headless Chrome (placeholder specs only — real hardware specs stay in the gitignored system-map.local.js), commit the diff when the map changed, and post the fresh image to Slack on every run. Use when the user wants to refresh or see the system architecture diagram — e.g. "/system-map", "update the architecture map", "regenerate the system diagram". Built to also run unattended on a weekly schedule.
---

# system-map

**Goal:** Keep one always-current, shareable picture of the whole personal fleet. Crawl the fleet, reconcile it against the written architecture, render the visual, commit when it changed, and drop the fresh image in Slack — every run, on-demand or scheduled.

**One machine-readable source of truth: `architecture/fleet.data.js`** (`window.FLEET = { …strict JSON… };`). The visual (`architecture/system-map.html`) is a pure renderer that reads it; `architecture/ARCHITECTURE.md` is the human-readable narrative that must agree with it. The acceptance matrix (`tests/run_acceptance.py`) fails loud if the fleet, the data file, and the doc ever drift apart — so keeping them in sync is enforced, not hoped for.

**Designed for unattended runs.** A weekly job invokes it via
`claude -p "/system-map" --permission-mode bypassPermissions` from the
`fleet-config` repo. Every step must degrade gracefully, never block on a prompt.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never leak hardware specs.** The render always forces `?placeholders=1`, so the committed PNG shows `<model> · <NN> GB` placeholders even though a local `system-map.local.js` exists. Do not put real specs into `ARCHITECTURE.md`, the `DATA` object, or the commit. (See `architecture/README.md`.)
- **Keep `DATA` and `ARCHITECTURE.md` in lockstep.** Any project add/remove/edit happens in *both* files in the same run.
- **Don't disturb in-progress work.** Only touch `architecture/` and only commit those paths.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Load the three sources

- `hooks/projects.toml` → the fleet: every `[<name>]` table's bare name is a repo; the `[global] architecture_ignore` array lists repos to exclude (vendored/legacy/out-of-scope). The fleet set = all repo names − `architecture_ignore`.
- `architecture/fleet.data.js` → the current map data (`window.FLEET`, strict JSON; `repo` set only where the display name differs).
- `architecture/ARCHITECTURE.md` → the current layer assignment + prose descriptions.

### 2. Reconcile the fleet against the map

Compute the difference between the fleet set (step 1) and the projects currently represented in the map:

- **New repo** (in the fleet, absent from the map): read its `E:/automation/<repo>/README.md` (first paragraph) and `CLAUDE.md` if present; write **one concise sentence** the way the existing cards read; assign a layer (default **L3 working — pipelines** unless it is plainly a *shared* enabling tool used by more than one app, in which case **L2**, with a fuller description). Add it to **both** `architecture/fleet.data.js` (the matching array: `enabling` / `web` / `pipe`; set `"repo"` when the display `nm` differs from the repo name) and `ARCHITECTURE.md` (the right layer table).
- **Departed repo** (in the map, no longer in the fleet, or newly in `architecture_ignore`): remove it from **both** files.
- **Otherwise**: no content change — proceed to render (specs/date may still refresh).

Keep edits minimal and in the existing card voice: shared/reused pieces get a real description; self-explanatory leaf apps get one short line. Don't restructure layers or rewrite untouched cards.

After editing, run `py tests/run_acceptance.py` — the `system_map:` checks fail loud if the fleet, `fleet.data.js`, and `ARCHITECTURE.md` disagree (a forgotten repo, a stale entry, or a doc that omits a mapped repo). Fix any failure before rendering.

### 3. Render the visual

```
py skills/system-map/render.py
```

This measures the page and screenshots `architecture/system-map.png` at 2× with placeholders forced. On a render failure it prints the real Chrome/console error — fix the `DATA`/HTML and re-run (the page logs a single `DIMS w h` line on success).

### 4. Compute the week-over-week change line

Before committing (so `HEAD` still points at the previous run), capture the
one-line "what changed" summary for the Slack post:

```
py skills/system-map/whatchanged.py
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

Resolve the channel from `hooks/projects.toml` `[global] slack_notify_channel`, then post, folding in the change line from step 4 so the recurring run reads as alive:

```
py hooks/slack_notify.py --channel <slack_notify_channel> \
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
