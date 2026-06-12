---
name: insights-weekly
description: Refresh Claude Code's built-in /insights report, then diff the newest report HTML against the previous one via the local LLM hub to produce a concise, personal "what changed this week" note — saved as a dated, traceable file under ~/.claude/usage-data/weekly/ and posted to Slack. The /insights command already writes a dated report-<timestamp>.html on every run, so that series is the history; this skill compares newest-vs-previous (no raw-JSON re-aggregation) and degrades to a baseline on the first run. Use when the user wants to see how their Claude Code usage is changing week-over-week — e.g. "/insights-weekly", "what changed in my insights this week", "weekly insights diff". Built to also run unattended on a weekly schedule.
---

# insights-weekly

**Goal:** Turn Claude Code's built-in **`/insights`** report into a weekly "what changed" signal. `/insights` already does the hard analysis and writes a self-contained, dated `report-<timestamp>.html`; that timestamped series **is** the history. This skill refreshes it, hands the **newest two reports to the local LLM hub** to narrate the week-over-week delta, saves a dated traceable note, and drops a concise digest in Slack — on-demand or scheduled.

**The hub does the analysis, not the orchestrator.** We do **not** re-aggregate the 200 raw `session-meta`/`facets` JSON files (insights already distilled them into the HTML), and the orchestrating session does **not** write the narrative itself — `report.py` delegates the comparison to `127.0.0.1:8000`. The whole artifact is **user-local and never committed** (`~/.claude/usage-data/` is outside this repo), so there is nothing to add to the repo or `.gitignore`.

## Execution rules (read first)

- **Run from the `claude-config` repo root** (`E:/automation/claude-config`) so the helper paths resolve.
- **Never commit insights data or reports.** The output lands under `~/.claude/usage-data/weekly/`, entirely outside the repo. Don't copy it in.
- **The model is the hub's job.** `report.py` POSTs to the hub via stdlib `urllib` (zero-install) — never re-implement a `claude -p` wrapper. Default model `claude_sonnet` (reliably up via the hub's claude backend); override with `INSIGHTS_DIFF_MODEL` (e.g. `gemma4_26b`, `qwen3.5-4b`, `gemini_flash`) when that backend is loaded.
- **Degrade gracefully, never block on a prompt** (this runs unattended): first run with one report → baseline, not a diff; hub unreachable → surface the error and skip Slack rather than hang.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Refresh the insights report

```
claude -p "/insights" --permission-mode bypassPermissions
```

`/insights` writes a fresh `report-<timestamp>.html` into `~/.claude/usage-data/`. If it can't refresh headlessly (no new file appears), proceed with the latest existing `report-*.html` — `report.py` always uses the two newest on disk. (On the scheduled job this whole skill is itself a `claude -p` run, so this is a normal nested invocation; on an interactive on-demand run it spawns a short child `claude`.)

### 2. Diff the two newest reports via the hub

```
py skills/insights-weekly/report.py
```

It finds the newest two `report-*.html`, strips each to clean text (`extract.py`), asks the hub to narrate the week-over-week delta, and writes `~/.claude/usage-data/weekly/insights-diff-<YYYY-MM-DD>.md`. It prints **the dated file path on line 1**, a blank line, then the **`TL;DR` digest** — capture both. On the first run (only one report) it writes a **baseline** instead and says so. Exit 3 means the hub call failed (model/backend down): report it, skip step 3, stop.

### 3. Post the digest to Slack

Resolve the channel from `hooks/projects.toml` `[global] slack_notify_channel`, then post the digest **as the caption of the dated report file** — so the phone push shows the at-a-glance summary *and* the full markdown is attached to open on mobile. Pass the **absolute** report path `report.py` printed on line 1 to `--file`, and pipe the digest body via stdin (it carries emoji / em-dash / bullet cleanly — `slack_notify` decodes stdin as UTF-8):

```
cat <<'EOF' | py hooks/slack_notify.py --channel <slack_notify_channel> \
   --file <absolute insights-diff-YYYY-MM-DD.md path from report.py line 1> \
   --title "Claude Code Insights — weekly <diff|baseline> <YYYY-MM-DD>"
🧠 Weekly Claude Code insights — <diff|baseline> <YYYY-MM-DD>

<the TL;DR digest>
EOF
```

Keep the caption tight — the digest is the phone-readable summary; the attached `.md` is the full read. The helper never raises; a missing token just logs and exits non-zero.

### 4. Report

Print: which two reports were compared (or "baseline"), the dated file path, the model used, and the Slack result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly — **target the first run for a Friday**:

```
claude -p "/insights-weekly" --permission-mode bypassPermissions
```

cwd = `E:/automation/claude-config`. Same executor as every other scheduled job (`/system-map`, `/audit-fleet`); the skill handles refresh + hub diff + Slack itself. (Alternatively a scheduled cloud agent invoking the same line.)
