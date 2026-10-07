---
name: system-map
description: Regenerate the fleet architecture map (crawl every repo under E:\automation, render to architecture/system-map.png) and post the refreshed image to Telegram. E.g. "/system-map", "update the architecture map", "regenerate the system diagram". Also runs unattended weekly.
---

# system-map

**Goal:** one always-current, shareable picture of the fleet. Crawl, reconcile against the written architecture, render, commit when changed, post the image to Telegram — every run, on-demand or scheduled.

**Self-describing map:** each repo declares its card in a root `.fleet.toml`; `.claude/skills/system-map/build_data.py` aggregates those (plus hand-maintained `architecture/fleet.residual.json`) into the *generated* `architecture/fleet.data.js`, read by both renderers — `architecture/system-map.html` (the PNG) and `.claude/skills/system-map/render_mermaid.py` (the text-native `.mmd` fleet-map doc `global-CLAUDE.md` points at). `architecture/ARCHITECTURE.md` is the human-readable narrative that must agree; `tests/run_acceptance.py` fails loud if fleet, data file, and doc drift apart. Exception: per-repo `.fleet.toml` aggregation reads sibling checkouts, so it reports drift as `SKIP` rather than failing fleet-config's gate, and **this skill owns fixing it** (step 2).

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never leak hardware specs.** The render forces `?placeholders=1`, so the committed PNG shows `<model> · <NN> GB` placeholders despite a local `system-map.local.js`. No real specs in `ARCHITECTURE.md`, the `DATA` object, or the commit. (See `architecture/README.md`.)
- **Keep the residual and `ARCHITECTURE.md` in lockstep.** Any project add/remove/edit goes in `architecture/fleet.residual.json` (or the repo's `.fleet.toml`) *and* `ARCHITECTURE.md` in the same run, then regenerate `fleet.data.js` with `build_data.py`. Never hand-edit `fleet.data.js`.
- **Don't disturb in-progress work.** Only touch `architecture/`, and only commit that path.
- **Degrade gracefully, never block on a prompt** — this runs unattended.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Load the sources

- `hooks/projects.toml` → the fleet: every `[<name>]` table's bare name is a repo; `[global] architecture_ignore` lists repos to exclude (vendored/legacy/out-of-scope). Fleet set = all repo names − `architecture_ignore`.
- each repo's `<cwd_prefix>/.fleet.toml` → that repo's self-declared card (authoritative when present). Schema in `architecture/README.md`.
- `architecture/fleet.residual.json` → the hand-maintained input: non-repo structure (access/edge/compute/external/principles) + fallback cards (curated order) + the `_adopted` registry of repos that MUST carry a `.fleet.toml`.
- `architecture/fleet.data.js` → the **generated** map data (`window.FLEET = { …strict JSON… };`); never hand-edit it.
- `architecture/ARCHITECTURE.md` → the current layer assignment + prose descriptions.

### 2. Reconcile the fleet, then regenerate

Diff the fleet set (step 1) against the projects in the map:

- **New repo** (in the fleet, absent from the map): prefer its own `<cwd_prefix>/.fleet.toml` — if present, the card comes from there automatically. If it has none, read its `README.md` (first paragraph) and `CLAUDE.md`, write **one concise sentence** in the existing card voice, assign a layer (default **working — pipelines** unless plainly a *shared* enabling tool used by more than one app), and add a fallback card to `architecture/fleet.residual.json` (matching array: `enabling` / `web` / `pipe`; set `"repo"` when the display `nm` differs). Also add it to `ARCHITECTURE.md`. (Ideally the repo then adopts a `.fleet.toml` via the standard fan-out, dropping the fallback.)
- **Departed repo** (in the map, no longer in the fleet, or newly in `architecture_ignore`): remove it from `fleet.residual.json`, `ARCHITECTURE.md`, and its `_adopted` entry if any.
- **Otherwise**: no content change — proceed to regenerate (specs/date may still refresh).

Keep edits minimal, in the existing card voice; don't restructure layers or rewrite untouched cards.

Then regenerate the data file and validate:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py     # residual + per-repo .fleet.toml → fleet.data.js
E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py
```

A `build_data: REFUSED <repo>: …` line means that repo's `.fleet.toml` description exceeds its two-line card (per-layer cap: `architecture/README.md`, fleet-config#1250). The file is still regenerated with the repo's residual fallback card, but the run exits 1. Name every refused repo in the step-7 report; fix = shorter description in the owning repo, never a raised cap.

The `system_map:` checks fail loud if the fleet, `fleet.data.js`, and `ARCHITECTURE.md` disagree (forgotten repo, stale entry, doc omitting a mapped repo). Fix any failure before rendering.

**This skill owns fleet-wide `.fleet.toml` freshness** (fleet-config#562). The `fleet_toml:` aggregate checks read *sibling repos'* live checkouts, so another repo's `.fleet.toml` commit would turn fleet-config's gate red, blocking `/issue-finish`, `/quick`, `/issue-yolo` here for a reason no commit here can fix. They therefore report `SKIP` in `tests/run_acceptance.py` (advisory; only fleet-config's own card is gated hard). Here they are load-bearing: **any `SKIP  fleet_toml:` line in this step is a failure of this run** — re-run `build_data.py`; if a line survives, fix it in the owning repo (or drop it from `_adopted`) before rendering. Never leave the step with drift outstanding: the weekly run is the only thing that clears it.

### 3. Render the visual

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render.py
```

Screenshots `architecture/system-map.png` at 2× with placeholders forced. On failure it prints the real Chrome/console error — fix the `DATA`/HTML and re-run (success logs a single `DIMS w h` line).

Then render the **text-native** companion (second consumer of the same `fleet.data.js`, no new crawl logic):

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_mermaid.py
```

Regenerates `architecture/system-map.mmd` (Mermaid flowchart — icons + names only, edges from each card's `tag` field), the fleet-map doc agents open on demand; `global-CLAUDE.md` only points at it, never embeds it (fleet-config#897). Idempotent — an unchanged week touches nothing; `--check` exits 1 when the committed file is stale.

### 4. Compute the week-over-week change line

Before committing (so `HEAD` is still the previous run), capture the one-line "what changed" summary:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/whatchanged.py
```

Diffs working `architecture/fleet.data.js` against the committed one (`git show HEAD:…`), printing one line — `+whatsapp-radar, −suna, 3 repos updated` (added/removed repos named, in-place edits counted). No-op week: `no fleet changes`; first run (no prior snapshot): `baseline`. Keep this string for step 6.

### 5. Commit when the map changed

```
git status --porcelain architecture/
```

If nothing changed, **skip the commit** (no-op week = no commit). Otherwise:

```
git add architecture/
git commit -m "docs: refresh system map (<YYYY-MM-DD>)"
```

If the current branch is `main` (the scheduled unattended case), also `git push`. On a feature branch, leave pushing to the normal PR/`issue-finish` flow.

### 6. Post the image to Telegram (every run)

Post the map with the step-4 change line. This is **activity-log** traffic: `--category log` (the helper resolves the `coding log` chat from `hooks/projects.toml` — never hardcode a channel id):

```
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --file architecture/system-map.png \
   --title "Roberto's System — architecture" \
   --text "🛠️ Fleet architecture map - refreshed <YYYY-MM-DD>. <change line from step 4>."
```

Always post, on-demand *and* scheduled. The helper never raises; a missing token just logs and exits non-zero.

### 7. Report

Print in a few lines: the step-4 change line, projects added/removed (if any), whether a commit was made (and pushed), the Telegram post result.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly, targeting the co-located `.claude/skills/system-map/run-weekly.bat`; it preserves `/system-map` plus bypass permissions and streams filtered milestones through the shared `claude_progress.py` adapter.

cwd = `E:/automation/fleet-config`. Same executor as every other scheduled job; the skill handles render + commit-if-changed + Telegram itself. (Alternatively a scheduled cloud agent invoking the same skill.)
