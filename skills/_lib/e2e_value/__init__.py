"""Value-and-time measurements for the `/e2e-audit` skill (fleet-config#1018).

`e2e_test_audit.py` answers "is the suite too big?" by counting nodes. That
misses where a gate's minutes go: app-launcher's #1215 trim cut nodes by 18%
and browser time by 3.5%, because the time is page loads and PTY spawns, and
merging tests keeps both (app-launcher#1220). This module reads what a gate
already wrote down and reports time and failure history. It never starts a
gate: a full run costs ~37 min and loads the box for everything else.

Timing source, in order (never estimated from node counts):

  1. `--log <path>` on the command line;
  2. `.fleet.toml` `[e2e] progress_log` (app-launcher: `webapp/verify-progress.log`),
     the per-test START/DONE/FAILED log app-launcher's `tests/_progress_log.py`
     writes (#534, #943);
  3. `.fleet.toml` `[e2e] junit_xml`, a JUnit XML the gate writes.

With none of them, every verdict is `unknown (no timing source)`.

`timing <repo-root> [--log <path>]` prints one JSON object:

    status        ok | unknown          reason (when unknown)
    source        {kind, path}
    run           {started, finished, wall_s, complete, exit_status, routed_tier, slice,
                   checkout {root, linked_worktree}, skip_reasons [{reason, nodes}] | null (JUnit only),
                   nodes, e2e_nodes (executed), e2e_skipped, e2e_summed_s}
    load          {state: quiet|loaded|unknown, scope, overlaps [..], checked [..]}
    phases        [{name, wall_s, nodes}]
    projections   {name: {nodes, seconds, mean_s}}        executed e2e nodes only
    buckets       [{bucket, nodes, seconds}]               executed e2e nodes only
    modules       [{module, seconds, nodes, page_loads, shots, pty_refs, real_agent}]  heaviest first
    tail          {slowest_n, slowest_share, top5pct_n, top5pct_share, max_s}
    first_node    {nodeid, seconds, median_s, boot}        carries the session boot; boot: single-run | repeated | not-repeated
    waits         [{file, line, kind, ms, scope, text, nodes, measured_s, paid_s, ceiling}]  paid seconds first, `timeout=` ceilings and `poll-sleep`s last
    slow_nodes    [{nodeid, seconds, median_s}]            executed nodes over 10x the median and 1 s, boot node left out
    app_timers    [{file, line, kind, ms, text}]           literal 1 s+ timers in app JS (tests, vendored, minified left out)
    slow_fixtures [{file, line, name, is_fixture, fixture_scope, autouse, timeout_s}]  fixtures/helpers with a 30 s+ seconds timeout
    failures      [{test, nodeid, projection, date, source, when, step}]  every log on disk
    race_candidates [{test, projections, steps [..], events}]
    runtime_drift {status, measured_min, claims [{file, line, text, claimed_min, delta}]}

The run measured is the latest completed full-tier run that executed e2e
nodes; a later backend-only run or surface slice does not displace it, and
when only a slice is on record `run.slice` says so (fleet-config#1134).
Skipped nodes are not executed nodes: they are counted apart.

`load` compares this run's window with every other checkout of the repo
(`git worktree list`) that holds the same progress log: an overlapping run
marks this one `loaded`. Suites in other repos are not visible, which `scope`
says. A loaded run is reported but never used for a budget or drift verdict.

`routing <repo-root> [--prs N] [--until ISO] [--config toml] [--proposed toml]`
imports the repo's own `scripts/classify_e2e.py` (never a copy) and routes
the last N merged PRs' file lists through it: the tier distribution, which
rule labels forced `full` (every PR containing one, and PRs where it was the
only cause), the paths that forced it most, unclassified paths, and paths a
broad rule took although a later, more specific, lower-tier rule matches too
(a README under a static prefix: a free fix). `--proposed` routes the same PRs
through a candidate table and lists every PR whose tier changes; the counterfactual also re-runs `unclassified`, `shadowed` and `import_holes` against it (`holes_opened` / `holes_closed`). `import_holes`
reads the other way (task-os#287): every file the suite loads (the test dirs,
the conftest, `_*.py` plugins) or imports one level deep, routed through the
same classifier, listing those the table sends to `none`: a diff touching only
a shared fixture would run no browser suite. `gate` is
`e2e_route.gate_contract`: whether the repo's gate runs the classifier at all
and splits its multi-target output (fleet-config#1134). When the
classifier exposes `changed_selectors` and either table declares
`shared_stylesheets`, each PR's changed CSS rules per sheet (merge commit vs
its first parent) ride into both `classify()` calls, so the counterfactual
shows stylesheet narrowing, and each sheet's PRs are bucketed: owned by one
surface, unmapped selector, spans surfaces, no rule changed, unsafe, or
unreadable (fleet-config#1033). An older classifier keeps file-list routing.

`parallel <repo-root>` lists static signs that xdist workers would share
state (a port picked and released, fixed log names, a file every process
appends to, real-agent tests outside an `xdist_group` or serial pass; a
worker-id reference or a retry marks one `mitigated`), projects the last
serial run's per-test times onto 2/3/4/6 workers (LPT, x1.15/x1.3/x1.5 load
inflation, per test and per module), and names tests red in a parallel run
but green serially: shared state between tests, never a flake.

`projection_fit` (in `timing`, fleet-config#1026) splits the modules that pay for a
second browser projection into those where the engine or viewport matters
(geometry, touch, input, nav, composer, safe-area) and Chromium-only candidates.

The `/e2e` 6b trigger (step 3): `time_budget` gives `within|over|unknown|
undeclared` for the browser leg of the latest quiet full-tier run against
`.fleet.toml` `[e2e] time_budget_s`; `growth` counts test functions since the
last audit's `write_audit_record` (machine-local hooks state); `audit_trigger`
turns nodes, time and growth into one `yes|no|unknown`.

`failures` is the raw material for the judgment layer's classing (real bug /
race / test bug / flake-load / unknown). A test whose log failures stopped at
two or more different steps is listed in `race_candidates`: the app-launcher
#1222 lesson is that such a test is a race until someone proves otherwise,
never a flake by default.

stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `fleet_toml` and `git_run` are top-level modules beside this package in skills/_lib.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .sources import *  # noqa: E402,F401,F403
from .measures import *  # noqa: E402,F401,F403
from .fit import *  # noqa: E402,F401,F403
from .waits import *  # noqa: E402,F401,F403
from .report import *  # noqa: E402,F401,F403
from .routing import *  # noqa: E402,F401,F403
from .parallel import *  # noqa: E402,F401,F403
from .budget import *  # noqa: E402,F401,F403
from .sources import _wall_s  # noqa: E402,F401  (tests/test_e2e_value.py calls it)
