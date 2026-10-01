# Phone-load playbook

The patterns that made app-launcher's phone load near-instant (fleet-config#1121),
each with its measured evidence and rough cost. `/perf-review` maps every
over-budget check to one of these ids. Pick by **gain per line of code**: cheapest
first, and only what the measurement says is over budget.

The budgets these serve live in `skills/_lib/perf_review/budgets.toml`.

## P1 — Measure first, against budgets

Run `/perf-review <repo>` before and after every fix. The probe times each hot
endpoint at the app's own poll spacing (p50/p95, cold request apart) and loads
the app on a throttled phone profile, cold and warm.

- Evidence: app-launcher#1324's numbers (`/api/jobs` p95 448 ms, ~15 s to open)
  are what made the fixes in #1345 obvious. An n=20 p95 was one outlier
  (213 ms) that n=80 put at 32 ms. Trust p95 only at a decent n.
- Cost: none, it is this skill.

## P2 — Compress responses

Starlette's `GZipMiddleware` (`minimum_size=1000`). Keep streaming responses
(MJPEG, SSE, chunked audio) out of it: route them around it or exclude them
by content type.

- Evidence: home-automation's pilot measurement (2026-10-01): its static
  JS/CSS/HTML is 1316 KB raw and 383 KB gzipped; the 153 KB entry document
  gzips to 29 KB; `/api/presence` 14.8 → 4.3 KB. No fleet app compressed at
  the time.
- Cost: ~5 lines + one test.

## P3 — ETag + 304 on the entry document

Cache the stamped `index.html` body in memory keyed on the file's
`(mtime_ns, size)`, ETag `sha256(body)[:20]`, answer a matching
`If-None-Match` (tolerate `W/` and `*`) with a body-less 304, and keep
`Cache-Control: no-cache, must-revalidate` so the phone still revalidates.

- Evidence: app-launcher PR #1345 (`webapp/routers/misc.py`): `/` p95
  4.0 → 2.3 ms, and a 135 KB body no longer re-sent on each launch.
- Cost: ~45 lines + one test.

## P4 — Serve hot reads from memory

A snapshot of each slow read, built off the request path:

- a background tick refreshes it **only while it is being read** (demand
  window, e.g. 60 s), so an unwatched app polls nothing upstream;
- a read older than a staleness bound rebuilds inline: never served as
  current;
- the answer carries its age (an additive key, shapes unchanged);
- a write in the same process marks the snapshot dirty or replaces it with
  the write's read-back, so a control never shows the old state.

- Evidence: app-launcher PR #1345 (`src/jobs_snapshot.py`): `/api/jobs` p95
  448 → 20 ms, `/api/board` p95 444 → 32 ms (n=80). A sequence number on dirty
  marks fixed a lost update between overlapping reads, caught in review.
- Cost: ~120 lines for the generic core, ~5–10 per endpoint wired to it.

## P5 — Fetch boot's independent panels concurrently

Keep only real data dependencies serial (config first, then everything else
at once); arm each poll on its own first fetch, never on all of them.

- Evidence: app-launcher PR #1271: first panel 1440 → 172 ms; at +150 ms
  latency 6086 → 3394 ms.
- Cost: ~100 lines changed in the boot module.

## P6 — Paint the last good data first

Save each read-only response to `localStorage` (allowlisted, versioned
envelope) and render it at boot before the network answers.

- Evidence: home-automation's `static/snapshots.js` already does this; its
  warm launch paints in ~130 ms while the data lands seconds later.
- Cost: ~60 lines + a restore call per panel.

## P7 — Remember the last tab

The vendored nav's `storageKey` reopens the PWA where it was left.

- Evidence: app-launcher PR #1304.
- Cost: ~10 lines.

## P8 — Fetch only what's new

A cursor (`?after=<tail>`) shared by the poll and any manual refresh; append,
never rebuild.

- Evidence: app-launcher PR #1294 (Chat).
- Cost: ~130 lines; worth it only for long, growing lists.

## P9 — Load heavy libraries on first use

Chart and map libraries (hundreds of KB) loaded by a classic `<script>` run
before the boot module on every cold launch. Import them on first use of the
view that needs them.

- Evidence: home-automation loads chart.js + leaflet (350 KB raw, ~110 KB
  gzipped) at boot for two tabs.
- Cost: ~25 lines.

## P10 — Log over-budget requests

A pure-ASGI middleware (no response buffering) that writes a line to a file
when a request exceeds its budget. A file, because a tray-launched app
discards its stdout.

- Evidence: app-launcher `webapp/observability.py` (`webapp/slow-requests.log`).
  It caught the `/api/board` tail #1345 could not explain.
- Cost: ~70 lines.

## Rollout notes

- Copy and adapt P3/P4/P10 per app for now; once a second app adopts one,
  move it into `project-scaffolding` as a vendored component (fleet-config#1121 Q3).
- File compression (P2) per app when that app's rollout comes.
