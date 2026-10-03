---
name: e2e-audit
description: On-demand audit of a repo's e2e/regression suite for redundancy, bloat, coverage gaps, gate time and failure history against project-scaffolding's "<15 tests total" target — deterministic inventory + near-duplicate clustering via e2e_test_audit.py, then a deduped e2e-redundancy issue for /cleanup-fleet. Never rewrites/deletes tests. Never on a clock — /e2e triggers it when a suite exceeds its node or time budget or grows ~10 tests. E.g. "/e2e-audit", "/e2e-audit app-launcher", "audit the e2e suite for bloat".
---

# e2e-audit

**Goal:** answer "is this e2e suite exhaustive without being infinite?" for one repo, **on demand — never as a weekly scheduled job** (fleet-config#406: bloat is feature-driven, not time-driven, and `audit-fleet` / `context-audit` / `config-map` / `system-map` / `insights-weekly` / `learning-log` already run weekly).

Run the deterministic scan (`skills/_lib/e2e_test_audit.py`) — file
inventory, raw + true (parametrize-expanded) node counts against
project-scaffolding's `docs/playwright-ui-testing.md` target ("Keep it small.
Target < 15 tests total. If tempted to add #20, delete two first."),
near-duplicate name clusters, shared-parametrize-matrix clusters, size
outliers, and (when the repo declares a `## UX surface` block) coverage gaps.
Apply LLM judgment only where measurement can't reach, then file exactly one
deduped `e2e-redundancy` issue per repo — the same audit→bucket→cleanup
machinery as `/codebase-audit` and `/design-sync`, cleared later by
`/cleanup-fleet e2e-redundancy`.

## Arguments

- No argument → the **current repo** (cwd).
- One path or repo name → that **target repo** (resolve as
  `E:/automation/<name>` or as a path; must be a git repo).
- `--target N` is **not** an argument here — the target is fixed at
  project-scaffolding's 15; don't let a run override it ad hoc.
- More than one path argument → say only one target is accepted and stop.
- The word `budget` (`/e2e-audit budget`, `/e2e-audit budget <repo>`) → a
  **triggered** run, which is how `/e2e` step 6b invokes it when
  `E2E_AUDIT_TRIGGER=yes` (over the node budget, over the time budget, or ~10
  new test functions since the last audit). It changes only step 6's
  no-findings outcome (see the exception there).
- The word `local` (`/e2e-audit local <repo>`) → a **dry run**: steps 1–4 and
  the step-6 summary only. Write the would-be issue body to step 5's temp file
  and print its path; skip step 5's `get`/`upsert` and step 5b's `record`, so
  nothing reaches GitHub and the growth baseline does not move.

## Steps

Run in order. Stop on any hard failure with a one-line error.

### 1. Pre-flight

In parallel, from the target repo root:
- `git rev-parse --is-inside-work-tree` — must print `true`, else stop:
  "Not inside a git repository."
- `git rev-parse --show-toplevel` — capture the repo root.
- `gh repo view --json nameWithOwner -q .nameWithOwner` — capture
  `OWNER/REPO`. On failure stop: "No GitHub remote — this skill files issues,
  can't run without one."

### 2. Detect a test suite — else skip

Run the scan (step 3) regardless, but read `test_dirs_resolved` **first**.
`false` means none of the resolved dirs exist on disk: the scan had nowhere
to look, so its `0` is *unknown*, not empty. Stop with `<repo>: could not
resolve a test dir (tried <test_dirs_missing>) — coverage unknown, not
audited.` and file nothing.

Only when `test_dirs_resolved` is `true` does `totals.files == 0` mean what it
says: stop with `<repo> has no test files under its resolved test dir(s)
(<dirs>) — nothing to audit.` File nothing.

### 3. Run the deterministic scan

One command computes every mechanically-checkable dimension (JSON to stdout):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_test_audit.py scan <repo-root>
```

Test-dir resolution: reads the repo's own `## CI expectations` CLAUDE.md
block for an "e2e surface" line and uses its backtick-quoted, test-like paths
(e.g. app-launcher's `tests/e2e/`); falls back to `tests/e2e/` when no block
or no test-like path is declared. Generic + project-driven, not a hardcoded
per-repo path. A heading inside a fenced code block is ignored, so a repo
that only *documents* the block template (project-scaffolding) reads as
undeclared and takes the fallback.

Fields returned:

- **`test_dirs_resolved` / `test_dirs_missing`** — whether any resolved dir
  exists on disk. `false` is an *unknown*, never a clean result (step 2).
- **`totals`** — `files`, `raw_tests` (plain `def test_` count), `node_count`
  (true pytest-collected count, parametrize expansion included — `null` when
  the repo has no `.venv` or pytest isn't collectible; report that as "not
  measured", never as zero).
- **`ratio`** — `(node_count or raw_tests) / target` against the 15-test
  target. The headline bloat signal (e.g. app-launcher: ~400 nodes / 15 ≈
  26×).
- **`clusters`** — tests whose normalized name collided across ≥2 files. A
  redundancy *candidate*, not a verdict — a generic name like `test_smoke`
  can collide innocently.
- **`matrix_clusters`** — tests in the *same file* sweeping the same
  `@pytest.mark.parametrize` matrix (`argnames@source`), which name
  clustering cannot see. Each entry carries `file`, `argnames`, `source`,
  `members`. High leverage — this is where node counts multiply
  (project-scaffolding's four geometry twins: 32 nodes → 8, no coverage
  loss). Also a candidate, not a verdict: a shared matrix over genuinely
  distinct assertions is legitimate breakpoint coverage.
- **`size_outliers`** — files far above the suite's median line count.
  Context, not automatically a finding.
- **`key_views_declared` / `coverage_gaps`** — only populated when the repo
  has a `## UX surface` block (`ux_surface.py`); a gap is a crude substring
  check the LLM layer must sanity-check before filing (step 4).
- **`waits`** — every clock wait in the test tree, with file, line,
  milliseconds and enclosing scope: `sleep` (`wait_for_timeout`,
  `time.sleep`), `poll-sleep` (the same call inside a `while` loop that can
  end on its condition, or a `for` retry loop whose `if` breaks or returns: it costs its interval at most, never `paid_s`, and
  ranks last like a ceiling), `page-timer` (a `setTimeout` in an init
  script; `unawaited: true` when its callback is empty, a no-op handle the
  test never waits for: never priced, ranked last, app-launcher#1375), `poll-constant` (`POLL_MS = 15_000`), `long-timeout` (`timeout=`
  of 10 s or more). Static, so present even with no timing source; `timing` ranks the
  same list by seconds (step 3b).
- **`slow_fixtures`** — fixtures and helpers (never tests) holding a
  seconds-valued `timeout=` of 30 s or more: a float literal, or a module
  constant holding one, anywhere; an int only in a call to `httpx`,
  `requests`, `urlopen`, `subprocess` or `socket` (a Playwright
  `timeout=45000` is milliseconds). Each entry has `file`, `line`, `name`,
  `is_fixture`, `fixture_scope`, `autouse`, `timeout_s`. `waits` reads
  `timeout=` as integer milliseconds and so never sees these: local-llm-hub's
  biggest cost, 40 s of a 76 s run, was a session fixture calling
  `httpx.get(..., timeout=_WARMUP_TIMEOUT)` with `_WARMUP_TIMEOUT = 90.0`, and
  `waits` ranked 4 s of sleeps first. The timeout is a ceiling, never the
  cost (step 4 a0).
- **`vacuous_candidates`** — negative text assertions
  (`not_to_contain_text("x")`, `assert "x" not in`) whose needle appears
  nowhere else in the repo, so nothing can ever render it: the shape of a
  leak check that passed whatever the UI showed (home-automation#781).
  Candidates only (step 4i).

### 3b. Time and failure history (fleet-config#1018)

Node count is not the cost: app-launcher's #1215 trim cut nodes 18% and browser time 3.5%, because the time is page loads and PTY spawns. Two more read-only measurements, JSON to stdout:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_test_audit.py timing <repo-root>
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_test_audit.py failures <repo-root>
```

Both read the gate's own record: `.fleet.toml` `[e2e] progress_log` (a START/DONE/FAILED log like app-launcher's `tests/_progress_log.py` writes), else `[e2e] junit_xml`, else `--log <path>`. They **never start a gate** (a full run costs ~37 min and loads the box for everything else).

- **`timing`** — the latest completed full-tier run that executed e2e nodes, in any checkout of the repo (gates run in worktrees too; a later backend-only run or surface slice never displaces it, and `run.slice` says when only a slice is on record). **The run is held to the suite's collected node count** (`pytest --collect-only`, or `--suite-nodes N`): `freshness` is `fresh`, `slice`, `unchecked` (count not measured) or `stale` (executed plus skipped nodes more than 10% from the suite's). A `stale` run has `status: stale`; its numbers stay in the JSON, but rank nothing from them and ask for a fresh full-gate baseline first (app-launcher#1375: the only log was a 612-node run against a 485-node suite, `timing` said `ok`, every `paid_s` was doubled and the runtime drift read 29.5 min against a real 13.9). Nodes are **executed** nodes; skipped ones are `e2e_skipped`, never a saving. `run.checkout` names the checkout the measured run lives in; `linked_worktree: true` means a `<repo>-wt-<N>` worktree, which lacks the primary's gitignored runtime files (certificates, `.env`), so its skip count and boot can differ from the primary's (photo-ocr#127: a cert test skipped as "not HTTPS" and the suite booted over plain HTTP). Say so in the report, treat that run's skips and boot as **unknown**, never as a saving, and prefer a run from the primary. `run.skip_reasons` (a JUnit source only; `null` for a progress log) groups the skipped nodes by their message, so a skip that is environmental ("not HTTPS") reads differently from a projection-only one. `first_node` is the node the session app boot lands on (2.5–3.5 s in both lanes of #1134): discount it in any per-module before/after. Its `boot` says whether the excess is a measured cost: `single-run` (one run on record, cold and unconfirmed), `repeated` (the two newest runs of that checkout's log both carry 1 s or more over the median) or `not-repeated` (a later run did not). `waits` ranks the scan's waits by what they are known to cost: a sleep or page timer by `paid_s` (its milliseconds times the executed nodes that reach it), a poll constant by `measured_s` (the seconds of the nodes that pay it, the ceiling on any saving), and every `long-timeout` last, flagged `ceiling` (a `timeout=` is a limit the test may never reach). `slow_nodes` lists executed nodes over 10x the suite median and over 1 s (the boot node left out) and `app_timers` the 1 s+ `setTimeout`/`setInterval`/`sleep(<ms>)` calls in the app's own JavaScript, a named delay (`setInterval(tick, RUNNING_APPS_POLL_MS)`) resolved through its `const NAME = <ms>` (the file's own, else another module's export; `name` says which; an ambiguous or undeclared name is left out, app-launcher#1375): the two halves of a wait the test-tree scan cannot see (step 4 a0). Also: phase wall times, per-projection nodes/seconds/mean, the duration buckets (<1, 1–3, 3–6, 6–10, ≥10 s), the heaviest modules with their static cost drivers (`page_loads`, `shots`, `pty_refs`, `real_agent`; `shots` is screenshot call sites, a lower bound: see step 4k), the tail share, and `load`: `loaded` when another checkout's run overlapped it (suites in other repos are not visible; `scope` says so). `projection_fit` applies `/e2e`'s second-projection rule (fleet-config#1026) per test (its body plus the module helpers it calls): tests on WebKit/Firefox showing a geometry, touch, input, engine-branch, nav or composer signal are `kept`; the rest are Chromium-only `candidates`, with their second-projection seconds. `runtime_drift` lists every runtime figure on a CLAUDE.md/README line about the gate, with its nearest measured span; `candidate` is more than 25% off.
- **`failures`** — every red in the logs on disk (test, projection, date, the step its traceback stopped at), mentions in the last 60 merged PR bodies and in `bug` issues (free text, a lower bound), and `race_candidates`: tests red at two or more different steps.
- **`routing`** — the last 60 merged PRs through the repo's own `scripts/classify_e2e.py` (imported read-only, never reimplemented): the tier distribution, which rule labels forced `full` (every PR containing one, and single-cause PRs), the paths forcing it most, unclassified paths, and `shadowed` paths (a broad rule took a path a later, more specific, lower-tier rule also matches). Each shadowed path names `next_rule`, the rule that wins once the first stops matching, and `drop_safe`: false means an in-between rule would take it (home-automation's vendored READMEs went to the full `app/webapp/` rule, not `*.md`), so the fix is an explicit rule. Shadowed and unclassified paths carry `check`, the exact `classify_e2e.py` command that routes them. `import_holes` reads the other way: every file the suite loads (test dirs, conftest, `_*.py` plugins), imports one level deep, or reads at runtime by a repo-relative path (`kind: read`: a string like `'config/config.sample.json'` or `ROOT / 'config' / 'x.json'` that names an existing git-tracked non-Python file; a gitignored local file can never be in a diff), routed through the same classifier, listed when the table sends it to `none` (`kind`, `rule`, `imported_by`, `check`). `kind: routing-source` is `.fleet.toml` or `scripts/classify_e2e.py` routed `none`: a diff that edits only the routing table reroutes the suite without running it (a `*.toml` or `scripts/` none rule is the usual cause). Recommend an explicit `full` rule for it and state the trade (every table edit, a description change included, then runs the browser suite); it is the owner's call, never filed as a time saving. A path built from a variable is not seen: say so rather than reporting the data files complete (facilitation-suite#165: the conftest read a sample config for every instance and the scan, following imports only, missed it). A merged-PR scan cannot see these until someone edits one: task-os's 12 e2e modules imported `tests/fixtures/*.py` and `tests/conftest.py`, which fell to the `tests/` none rule (task-os#287). `gate` is whether the repo's gate runs this routing at all (`e2e_route.gate_contract`): `not-consumed` means table edits save the gate nothing, `broken` means the gate passes multi-target output as one pytest argument. `--proposed <toml>` routes the same PRs through a candidate table and lists every tier change: the counterfactual a routing proposal needs. It also recomputes `unclassified`, `shadowed` and `import_holes` against the candidate (`counterfactual.unclassified` / `.shadowed` / `.import_holes`, plus `holes_opened` and `holes_closed`), so a proposal's effect on coverage is read from the report, not proved by swapping the file in and classifying paths by hand (facilitation-suite#165). A rule that closes a hole lists it in `holes_closed`; one that opens a hole is a regression to fix before filing. Keep the candidate at the repo root: the classifier resolves paths against the table's folder. When the classifier exposes `changed_selectors` and a table declares `shared_stylesheets`, each PR's changed CSS rules per sheet ride into both routings, and `sheet_routing` buckets each sheet PR: owned by one surface, unmapped selector, spans surfaces, no rule changed, unsafe, or unreadable (fleet-config#1033).
- **`parallel`** — static signs xdist workers would share state (`free_port_race`, `fixed_log_names`, `shared_append`, `load_sensitive_ungrouped`; a worker-id reference or a port retry marks one `mitigated`), session fixtures and module state for context, `pytest-xdist` installed/declared, an LPT projection of the last serial run onto 2/3/4/6 workers with ×1.15/×1.3/×1.5 inflation and the two serial floors (heaviest module, slowest test), and `shared_state`: tests red under workers and green serially. Those are shared state between tests, **never** flakes.
- **No timing source** is itself the first finding, because every estimate is `unknown` without one. Recommend a durable, append-only progress log in the **primary** checkout (browser runs only, last ~20 runs, a named mutex; home-automation#780), not a per-checkout file overwritten each run, which dies with every worktree; or `--junitxml` in the gate plus `[e2e] junit_xml`. The fixer gets per-test seconds once with `pytest <e2e dir> --durations=0`; this skill never runs it.
- `status: unknown` (no source, no completed run, no e2e node, no classifier, no `gh`), `status: stale`, `freshness: unchecked` and `load: unknown` are their own states. **A loaded, stale or unknown run never feeds a verdict:** report its numbers as loaded, and file no drift finding from it.

### 4. Apply judgment

Rank by **executed nodes and measured seconds**, never collected nodes. In both #1134 lanes the time was in waits: a real 15 s poll a test waited out (about 29 s saved with `page.clock`, home-automation#783) and nine copies of a fixed 750 ms page timer (16 s, home-automation#789), while folding 34 nodes saved about 7–10 s.

- **(a0) Waits first.** Confirm each `waits` entry before proposing any fold. A poll-driven test → drive it with `page.clock` (`install()`, then `run_for(<poll ms>)`) and keep a red proof that disabling the poll fails it. A loading state → a fetch held until the test releases it (an init script that wraps `fetch` in a promise the test resolves), instead of a fixed timer. A `long-timeout` is only a hint, and never the story's measured cost: task-os's archive story had 30 s `timeout=` ceilings and 1.5 s of real waits in its 20.9 s (task-os#284), and the sleeps and polls the scan listed saved about 3 s in all. Read whether the test really waits that long, and price a wait by `paid_s`. A `poll-sleep` is a deadline poll, not a wait: leave it unless the loop's own condition is wrong (facilitation-suite#165: 6 of 11 sleeps were polls, and the 3.5 s "paid" figure was about 2 s measured). A fixed wait next to an opt-in capture (`time.sleep(0.6)` before a `shot()` that only runs under an env flag) is paid on every run though no shot is taken: move it into the capture helper (`shot(..., settle=0.6)`). **A wait that lives in app source is invisible to `waits`** (photo-ocr#127: a 1 s `sleep(1000)` status poll in `poll.js`, waited out twice by one test, was 4 of the 7 s saved, and only the JUnit showed it: 2.6 s against a 0.2 s median). For every `slow_nodes` entry, read the test, then match it to an `app_timers` entry its stubbed endpoints would trigger; a poll or timer it waits out is a wait to drive with `page.clock`, priced by the node's seconds. A `slow_nodes` entry with no timer behind it is a candidate for the fixer to read, never a number to file. **A fixture is a wait `waits` cannot see** (local-llm-hub#644: a session fixture was 40 s of a 76 s run, a 42–44 s `setup` on the first node, ranked below 4 s of sleeps). Read `slow_fixtures` for every `timing.first_node` that is `repeated` and far above the median, and for every `slow_nodes` entry: an autouse or session-scoped entry is paid once per run, by whichever node comes first. Price it by the measured setup, never by its `timeout_s`: with no timing source, or a JUnit that does not split phases, tell the fixer to run `pytest --durations=0` once and read the `setup` rows before trusting the `waits` ranking. A fixture whose cost grows with the host (a cold scan of the machine's own session history, read from outside the test's temp dir) is also a determinism bug: the fixer points the e2e instance at an empty state dir and keeps the real-volume scan as a unit or perf canary, not an e2e step. The audit cannot detect a host read statically, so it never files one without the `--durations` evidence.
- **(a) Confirm each cluster.** Read the colliding tests' actual
  bodies/selectors. A genuine near-duplicate (same view, same assertion, same
  setup) is a merge candidate — say which to keep. A coincidental name
  collision is **not** a finding — say so and drop it; don't force every
  cluster into the issue.
- **(a2) Confirm each matrix cluster.** Read the members' bodies. Twins that
  differ only in which violation they assert, all swept over the same matrix,
  collapse into one test with no coverage loss. **Parametrizing N tests into
  one saves functions, not nodes**: a 3-way parametrize over 2 projections
  still collects 6. Nodes fall only when legs merge into one test body and
  one page load (home-automation's security editors: 6 → 3). Tests that
  genuinely assert different behaviour across the matrix are legitimate
  coverage: drop them, don't pad the issue.
- **(a3) Price every fold in nodes and seconds, separately.** A fold whose
  cases each `goto` or `reload` saves about 0 s (a reload costs what a
  separate test did; home-automation#787). A fold across feature modules
  that each own a stub breaks a one-module-per-feature layout for a second or
  two; a shared helper inside the modules is usually the better fix
  (home-automation#789 de-duplicated 20 tests that way).
- **(b) Confirm each coverage gap.** The helper's check is a crude substring
  match on test names/paths — read the actual suite before filing; a view
  covered under a very differently-worded test name is a false positive.
- **(c) Materiality bar.** A single coincidental collision or a two-line
  ratio drift is not a finding; a real cluster of ≥3 tests re-asserting the
  same thing, a suite multiple times over target with no organizational
  structure, or a genuinely uncovered key view is.
- **(d) Positive-shape reference.** `docs/playwright-ui-testing.md` documents
  what a *well-organized* suite looks like (the vendored `_geometry.py`
  helper, a `KEY_VIEWS`-driven matrix pattern) — when proposing a merge/split,
  point at that pattern rather than inventing a new structure.
- **(e) Class every failure event** (log reds and mentions) as exactly one of: **real bug** (engine-specific), **race** (a real ordering bug one engine or load exposes first — app-launcher#732, #1222), **test bug**, **flake/load** (timeouts under concurrency, `ERR_NO_BUFFER_SPACE` port exhaustion, real-agent replay), or **unknown**. A mention is often a pre-fix red proof, not a failure of the suite: read the line. **Never assign `flake` by default.** A `race_candidate` (red at different steps, typically on the slower engine, under load, and green alone) stays `unknown` until someone repeats the app-launcher#1229 recipe: loop the test on that engine, hold the suspected slow dependency pending, and see whether the product, not the test, is waiting. Tests that need a PTY or overlay to paint and run near their wait budget are **load-sensitive**, not flakes (app-launcher#887).
- **(f) Routing and parallelism are proposals, not changes.** Read `routing.gate` first: `broken` is a finding of its own, ranked first (it fails every branch touching two e2e modules); `not-consumed` means no rule change saves gate time, so never rank one as time (voice-transcriber's routing PR changed nothing its gate ran). A `shadowed` or unclassified path becomes a table-rule recommendation: use `next_rule`, not the shadowed rule, to say where the path lands, and propose an explicit rule when `drop_safe` is false. An `import_holes` entry that is test support (a fixture, a conftest, a fake, a data file the suite reads) is a coverage fix: a table-rule recommendation to route it `full`, never ranked as time (it only adds browser runs). One that is the app's own source (`src/*.py` routed `none` while every instance boots it) is a documented gate-time trade, the owner's call: list it as an owner question with the trade stated, never as a hole to fix. A narrowing rule set needs its `--proposed` counterfactual **and** the `check` command run on the candidate table for every affected path. A counterfactual with 0 tier changes is hygiene, worth doing, never ranked as time. Before routing a launcher or hygiene file (`.bat`, `.gitignore`, a sample config) to `none`, read the e2e conftest to confirm the suite can never execute it. Adopting workers is its own issue, validated the app-launcher#1231 way: 3 gate runs at the proposed n, recording wall time, reds and peak TIME_WAIT against a serial control (fleet-config#440, #498).
- **(g) Confirm each drift candidate.** Match the figure to the span it describes (whole gate, non-e2e, browser leg); a figure that still disagrees by more than 25% on a quiet run is a finding: correct the documented runtime.
- **(h) Confirm each second-projection candidate** (`projection_fit`, fleet-config#1026). Read the test: one that asserts no geometry, touch, input, nav, composer or safe-area behaviour and has no engine branch belongs on one engine. The finding is one ranked item for the confirmed set: seconds saved (their second-projection time), and what it gives up: WebKit-only JS behaviour in those flows and the slower engine exposing a race first (cite how often `## Failure history` shows either). Name the repo's own mechanism for pinning; never edit a test.
- **(i) Confirm each vacuous candidate.** Read the test and the app: a needle the app builds by concatenation (`'broken: ' + reason`) can render, so drop it; a needle subsumed by a broader assertion in the same test is redundant, not vacuous. File only one the fixture or stub cannot produce, with the proof the fixer must run: make the app produce the needle and watch the assertion stay green.
- **(j) Moving or rewriting an assertion needs a red proof.** Every finding that folds a test, moves an assertion to another suite or rewrites one says so in its `Fix:`: the fixing PR carries an old → new assertion table and a mutation run per moved assertion (break the app, watch the new assertion fail).
- **(k) Report screenshots as context, never as a fold target.** Sum `shots` over the modules and say so in `## Time`. A suite that settles (fonts, animations, stable frames) before every capture pays about 0.3 s per shot: task-os's ~195 shots were about 55–60 s of 146 s (a probe of two stories: 39 shots, 9.1 s settle, 2.7 s capture). The determinism rules guard that settle, so trimming it blindly is unsafe: the finding states the count and the probe the fixer can run (time `settle()` and the capture over one story), and proposes nothing to remove.
- **(l) Process boot and import cost is invisible to every static scan: ask for it by name.** In task-os it was the biggest real saving: 13 instance boots at about 2.4 s each were a third of the suite, and 1.5 s of a 2.13 s `import app.webapp.server` was one SDK imported at module level; importing it on first use cut an import to 0.69 s, a boot to about 1.1 s and the full e2e from 167 s to 144 s (task-os#286). Read `timing.first_node` (the session boot lands there; `seconds` against `median_s`) and look for boot fixtures that start the app once per module (a `scope="module"` or `"session"` fixture that spawns the server). When the first node exceeds the median by 1 s or more and `boot` is `repeated`, or every module boots its own instance, rank boot as a finding of its own, priced as boots × the excess. **Never price a boot from one run** (photo-ocr#127: "session boot ≈ 6.9 s, 39% of the run" was the first run in a fresh worktree, new `.pyc` and first browser launch; warm, the server was ready in about 1 s and the whole suite ran 5.5 s): `single-run` is a cold figure, so label it `cold single run, unconfirmed`, rank it below the measured waits, and leave the fixer to re-run once warm; `not-repeated` is no finding. Fix: the fixer runs `python -X importtime -c "import <server entry module>" 2> imports.txt` and sorts by cumulative time; a heavy SDK imported at module level moves inside the function that calls it, with a test that a fresh interpreter importing the server does not load it. This skill never runs the import probe itself: it executes the target's code. No timing source means the first node is unknown, and boot is then a hypothesis for the fixer to measure, never a number to file.

### 5. Dedupe and upsert the `e2e-redundancy` issue

Exactly one managed `e2e-redundancy` issue per repo, reused across runs —
identical mechanics to `/codebase-audit`'s bucket issues. Never `gh issue
create` by hand.

1. **Ensure the label** (idempotent):

   ```
   gh label create e2e-redundancy --color '5319e7' --description 'e2e/regression test suite redundancy, bloat, or coverage gaps' || true
   ```

2. **Fetch the existing issue:**

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind e2e-redundancy
   ```

3. **Build the merged body.** Fresh → the template below. Existing → merge
   this run's findings: preserve every ticked `- [x]` verbatim, update the
   inventory table and ratio, keep items not re-surfaced (flag them in the
   run log), never tick/close anything yourself, never add `Closes #`. Append
   a dated bullet to `## Run log`.

4. **Upsert** (creates / edits / collapses strays, stamps the marker):

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind e2e-redundancy --label e2e-redundancy \
     --title "audit: e2e-redundancy findings" --body-file <tmpfile>
   ```

   Use a repo-scoped, unique temp file: `E:/tmp/e2e-audit-<owner>-<repo>-<short-sha>.md`
   (`<owner>-<repo>` = `OWNER/REPO` with the slash → hyphen). Never a fixed
   shared name.

**Body shape** for a fresh issue (no hard-wrapped paragraphs — the global
CLAUDE.md rendered-markdown rule applies; the helper prepends the marker):

```markdown
Surfaced by `/e2e-audit`, kept up to date across runs. Suite target: project-scaffolding's `docs/playwright-ui-testing.md` — "Keep it small. Target < 15 tests total. If tempted to add #20, delete two first." Measured by `skills/_lib/e2e_test_audit.py` (deterministic); judgment items marked.

## Findings

<ranked recommendations, most minutes saved per unit of coverage given up first; zero-coverage-cost items (workers, routing hygiene, a stale runtime figure) lead. Every item names the change, the estimate, the cost and the evidence:>

- [ ] **<change>** (e.g. run the browser suite on n workers; keep projection X only for geometry, input and engine-branch tests; add a routing rule; merge setup-identical tests that each pay a page load; fix a named race; correct a stale runtime figure) — saves ~<m> min (<low>–<high> at ×1.15–×1.5 load, from measured durations). Gives up: <what the suite stops catching before merge, and how often that class caught something in `## Failure history`; "nothing" only when true>. Evidence: <log path, PR numbers, `file::test`>.
- [ ] **<file>:<line> <wait>** (e.g. a real 15 s poll; a fixed 750 ms timer in 9 tests) — saves up to ~<measured_s> s over <n> executed nodes. Gives up: nothing when the red proof shows the test still fails without the behaviour. Fix: `page.clock` / a held fetch; keep a red proof.
- [ ] **<file>:<test name> ~ <file>:<test name>** — near-duplicate intent; merge candidate. Saves <n> executed nodes and ~<s> s (measured; 0 s when every case reloads), stated separately. Gives up: nothing if the setup and assertion are identical; say so. Fix: keep <which>, drop <which>, and say why.
- [ ] **<view>** — declared key view with no matching test found (confirmed by reading the suite). Costs ~<s> s. Fix: add coverage for it.

Estimates come from measured durations only, never from node counts; without a timing source, say `saves: unknown (no timing source)`. Headless projections cannot see `env(safe-area-inset-*)` or installed-PWA geometry, so "gives up" never claims the suite caught what it cannot see (app-launcher#1099).

## Suite inventory

| metric | value |
|---|---|
| test dirs | <dirs> |
| files | <n> |
| raw test functions | <n> |
| collected nodes (pytest --collect-only) | <n | not measured> |
| target (project-scaffolding) | 15 |
| ratio (nodes/target) | <x.xx>x |

## Redundancy candidates

<one line per cluster judged in step 4a: `<file>:<name> ~ <file>:<name> — merge candidate: <why> | coincidental, not filed`>

## Coverage gaps

<one line per confirmed gap, or "none declared" / "none found">

## Time

<from `timing`: source log + run date + tier (+ `slice` if any) + load state; a table of phase | wall time; projection | executed nodes | seconds | mean, and skipped nodes; the duration buckets; the top 5 modules with seconds and cost drivers; the first node and its boot share; the top waits with measured seconds; the screenshot call sites (`shots`, context only: step 4k); the tail share. Or `timing: unknown (<reason>)` plus the timing-source recommendation from step 3b.>

## Routing

<from `routing`: PRs sampled, tier distribution, the share of browser-relevant PRs that went full, the forcing-class table (containing / single cause), the top forcing paths, shadowed and unclassified paths, import holes, and any `--proposed` counterfactual. Or `routing: unknown (<reason>)`.>

## Parallelism

<from `parallel`: xdist state, each blocker with file and state, the worker projection table (n | load / loadscope at the three inflations), the serial floors, and any shared-state evidence. Or `parallel: unknown (<reason>)`.>

## Failure history

<from `failures` + step 4e: a projection table (red only on X / only on Y / both) and a flake list — test, red count, projections, class, tracking issue. Race candidates listed with their steps and "unknown until the #1229 recipe is run". Or `failures: unknown (<reason>)`.>

## Context

<short paragraph: overall shape (e.g. "196 raw / ~400 collected nodes vs a 15-test target — no per-file duplication found, but the suite has never been pruned"), the biggest opportunity, anything the next fixer should know.>

## Run log

- <YYYY-MM-DD> @ <short-sha>: initial · tests=<raw tests> nodes=<collected nodes> · trigger: <on demand | the E2E_AUDIT_TRIGGER_REASON>.
```

Title is **stable** — `audit: e2e-redundancy findings`, no count suffix.

### 5b. Record the growth baseline

Every run ends by recording this audit's test counts, whether or not it filed anything, so `/e2e`'s growth trigger measures from here:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_test_audit.py record <repo-root>
```

It writes machine-local hooks state (`e2e-audit/<owner>-<repo>.json`), never the repo, and prints `AUDIT_RECORD_TESTS=` for the run-log line.

### 6. Final report

Print one summary and stop:

```
/e2e-audit summary — <repo>

  test dirs: <dirs>
  files: <n>   raw tests: <n>   collected nodes: <n | not measured>
  target: 15   ratio: <x.xx>x
  clusters: <n> candidate(s) -> <n> confirmed, <n> dismissed as coincidental
  matrix clusters: <n> candidate(s) -> <n> confirmed, <n> legitimate coverage
  size outliers: <n> (<top files>)
  coverage gaps: <n confirmed | none declared | none found>
  time: <browser <s> s over <n> executed nodes (<k> skipped), <load> | unknown (<reason>)>
  waits: <n> (<top: file:line, kind, measured s>)   vacuous: <n> candidate(s) -> <n> confirmed
  failures: <n events, <n> race candidates | unknown (<reason>)>
  routing: <n PRs: skip/static/surface/full counts, top forcing class; gate <verdict> | unknown (<reason>)>
  parallel: <n blockers; n=4 projects <min> min | unknown (<reason>)>
  filed: https://github.com/<owner>/<repo>/issues/<N>   (e2e-redundancy)
```

Zero test files → report and stop (step 2), file nothing. Tests but zero
confirmed clusters/outliers/gaps after step 4 → say `Suite is <ratio>x the
target with no redundancy/gap candidates confirmed this run.` and still no-op
the issue if none of this run's findings survived judgment (don't file an
empty one).

**Exception: a run triggered by `/e2e`'s budget step** (fleet-config#905).
There the suite is already known to be over budget. Filing nothing would leave
no open issue, so the next finish would run this whole audit again, and again
after that. Instead, upsert the managed issue with exactly one finding:

```markdown
- [ ] **.fleet.toml** — suite is <count> collected nodes against a budget of <limit>, with no redundancy confirmed on <YYYY-MM-DD>; its size is structural. Fix: declare `[e2e] test_budget = <count>` under `[e2e]` to ratchet at the current size (lower it as the suite trims), or scope down which behaviours need standing e2e coverage.
```

File it through step 5's mechanics unchanged: `get`, then the standard body
template with this line as the only `## Findings` entry plus the inventory,
context and run-log sections, then `upsert`. The open issue stops the
re-trigger, and the fix is a one-line, reviewable ratchet. An on-demand run
keeps the no-op above.

The same holds for the **time budget** (fleet-config#1018): a run triggered by
`E2E_TIME_BUDGET=over` that confirms no saving files one finding, `- [ ]
**.fleet.toml** — the browser leg took <s> s on the quiet full run of <date>
against a budget of <limit> s, with no saving confirmed; Fix: ratchet `[e2e]
time_budget_s = <s>` or act on the ranked options above`. A run triggered by
**growth alone** that confirms nothing files nothing: step 5b's record already
stops the re-trigger.

## Hard rules

- **Measure with `e2e_test_audit.py`, never by eye.** File/test counts, node
  counts, clusters (name *and* matrix), and outliers come from the helper
  (step 3) — the LLM never re-derives them by reading test files. Judgment is
  confined to step 4 (confirming clusters, confirming gaps, materiality,
  writing the issue).
- **A scan that could not resolve a test dir reports `unknown`, never clean.**
  `test_dirs_resolved: false` is its own outcome (step 2) — never summarized
  as "no tests", which is what let a bogus resolution read as a clean suite.
- **Never edits, merges, or deletes a test.** Report-only, always — a test
  suite is safety equipment: this skill proposes, a human disposes. Actually
  consolidating tests is separate, explicitly-scoped follow-up work.
- **One managed issue per repo per kind — the helper owns identity.** Always
  go through `skills/_lib/audit_issue.py` (`get` then `upsert`) with `--kind
  e2e-redundancy`. Never hand-roll a `gh issue create`.
- **Not a coincidence detector without confirmation.** A helper-reported
  cluster or gap is a candidate, not a finding, until step 4 confirms it by
  reading the actual suite.
- **Citations or it didn't happen.** Every finding points at a real
  `file:test-name` (or `file` for a size finding) or a real declared view.
- **Never auto-tick or auto-close** the issue — it's a living backlog;
  closing is the user's call via `/issue-finish`.
- **No AI attribution; no hard-wrapped issue-body paragraphs** (per global
  CLAUDE.md).
- **Never scheduled weekly.** Per fleet-config#406, do not wire this into
  `run-weekly.bat` or any cron. It runs on demand, or when `/e2e`'s budget
  step prints `E2E_AUDIT_TRIGGER=yes` with no open `e2e-redundancy` issue:
  over `[e2e] test_budget` (default 15, fleet-config#901), over `[e2e]
  time_budget_s`, or ~10 new test functions since the last audit
  (fleet-config#1018, Roberto's 2026-09-25 decision). That trigger is
  feature-driven: it fires on a finish, never on a clock.
- **Never starts a gate.** Time comes from logs the gate already wrote.

## Notes

- Decision record + the reusable audit/dedupe pattern: fleet-config#406.
  Standalone skill vs. a `/codebase-audit` lens was decided **standalone** —
  folding in would put this on that skill's weekly cadence, which #406
  rejects.
- `e2e-redundancy` is a first-class audit bucket (`audit_issue.py` `KINDS`) —
  `/cleanup-fleet e2e-redundancy` fans out fixers, and `/issue-triage` treats
  it like any other issue.
- The suite-size target and the "delete with the feature" discipline are
  owned by `project-scaffolding`'s `docs/playwright-ui-testing.md` — this
  skill measures against that target, it doesn't redefine it.
- **Split with `/e2e` (fleet-config#556):** `/e2e` is the *execution +
  inline maintenance* half — it routes and runs the proportionate slice for
  the current diff and edits tests in-branch (delete-with-the-feature,
  qualifying additions). This skill stays the *review* half: on-demand,
  whole-suite, report-only. The report-only hard rule above is unchanged.
- First validated target: `app-launcher`'s `tests/e2e/` (~60 files / ~400
  collected nodes against the 15-test target).
