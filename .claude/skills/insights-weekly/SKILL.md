---
name: insights-weekly
description: Diff Claude Code's newest /insights report against the previous one (via the local LLM hub) into a concise weekly "what changed" note posted to Telegram — how Claude Code usage is shifting week-over-week. E.g. "/insights-weekly", "what changed in my insights this week", "weekly insights diff". Also runs unattended weekly.
---

# insights-weekly *(Claude Code only — skip on other agents)*

**Goal:** turn Claude Code's **`/insights`** report into a weekly "what changed" signal. `/insights` writes a dated `report-<timestamp>.html`; that series **is** the history. This skill refreshes it, hands the **newest two reports to the local LLM hub** to narrate the week-over-week delta, saves a dated note, and drops a concise digest in Telegram — on-demand or scheduled.

**The hub does the analysis, not the orchestrator.** Never re-aggregate the raw `session-meta`/`facets` JSON files (already distilled into the HTML), and never write the narrative in-session — `report.py` delegates the comparison to `127.0.0.1:8000`. The artifact is user-local and never committed (`~/.claude/usage-data/` is outside this repo).

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **Never commit insights data or reports.** Output lands under `~/.claude/usage-data/weekly/`, outside the repo.
- **The model is the hub's job.** `report.py` POSTs to the hub via stdlib `urllib` — never re-implement a `claude -p` wrapper. Default model `claude_sonnet`; override with `INSIGHTS_DIFF_MODEL` (e.g. `gemma4_26b`, `qwen3.5-4b`, `gemini_flash`) when loaded.
- **Degrade gracefully, never block on a prompt** (unattended): first run with one report → baseline, not a diff; hub unreachable → surface the error and skip the ping rather than hang.

## Steps

Run in order. A step failure prints a short error and stops.

### 1. Refresh the insights report

```
MSYS_NO_PATHCONV=1 claude -p "/insights" --permission-mode bypassPermissions
```

Run it through the **Bash tool exactly as written** — without the prefix, Git Bash's MSYS path conversion rewrites `/insights` into `C:/Program Files/Git/insights` garbling the child's prompt (fleet-config#842). From the PowerShell tool, drop the prefix (no MSYS conversion there; it would read as a command).

`/insights` writes a fresh `report-<timestamp>.html` into `~/.claude/usage-data/`. If no new file appears (can't refresh headlessly), proceed with the latest existing `report-*.html` — `report.py` always uses the two newest on disk. (On the scheduled job this is a normal nested `claude -p`.)

### 2. Diff the two newest reports via the hub

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/insights-weekly/report.py
```

Takes the newest two `report-*.html`, strips each to text (`extract.py`), asks the hub to narrate the delta, writes `~/.claude/usage-data/weekly/insights-diff-<YYYY-MM-DD>.md`. Prints **the dated file path on line 1**, a blank line, then the **`TL;DR` digest** — capture both. On the first run (only one report) it writes a **baseline** instead and says so. Exit 3 means the hub call failed (model/backend down): report it, skip step 3, stop.

### 3. Post the digest to Telegram

Post the digest **as the caption of the dated report file** (summary in the push, full markdown attached). **Activity-log** traffic, so route with `--category log` (the helper resolves the `coding log` chat from `hooks/projects.toml` — never hardcode a channel id). Pass the **absolute** report path `report.py` printed on line 1 to `--file`, and pipe the digest body via stdin (`notify_send` decodes stdin as UTF-8):

```
cat <<'EOF' | E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --file <absolute insights-diff-YYYY-MM-DD.md path from report.py line 1> \
   --title "Claude Code Insights — weekly <diff|baseline> <YYYY-MM-DD>"
🧠 Weekly Claude Code insights — <diff|baseline> <YYYY-MM-DD>

<the TL;DR digest>
EOF
```

Keep the caption tight. The helper never raises; a missing token just logs and exits non-zero.

### 4. Report

Print a few lines: which two reports were compared (or "baseline"), the dated file path, the model used, the Telegram result.

## Wiring the weekly schedule

An **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) runs this weekly — **target the first run for a Friday** — pointing at `.claude/skills/insights-weekly/run-weekly.bat`, which preserves `/insights-weekly` plus bypass permissions and streams filtered milestones through `claude_progress.py`.

cwd = `E:/automation/fleet-config`. Same executor as other scheduled jobs (`/system-map`, `/audit-fleet`); the skill handles refresh + hub diff + Telegram itself. (Alternatively a scheduled cloud agent invoking the same skill.)
