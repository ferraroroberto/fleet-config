---
name: learning-log
description: Turn the fleet's GitHub work stream into a weekly learning log + forward horizon — gather the merged PRs and closed issues across every ferraroroberto repo since the last run, hand them to the local LLM hub to narrate what shipped, what was learned, and what's worth archiving, grade last week's horizon (shipped / slipped / emerged-unplanned) and set the next one, then keep it all in one canonical learning-log ledger issue (durable archive in the body, week-by-week narrative in the comments) and fire a Slack completion ping. Reads NO source code — purely the GitHub work stream. Use when the user wants the week's learning journey distilled — e.g. "/learning-log", "weekly learning log", "what did we learn and ship this week". Built to also run unattended on a weekly schedule.
---

# learning-log

**Goal:** Surface the *learning journey* that's otherwise buried inside individual issues and PRs. Once a week, read the **work stream itself** — every merged PR and closed issue across the `ferraroroberto` fleet since the last run — and let the local hub narrate *what shipped, what we learned, what to remember,* and *what's next*. Keep a backward/forward retrospective loop: each run **grades last week's horizon** against what actually shipped (the unplanned usually wins), then writes the next horizon.

**This is the journey lens, not the others.** It deliberately does **not** touch source code (that's `/audit-fleet`), does not regenerate the architecture picture (that's `/system-map` — this only *cross-links* it), and does not read Claude Code usage metrics (that's `/insights-weekly`). Its only input is GitHub: merged PRs + closed issues.

**The hub does the analysis, not the orchestrator.** `report.py` gathers via `gh search` and POSTs to `127.0.0.1:8000`; the orchestrating session does not write the narrative itself (mirrors `/insights-weekly`). The orchestrator's only writes are the **ledger upsert**, the **digest comment**, and the **Slack ping**.

**Designed for unattended runs.** A weekly app-launcher job invokes this via `claude -p "/learning-log" --permission-mode bypassPermissions`. Every step degrades gracefully rather than block on a prompt.

## Arguments

- No argument → auto window (since the ledger's `last-run-at`, or trailing 7 days on the first run).
- `since <YYYY-MM-DD>` → override the window start (used for the first backfill / validation run, e.g. `since 2026-05-01`).

## Execution rules (read first)

- **Run from the `claude-config` repo root** (`E:/automation/claude-config`) so the helper paths resolve.
- **Read-only on GitHub except three writes:** the `kind=learning` ledger issue (upsert), the digest comment on it, and the Slack ping. Never edits source, commits, pushes, or restarts anything.
- **The model is the hub's job.** `report.py` POSTs via stdlib `urllib` — never a `claude -p` wrapper. Default model `gemini_flash` (reliably up via the hub and fast on the ~5k-token rollup; `claude_sonnet` hangs on inputs this size and the local llama backends 502 when cold). Override with `LEARNING_LOG_MODEL` (e.g. `gemma4_26b` for a fully-local run when you keep that backend warm). The input is GitHub-public PR/issue titles, not secrets.
- **Degrade gracefully, never block** (this runs unattended): hub unreachable → `report.py` exits 3, so surface the error and skip the comment/ping rather than hang. A quiet week (no PRs/issues) still records the run so the ledger keeps cadence.
- **No AI attribution; no hard-wrapped digest paragraphs** (per global `CLAUDE.md`).

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Narrate the week via the hub

```
py skills/learning-log/report.py            # scheduled: auto window
py skills/learning-log/report.py --since 2026-05-01   # override window (backfill/validation)
```

It reads the `kind=learning` ledger (for `last-run-at` + last week's horizon), gathers the window's merged PRs + closed issues across the fleet via `gh search`, asks the hub to narrate + grade, and writes two files. It prints `KEY=VALUE` lines then a blank line then the TL;DR — capture:

- `DIGEST_FILE=` — the weekly narrative (the comment body to post).
- `LEDGER_BODY_FILE=` — the updated ledger body (state stamp + next horizon + grown archive).
- `SINCE=` / `ITEMS=` — window start and the `<N> PRs / <M> issues` count (for the ping summary).

Exit 3 means the hub call failed (backend down): report it and stop — no comment, no ping.

### 2. Upsert the ledger + post the weekly narrative as a comment

One canonical issue per the `kind=learning` marker, deduped by `skills/_lib/audit_issue.py` (same machinery as the audit ledgers). Title is the stable `learning log — fleet`; label `audit-meta` so `/issue-triage` filters it out of actionable work.

```
py skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/claude-config --kind learning --label audit-meta \
  --title "learning log — fleet" --body-file <LEDGER_BODY_FILE>
```

Capture the printed URL as `LEDGER_URL`. Then post this week's narrative as a comment — the running week-by-week log:

```
COMMENT_URL=$(gh issue comment "<LEDGER_URL>" --repo ferraroroberto/claude-config --body-file <DIGEST_FILE>)
```

`gh issue comment` prints the comment permalink on stdout. If the upsert or comment fails, note it and carry on to whatever did succeed — never fail the whole run over a single GitHub write.

### 3. Fire the Slack completion ping

Deterministic, like every other skill — hand `notify_complete.py` the comment URL and a one-line summary; it builds the canonical `📓 Learning log` message and posts it (single-sourced mention decision, opt-in via `[global] slack_notify_channel`):

```
py hooks/notify_complete.py --kind learning \
  --comment-url "<COMMENT_URL>" \
  --summary "<ITEMS> distilled · <one-line horizon grade>"
```

For `<one-line horizon grade>` read the `## Horizon grading` line from `DIGEST_FILE` and compress it (e.g. `2/3 horizon shipped, 1 unplanned`); on the first run say `baseline`. If `COMMENT_URL` is empty (comment was skipped), omit `--comment-url` so the ping still goes out link-less. This call is a silent no-op when no channel is configured; it always exits 0 and can never block the finish.

### 4. Report

Print a few lines: the window (`SINCE → today`), `ITEMS`, the ledger URL, the comment URL (or skipped reason), and the Slack result. Stop.

## Notes

- **Why a ledger issue, not a `docs/` file:** the global `CLAUDE.md` rule — durable knowledge lives in *one canonical issue with a dated decision log*, never a per-run dated `docs/` retrospective. The learning archive *is* that pattern: the issue body is the deduped durable memory, its comments are the week-by-week record.
- **Why anchor the window to `last-run-at`, not a fixed 7 days:** a missed or late scheduled run never drops a week — the next run simply covers the wider span. The ledger stamps `last-run-at` every run; the first run with no ledger falls back to trailing 7 days.
- **Separate from `/system-map`:** the architecture PNG stays a clean "what exists" poster; this log cross-links it and calls out milestones crossed. (Distinct from claude-config#95, which adds live status to the map itself.)
- **The weekly job** that schedules this is an app-launcher Job (`config/jobs.json`, a `weekly` schedule, `visible: true`) calling the thin `skills/learning-log/run-weekly.bat` wrapper in this repo. See that repo for the trigger; this skill is the work.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly — **staggered clear of the other Friday claude-runs** (audit-fleet 00:00, system-map 01:00, insights 01:30):

```
claude -p "/learning-log" --permission-mode bypassPermissions
```

cwd = `E:/automation/claude-config`. Same executor as every other scheduled job; the skill handles gather + hub narration + ledger + comment + Slack itself.
