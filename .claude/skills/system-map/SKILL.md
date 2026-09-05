---
name: system-map
description: Regenerate the fleet architecture map (crawl every repo under E:\automation, render to architecture/system-map.png) and post the refreshed image to Telegram. E.g. "/system-map", "update the architecture map", "regenerate the system diagram". Also runs unattended weekly.
---

# system-map

**Goal:** keep one always-current, shareable picture of the whole personal fleet. Crawl the fleet, reconcile it against the written architecture, render the visual, commit when it changed, drop the fresh image in Telegram — every run, on-demand or scheduled.

**The map is self-describing:** each repo declares its own card in a root `.fleet.toml`, and `.claude/skills/system-map/build_data.py` aggregates those (plus the hand-maintained `architecture/fleet.residual.json`) into the *generated* `architecture/fleet.data.js` that both renderers read — `architecture/system-map.html` (the PNG) and `.claude/skills/system-map/render_mermaid.py` (the text-native `.mmd` + `global-CLAUDE.md` block). `architecture/ARCHITECTURE.md` is the human-readable narrative that must agree with it; `tests/run_acceptance.py` fails loud if the fleet, the data file, and the doc ever drift apart — enforced, not hoped for. Per-repo `.fleet.toml` aggregation is the one exception: its inputs live in sibling checkouts, so it reports drift as `SKIP` rather than failing fleet-config's gate, and **this skill owns fixing it** (step 2).

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never leak hardware specs.** The render always forces `?placeholders=1`, so the committed PNG shows `<model> · <NN> GB` placeholders even though a local `system-map.local.js` exists. Do not put real specs into `ARCHITECTURE.md`, the `DATA` object, or the commit. (See `architecture/README.md`.)
- **Keep the residual and `ARCHITECTURE.md` in lockstep.** Any project add/remove/edit happens in `architecture/fleet.residual.json` (or the repo's `.fleet.toml`) *and* `ARCHITECTURE.md` in the same run, then regenerate `fleet.data.js` with `build_data.py`. Never hand-edit `fleet.data.js`.
- **Don't disturb in-progress work.** Only touch `architecture/` and the marked fleet-map block in `global-CLAUDE.md`, and only commit those paths.
- **Degrade gracefully, never block on a prompt** — this runs unattended.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Load the sources

- `hooks/projects.toml` → the fleet: every `[<name>]` table's bare name is a repo; the `[global] architecture_ignore` array lists repos to exclude (vendored/legacy/out-of-scope). The fleet set = all repo names − `architecture_ignore`.
- each repo's `<cwd_prefix>/.fleet.toml` → that repo's self-declared card (authoritative when present). Schema in `architecture/README.md`.
- `architecture/fleet.residual.json` → the hand-maintained input: non-repo structure (access/edge/compute/external/principles) + fallback cards (curated order) + the `_adopted` registry of repos that MUST carry a `.fleet.toml`.
- `architecture/fleet.data.js` → the **generated** map data (`window.FLEET = { …strict JSON… };`); never hand-edit it.
- `architecture/ARCHITECTURE.md` → the current layer assignment + prose descriptions.

### 2. Reconcile the fleet, then regenerate

Compute the difference between the fleet set (step 1) and the projects currently represented in the map:

- **New repo** (in the fleet, absent from the map): prefer its own `<cwd_prefix>/.fleet.toml` — if present, the card comes from there automatically. If it has none, read its `README.md` (first paragraph) and `CLAUDE.md`, write **one concise sentence** in the existing card voice, assign a layer (default **working — pipelines** unless plainly a *shared* enabling tool used by more than one app), and add a fallback card to `architecture/fleet.residual.json` (matching array: `enabling` / `web` / `pipe`; set `"repo"` when the display `nm` differs). Also add it to `ARCHITECTURE.md`. (Ideally the repo then adopts a `.fleet.toml` via the standard fan-out so the central fallback can be dropped.)
- **Departed repo** (in the map, no longer in the fleet, or newly in `architecture_ignore`): remove it from `fleet.residual.json`, `ARCHITECTURE.md`, and its `_adopted` entry if any.
- **Otherwise**: no content change — proceed to regenerate (specs/date may still refresh).

Keep edits minimal and in the existing card voice. Don't restructure layers or rewrite untouched cards.

Then regenerate the data file and validate:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py     # residual + per-repo .fleet.toml → fleet.data.js
E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py
```

The `system_map:` checks fail loud if the fleet, `fleet.data.js`, and `ARCHITECTURE.md` disagree (a forgotten repo, a stale entry, or a doc that omits a mapped repo). Fix any failure before rendering.

**This skill owns fleet-wide `.fleet.toml` freshness** (fleet-config#562). The `fleet_toml:` aggregate checks read *sibling repos'* live checkouts, so a `.fleet.toml` commit in any other repo would otherwise turn fleet-config's own gate red — blocking every `/issue-finish`, `/quick`, and `/issue-yolo` here for a reason no commit in this repo can fix. They therefore report as `SKIP` in `tests/run_acceptance.py` (advisory: drift is named, run not failed) and only fleet-config's own card is gated hard there. Here they are load-bearing: **any `SKIP  fleet_toml:` line in this step is a failure of this run** — the regeneration above resolves it, so re-run `build_data.py`, and if a line survives, fix it in the owning repo (or drop it from `_adopted`) before rendering. Never leave the step with drift outstanding: the weekly run is the only thing that clears it.

### 3. Render the visual

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render.py
```

Measures the page and screenshots `architecture/system-map.png` at 2× with placeholders forced. On a render failure it prints the real Chrome/console error — fix the `DATA`/HTML and re-run (the page logs a single `DIMS w h` line on success).

Then render the **text-native** companion — a second, independent consumer of the same `fleet.data.js`, no new crawl logic:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_mermaid.py
```

Regenerates `architecture/system-map.mmd` (a Mermaid flowchart — icons + names only, edges from each card's `tag` field) *and* refreshes the marked `<!-- system-map:mermaid:start -->…:end` block inside `global-CLAUDE.md`'s "Project fleet" section in the same run, so the always-on context an agent loads at session start stays in sync with the map. Both writes are idempotent — an unchanged week touches neither file.

### 4. Compute the week-over-week change line

Before committing (so `HEAD` still points at the previous run), capture the one-line "what changed" summary for the Telegram post:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/whatchanged.py
```

Diffs the freshly-reconciled working `architecture/fleet.data.js` against the previously-committed one (`git show HEAD:…`) and prints a single line — `+whatsapp-radar, −suna, 3 repos updated` (added/removed repos named, in-place edits counted). A no-op week prints `no fleet changes`; the very first run (no prior snapshot) prints `baseline`. Keep this string for step 6.

### 5. Commit when the map changed

```
git status --porcelain architecture/ global-CLAUDE.md
```

If nothing changed, **skip the commit** (idempotent — a no-op week makes no commit). If it did:

```
git add architecture/ global-CLAUDE.md
git commit -m "docs: refresh system map (<YYYY-MM-DD>)"
```

If the current branch is `main` (the scheduled unattended case), also `git push`. On a feature branch, leave pushing to the normal PR/`issue-finish` flow.

### 6. Post the image to Telegram (every run)

Post the refreshed map, folding in the change line from step 4 so the recurring run reads as alive. This is **activity-log** traffic, so route it with `--category log` (the helper resolves the `coding log` chat from `hooks/projects.toml` — never hardcode a channel id):

```
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --file architecture/system-map.png \
   --title "Roberto's System — architecture" \
   --text "🛠️ Fleet architecture map - refreshed <YYYY-MM-DD>. <change line from step 4>."
```

Always post — on-demand *and* scheduled — so the fresh picture lands on the phone. The helper never raises; a missing token just logs and exits non-zero.

### 7. Report

Print: the change line from step 4, projects added/removed (if any), whether a commit was made (and pushed), and the Telegram post result. Keep it to a few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly, targeting the co-located `.claude/skills/system-map/run-weekly.bat`; it preserves `/system-map` plus bypass permissions and streams filtered milestones through the shared `claude_progress.py` adapter.

cwd = `E:/automation/fleet-config`. Same executor as every other scheduled job; the skill handles render + commit-if-changed + Telegram itself. (Alternatively a scheduled cloud agent invoking the same skill.)
