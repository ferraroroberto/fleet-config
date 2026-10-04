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

- A repo name (a `hooks/projects.toml` table) or a path → the target. Its port is the table's `webapp_port` and
  `browser_scheme`, else the `port` its own `.fleet.toml` declares with the scheme probed on that loopback port
  (facilitation-suite declares only the latter). No argument → the current repo.
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
- A short run (`--duration` too small for enough endpoint samples) leaves a
  check `unmeasured`, so the verdict is `unmeasured`, never `pass`. That is
  correct: a clean `pass` needs a full run.
- A `text/event-stream` endpoint is never timed (an SSE feed has no end): its `ENDPOINT` line reads `skipped (stream)`
  and it neither passes nor spoils the API check. Every request also has a 30 s deadline on the whole exchange; one
  that runs out reads `timeout` (`unmeasured`), so a stalled route cannot hang the run.
- Otherwise it prints one `CHECK` line per budget, one `ENDPOINT` line per
  timed path, a `DIFF` line against the previous run, a `BUILD` line and `PERF=pass|over-budget|unmeasured`.
  Exit is 0, 1 or 3 respectively. The run dir (`RUN_DIR=`) holds
  `load.json`, `probe.json`, `verdict.json` and a preview `issue-body.md`.
  `BUILD state=` compares the build the app serves (its `api_version_path` `git_sha`) with the checkout's HEAD: `live`, `behind` (`behind_by=N` commits), `differs` or `unknown` (no build id: never read as live). On `behind` or `differs` a `BUILD_WARNING` follows: a fix merged since is **not live in this run** (facilitation-suite#164: a session held the app, so no restart, and a re-run measured the old build). Say so first in the report, give the repo's own restart instruction, and never read the `DIFF` as "not fixed"; this skill restarts nothing.

What it measures:

