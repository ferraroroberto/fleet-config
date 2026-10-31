---
name: perf-sweep
description: Weekly phone-speed check of every fleet web app, one at a time — runs /perf-review's measure and file for each, then prints one digest of what is over budget and what got slower since the last run. Never restarts an app. E.g. "/perf-sweep", "/perf-sweep home-automation", "which apps got slower this week". Also runs weekly unattended.
---

# perf-sweep

**Capability preflight:** read [workflow-capabilities](E:/automation/fleet-config/docs/workflow-capabilities.md) and bind waits and results to this session's actual tools before proceeding. Tool names below are conditional examples; the contract governs adaptation.

**Goal:** the fleet-wide, unattended half of `/perf-review` (`fleet-config#1686`). One deterministic command measures every web app declared in `hooks/projects.toml`, files each app's managed `perf-review` issue and prints one digest. This skill starts it, waits for it and relays the digest. It assigns no status and ranks no fix: the numbers, the budgets and the `DIFF` against the last run all come from `skills/_lib/perf_review/`.

## Arguments

- No argument → every web app in scope (a `webapp_port` in `hooks/projects.toml`, or a `working-web` repo's own `.fleet.toml` `port`; not dormant). The digest's `## Left out` section names every live repo that looks like a web app but has no port to measure, with the reason.
- A bare repo name → just that app, even when dormant.

## Rules

- **Read-only on the apps.** The sweep only loads pages and sends GETs. It never starts, restarts or kills anything. A down port is `unmeasured`, never a pass; restarting it is the owner's call.
- **One app at a time, and only on a quiet box.** Each app is timed under an exclusive `box_lock` (no lane gate runs meanwhile). An app whose lock does not free within the wait is `unmeasured (BOX_BUSY)`.
- **Never end the turn while the sweep runs.** It takes about 7 minutes per app and nothing wakes a session that ends its turn (`fleet-config#314`). Launch it detached and block on the sentinel file with `wait_for_sentinel.py`, re-invoking on an unfinished result — never a model-composed `Monitor` wait (`fleet-config#609`, `fleet-config#1558`: one such wait returned without blocking and ended a headless turn on a notification that never came).
- **Public-safe output.** The digest holds repo names, check ids and numbers only: no hostnames, no page text, no screenshots.

## Steps

### 1. Pre-flight

`gh auth status` must be authenticated as `ferraroroberto`. Else stop: "Not authenticated — run `gh auth login`."

### 2. Run the sweep — launched detached, polled to completion

A full sweep (about 7 minutes per app) regularly runs past the Bash tool's 600s ceiling, so this step launches the
sweep **detached** and polls a **sentinel file** — concrete, externally observable — instead of an opaque
background task, the same fix `/audit-fleet` step 2 applies for the identical reason (`fleet-config#609`).

**Launch** (returns in well under a second):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/perf_review sweep [--only <repo>] --detach
```

Prints exactly one line: `LAUNCHED pid=<pid> out=<result-path> log=<log-path>`. Capture `out=` verbatim as
`SWEEP_RESULT` — never construct/guess the path; a fresh unique path is generated per invocation, so overlapping
runs never collide. The detached child is the same `cmd_sweep`, spawned `CREATE_NEW_PROCESS_GROUP | NO_WINDOW` so it
outlives this tool call, and publishes `{"rc": ..., "digest_file": ...}` (or `{"error": ...}` if it raises) to
`SWEEP_RESULT` atomically on completion.

**Wait** with the foreground blocking helper — never `Monitor`, never a raw backgrounded Bash call:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/scheduled/wait_for_sentinel.py --path "$SWEEP_RESULT" --timeout-seconds 560
```

**Exit 0** (`SENTINEL-READY <path>`) — sweep done: read the JSON at `SWEEP_RESULT`. **Exit 2**
(`SENTINEL-NOT-READY <path>`) — not a wait-for-notification signal; re-invoke the exact same command again
immediately, same turn. `--timeout-seconds 560` keeps a call under the Bash tool's 600s ceiling — the retry is
normal, not a failure. **Cap retries at 26** (~4 hours). If `SWEEP_RESULT` still absent after 26 exit-2 results, or
carries `{"error": ...}`: report "Perf sweep — did not complete: <reason>" and stop; never fabricate a digest from
an unfinished sweep. **Never end this turn on an exit-2 result** — only two valid stops: resolved exit 0, or
exhausted retry cap.

Once read, `SWEEP_RESULT`'s `rc` is the sweep's own exit code: 0 at least one app measured, 3 none measured (the
digest says why), 2 refused (no web app in scope, or the fleet list is missing). `digest_file` is the markdown path
for step 3.

### 3. Relay the digest

`cat` the `digest_file` path from step 2's `SWEEP_RESULT` and print it verbatim. Then one line: `SWEEP=done
measured=N unmeasured=M` and the issues filed or refreshed (both already on disk at the end of the digest text).

Call out, in one sentence each: apps with a regression or a slower p95 since the last run, apps over budget, apps `unmeasured` and why (a restart recipe, if the repo's `CLAUDE.md` has one, goes in the line as a suggestion for the owner), and apps serving an older build than their checkout.

## Notes

- **The per-app engine is `/perf-review`** (global `skills/`); this is its fleet-wide orchestrator, in the fleet-only `.claude/skills/` tier like `/design-sweep`.
- **The weekly job** lives in app-launcher (`config/jobs.json`) and calls this folder's `run-weekly.bat`. Schedule it at a quiet hour away from the other weekly jobs.
