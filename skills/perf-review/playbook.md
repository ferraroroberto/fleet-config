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
- A cold number is the median of three fresh loads, and the report names an
  outlier. A short run (`--duration` too small for enough endpoint samples)
  leaves a check `unmeasured`, so the verdict is `unmeasured`, never `pass`.
- Cost: none, it is this skill.

## P2 — Compress responses

Starlette's `GZipMiddleware` (`minimum_size=1000`). Keep streaming responses
(MJPEG, SSE, chunked audio) out of it: route them around it or exclude them
by content type.

- **Order matters.** Register `GZipMiddleware` first, so it sits **inside**
  any `BaseHTTPMiddleware` (an auth gate). Outside one, gzip sees re-streamed
  chunks: `minimum_size` is ignored (a 50-byte JSON got compressed) and a
  bodyless 304 is gzipped too. A one-line test that `/healthz` stays
  uncompressed catches it.
- **Verify a stream survives.** Starlette's gzip skips `text/event-stream`,
  but nothing in `/perf-review` checks it: its probe only sends bounded GETs,
  and a stream never ends. Once per app, `GET` the stream with
  `Accept-Encoding: gzip` and confirm there is no `Content-Encoding` and the
  first event arrives at once.
- **Keep precompressed media out too.** The middleware skips a response only
  for `text/event-stream` or when it already carries a `Content-Encoding`;
  everything else over `minimum_size` is compressed, so stored JPEGs, audio
  and other already-compressed bodies are gzipped for no gain. Mark those
  responses `Content-Encoding: identity` (the middleware treats it as already
  encoded) and add a test that the media endpoint comes back without `gzip`
  (photo-ocr#136: a test with the middleware on returned its stored photos
  `Content-Encoding: gzip` until the header was set).
- Evidence: home-automation's pilot measurement (2026-10-01): its static
  JS/CSS/HTML is 1316 KB raw and 383 KB gzipped; the 153 KB entry document
  gzips to 29 KB; `/api/presence` 14.8 → 4.3 KB. No fleet app compressed at
  the time. voice-transcriber#218: cold transfer 184 → 73 KB, warm 28 → 4 KB
  together with P3.
- Cost: ~5 lines + one test.

## P3 — ETag + 304 on the entry document

Answer a matching `If-None-Match` (tolerate `W/` and `*`) with a body-less 304,
and keep `Cache-Control: no-cache, must-revalidate` so the phone still
revalidates. Key the ETag on a **build fingerprint**, not the body alone: the
stamped HTML names only the directly referenced assets, so a changed transitive
module leaves the HTML byte-identical and a body-only ETag would answer 304
for an out-of-date build. Hash the stamped HTML plus the git sha plus every
asset hash, and send it as a **weak** validator (`W/"..."`), because the wire
bytes vary with `Content-Encoding`.

- Test that the ETag changes for a new commit, for a changed transitive
  module, and for an edited `index.html`. This is the risky half of P3 and the
  helper cannot see it: a 304 that is stale across builds needs two builds,
  and `/perf-review` is read-only.
- **Do not walk the static tree with a `stat` per file per request.** `Path.rglob`
  plus a `stat` each cost 11.6 ms per hit on Windows (200 calls), and
  parking-manager's `/` went p50 20 → 34 ms and p95 31 → 43 ms: still inside
  the 50 ms budget, so every check stayed green. Walk with `os.scandir` (size
  and mtime come with the directory listing; 1.2 ms), key a per-file digest
  cache on `(mtime_ns, size)`, and re-compare `/` p50 with the previous run after
  adding the fingerprint (`DIFF` flags a rise as `slower_p95_ms`). Computing it
  once at startup is right only when a restart is the only way the tree changes.
  Evidence: parking-manager#68.
- Optional: caching the body in memory keyed on `(mtime_ns, size)`. Re-reading
  a small file per request left `/` p95 at 27 ms in voice-transcriber, so add it
  only when `endpoints.index_p95_ms` fails.