- **Load leg** (Playwright, under the target's own `.venv`):
  - iPhone (WebKit) cold. It is unthrottled, so it is reported, not scored.
  - Android (Chromium) cold and warm, throttled to the phone profile.
    "Warm" is a PWA relaunch: HTTP cache and `localStorage` kept. Cold is
    taken three times, each in a fresh context, and scored by its median.
  - "Ready" is the target's declared `ready_selector` being visible, else
    first contentful paint.
  - "Boot data" is when the last `/api/` response of the boot landed. An API
    path is any path with an `/api/` segment, so an app mounted under
    `/admin/api/` is measured too (local-llm-hub#644: with a leading-`/api/`
    match, `warm.data_ms` and `endpoints.api_p95_ms` read unmeasured and the
    `SPLIT` line `api=0 KB`). A boot-data check still `unmeasured` is a
    measurement gap, never a pass.
- **HTTP leg** (stdlib):
  - `/` plus every query-less `/api/` GET the boot made, each at the poll
    interval the page was seen using.
  - Whether `/` is compressed and answers a repeat with 304.
- **Code leg** (stdlib, read-only): `cache.stamping` classifies the target's
  `src/static_versioning.py` as one fleet hash, a transitive graph hash, or
  an unsafe per-file hash (playbook P11). No such module, no check.
- A failing `cold.bytes_kb` or `warm.bytes_kb` check also lists the five
  largest responses (`TOP` lines: path without query, wire bytes, encoding,
  share of the transfer). The HTTP leg times only query-less endpoints, so
  without this a big API payload hides behind an assets guess. Read the `TOP`
  lines before ranking: one response at 70% of the transfer is P12, not P2/P9.
- A failing `warm.bytes_kb` also prints a `SPLIT` line: KB of `/api/` data
  re-fetched vs KB of other responses that missed the cache (assets, entry
  document). The 100 KB budget is reachable when the bytes are cached assets
  but not for an app whose boot legitimately re-fetches live data (task-os:
  256 KB warm, nearly all `/api/`). Mostly `/api/` is a payload problem
  (P12, P8); never propose a cache fix for it. Mostly other is a cache
  problem (P3, P11).
- Periodic polls are not boot traffic: a GET to an `/api/` path starting more
  than 1.0 s after the previous request to that path is left out of
  `requests`, `from_cache`, `bytes`, `api_bytes` and `TOP`, so the budgets and
  the `SPLIT` line read boot-only figures. A `POLLS` line (and a report
  sentence) names the `poll_requests` / `poll_bytes` left out, so nothing is
  hidden. Budgets are unchanged.
- A warm leg whose cache could not be trusted is marked `unmeasured`, never
  scored. That happens when the app's cert does not verify for any name the
  probe can map to loopback.

Reading the cold numbers (fleet-config#1139):

- A cold paint on a **round number** (3000, 4089) with the rest of the
  timeline shifted by the same amount is the harness, not the app. Record a
  Chromium net log and look for the gap and what it sits in front of before
  touching app code. The known cause, Windows proxy auto-detect, is fixed in
  `load.py` (`--proxy-server=direct://`; `--no-proxy-server` and
  `--proxy-bypass-list` do not take effect).
- The scored cold "ready" is the **median of three** fresh-context loads
  (`--cold-samples`). `CHECK cold.ready_ms` lists the samples and names an
  outlier (over twice the median and 500 ms above it); say so in the report.
  A lone slow first load after a restart is not a regression, but three slow
  loads are.
- The load profile is a **coarse-pointer phone**, so a desktop-first app can
  land on a different tab than a human sees on the PC (task-os: Today, not
  Board). Declare the selector for the tab the phone lands on.
- **A landing view with no data-dependent card** (a capture surface whose
  config and history fill other tabs) has nothing honest to wait for.
  Declare its **primary action**, the control the user needs to start the
  task (photo-ocr: the Add photo button, `ready.selector` visible). Say in
  the report that ready then barely moves from first contentful paint, so a
  green ready check on such an app says less than one on a data card.
- **Declaring or changing `ready_selector` moves the baseline.** "Ready"
  goes from a painted shell (first contentful paint) to data visible, so the
  number rises with no code change (task-os cold ready: 516 to 1967 ms). The
  `DIFF` line says `ready_by=fcp->selector (baselines do not compare)` and
  the report repeats it. Say so; it is the stricter definition, not a
  regression. A previous run from before `ready_by` was recorded leaves the
  change unflagged, so state it yourself for a first declaration.
- `ready_selector` must be **visible on the landing view**. The helper
  probes it: `CHECK ready.selector` is `fail` ("not visible on cold and
  warm") when a declared selector never shows, and the ready checks go
  `unmeasured` behind it. Fix the selector in the target's `.fleet.toml`
  first; it is a config fix, not an app finding. Do not rank a playbook
  pattern for it. With no selector declared, "ready" is first contentful
  paint and the report says it can be a painted shell.

### 2. Rank the fixes

For each `fail`, name the playbook pattern (`verdict.json` and the seed
`## Fixes` list in `issue-body.md` carry the mapping). Then rank by gain per
LOC, cheapest first.

- **Check the target's code before proposing a pattern.** It may already be
  in place. home-automation already had P5–P7 when it was piloted.
- **A cold-paint finding is a hypothesis until a trace confirms it.** Record
  a net log (or the DevTools network timeline) and see what the time sits in
  front of. The first lead on home-automation's "3 s stall" was render-blocking
  CSS; one trace showed it was the harness.
- **Say what the helper cannot see.** It never checks that a streaming
  response survives compression, or that a 304 stays correct across builds
  (playbook P2 and P3 give the one-off check and the test).
- **Prefer one fix per issue/PR**, so each reverts on its own.

### 3. Report

Summarize to the user:

- the verdict line and the over-budget checks, with measured vs budget;
- the slowest endpoints;
- the ranked fixes with their pattern ids and rough LOC;
- what changed since the last run (`DIFF`): `fixed`, `regressed` (a status flip) and `slower_p95_ms`, an endpoint whose p95 rose 25% and 10 ms or more since the last run **even while it stays inside its budget**. Say it as a regression: parking-manager's `/` went 31 to 43 ms under a 50 ms budget after a per-request fingerprint, every status stayed `pass`, and `regressed=none` read clean.

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
