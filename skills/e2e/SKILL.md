---
name: e2e
description: Decide, run, and maintain a repo's end-to-end tests proportionate to the diff — deterministic classify_e2e routing where adopted, self-healing bootstrap where not, LLM judgment only as fail-safe (always escalating to full, never narrowing). Delegated to by /issue-finish, /issue-yolo, /issue-batch; also standalone — e.g. "/e2e", "/e2e plan", "/e2e full", "run the e2e", "do we need an e2e run for this?".
---

# e2e

**Goal:** One place that answers — and acts on — "what end-to-end testing does
this diff actually need?" Stop burning CPU, wall-clock, and tokens on full
browser suites for diffs that don't touch the browser surface, **without ever
under-testing**: uncertainty always escalates to the full suite, never narrows
it. The issue-* skills delegate here instead of each embedding its own e2e
criteria; the user can also invoke it standalone.

The routing *mechanism* is project-scaffolding's diff-proportionate e2e
routing (`docs/e2e-routing.md`): each repo's own `scripts/classify_e2e.py`
reads that repo's `.fleet.toml` `[e2e]` table and maps the changed-file set to
a tier — `skip` (no browser suite), `static` (narrow smoke slice), `full` —
fail-safe to `full`. A repo declaring `[[e2e.surface]]` also gets `surface`: a
`full` diff whose every full-tier path sits in one declared surface runs only
that surface's targets (project-scaffolding#258). This skill fronts it fleet-wide: runs it where adopted,
**bootstraps it where missing** (self-healing adoption), and falls back to
same-vocabulary LLM judgment only where the classifier can't exist yet.

## Browser projections: a second engine only where it matters

