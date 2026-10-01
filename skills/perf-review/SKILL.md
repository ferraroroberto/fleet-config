---
name: perf-review
description: Measure how fast a running fleet web app loads on a phone — a throttled phone-profile load (cold and warm) plus GET-only endpoint timing at the app's own poll spacing — against versioned budgets, then rank fixes from the app-launcher playbook. Read-only; never restarts the app. E.g. "/perf-review home-automation", "/perf-review home-automation file", "why is the app slow on my phone".
---

# perf-review

**Goal:** Turn "the app feels slow on my phone" into numbers against a
standard, and a short list of fixes ranked by gain per line of code. The
standard is `skills/_lib/perf_review/budgets.toml`; the fixes are the
patterns in [`playbook.md`](playbook.md), each with the app-launcher evidence
that earned it a place (fleet-config#1121).

**Measure with the helper, never by eye.** Every number and status comes
from `skills/_lib/perf_review/` (deterministic, unit-tested). This skill runs
it, reads the verdict, and maps each over-budget check to a playbook pattern.
It assigns no status of its own.

**Read-only, always.**
- The probe only sends GETs, each path no more often than the app itself
  polls it (floor 5 s).
- The page load navigates and waits. It never clicks or types.
- It never starts, restarts or kills the app. A dead port is reported as
  `unmeasured`, never fixed.
- Run output stays local, under `~/.claude/hooks/state/perf-review/<repo>/`.
- An issue gets numbers and URL paths only. Never a hostname, an IP,
  captured page text or a screenshot.

## Arguments

- A repo name (a `hooks/projects.toml` table with a `webapp_port`) or a path
  → the target. No argument → the current repo.
- `file` → after measuring, upsert the repo's one managed `perf-review`
  issue (step 4). Without it, step 4 is a dry run.

## Steps

Every command is this repo's venv Python on the package directory:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/perf_review <cmd> ...
```

### 1. Measure

```
... perf_review measure <repo>
```

Takes about 6 minutes: ~1 minute of page loads, then 5 minutes of endpoint
timing (`--duration` to change). Run it in the foreground, or poll it to
completion inside your own turn. Nothing wakes a session that ends its turn
waiting.

- `PERF=unmeasured reason=NOT_LISTENING|TIMEOUT` → **stop**. Quote the
  target `CLAUDE.md`'s restart recipe as the next action for the user. Never
  run it yourself.
- `PERF=unmeasured reason=BAD_TARGET|BAD_FLEET_TOML` → stop and print it.
- Otherwise it prints one `CHECK` line per budget, one `ENDPOINT` line per
  timed path, a `DIFF` line against the previous run, and `PERF=pass|over-budget|unmeasured`.
  Exit is 0, 1 or 3 respectively. The run dir (`RUN_DIR=`) holds
  `load.json`, `probe.json`, `verdict.json` and a preview `issue-body.md`.

What it measures:

- **Load leg** (Playwright, under the target's own `.venv`):
  - iPhone (WebKit) cold. It is unthrottled, so it is reported, not scored.
  - Android (Chromium) cold and warm, throttled to the phone profile.
    "Warm" is a PWA relaunch: HTTP cache and `localStorage` kept.
  - "Ready" is the target's declared `ready_selector` being visible, else
    first contentful paint.
  - "Boot data" is when the last `/api/` response of the boot landed.
- **HTTP leg** (stdlib):
  - `/` plus every query-less `/api/` GET the boot made, each at the poll
    interval the page was seen using.
  - Whether `/` is compressed and answers a repeat with 304.
- A warm leg whose cache could not be trusted is marked `unmeasured`, never
  scored. That happens when the app's cert does not verify for any name the
  probe can map to loopback.

### 2. Rank the fixes

For each `fail`, name the playbook pattern (`verdict.json` and the seed
`## Fixes` list in `issue-body.md` carry the mapping). Then rank by gain per
LOC, cheapest first.

- **Check the target's code before proposing a pattern.** It may already be
  in place. home-automation already had P5–P7 when it was piloted.
- **Prefer one fix per issue/PR**, so each reverts on its own.

### 3. Report

Summarize to the user:

- the verdict line and the over-budget checks, with measured vs budget;
- the slowest endpoints;
- the ranked fixes with their pattern ids and rough LOC;
- what changed since the last run (`DIFF`).

Then stop. Each fix ships through the target repo's own issue → branch → PR
→ gate; this skill never edits the target.

### 4. File (`file` only)

```
... perf_review file <repo>            # dry run: prints the plan and the body
... perf_review file <repo> --apply    # upsert the managed issue
```

There is one managed issue per repo, titled `perf-review: phone-load findings`
with label `perf-review`, and its body has three parts:

- **Budget and endpoint tables:** regenerated on every run.
- **`## Fixes` checklist:** seeded from the failing checks on the first
  filing, then kept verbatim. Link each per-fix issue there.
- **`## Run log`:** gets one line per run.

## Per-app configuration (optional)

Declare any of these in the target's `.fleet.toml` under `[perf.review]`:

- `ready_selector`: what "ready" means for this app.
- `exclude`: path prefixes never timed, such as calls that reach an outside
  service.
- `endpoints`: extra paths to time.
- `cadence`: seconds between two GETs of one path.
- `budgets`: any `budgets.toml` key.

The full schema is in `skills/_lib/perf_review/__init__.py`.
