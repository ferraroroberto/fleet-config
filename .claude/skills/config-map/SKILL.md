---
name: config-map
description: Regenerate the fleet config & convention map (introspect install.ps1, the skill/hook dirs, a per-repo git sweep; render to architecture/config-map.png) and post it to Telegram — the cross-agent configuration picture. E.g. "/config-map", "update the config map", "what skills/hooks does each agent get". Companion to /context-audit. Runs weekly unattended.
---

# config-map

**Goal:** one always-current picture of the cross-agent *configuration* surface — skills, hooks, context file, design system, statusline, settings per agent (Claude Code · Codex · Pi · Copilot · Antigravity); universal vs repo-specific; conventions in force. Descriptive counterpart to `/context-audit`: this skill is the weekly **photo**; `/context-audit` flags the **drift**.

**Derived, not declared.** Unlike `/system-map` (aggregates self-describing per-repo `.fleet.toml` cards), config is centralized in `fleet-config`, so `.claude/skills/config-map/build_data.py` *introspects* it: per-agent matrix from `install.ps1`'s link table + `codex-hooks.json`; skills from `skills/` and `.claude/skills/`; hooks from `hooks/*.py` + `settings.template.json`; repo-specific skills from a git sweep of each fleet repo's committed `.claude/skills`. Hand-maintained input: `architecture/config.residual.json` — agent columns, matrix row structure (non-derivable cells only), universal-skill scope set, project-wired hooks, conventions prose. Visual (`architecture/config-map.html`) is a pure renderer over generated `architecture/config.data.js` (`window.CONFIG = { …strict JSON… }`). `tests/run_acceptance.py` reports `config.data.js` drifting from `build_data.py`'s output as a `SKIP`, not a failure — the git sweep reads *sibling* repos and no fleet-config commit can turn a sister repo's new skill green (fleet-config#562). **This skill owns that freshness** (step 1).

**Unattended-safe:** every step degrades gracefully, never blocks on a prompt.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`). All paths below are relative to it.
- **Never hand-edit `config.data.js`.** It is generated. Change a real source (a skill, a hook, `install.ps1`, `settings.template.json`, `codex-hooks.json`, a repo's `.claude/skills`) or `architecture/config.residual.json`, then regenerate.
- **The dataset carries only wiring/structure — never a secret.** `build_data.py` reads `settings.template.json` (the committed template), never the live `~/.claude/settings.json`. Keep it that way.
- **Don't disturb in-progress work.** Only touch `architecture/` and only commit those paths.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Regenerate the data

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py     # introspect → architecture/config.data.js
E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py
```

`config_map:` checks fail loud if a `whatchanged` invariant breaks, and print `SKIP  config_map: config.data.js matches build_data.py output` when the committed snapshot is stale. **Treat that SKIP as a failure of this run** — advisory only for fleet-config's own gate; this skill is what clears it, so investigate rather than proceeding. Fix any failure or SKIP before rendering. If a new agent, config class, statusline kind, or project-wired hook appeared that introspection can't derive, add it to `architecture/config.residual.json` first, then regenerate.

### 2. Render the visual

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/render.py
```

Measures the page and screenshots `architecture/config-map.png` at 2×. On a render failure it prints the real Chrome/console error — fix the data/HTML and re-run (the page logs a single `DIMS w h` line on success).

### 3. Compute the week-over-week change line

Before committing (so `HEAD` still points at the previous run):

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/whatchanged.py
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

### 5. Post the image to Telegram (every run)

Activity-log traffic, so route with `--category log` (the helper resolves the `coding log` chat from `hooks/projects.toml` — never hardcode a channel id):

```
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --file architecture/config-map.png \
   --title "Fleet config & conventions" \
   --text "🗺️ Cross-agent config map - refreshed <YYYY-MM-DD>. <change line from step 3>."
```

Always post — on-demand *and* scheduled. The helper never raises; a missing token just logs and exits non-zero.

### 6. Report

Print: the change line from step 3, whether a commit was made (and pushed), and the Telegram post result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly via the co-located launcher `.claude/skills/config-map/run-weekly.bat`; the wrapper preserves `/config-map` plus bypass permissions and streams filtered milestones through the shared `claude_progress.py` adapter.

cwd = `E:/automation/fleet-config`. **Stagger it off `/system-map`'s Friday 01:00 slot** (e.g. Friday 02:30) so the two weekly map refreshes don't collide.
