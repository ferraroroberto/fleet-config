---
name: config-map
description: Regenerate the fleet config & convention map (introspect install.ps1 + the skill/hook dirs + a per-repo git sweep, render to architecture/config-map.png) and post the refreshed image to Slack. Use when the user wants to see or refresh the cross-agent configuration picture — e.g. "/config-map", "update the config map", "what skills/hooks does each agent get". The descriptive companion to /context-audit (which enforces drift). Also runs unattended on a weekly schedule.
---

# config-map

**Goal:** Keep one always-current, shareable picture of the whole cross-agent *configuration* surface — what skills, hooks, context file, design system, statusline and settings each agent (Claude Code · Codex · Pi · Copilot · Antigravity) gets, which are universal vs repo-specific, and which conventions are in force. The descriptive counterpart to `/context-audit`: this skill is the weekly **photo**; `/context-audit` flags the **drift**.

**The map is derived, not declared.** Unlike `/system-map` (which aggregates self-describing per-repo `.fleet.toml` cards), almost all config is centralized in `fleet-config`, so `.claude/skills/config-map/build_data.py` *introspects* it: the per-agent matrix from `install.ps1`'s link table + `codex-hooks.json`; the skills from `skills/` and `.claude/skills/`; the hooks from `hooks/*.py` + `settings.template.json`; the repo-specific skills from a git sweep of each fleet repo's committed `.claude/skills`. The thin hand-maintained input is `architecture/config.residual.json` — the agent columns, the matrix row structure (non-derivable cells only), the universal-skill scope set, the project-wired hooks, and the conventions prose. The visual (`architecture/config-map.html`) is a pure renderer over the generated `architecture/config.data.js` (`window.CONFIG = { …strict JSON… }`). The acceptance matrix (`tests/run_acceptance.py`) fails loud if `config.data.js` is not exactly what `build_data.py` regenerates — so the picture can't go stale.

**Designed for unattended runs.** A weekly job invokes it via `claude -p "/config-map" --permission-mode bypassPermissions` from the `fleet-config` repo. Every step degrades gracefully, never blocks on a prompt.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never hand-edit `config.data.js`.** It is generated. Change a real source (a skill, a hook, `install.ps1`, `settings.template.json`, `codex-hooks.json`, a repo's `.claude/skills`) or `architecture/config.residual.json`, then regenerate.
- **The dataset carries only wiring/structure — never a secret.** `build_data.py` reads `settings.template.json` (the committed template), never the live `~/.claude/settings.json`. Keep it that way.
- **Don't disturb in-progress work.** Only touch `architecture/` and only commit those paths.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Regenerate the data

```
py .claude/skills/config-map/build_data.py     # introspect → architecture/config.data.js
py tests/run_acceptance.py
```

The `config_map:` checks fail loud if `config.data.js` is stale (not what `build_data.py` regenerates) or a `whatchanged` invariant breaks. Fix any failure before rendering. If a new agent, config class, statusline kind or project-wired hook appeared that the introspection can't derive, add it to `architecture/config.residual.json` first, then regenerate.

### 2. Render the visual

```
py .claude/skills/config-map/render.py
```

Measures the page and screenshots `architecture/config-map.png` at 2×. On a render failure it prints the real Chrome/console error — fix the data/HTML and re-run (the page logs a single `DIMS w h` line on success).

### 3. Compute the week-over-week change line

Before committing (so `HEAD` still points at the previous run):

```
py .claude/skills/config-map/whatchanged.py
```

Diffs the freshly-built working `config.data.js` against the previously-committed one and prints one line — `+config-map, −old-hook, 3 updated` (added/removed entries named across skills/hooks/matrix/conventions, in-place edits counted). A no-op week prints `no config changes`; the first run prints `baseline`. Keep this string for step 5.

### 4. Commit when the map changed

```
git status --porcelain architecture/
```

If nothing under `architecture/` changed, **skip the commit** (idempotent). If it did:

```
git add architecture/
git commit -m "docs: refresh config map (<YYYY-MM-DD>)"
```

If the current branch is `main` (the scheduled unattended case), also `git push`. On a feature branch, leave pushing to the normal PR/`issue-finish` flow.

### 5. Post the image to Slack (every run)

Activity-log traffic, so route with `--category log` (the helper resolves the `#log` channel from `hooks/projects.toml` — never hardcode a channel id):

```
py hooks/slack_notify.py --category log \
   --file architecture/config-map.png \
   --title "Fleet config & conventions" \
   --text "🗺️ Cross-agent config map — refreshed <YYYY-MM-DD>. <change line from step 3>."
```

Always post — on-demand *and* scheduled. The helper never raises; a missing token just logs and exits non-zero.

### 6. Report

Print: the change line from step 3, whether a commit was made (and pushed), and the Slack post result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly via the co-located launcher `.claude/skills/config-map/run-weekly.bat`:

```
claude -p "/config-map" --permission-mode bypassPermissions
```

cwd = `E:/automation/fleet-config`. **Stagger it off `/system-map`'s Friday 01:00 slot** (e.g. Friday 02:30) so the two weekly map refreshes don't collide.
