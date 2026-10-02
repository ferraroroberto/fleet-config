"""Deterministic core of /perf-review (fleet-config#1121): how fast does a fleet web app load on a phone?

The skill (`skills/perf-review/SKILL.md`) orchestrates; this package measures
and scores, with no LLM in the verdict. Read-only by construction: it refuses
a dead port rather than start anything, and it only ever sends GETs.

Two legs, two interpreters (the `/design-review` split):

  load      `load.py`, a Playwright child under the **target repo's** `.venv`:
            iPhone (WebKit) cold, Android (Chromium, CDP-throttled to the
            phone profile) cold and warm — ready time, boot-data time,
            requests, bytes on the wire, cache hits, and each `/api/` path's
            poll interval.
  http      `http_probe.py`, stdlib in this repo's venv: `/` plus every
            query-less `/api/` GET the page made, each timed at its own poll
            spacing (p50/p95, cold apart), and whether `/` is compressed and
            answers a repeat with 304.

`report.py` scores both against `budgets.toml` (a target overrides any key in
`[perf.review.budgets]`), keeps a local ledger at
`<hooks state>/perf-review/<target>/ledger.json`, and renders the one managed
`perf-review` issue body: numbers and URL paths only — never a host, an IP,
page text or a screenshot.

Optional `[perf.review]` keys in the target's `.fleet.toml`:

    ready_selector   = "#unitsGrid .card"   # "ready" = this visible on the landing view (checked: ready.selector); default: first contentful paint
    exclude          = ["/api/location"]    # path prefixes never timed (outside calls, costly reads)
    endpoints        = ["/api/extra"]       # timed even when the boot did not request them
    api_version_path = "/api/version"       # where the live build's git_sha is read
    [perf.review.cadence]                   # seconds between two GETs of one path (floor 5)
    "/api/units" = 30
    [perf.review.budgets.warm]              # any budgets.toml key
    data_ms = 2000
"""
from __future__ import annotations

from . import http_probe, report  # noqa: F401
from .cli import main  # noqa: F401