A second browser projection (WebKit, usually with an iPhone descriptor) runs
**only** for tests where the engine or the viewport changes the outcome:
layout and geometry, touch targets, the nav, the composer and keyboard input,
safe-area. Functional tests (routes, polling, payloads, menus, readbacks,
dialogs with no geometry and no engine branch) run on **one** engine
(fleet-config#1026, Roberto's 2026-09-25 decision). Evidence, app-launcher#1220:

- 0 WebKit-engine product bugs in 400 merged PRs. Of 32 WebKit-only reds, one
  was an engine-independent race that the slower engine showed first (#732);
  the rest were test bugs, flakes or load.
- WebKit was 61% of browser time (1212 s against 762 s, 310 nodes each).
- iOS-specific bugs don't reproduce in desktop WebKit anyway: the headless
  projection can't see `env(safe-area-inset-*)` or installed-PWA geometry
  (#1099), and the phone-visible bugs of that window were found on the device.
  The real iPhone check stays on-device.

What it gives up: WebKit-only JS behaviour in functional flows (a
Safari-divergent API on a path only a behavioural test drives), and the
slower engine exposing a race first. Say so when you apply it.

## Arguments

- Nothing (`/e2e`) → classify the accumulated diff (branch + working tree),
  state tier + reason, run the routed slice.
- `plan` → classify and report only; run nothing. (Dry-run.)
- `full` → force the full suite regardless of routing. (`full` is the maximum
  tier, so forcing *up* is always allowed; there is no argument to force
  *down* — narrowing below the classifier is never permitted.)

## Steps

Run in order. Stop on any hard failure with a one-line error.

### 1. Probe the repo — deterministic facts first

From the repo root, read the project's `CLAUDE.md` (verification gate, `## CI
expectations` block) and run:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_route.py probe .
```

It prints `CLASSIFIER=`, `CLASSIFIER_MATCHES_SCAFFOLD=`, `E2E_TABLE=`,
`SUITE=`, `WEB_SURFACE=`, `WEB_KIND=`, `WEB_REASON=`. Every branch below is
keyed on these printed facts — never re-derive them by eye.

### 2. No suite at all (`SUITE=absent`) — evaluate, don't route

Nothing to route. Two cases, keyed on the probe's `WEB_SURFACE`:

- **`WEB_SURFACE=no`** (pipeline/library repo) → report `e2e: n/a — no suite,
  no web surface; deterministic tests are the coverage here` and stop. Never
  recommend an e2e suite for a non-web repo.
- **`WEB_SURFACE=yes`** (webapp or Streamlit) → evaluate whether a suite is
  worth *starting*, against project-scaffolding's Loop-2 promotion bar
  (`docs/playwright-ui-testing.md`): silent breakage would hurt, no unit test
  can catch it, and the behavior has stabilized. Bar met → propose a minimal
  starter suite (boot-or-adopt fail-loud conftest, a handful of tests, well
  under the 15-test target) — **propose in the report; build it only on the
  user's OK or as its own issue**, never as a silent side effect of a finish
  flow. Bar not met (early spike, still churning) → say so and stop.

### 3. Suite present, classifier missing — self-heal

When `SUITE=present` and `CLASSIFIER=absent`, bootstrap the router **into the
current working branch** as part of this run:

1. Copy the scaffold's parameterized classifier byte-verbatim:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_route.py bootstrap .
   ```
   `BOOTSTRAP=copied` / `exists-identical` → proceed. `BOOTSTRAP=refused`
   (an existing *custom* classifier differs from the scaffold's) → **honor
   the custom one as-is** and skip to step 4; migrating it to the
   parameterized version is deliberate work needing the user's OK
   (`--force`), never a side effect.
2. If `E2E_TABLE=absent`, author a **conservative starter `[e2e]` table** in
   `.fleet.toml` from the repo's real layout (schema:
   project-scaffolding `docs/e2e-routing.md`): explicit `none` rules only for
   plainly inert paths (`docs/`, `*.md`, backend-only dirs with no rendered
   surface), `static` for image/font asset trees, a `full` rule for the
   app/web dir. Everything unmatched already fails safe to `full` by
   construction — when in doubt, leave a path unclassified rather than
   guessing it narrow.
3. Add the anti-drift test the routing convention requires
   (`tests/test_classify_e2e.py`, per `docs/e2e-routing.md`): load the real
   `.fleet.toml`, assert one representative path per rule lands in its
   intended tier.

**Branch discipline:** these are working-tree edits that ride the current
feature branch. Standalone invocation while sitting on `main` → do this run's
classification by judgment instead, and *offer* the bootstrap as a follow-up
(its own branch/issue) — never edit `main` directly.

### 4. Route the diff

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_route.py route .
```

- `SOURCE=classifier` → honor the printed `E2E_TIER` / `E2E_BROWSERS` /
  `E2E_PYTEST_TARGET` / `E2E_REASON` (and `E2E_SURFACE` for `surface`)
  **verbatim**. Never override downward. The helper has already turned an
  unrecognised tier, or a `surface` with no name or targets, into
  `SOURCE=classifier-error`.
- `SOURCE=classifier-error` → the helper already escalated to `full`; run
  full and surface the error in the report.
- `SOURCE=judgment` (no classifier and bootstrap wasn't possible this run) →
  classify the changed-file set yourself using the *same tier vocabulary* and
  the same fail-safe: only a diff you can positively argue has **no** browser
  impact (backend-only, docs-only, tooling-only) may route below `full`;
  anything mixed, uncertain, or unfamiliar runs `full`. Judgment **never**
  emits `surface`; only a classifier reading a declared surface map may.
- The `full` argument forces `E2E_TIER=full` regardless of the above; `plan`
  stops here and reports the decision without executing.

### 5. Execute the routed slice — synchronously

- `skip` → run nothing browser-shaped. Say so explicitly (`e2e: skip —
  <reason>`); the repo's deterministic pytest/gate still covers the backend.
- `static` / `full` / `surface` → run the printed pytest target through the
  **repo's own venv** (`& .\.venv\Scripts\python.exe -m pytest <target>` plus
  the routed `--browser` flags where the suite supports them). Browser legs
  come from the table (`static_browsers`) or the repo's own conventions
  (phone-first repos parametrize WebKit themselves) — never invent a leg the
  repo doesn't declare, and never add a second projection to a functional
  slice (see "Browser projections" above). A `surface` target is space-separated: pass each path
  as its own pytest argument, with suite-default browsers exactly as `full`.
- **Deduplicate against the verification gate:** when a repo's pre-ship gate
  (e.g. `scripts/verify-before-ship.ps1`) already executed this same routed
  slice in this session, do **not** re-run it — carry that result into the
  report. One proportionate run per session is the whole point.
- **Synchronous only.** Run to completion in the foreground (or poll a
  background task to completion within this turn) — never fire-and-forget
  and end the turn (global CLAUDE.md; fleet-config#314). Boot-or-adopt is
  the suite conftest's job — never hand-boot the app around it.
- Report failures faithfully, with output. A red slice stops any delegating
  finish flow exactly like a red gate.

### 6. Inline suite maintenance (same run, same branch)

Cleanup is part of the flow, not a periodic chore:

- **Feature removed by this diff** → find the e2e tests that covered it
  (selectors, routes, widget keys, view names the diff deleted) and **remove
  them in this same branch** — the scaffold's "delete the test when you
  delete the feature" rule, operationalized.
- **New user-visible behavior meeting the Loop-2 bar** (silent breakage
  hurts + no unit test can catch it + behavior stabilized) → **add** the
  regression test directly in this branch, respecting the <15-test target —
  at or over target, merge/delete before adding
  (`docs/playwright-ui-testing.md`: "if tempted to add #20, delete two
  first"). Put it on the second projection only when it asserts geometry,
  touch, input, nav, composer or safe-area behaviour; otherwise pin it to one
  engine with the repo's own mechanism (a `chromium_projection_only`-style
  fixture or marker).
- **Table maintenance** — two drift signals, fixed in the same branch when
  they fire: a path that routed `full` as *unmatched* but is plainly inert →
  add its narrowing rule + a representative assertion in
  `tests/test_classify_e2e.py`; a new e2e-relevant directory with no `full`
  rule → add rule + assertion (the same-PR anti-staleness contract from
  `docs/e2e-routing.md`).

Nothing here runs for `plan` invocations.

### 6b. Suite budget — the `/e2e-audit` trigger

Whenever `SUITE=present`, measure the suite against its budgets and its growth
(fleet-config#901, #1018). Bloat is feature-driven, so the check rides every
finish rather than a clock (fleet-config#406):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_test_audit.py budget .
```

It prints three measurements and one decision:

- **Nodes:** `E2E_BUDGET` against `.fleet.toml` `[e2e] test_budget` when
  declared (a per-repo ratchet: set at the current size, lowered as the suite
  trims), else the scaffold's 15.
- **Time:** `E2E_TIME_BUDGET` for the browser leg of the latest quiet
  full-tier gate run in the repo's progress log, against `[e2e]
  time_budget_s` (`undeclared` when there is none). A loaded run, a run
  routed below `full` or no log is `unknown`, never `within`.
- **Growth:** `E2E_GROWTH`, test functions gained since the last
  `/e2e-audit` recorded its count (`none-recorded` before the first).
- **Decision:** `E2E_AUDIT_TRIGGER=yes|no|unknown` with its reason: `yes`
  when over either budget or at +10 test functions since the last audit.

Then:

- `E2E_AUDIT_TRIGGER=no` → report the lines, nothing else.
- `E2E_AUDIT_TRIGGER=unknown` → report `budget: unknown
  (<E2E_AUDIT_TRIGGER_REASON>)`. Never fold it into `no`.
- `E2E_AUDIT_TRIGGER=yes` → look for the repo's open managed issue:
  `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind e2e-redundancy`.
  If one is open, cite it in the report. If none is open, run **`/e2e-audit`**
  on this repo in this same run as `/e2e-audit budget` (report-only; it
  files the deduped issue; when no finding survives an over-budget trigger it
  still files the `test_budget` / `time_budget_s` ratchet finding, per its
  step 6 exception, and a growth-only trigger that finds nothing files nothing
  because its recorded count stops the re-trigger), then cite what it filed.
  If the lookup itself fails, report
  `budget: triggered, audit issue state unknown` and do not run the audit blind.
  In a `plan` invocation, report the verdict only and run no audit.

The budget never blocks the finish. Its job is to make the overage
impossible to miss and to file the pruning work exactly once.

### 7. Report

One block, echoed verbatim by delegating skills into their finish summary:

```
/e2e — <repo>
  source: classifier | judgment | bootstrapped-this-run | classifier-error
  tier: skip | static | full | surface (<name>) | n/a   reason: <E2E_REASON or judgment rationale>
  ran: <pytest target + browsers | nothing | carried from gate run>
  result: PASS | FAIL (<counts>) | not run (plan) | n/a
  maintenance: <n removed / n added / table rules added | none>
  budget: <nodes <count>/<limit>, time <s>/<limit> s | undeclared, growth <+N | none-recorded> — trigger <no | yes: e2e-redundancy #<N> | unknown (<reason>)> | n/a>
  suite: <n/a | absent — recommendation: <one line>>
```

## Hard rules

- **Never narrow below the classifier; uncertainty escalates to `full`.**
  The routing convention's core invariant (`docs/e2e-routing.md`) — under-
  testing must never be the outcome of uncertainty, a malformed table, an
  unmatched path, or a classifier error.
- **Evaluation is mandatory before every PR; execution is proportionate.**
  `/issue-finish` and `/issue-yolo` always run this skill — the *decision*
  always happens and lands in the finish summary; `skip` is a legitimate
  outcome, silence is not.
- **Measure with `e2e_route.py`, never by eye.** Probe facts, routing, and
  the bootstrap copy all come from the helper; LLM judgment is confined to
  the fallback classification, the starter table, maintenance, and the
  suite-worth evaluation.
- **Bootstrap is byte-verbatim from the scaffold.** Never hand-author a
  classifier; never overwrite a custom one without the user's explicit OK.
- **Suite-worth evaluation is web-only.** `WEB_SURFACE=no` repos never get
  an e2e-suite recommendation.
- **All edits ride the current feature branch — never `main` directly**, and
  committing follows the global git discipline (prepare, don't auto-commit,
  unless a delegating flow owns the commit step).
- **Synchronous execution only** — a scheduled/unattended caller gets a
  completed result or a loud failure, never a backgrounded orphan.

## Notes

- **Split with `/e2e-audit`:** this skill is the *execution + inline
  maintenance* half (acts on the current diff, edits tests in-branch);
  `/e2e-audit` is the *review* half — on-demand, whole-suite, **report-only**.
  They never overlap: this skill doesn't audit the resting suite; the audit
  never runs or edits tests.
- Decision record: fleet-config#556 (delegation contract, self-healing
  adoption, direct test additions, web-only suite evaluation).
- Mechanism + schema ownership stays with project-scaffolding
  (`docs/e2e-routing.md`, `docs/playwright-ui-testing.md`) — this skill
  consumes the convention; it doesn't redefine targets or tiers.
