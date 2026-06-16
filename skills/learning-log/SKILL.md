---
name: learning-log
description: Weekly learning log + forward horizon + productivity stats distilled from the fleet's GitHub work stream (merged PRs and closed issues, no source code). Use when the user wants the week's learning journey and productivity distilled — e.g. "/learning-log", "weekly learning log", "what did we ship and learn this week". Also runs unattended on a weekly schedule.
---

# learning-log

**Goal:** Surface the *learning journey* and *productivity shape* otherwise buried inside individual PRs and issues. Once a week, read the **work stream itself** — every merged PR and closed issue across the `ferraroroberto` fleet since the last run — then (a) compute **exact productivity tables** (PRs / issues / LOC, by project and by work-type) and (b) fan out **one Sonnet sub-agent per work-type bucket** to *extract insights* (patterns, recurring root-causes, decisions, durable lessons). Aggregate into a themed log, **grade last week's horizon**, and set the next one.

**The journey + productivity lens, not the others.** Reads **no source code** (that's `/audit-fleet`); does not regenerate the architecture PNG (that's `/system-map` — only cross-links it); is not Claude Code usage metrics (that's `/insights-weekly`). Its only input is GitHub: merged PRs + closed issues.

**Scatter-gather, like `/audit-fleet`.** A deterministic Python helper (`gather.py`) does the GitHub gather + the exact stats + the per-bucket partition; the orchestrator fans out **Sonnet** sub-agents (one per bucket — Sonnet is exempt from the Opus concurrency cap, so they run in parallel), each returning a **fixed format** so the aggregate is uniform. The orchestrator never reads source; it weaves the bucket insights, grades the horizon, and assembles the digest.

**Designed for unattended runs.** A weekly app-launcher job invokes this via `claude -p "/learning-log" --model claude-sonnet-4-6 --permission-mode bypassPermissions`. Every step degrades gracefully rather than block on a prompt.

## Arguments

- No argument → auto window (since the ledger's `last-run-at`, or trailing 7 days on the first run).
- `since <YYYY-MM-DD>` → override the window start (first backfill / validation, e.g. `since 2026-05-01`).

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **Public repos only.** The digest and its stats are published in a public ledger issue, so `gather.py` lists with `--visibility public` — private-repo activity (and even repo names) is never gathered, counted, or narrated. A sub-agent must never cite a `repo#N` outside the public set it was handed.
- **Read-only on GitHub except three writes:** the `kind=learning` ledger issue (upsert), the weekly comment on it, and the Slack ping. Never edits source, commits, pushes, or restarts.
- **Stats are deterministic — never let the model invent numbers.** Every count and LOC figure comes from `gather.py` (Python over `gh` JSON), pasted verbatim. The sub-agents narrate *insight*, not statistics.
- **Sub-agents are Sonnet, fan out freely** (exempt from the 3-Opus cap). One per non-empty bucket. They are READ-ONLY analysts — they file nothing and change no state.
- **Degrade gracefully, never block** (unattended). A bucket sub-agent that errors is recorded as such and skipped; the run still produces a log. A quiet week (no PRs/issues) still records the run so the ledger keeps cadence.
- **No AI attribution; no hard-wrapped paragraphs** (global `CLAUDE.md`).

## Steps

### 1. Gather + stat the work stream

```
py skills/learning-log/gather.py gather              # scheduled: auto window
py skills/learning-log/gather.py gather --since 2026-05-01   # override (backfill/validation)
```

It lists every fleet repo (`gh repo list`), reads each repo's merged PRs + closed issues **per repo** (`gh pr list` / `gh issue list` — REST, so the full window is covered with no cap and no search rate-limit), buckets each item by work type, computes exact stats, and writes into `<OUT_DIR>`: `stats.md` (the productivity tables), `prior-horizon.md`, and one `bucket-<slug>.md` per non-empty bucket. It prints a **manifest** — capture every line:

- `SINCE=` / `TOTALS=` — window start and grand totals.
- `STATS_FILE=` — the productivity tables (paste verbatim into the digest).
- `PRIOR_HORIZON_FILE=` — last week's horizon (grade against it).
- `OUT_DIR=` and one `BUCKET=<slug>|<name>|prs=N|issues=M|file=<path>` per non-empty bucket — dispatch one sub-agent per line.

### 2. Scatter — one Sonnet sub-agent per bucket

For each `BUCKET=` line, dispatch a **background `Agent`** (`subagent_type: general-purpose`, `model: sonnet`) — all in parallel (Sonnet is exempt from the Opus cap). Each reads only its `bucket-<slug>.md` and EXTRACTS INSIGHTS in this exact format (so the aggregate is uniform):

```
### <Bucket name>
**Themes:** 2-4 short labels.
**Insights & learnings:**
- <durable, non-obvious lesson or recurring pattern — not a restatement of one PR title> (repo#N)
- … (3-6 bullets)
**Notable:**
- <1-3 most significant items and why> (repo#N)
**Focus signal:** <one line — what this bucket says about where effort/attention went>
```

Tell each agent: read-only (file nothing, change no state); cite evidence as `repo#N`; be concrete to THIS fleet. Collect each report as it returns.

### 3. Aggregate into the digest

Compose the weekly digest as markdown (single long lines, no hard wraps). Order:

- `# 📓 Weekly learning log — <SINCE> → <today>` + a one-line subtitle with the grand totals.
- `## TL;DR` — 3-5 phone bullets synthesizing the biggest cross-bucket signals.
- `## What shipped & what we learned` — the per-bucket sections from the sub-agents, in `BUCKETS` order, verbatim (lightly normalized).
- `## Discoveries to archive` — 4-8 durable, dated-worthy bullets pulled from across the buckets, each tagged `repo#N`.
- `## Horizon grading` — grade ONLY the items in `prior-horizon.md` (shipped / slipped) + what emerged UNPLANNED. If it says first run, write `First run — baseline, no prior horizon to grade.`
- `## Horizon → next week` — 4-8 forward checkboxes inferred from open threads + direction of travel.
- The contents of `STATS_FILE` pasted verbatim (the productivity tables).
- A one-line cross-link to the current `architecture/system-map.png` (milestones crossed).

### 4. Assemble the ledger body + upsert

Write the new horizon bullets to `horizon.md` and the discovery bullets to `discoveries.md` (in `OUT_DIR`), then let Python preserve the durable archive + stamp `last-run-at`. `build_ledger_body` also renders a fixed **Fleet map** link near the top of the body — the `architecture/system-map.png` produced by `/system-map` (cross-linked, never regenerated here):

```
py skills/learning-log/gather.py assemble-ledger \
  --repo ferraroroberto/fleet-config \
  --horizon-file <OUT_DIR>/horizon.md --discoveries-file <OUT_DIR>/discoveries.md \
  --out <OUT_DIR>/ledger-body.md
```

Then upsert the one canonical `kind=learning` ledger (deduped by `skills/_lib/audit_issue.py`; title `learning log — fleet`; label `audit-meta` so `/issue-triage` filters it out):

```
py skills/_lib/audit_issue.py upsert --repo ferraroroberto/fleet-config \
  --kind learning --label audit-meta --title "learning log — fleet" --body-file <OUT_DIR>/ledger-body.md
```

Capture `LEDGER_URL`. Post the digest as a comment (the running week-by-week log):

```
COMMENT_URL=$(gh issue comment "<LEDGER_URL>" --repo ferraroroberto/fleet-config --body-file <digest file>)
```

### 5. Slack completion ping

```
py hooks/notify_complete.py --kind learning --comment-url "<COMMENT_URL>" \
  --summary "<N> PRs / <M> issues across <K> repos · <one-line horizon grade>"
```

`📓 Learning log` — opt-in (silent no-op if no `[global] slack_notify_channel`), never blocks. Omit `--comment-url` if the comment was skipped.

### 6. Report

A few lines: window, grand totals, buckets analysed (+ any agent that errored), ledger + comment URLs, Slack result.

## Notes

- **Why deterministic stats + LLM insight (not LLM stats):** counts and LOC must be exact and reproducible, so Python computes them from `gh` JSON; the Sonnet agents do the *judgement* (patterns, lessons) that a table can't capture. GitHub gives per-repo Pulse/contributor stats but nothing **cross-fleet** or **per-work-type**, so these tables are additive, not a duplicate.
- **Why a ledger issue, not `docs/`:** the global `CLAUDE.md` rule — durable knowledge lives in *one canonical issue with a dated decision log*. The issue body is the deduped durable archive + live horizon; its comments are the week-by-week record (narrative + tables).
- **Why anchor the window to `last-run-at`:** a missed/late run never drops a week — the next run widens. First run with no ledger falls back to trailing 7 days.
- **Buckets** are by work type — PRs by conventional-commit prefix (`feat`/`fix`/`chore`/`docs`/`refactor`/…), issues by type label. Items with neither land in **Other** (a known limitation; tighten with keyword heuristics in `gather.py` if it grows).
- **Separate from `/system-map`** (cross-linked, not modified) and from fleet-config#95.

## Wiring the weekly schedule

An app-launcher Job (`config/jobs.json`, weekly, `visible: true`) calls `skills/learning-log/run-weekly.bat`, staggered clear of the other Friday claude-runs:

```
claude -p "/learning-log" --model claude-sonnet-4-6 --permission-mode bypassPermissions
```

cwd = `E:/automation/fleet-config`. The Sonnet orchestrator gathers, fans out the Sonnet bucket sub-agents, aggregates, and writes the ledger + comment + Slack itself.
