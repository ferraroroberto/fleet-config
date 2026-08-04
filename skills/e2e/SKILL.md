---
name: e2e
description: Decide, run, and maintain a repo's end-to-end tests proportionate to the actual diff — deterministic classify_e2e routing where adopted, self-healing bootstrap where not, LLM judgment only as fail-safe (always escalating to full, never narrowing). Removes tests with removed features, adds qualifying regressions, keeps the routing table honest. Delegated to by /issue-finish, /issue-yolo, /issue-batch before shipping; also standalone — e.g. "/e2e", "/e2e plan", "/e2e full", "run the e2e", "do we need an e2e run for this?".
---

# e2e

**Goal:** One place that answers — and acts on — "what end-to-end testing does
this diff actually need?" The point is to stop burning CPU, wall-clock, and
tokens on full browser suites for diffs that don't touch the browser surface,
**without ever under-testing**: uncertainty always escalates to the full
suite, never narrows it. The issue-* skills delegate here instead of each
embedding its own e2e criteria; the user can also invoke it standalone at any
time ("I changed a few things — e2e").

The routing *mechanism* is project-scaffolding's diff-proportionate e2e
routing (`docs/e2e-routing.md`): each repo's own `scripts/classify_e2e.py`
reads that repo's `.fleet.toml` `[e2e]` table and maps the changed-file set to
a tier — `skip` (no browser suite), `static` (narrow smoke slice), `full` —
fail-safe to `full`. This skill fronts that mechanism fleet-wide: runs it
where adopted, **bootstraps it where missing** (self-healing adoption), and
falls back to same-vocabulary LLM judgment only where the classifier can't
exist yet.

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

From the repo root, read the project's `CLAUDE.md` (verification gate,
`## CI expectations` block) and run:

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
  can catch it, and the behavior has stabilized. If the bar is met, propose a
  minimal starter suite (boot-or-adopt fail-loud conftest, a handful of tests,
  well under the 15-test target) — **propose in the report; build it only on
  the user's OK or as its own issue**, never as a silent side effect of a
  finish flow. If the bar isn't met (early spike, still churning), say so and
  stop.

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
  `E2E_PYTEST_TARGET` / `E2E_REASON` **verbatim**. Never override downward.
- `SOURCE=classifier-error` → the helper already escalated to `full`; run
  full and surface the error in the report.
- `SOURCE=judgment` (no classifier and bootstrap wasn't possible this run) →
  classify the changed-file set yourself using the *same tier vocabulary* and
  the same fail-safe: only a diff you can positively argue has **no** browser
  impact (backend-only, docs-only, tooling-only) may route below `full`;
  anything mixed, uncertain, or unfamiliar runs `full`.
- The `full` argument forces `E2E_TIER=full` regardless of the above; `plan`
  stops here and reports the decision without executing.

### 5. Execute the routed slice — synchronously

- `skip` → run nothing browser-shaped. Say so explicitly (`e2e: skip —
  <reason>`); the repo's deterministic pytest/gate still covers the backend.
- `static` / `full` → run the printed pytest target through the **repo's own
  venv** (`& .\.venv\Scripts\python.exe -m pytest <target>` plus the routed
  `--browser` flags where the suite supports them). Browser legs come from
  the table (`static_browsers`) or the repo's own conventions (phone-first
  repos parametrize WebKit themselves) — never invent a leg the repo doesn't
  declare.
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

The suite stays right-sized as a side effect of using this skill — cleanup is
part of the flow, not a periodic chore:

- **Feature removed by this diff** → find the e2e tests that covered it
  (selectors, routes, widget keys, view names the diff deleted) and **remove
  them in this same branch** — the scaffold's "delete the test when you
  delete the feature" rule, operationalized.
- **New user-visible behavior meeting the Loop-2 bar** (silent breakage
  hurts + no unit test can catch it + behavior stabilized) → **add** the
  regression test directly in this branch, respecting the <15-test target —
  at or over target, merge/delete before adding
  (`docs/playwright-ui-testing.md`: "if tempted to add #20, delete two
  first").
- **Table maintenance** — two drift signals, fixed in the same branch when
  they fire: a path that routed `full` as *unmatched* but is plainly inert →
  add its narrowing rule + a representative assertion in
  `tests/test_classify_e2e.py`; a new e2e-relevant directory with no `full`
  rule → add rule + assertion (the same-PR anti-staleness contract from
  `docs/e2e-routing.md`).

Nothing here runs for `plan` invocations.

### 7. Report

One block, echoed verbatim by delegating skills into their finish summary:

```
/e2e — <repo>
  source: classifier | judgment | bootstrapped-this-run | classifier-error
  tier: skip | static | full | n/a   reason: <E2E_REASON or judgment rationale>
  ran: <pytest target + browsers | nothing | carried from gate run>
  result: PASS | FAIL (<counts>) | not run (plan) | n/a
  maintenance: <n removed / n added / table rules added | none>
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
  `/e2e-audit` is the *review* half — on-demand, whole-suite,
  **report-only** (redundancy/bloat/gap findings into a managed issue). They
  never overlap: this skill doesn't audit the resting suite; the audit never
  runs or edits tests.
- Decision record: fleet-config#556 (delegation contract, self-healing
  adoption, direct test additions, web-only suite evaluation).
- Mechanism + schema ownership stays with project-scaffolding
  (`docs/e2e-routing.md`, `docs/playwright-ui-testing.md`) — this skill
  consumes the convention; it doesn't redefine targets or tiers.
