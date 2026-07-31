---
name: sota-watch
description: Recurring state-of-the-art watch over the fleet's adopted tooling, model, and practice choices — walk the watchlist, deep-research due areas, relay the local-llm-hub frontier ledger, and report only changes (challenger vs no-change) to the sota-watch ledger issue + Slack. Use when the user wants the sweep — e.g. "/sota-watch", "is our tooling still state of the art", "check if something better shipped". Also runs unattended on a weekly schedule.
---

# sota-watch

**Goal:** Notice when the *next* #219 is due. Each prior evaluation spike (token reduction, model tiers, MCP surface) reached a verdict and went silent; the market did not. This skill walks `watchlist.toml` — each area's adopted choice, recorded verdict, and the disqualifiers that would have to change — re-researches only the areas whose per-area cadence has elapsed, and reports **only changes**: one line for "no change", a short evidenced block when a challenger appears to clear the recorded disqualifiers.

**Advisory, never auto-adopting.** The digest may *draft* a spike-issue outline for a challenger, but this skill never creates the spike, never edits the watchlist verdicts, and never adopts anything — the user files the evaluation spike from the digest (the #219 pattern: measure, trust-gate, verdict).

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`).
- **Never commit anything.** Run state lives in `~/.claude/sota-watch/state.json` (outside the repo); the ledger lives in a GitHub issue. Watchlist *verdicts* change only via a human-reviewed PR after a spike concludes — never during a run.
- **Poll synchronously; never background-and-wait** (fleet-config#314): a scheduled headless `claude -p` that ends its turn expecting a resume exits `0` having done nothing. Any background work the research step spawns must be polled to completion inside the same turn.
- **Partial failure degrades one area, never the run.** A failed research pass or unreachable delegate becomes a "not checked — <reason>" line in the digest; the other areas still complete and the run still reports.
- **Degrade gracefully, never block on a prompt** (this runs unattended).

## Steps

### 1. Compute the work-list

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/sota-watch/watchlist.py due
```

One `AREA=` line per area — `due` (research now), `fresh` (report "next due <date>" in one line, nothing else), or `delegated` — plus a `DUE=` total. A typical weekly run has 0–2 due areas; a run where everything is fresh still posts the digest (staleness relay + next-due table is the product, not a failure).

### 2. Relay delegated areas

For each `status=delegated` area (currently `local-model-frontier` → [local-llm-hub#272](https://github.com/ferraroroberto/local-llm-hub/issues/272)):

```
gh issue view 272 --repo ferraroroberto/local-llm-hub --comments
```

Read the **latest** comment — by that ledger's own contract it is the current state of the local-model frontier. Extract the run date and the per-role verdict table. Report: the verdict table relayed in one compact block, plus a **staleness flag** if the latest run is older than the area's `stale_after` days (the delegate's own cadence has slipped — that is a finding about the *process*, not the models). Never re-research a delegated area here; local-llm-hub's `/frontier-refresh` owns it.

### 3. Research each due area

One area at a time, **sequentially** — never fan out research agents in parallel from an unattended run (Opus burst cap + #314). Per area, invoke the **`deep-research` skill** with a question built from the watchlist entry:

> Area: `<name>`. The fleet's adopted choice is `<adopted>` (verdict: `<verdict>`, `<verdict_date>`, see `<links>`). Has anything shipped or materially changed since `<verdict_date>` that clears these recorded disqualifiers: `<disqualifiers>`? Evidence over marketing claims; a "no change" conclusion is expected and valid.

If deep-research launches background work, poll it to completion in-turn (`TaskOutput`/`Monitor`) before moving on. **Fallback:** if the harness is unavailable or fails in the headless run, do 3–5 targeted `WebSearch`/`WebFetch` calls inline for that area instead — a bounded sweep only needs to *detect* a challenger; the filed spike does the real evaluation. Either way the area is checked; log which path ran.

### 4. Classify

Per checked area, exactly one of:

- **No change** — one line: `<area>: no change (checked <date>, next <date>)`. Expected and valid; do not pad it.
- **Challenger** — a short block: what shipped/changed (with dates + links), which recorded disqualifier(s) it appears to clear, what it does NOT clear, and a 3-line draft outline for the evaluation-spike issue the user would file (scope + how to verify + trust gate). Claims must be evidenced — a vendor README is a claim, not evidence.
- **Not checked** — one line with the reason (research failed, delegate unreachable).

### 5. Mark checked areas

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/sota-watch/watchlist.py mark --area <name>
```

Once per successfully checked area (research areas *and* the delegated relay). Skip areas that ended "not checked" — they stay due and retry next run.

### 6. Post to the ledger issue

The standing ledger is the audit-managed issue `kind=sota-watch` (title `sota-watch ledger`, label `audit-meta`) — resolve/create it deterministically, never by searching titles yourself:

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/audit_issue.py get --repo ferraroroberto/fleet-config --kind sota-watch
```

If `number` is null, `upsert` it once with a short standing body (what the ledger is, link to this skill + #393, `--label audit-meta`). Then post the run digest as a **comment**:

- run date + `DUE=` summary line
- per-area lines/blocks from step 4 (delegated relay included)
- one-line diff vs the previous run comment ("no change anywhere" is valid and expected)
- unwrapped markdown (rendered by GitHub/Slack — no hard wraps)

The last comment on the ledger = the current state of the watch, same contract as local-llm-hub#272.

### 7. Slack ping

```
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/slack_notify.py --category log --text "🔭 sota-watch - <date> - <N> due, <M> no-change, <K> challenger - <ledger comment URL>"
```

`slack_notify --text` has no separator token (a `--text` body carries markdown, where `|` is a table cell), so keep the *punctuation* ASCII here: a literal `·` in a Windows command line reached Slack as `??` (fleet-config#507). The leading emoji stays — it is the glanceable cue, and it is the punctuation that was observed to corrupt — but every separator is a hyphen.

Challenger found → one extra line naming the area. The helper never raises; a missing token logs and exits non-zero — report it, don't fail the run.

### 8. Report

Print: areas checked / fresh / not-checked, each verdict in one line, the ledger comment URL, the Slack result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) targeting `.claude/skills/sota-watch/run-weekly.bat`, cwd `E:/automation/fleet-config`, weekly overnight — same executor as every other scheduled job. Per-area cadence means most weekly runs are cheap (few or zero due areas).
