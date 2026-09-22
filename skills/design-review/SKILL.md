---
name: design-review
description: Review a running fleet web app's rendered design — measure the live app in real browsers (iPhone/Android/desktop × light/dark), score it against the versioned design rubric, and render one self-contained HTML report with now-vs-proposed mock-ups. Never starts, restarts or kills the app. E.g. "/design-review app-launcher", "/design-review app-launcher --no-publish", "review the rendered design", "how does the app score against the rubric".
---

# design-review

**Goal:** Grade what a fleet web app actually *renders* — computed font
sizes, WCAG contrast against the composited background, effective hit
rectangles, icon boxes, overflow, accessible names, controls per row — the
facts `/design-sync`'s static lint cannot see. Measure a **running** app in
real browsers, score every rule of `design.rubric.toml` from the numbers,
and hand back one HTML report a person reads: verdict and grade, scorecard,
findings by category with the standard each cites, now-vs-proposed mock-ups,
and where each fix lands (spec / scaffold / app).

**Measure with the helper, never by eye.** Every number, status, grade and
finding sentence comes from `skills/_lib/design_review/` (deterministic,
unit-tested); the report's sentences are the rubric's own `fix_template` +
`standard` with the measured values substituted. This skill orchestrates
four commands and prints a summary — it authors no finding, assigns no
grade, and (until #973) applies no judgment.

**Read-only, always.** The walk clicks primary tabs, opens `<details>` and
`<dialog>`s, screenshots, measures, and leaves. It never starts, restarts
or kills the app or anything else, never writes into the target repo,
never commits run output, and never attaches a screenshot or captured page
text to an issue, PR or comment.

## Arguments

- One argument that is a repo name or path → the **target repo** (a key of
  `hooks/projects.toml` with a `webapp_port`, or a path that resolves to
  one; `<repo>-wt-<N>` worktree paths resolve to their repo). No argument →
  the **current repo** (cwd).
- `--no-publish` → write the report and print its path; skip the artifact.
- `file` and `fleet` are **reserved, not yet** (#974: report from an
  existing run directory / a fleet-wide sweep). Say so and stop if passed.

More than one target → say only one is accepted and stop.

## Steps

Run in order. Stop on any hard failure with a one-line error. Every command
is this repo's venv Python on the package directory:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/design_review <cmd> ...
```

### 1. Pre-flight — is the app listening?

```
... design_review probe <repo>
```

Prints `TARGET= BASE_URL= ROOT= CLAUDE_MD= PROBE= DETAIL=`. Read `PROBE=`:

- `listening` → continue.
- `NOT_LISTENING` (the port refused) or `TIMEOUT` (no answer) → **stop**.
  Open the file named by `CLAUDE_MD=` (the target's own `CLAUDE.md`) and
  quote its restart recipe pointer — the tray / restart section, typically
  `tray.bat --restart` — as the one next action for the user. **Never run
  it, never start, restart or kill anything yourself**: a launcher-spawned
  session cannot know what the live process is serving, and the repo's
  `CLAUDE.md` owns the restart decision. Print the `DETAIL=` line so the
  two conditions stay distinguishable.
- `BAD_URL` or `ERROR=` → stop and print it: the target is not a declared
  web app (`webapp_port` missing) or the name is unknown.

### 2. Measure — the read-only walk

```
... design_review measure <repo>
```

Runs the walk as a child of the **target repo's** `.venv` interpreter (that
is where Playwright lives; this repo's venv is stdlib-only), over the fixed
matrix `iphone` (WebKit) / `android` (Chromium) / `desktop` (Chromium) ×
light / dark — every primary tab, every `<dialog>`, and any
`[design.review].extra_steps` the target's `.fleet.toml` opts into. A full
app-launcher matrix takes about four minutes and leaves ~144 PNGs in the
run directory; run it once per review, never in a loop.

Read the printed lines: `RUN_DIR=`, `METRICS=`, `SCREENS=<ok>/<total>`,
`UNMEASURED=<reason>|none`, `COMMIT=`. The run directory is
`~/.claude/hooks/state/design-review/<target>/<UTC stamp>/` — gitignored
state, never a tracked tree. `UNMEASURED≠none` (`PLAYWRIGHT_MISSING`,
`NOT_LISTENING` mid-run, `BROWSER_FAILED`, …) is a **result, not a crash**:
continue, and the report will carry every affected rule as `unmeasured`
rather than passed. Exit 2 (target unresolved, rubric invalid) → stop and
print the `ERROR=` line.

### 3. Evaluate — rules, scores, grades

```
... design_review evaluate <METRICS> --out <RUN_DIR>/evaluate.json
```

Pure stdlib: `metrics.json` + `design.rubric.toml` + `~/.claude/design.md`
/ `design.dark.md` tokens → one JSON document with every rule's
`pass | fail | unmeasured`, evidence, measured values, and the category and
overall grades (each category starts at 100 and loses the rubric's penalty
once per failing rule). Prints `EVALUATE=`, `GRADE=`, `SCORE=`,
`FAILED=<n>/<total>`, `UNMEASURED=`, `CATEGORIES=`, `MOCKUPS=`. Keep
`evaluate.json` in the run directory — #974 diffs runs from it.

### 4. Render — the report

```
... design_review render <RUN_DIR>/evaluate.json
```

Writes `<RUN_DIR>/report.html` (`REPORT=`) — self-contained, inline CSS,
no external request, follows the viewer's theme, works at phone width.
Findings are ordered by severity then rule id within each category, every
finding carries its rule id, the header prints the rubric version, grades
are printed from the JSON, and mock-ups are redrawn with placeholder names
only. A rule whose mock-up template needs a value the run did not measure
renders without one and says which value was missing. The page never
embeds or links a screenshot.

### 5. Publish (private artifact) or print the path

- `--no-publish` → skip; print `REPORT=`.
- Otherwise, **only when this host exposes an artifact tool** (Claude
  Code's `Artifact`): publish `report.html` as a **private** artifact and
  print its URL beside the path. The page contains selectors and short text
  samples from the app, so it is never made public and never pasted into an
  issue, PR or comment; the file stays in the run directory either way.
- No artifact tool (Codex, Pi, Copilot, a headless run) → print the path
  and stop; do not look for another way to host it.

### 6. Final summary

Print one block and stop:

```
/design-review summary — <target> @ <commit, 12 chars>

  overall:    <grade>  <score> / 100   <(partly unmeasured)>
  categories: typography <G> · color <G> · touch <G> · navigation <G> · layout <G> · components <G> · a11y <G>
  rules:      <n> fail · <n> pass · <n> unmeasured   (rubric v<version>)
  screens:    <ok>/<total> measured   (<UNMEASURED reason | none>)
  mock-ups:   <template ids drawn | none>
  run dir:    <RUN_DIR>
  report:     <REPORT>   <artifact URL | not published: --no-publish | no artifact tool on this host>
```

Every line comes from the `measure` / `evaluate` / `render` output, never
from reading the page.

## Hard rules

- **Never start, restart or kill anything.** A dead port ends the run at
  step 1 with the target `CLAUDE.md`'s restart pointer; the user runs it.
- **Read-only walk, loopback only.** No form submits, no destructive
  clicks beyond the target's own opt-in `extra_steps`; a `no_go` selector
  list in `.fleet.toml` is honoured by the walk.
- **Nothing is authored here.** Sentences are `fix_template` + `standard`;
  grades are the rubric's arithmetic; mock-ups are library templates filled
  from measurements. Where a value is missing the report says so — no
  improvised number, no improvised mock-up.
- **Screenshots and captured page text stay in the run directory.** Never
  attached, never committed, never quoted into GitHub.
- **Unknown is a state.** `unmeasured` rules and categories are printed as
  such; a category flagged `unmeasured` is never read as passing.
- **One run per review.** The matrix is minutes and hundreds of files; the
  determinism contract (same build → identical findings, severities, grades
  and mock-up set) is proven in the package tests, not by re-running here.

## Notes

- Deterministic core: `skills/_lib/design_review/` — `plan` (target/matrix),
  `capture` (run dir, probe, spawn), `walk` (the Playwright child), `measure`
  (the in-page script), `rubric`, `evaluate`, `mockups`, `report`, `cli`.
  Reference: `docs/skills.md` "Design review core".
- Rubric: `design.rubric.toml` at the repo root, versioned on its own; a
  rule's `mockup` id must exist in the library (validation refuses
  otherwise). Bump `[meta].version` on any rule change.
- Roadmap (#970): #973 adds a judgment stage (`judgment` slot in the report —
  checklist answers + an "Uncatalogued" list); #974 adds the run-to-run
  diff (`diff` slot), the ledger / issue upsert, and the `file` / `fleet`
  arguments. Both slots already render when the JSON carries the data.
- `/design-sync` is the static, authored-CSS sibling (tokens, contracts,
  vendored bytes); this skill is the rendered leg it reports as
  `unmeasured`. They file nothing in common yet.
