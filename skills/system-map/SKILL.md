---
name: system-map
description: Regenerate the fleet architecture map — crawl every repo under E:\automation (from hooks/projects.toml, minus the architecture_ignore list), reconcile it against the layered source-of-truth doc (architecture/ARCHITECTURE.md) and the visual's data (architecture/system-map.html), render the light, horizontal, Janis-style infographic to architecture/system-map.png with headless Chrome (placeholder specs only — real hardware specs stay in the gitignored system-map.local.js), commit the diff when the map changed, and post the fresh image to Slack on every run. Use when the user wants to refresh or see the system architecture diagram — e.g. "/system-map", "update the architecture map", "regenerate the system diagram". Built to also run unattended on a weekly schedule.
---

# system-map

**Goal:** Keep one always-current, shareable picture of the whole personal fleet. Crawl the fleet, reconcile it against the written architecture, render the visual, commit when it changed, and drop the fresh image in Slack — every run, on-demand or scheduled.

**Source of truth is the words, not the picture.** `architecture/ARCHITECTURE.md` holds the layered, fixed-schema description (compute → connectivity → enabling tools → working apps → governance). The visual (`architecture/system-map.html`, an `const DATA = {…}` object + CSS) is generated *from* that. These two must always agree; this skill is what keeps them — and the fleet — in sync.

**Designed for unattended runs.** A weekly job invokes it via
`claude -p "/system-map" --permission-mode bypassPermissions` from the
`claude-config` repo. Every step must degrade gracefully, never block on a prompt.

## Execution rules (read first)

- **Run from the `claude-config` repo root** (`E:/automation/claude-config`). All paths below are relative to it.
- **Never leak hardware specs.** The render always forces `?placeholders=1`, so the committed PNG shows `<model> · <NN> GB` placeholders even though a local `system-map.local.js` exists. Do not put real specs into `ARCHITECTURE.md`, the `DATA` object, or the commit. (See `architecture/README.md`.)
- **Keep `DATA` and `ARCHITECTURE.md` in lockstep.** Any project add/remove/edit happens in *both* files in the same run.
- **Don't disturb in-progress work.** Only touch `architecture/` and only commit those paths.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Load the three sources

- `hooks/projects.toml` → the fleet: every `[<name>]` table's bare name is a repo; the `[global] architecture_ignore` array lists repos to exclude (vendored/legacy/out-of-scope). The fleet set = all repo names − `architecture_ignore`.
- `architecture/ARCHITECTURE.md` → the current layer assignment + descriptions.
- `architecture/system-map.html` → the current `DATA` object (what the picture shows).

### 2. Reconcile the fleet against the map

Compute the difference between the fleet set (step 1) and the projects currently represented in the map:

- **New repo** (in the fleet, absent from the map): read its `E:/automation/<repo>/README.md` (first paragraph) and `CLAUDE.md` if present; write **one concise sentence** the way the existing cards read; assign a layer (default **L3 working — pipelines** unless it is plainly a *shared* enabling tool used by more than one app, in which case **L2**, with a fuller description). Add it to **both** `ARCHITECTURE.md` (the right layer table) and the `DATA` object (the matching array: `enabling` / `web` / `pipe`).
- **Departed repo** (in the map, no longer in the fleet, or newly in `architecture_ignore`): remove it from **both** files.
- **Otherwise**: no content change — proceed to render (specs/date may still refresh).

Keep edits minimal and in the existing card voice: shared/reused pieces get a real description; self-explanatory leaf apps get one short line. Don't restructure layers or rewrite untouched cards.

### 3. Render the visual

```
py skills/system-map/render.py
```

This measures the page and screenshots `architecture/system-map.png` at 2× with placeholders forced. On a render failure it prints the real Chrome/console error — fix the `DATA`/HTML and re-run (the page logs a single `DIMS w h` line on success).

### 4. Commit when the map changed

```
git status --porcelain architecture/
```

If nothing under `architecture/` changed, **skip the commit** (idempotent — a no-op week makes no commit). If it did:

```
git add architecture/
git commit -m "docs: refresh system map (<YYYY-MM-DD>)"
```

If the current branch is `main` (the scheduled unattended case), also `git push`. On a feature branch, leave pushing to the normal PR/`issue-finish` flow.

### 5. Post the image to Slack (every run)

Resolve the channel from `hooks/projects.toml` `[global] slack_notify_channel`, then:

```
py hooks/slack_notify.py --channel <slack_notify_channel> \
   --file architecture/system-map.png \
   --title "Roberto's System — architecture" \
   --text "🛠️ Fleet architecture map — refreshed <YYYY-MM-DD>. <changed|unchanged this week>."
```

Always post — on-demand *and* scheduled — so the fresh picture lands on the phone. The helper never raises; a missing token just logs and exits non-zero.

### 6. Report

Print: projects added/removed (if any), whether a commit was made (and pushed), and the Slack post result. Keep it to a few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly:

```
claude -p "/system-map" --permission-mode bypassPermissions
```

cwd = `E:/automation/claude-config`. Same executor as every other scheduled job; the skill handles render + commit-if-changed + Slack itself. (Alternatively a scheduled cloud agent invoking the same line.)