- Evidence: app-launcher PR #1345 (`webapp/routers/misc.py`): `/` p95
  4.0 → 2.3 ms, and a 135 KB body no longer re-sent on each launch.
  voice-transcriber#219 (fingerprint + weak ETag). home-automation already
  takes the ETag over a stamped body that carries the build's asset hash.
- Cost: ~45 lines + three tests.

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

- Move the library's **stylesheet** with its script. home-automation still
  linked `leaflet.css` as a render-blocking `<link>` in `<head>` after its
  script moved to first use (#793); the red test is that the index must not
  link it.
- Evidence: home-automation loads chart.js + leaflet (350 KB raw, ~110 KB
  gzipped) at boot for two tabs.
- Rule the harness out before proposing this for a slow cold paint: the same
  stylesheets paint in ~300 ms when no proxy stall is present.
- Cost: ~25 lines.

## P10 — Log over-budget requests

A pure-ASGI middleware (no response buffering) that writes a line to a file
when a request exceeds its budget. A file, because a tray-launched app
discards its stdout.

- Evidence: app-launcher `webapp/observability.py` (`webapp/slow-requests.log`).
  It caught the `/api/board` tail #1345 could not explain.
- Cost: ~70 lines.

## P11 — Cache-busting stamps cover the import graph

Stamp JS and CSS so a stamp changes whenever **anything the file loads** changes,
then cache them for a year. Either:

- one **fleet hash** over every asset (app-launcher, home-automation and most
  fleet apps; any edit rotates every stamp), or
- per-file stamps hashed over the file **plus every module it transitively
  imports** (a visited set, so an import cycle terminates).

A per-file stamp from the file's own bytes only is the trap: behind a one-year
`immutable` cache, editing a nested module leaves a phone that cached the
importer on the old import URLs.

- The opposite trap: serving every asset `no-cache` ("revalidate, no stamping
  step") makes a warm launch as slow as a cold one. Each asset costs a 304
  round trip, and the ES-module imports chain them serially. On
  facilitation-suite and parking-manager, warm ready was ~1100–1200 ms
  against ~1250 ms cold over 80 ms RTT: 39 and 31 requests, none from cache,
  9–10 round trips deep (fleet-config#1236). home-automation's stamped,
  `immutable` assets serve 63 of 80 warm requests from cache and are ready in
  ~200 ms. The fix is project-scaffolding's `docs/app-onboarding.md` §4a.
- Evidence: voice-transcriber#220/#221 reproduced it red 3 of 5 and fixed it
  with a transitive import-graph hash. home-automation was probed under its fleet
  hash: editing only a nested module rotated the importer's stamp.
- Check: `cache.stamping` reads the target's `src/static_versioning.py` and
  fails on `per-file` (`stamping.py`). Cost: ~20 lines + a test that edits a
  nested module and expects the importer's stamp to change.

## P12 — Send only what the screen renders

When the `TOP` lines show one response is most of a failing transfer check,
fix that response before reaching for compression or lazy libraries. An
opt-in slim form (`?descriptions=false`) on the boot call, with the default
shape unchanged for every other caller, takes out the field nothing renders.
Whatever the drawer or detail view needs, it fetches on open.

- Evidence: task-os#291. `GET /api/tasks/tree?include_closed=true` was 5.56 MB,
  4.35 MB of it the `description` of closed tasks the rows never read. Gzip alone
  (P2) took it to 1.67 MB; the slim form took it to 104 KB on the wire.
- Check: `TOP` lines under `cold.bytes_kb` / `warm.bytes_kb` list the five
  largest responses (path, wire bytes, encoding, share of the transfer). Cost:
  ~10 lines + a test that the key is absent when opted out and present by default.

## Rollout notes

- Copy and adapt P3/P4/P10 per app for now; once a second app adopts one,
  move it into `project-scaffolding` as a vendored component (fleet-config#1121 Q3).
- File compression (P2) per app when that app's rollout comes.
