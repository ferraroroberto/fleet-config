---
name: e2e-audit
description: On-demand audit of a repo's e2e/regression suite for redundancy, bloat, and coverage gaps against project-scaffolding's "<15 tests total" target — deterministic inventory + near-duplicate clustering via e2e_test_audit.py, then a deduped e2e-redundancy issue for /cleanup-fleet. Never rewrites/deletes tests. Never scheduled — run it after a burst of feature work. E.g. "/e2e-audit", "/e2e-audit app-launcher", "audit the e2e suite for bloat".
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

### 4. LLM judgment layer (only where measurement can't reach)

- **(a) Confirm each cluster.** Read the colliding tests' actual
  bodies/selectors. A genuine near-duplicate (same view, same assertion, same
  setup) is a merge candidate — say which to keep. A coincidental name
  collision is **not** a finding — say so and drop it; don't force every
  cluster into the issue.
- **(a2) Confirm each matrix cluster.** Read the members' bodies. Twins that
  differ only in which violation they assert, all swept over the same matrix,
  collapse into one parametrized test with no coverage loss — usually the
  largest node-count win available. Tests that genuinely assert different
  behaviour across the matrix are legitimate coverage: drop them, don't pad
  the issue.
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

- [ ] **<file>:<test name> ~ <file>:<test name>** — near-duplicate intent; candidate to merge. Fix: keep <which>, drop <which>, and say why.
- [ ] **<file>** — <n> lines, far above the suite median; candidate to split or de-duplicate internally.
- [ ] **<view>** — declared key view with no matching test found (confirmed by reading the suite, not just the substring check). Fix: add coverage for it.

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

## Context

<short paragraph: overall shape (e.g. "196 raw / ~400 collected nodes vs a 15-test target — no per-file duplication found, but the suite has never been pruned"), the biggest opportunity, anything the next fixer should know.>

## Run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Title is **stable** — `audit: e2e-redundancy findings`, no count suffix.

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
  filed: https://github.com/<owner>/<repo>/issues/<N>   (e2e-redundancy)
```

Zero test files → report and stop (step 2), file nothing. Tests but zero
confirmed clusters/outliers/gaps after step 4 → say `Suite is <ratio>x the
target with no redundancy/gap candidates confirmed this run.` and still no-op
the issue if none of this run's findings survived judgment (don't file an
empty one).

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
- **Never scheduled weekly.** On-demand only, per fleet-config#406 — do not
  wire this into `run-weekly.bat` or any cron. Run it after a burst of
  feature work, not on a clock.

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
