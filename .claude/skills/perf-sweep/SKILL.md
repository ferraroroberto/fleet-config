---
name: perf-sweep
description: Weekly phone-speed check of every fleet web app, one at a time — runs /perf-review's measure and file for each, then prints one digest of what is over budget and what got slower since the last run. Never restarts an app. E.g. "/perf-sweep", "/perf-sweep home-automation", "which apps got slower this week". Also runs weekly unattended.
---

# perf-sweep

**Capability preflight:** read [workflow-capabilities](../../../docs/workflow-capabilities.md) and bind waits and results to this session's actual tools before proceeding. Tool names below are conditional examples; the contract governs adaptation.

**Goal:** the fleet-wide, unattended half of `/perf-review` (`fleet-config#1686`). One deterministic command measures every web app declared in `hooks/projects.toml`, files each app's managed `perf-review` issue and prints one digest. This skill starts it, waits for it and relays the digest. It assigns no status and ranks no fix: the numbers, the budgets and the `DIFF` against the last run all come from `skills/_lib/perf_review/`.

## Arguments

- No argument → every web app in scope (a `webapp_port` in `hooks/projects.toml`, not dormant).
- A bare repo name → just that app, even when dormant.

## Rules

- **Read-only on the apps.** The sweep only loads pages and sends GETs. It never starts, restarts or kills anything. A down port is `unmeasured`, never a pass; restarting it is the owner's call.
- **One app at a time, and only on a quiet box.** Each app is timed under an exclusive `box_lock` (no lane gate runs meanwhile). An app whose lock does not free within the wait is `unmeasured (BOX_BUSY)`.
- **Never end the turn while the sweep runs.** It takes about 7 minutes per app and nothing wakes a session that ends its turn (`fleet-config#314`). Start it in the background and poll to completion inside this turn.
- **Public-safe output.** The digest holds repo names, check ids and numbers only: no hostnames, no page text, no screenshots.

## Steps

### 1. Pre-flight

`gh auth status` must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."

### 2. Run the sweep

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/perf_review sweep [--only <repo>]
```

Start it with `run_in_background`, then block on the `Monitor` tool with an until-loop on the output file for its final `SWEEP=` line (`SWEEP=done ...` or `SWEEP=refused ...`). Print each `[i/N]` progress line as it lands. If the loop times out, extend it: the process is still measuring. Only a process that has exited without a `SWEEP=` line is a failure; report it with the last output lines.

Exit codes: 0 at least one app measured, 3 none measured (the digest says why), 2 refused (no web app in scope, or the fleet list is missing).

### 3. Relay the digest

Print the digest markdown verbatim (it sits between the progress lines and `DIGEST_FILE=`; the same text is on disk at that path). Then one line: `SWEEP=done measured=N unmeasured=M` and the issues filed or refreshed.

Call out, in one sentence each: apps with a regression or a slower p95 since the last run, apps over budget, apps `unmeasured` and why (a restart recipe, if the repo's `CLAUDE.md` has one, goes in the line as a suggestion for the owner), and apps serving an older build than their checkout.

## Notes

- **The per-app engine is `/perf-review`** (global `skills/`); this is its fleet-wide orchestrator, in the fleet-only `.claude/skills/` tier like `/design-sweep`.
- **The weekly job** lives in app-launcher (`config/jobs.json`) and calls this folder's `run-weekly.bat`. Schedule it at a quiet hour away from the other weekly jobs.
