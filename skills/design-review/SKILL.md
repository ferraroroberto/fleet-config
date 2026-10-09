---
name: design-review
description: Review a running fleet web app's rendered design — measure the live app in real browsers (iPhone/Android/desktop × light/dark), score it against the versioned design rubric, have a fresh-context judge answer the rubric's bounded checklist from the local screenshots, diff the run against the last one from a local ledger, and render one self-contained HTML report with now-vs-proposed mock-ups; `file` upserts the findings into the repo's one managed issue, `fleet` sweeps every declared web app serially; `full-app` runs the whole-app redesign review (inventory, one shared system, Step N/M builds, write-back into the design system). Never starts, restarts or kills the app. E.g. "/design-review app-launcher", "/design-review app-launcher file", "/design-review fleet", "/design-review app-launcher --judges 2", "/design-review app-launcher full-app", "redesign the whole app", "review the rendered design", "how does the app score against the rubric".
---

# design-review

**Goal:** Grade what a fleet web app actually *renders* — computed font
sizes, WCAG contrast against the composited background, effective hit
rectangles, icon boxes, overflow, accessible names, controls per row — the
facts `/design-sync`'s static lint cannot see. Measure a **running** app in
real browsers, score every rule of `design.rubric.toml` from the numbers,
have a fresh-context judge answer the rubric's bounded `[[judgment]]`
checklist from the run's own screenshots, record the run in a local ledger
and diff it against the previous one by rule id, and hand back one HTML
report a person reads: verdict and grade, scorecard, what changed since
the last run, findings by category with the standard each cites, the
checklist answers with anything uncatalogued (outside the grade),
now-vs-proposed mock-ups, and where each fix lands (spec / scaffold / app).
On request (`file`) the app-owned findings become one managed GitHub
issue per repo; `fleet` runs the same review over every declared web app.

**Measure with the helper, never by eye.** Every number, status, grade and
finding sentence comes from `skills/_lib/design_review/` (deterministic,
unit-tested); the report's sentences are the rubric's own `fix_template` +
`standard` with the measured values substituted. This skill orchestrates
the commands and prints a summary — it authors no finding, assigns no
grade, and composes no issue body. The one judgment it applies is **bounded**: a fixed checklist, a
fresh agent that sees only this run's screenshots and `metrics.json`,
yes / no / na per question, schema-validated, never a grade input.

**Read-only, always.** The walk clicks primary tabs and the header Settings
gear, opens `<details>` and `<dialog>`s, screenshots, measures, and leaves. It never starts, restarts
or kills the app or anything else, never writes into the target repo,
never commits run output, and never attaches a screenshot or captured page
text to an issue, PR or comment.

## Arguments

- One argument that is a repo name or path → the **target repo** (a key of
  `hooks/projects.toml` with a `webapp_port`, or a path that resolves to
  one; `<repo>-wt-<N>` worktree paths resolve to their repo). No argument →
  the **current repo** (cwd).
- `--no-publish` → write the report and print its path; skip the artifact.
- `--synthetic` → measure the target's **synthetic instance** instead of the
  live app: step 1's probe is skipped, and step 2 passes `--synthetic` to
  `measure`, which boots the command the target declares in
  `[design.review.synthetic]` (a throwaway copy with synthetic data), walks
  the URL it prints — steps marked `synthetic = true` included, under that
  block's own `no_go` — and stops it. Use it for surfaces the live walk must
  not open, such as a session's Chat and Terminal views. A target without
  the block measures `UNMEASURED=SYNTHETIC_UNDECLARED`; a launcher that
  never prints its URL, `SYNTHETIC_FAILED`. Synthetic runs are diffed only
  against earlier synthetic runs. A **live** run measures only what the app
  held at capture time, so the report's method list says `states not walked`
  (rows, per-item controls and populated overlays an empty app never shows;
  photo-ocr#129: sub-44px per-photo controls beside a fixed TOUCH-01) and, with
  a synthetic instance declared, points at `--synthetic`; carry that line into
  the summary, and when a rule reads as fixed, say that only the walked states
  were measured (fleet-config#1163).
- `--no-judgment` → skip step 4 (no judge agent spawned); the report has no
  judgment section and the summary says `judgment: skipped (--no-judgment)`.
- `--judges N` → `N` independent fresh-context judges in step 4 (default
  `1`; `2` is the value the issue names). Answers every judge agrees on are
  kept, disagreements are listed as `not confirmed`, an uncatalogued
  finding survives only when every judge raised it (same title after
  normalisation, or the same question). `N` above the host's concurrent
  hard-tier cap is run in batches, never refused.
- `file` → after the report, **upsert the app-owned findings** into the
  target repo's one managed `design-review` issue (step 6b). Opt-in per
  run: without it step 6b is a dry run whose one summary line says what
  *would* be filed, and nothing reaches GitHub.
- `fleet` → the **fleet sweep** (below): every `hooks/projects.toml` table
  with a `webapp_port`, serially, one digest; `--devices` applies, the
  judgment stage is skipped, `file` opts into filing the app issues and the
  two digest issues. No other target argument is accepted with `fleet`.
- `full-app` → the **full-app review** (below): steps 1–5 on the target,
  then the whole-app redesign investigation, its build steps and the
  write-back into the design system. Combines with `--synthetic`; not with
  `fleet`.

More than one target → say only one is accepted and stop.

**Capability preflight (step 4 only):** read
[workflow-capabilities](../../docs/workflow-capabilities.md) and bind
*spawn* (a fresh, independent context — never a fork or a full-history
worker) and *collect* (the worker's terminal reply, within this turn) to
this session's actual tools before step 4. Claude Code: a fresh `Agent`
(general-purpose type, `run_in_background` + poll to completion, or a
foreground call); Codex native collaboration: `spawn_agent` with
`fork_turns: "none"` and `wait_agent` until the `FINAL_ANSWER` arrives. No
fresh spawn on this host → step 4 is **not run** and says so; never answer
the checklist yourself from the screenshots — a judge that shares the
orchestrator's context is the divergence the checklist exists to bound.

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
matrix `iphone` (WebKit, 390px) / `android` (Chromium) / `desktop` (Chromium) ×
light / dark — every primary tab, every `<dialog>`, the Settings pane behind
the header gear, and any `[design.review].extra_steps` the target's `.fleet.toml` opts into. A full
app-launcher matrix takes about four minutes and leaves ~144 PNGs in the
run directory; run it once per review, never in a loop.

Read the printed lines: `MODE=live|synthetic`, `RUN_DIR=`, `METRICS=`, `SCREENS=<ok>/<total>`,
`ABSENT=<n>`, `UNMEASURED=<reason>|none`, `COMMIT=`. `ABSENT` counts extra
steps whose target never appeared (`STEP_TARGET_ABSENT`, e.g. a row menu on
an empty list): that surface does not exist in this app state, so it is
named in the report but leaves unrelated rules alone. The run directory is
`~/.claude/hooks/state/design-review/<target>/<UTC stamp>/` — gitignored
state, never a tracked tree. `UNMEASURED≠none` (`PLAYWRIGHT_MISSING`,
`NOT_LISTENING` mid-run, `BROWSER_FAILED`, …) is a **result, not a crash**:
continue, and the report will carry every affected rule as `unmeasured`
rather than passed. Exit 2 (target unresolved, rubric invalid) → stop and
print the `ERROR=` line.

Each screen also records whether the app rendered the theme the leg asked
for (`theme_applied`, from `html[data-theme]` and the canvas luminance). An
app that re-applies its own theme after the walk stamps one makes a "light"
leg render dark (#1216); that screen is `unmeasured` for the colour rules
COLOR-02 to COLOR-05, never pass or fail against the wrong palette, and the
report's method section says so. The walk never fights the app for it. Say
it when you summarise: a light grade from such a run is not a light grade.

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

### 4. Judge — the bounded checklist (skip with `--no-judgment`)

The rubric's `[[judgment]]` entries (`J-01`…, ten seed questions: row
primary action, most prominent control, destructive prominence, one
reading order, icon-only learnability, shared tab anatomy, user-language
labels, casing, empty states, key fact at a glance) are answered by a
**fresh-context judge** that sees only this run's screenshots and
`metrics.json`. Three commands and one spawn:

```
... design_review judge-prompt <RUN_DIR>
```

Writes `<RUN_DIR>/judge-prompt.md` and prints `PROMPT=`, `SCREENS=`,
`QUESTIONS=`. The prompt is deterministic (same run + rubric → same bytes)
and carries only the checklist, the screen table with the **local**
`shots/<id>.png` (+ `-full.png`) paths, the `metrics.json` path and the
required output schema — never `evaluate.json`, `report.html`, another run
or an expected answer. Do not edit it and do not add to it.

Then **spawn one fresh agent per judge** (`--judges N`, default 1) through
the capability contract bound above, giving it **only** this instruction
plus the prompt file — nothing from this session:

> Your entire task is defined by the file `<PROMPT>`. Read it first and
> follow it exactly: read the screenshots it lists and the metrics file it
> names with your file-reading tool, and nothing else — no other file in
> that directory, no other run directory, no URL, no process. Your final
> message must be exactly the one JSON object the file asks for — no prose,
> no fence, no commentary.

Keep at most the host's concurrent hard-tier cap in flight (`docs/model-tiers.md`;
Claude: three) and **collect every judge's terminal reply within this
turn** — poll a background worker to completion, never end the turn
expecting a wake-up. Save each reply verbatim as `<RUN_DIR>/judge-<n>.json`
(a stray ```` ```json ```` fence is tolerated by the validator; anything
else that is not one JSON object is not). Then:

```
... design_review judge-merge <RUN_DIR> <RUN_DIR>/judge-1.json [<RUN_DIR>/judge-2.json ...]
```

Validates every reply against the schema (each seed id exactly once,
`yes | no | na`, `evidence` a real screen id in the question's scope, every
`no` mapped to one of its question's rule ids or backed by an uncatalogued
entry with a proposed metric + threshold), merges the judges, writes the
`judgment` document into `<RUN_DIR>/evaluate.json`, and prints
`JUDGMENT=ok|partial|unmeasured|not_confirmed`, `ANSWERS=<yes>/<no>/<na>`,
`UNCATALOGUED=<n>`, `ERRORS=<n>`, `DROPPED=<n>` (+ one `ERROR_DETAIL=` per
envelope violation, one `DROPPED_DETAIL=<why>` per dropped answer — name each
in the summary, one
`NOT_CONFIRMED=<id>:<votes>` per disagreement, one `NORMALIZED=<id>:<from>-><to>` per
`<screen id>-full` evidence read as its screen (the full-page PNG's stem; five of
seven recorded `unmeasured` judgments were only this, fleet-config#1158; say how
many in the summary), and `RUBRIC_MISMATCH=` when
`evaluate.json` was scored under another rubric version than the checklist
— carry that line into the summary). **The checklist is partial**
(fleet-config#1185): a malformed answer (an answer outside `yes | no | na`
such as `maybe`, a missing `severity`, evidence that is no screen id) is
dropped **on its own** and listed with its reason, the valid answers are kept,
and the judgment is `partial`; a `no` whose uncatalogued entry was dropped
goes with it. Only a reply that is not one JSON object of the two keys, or from
which no answer survives, makes the **whole** judgment `unmeasured` — the
report says so; it is a result, exit 0, and the run continues. Never re-prompt a judge to "fix"
its JSON, never hand-edit a reply, never answer a question yourself.
Grades cannot move here: `judge-merge` adds one key and touches nothing
else, and the tests assert it.

A dead port, a missing run directory or a judge that could not be spawned
is never a reason to start, restart or kill the app.

### 4b. Ledger — record the run, diff it against the last one

```
... design_review ledger <RUN_DIR>
```

After the judgment (so the entry carries `judgment_rubric_version`) and
before the render (so the report opens with the diff). Appends one few-KB
entry to `~/.claude/hooks/state/design-review/<target>/ledger.json` — run
id, stamps, `commit` (the checkout) and `live_build` (what the running
process reports on its `api_version_path`, `unknown` when unreadable —
two facts, never folded into one), rubric versions, grades, `rules{id:
status}` and the uncatalogued titles; the last 20 are kept — and writes
`diff` into `<RUN_DIR>/evaluate.json`. Read the printed lines:
`PREVIOUS=<run id>|none`, `FIXED=`, `REGRESSED=`, `NEW=` (with the ids),
`UNCHANGED=`, `UNMEASURED=`, `RUBRIC_CHANGED=<from>-><to>|none`,
`LIVE_BUILD=`, `COMMIT=`. `fixed` is fail→pass, `regressed` pass→fail,
`new` a failing id the previous entry did not have; a rule unmeasured on
either side is `unmeasured`, never fixed or regressed. `PREVIOUS=none` is
the first recorded run for this target. A `RUBRIC_CHANGED` line means a
moved rule may be the rubric, not the app — carry it into the summary.
The ledger never carries a screenshot path or captured text.

### 5. Render — the report

```
... design_review render <RUN_DIR>/evaluate.json
```

Writes `<RUN_DIR>/report.html` (`REPORT=`) — self-contained, inline CSS,
no external request, follows the viewer's theme, works at phone width.
The page opens with the scorecard and the "since the previous run" diff
from step 4b. Findings are ordered by severity then rule id within each
category, every finding carries its rule id, the header prints the rubric
version, grades are printed from the JSON, and mock-ups are redrawn with
placeholder names only. A rule whose mock-up template needs a value the run did not measure
renders without one and says which value was missing. The judgment section
(answers, evidence, mapped rule ids, the uncatalogued list with each
proposed rule) renders from the same JSON, outside the grade, and prints
`JUDGMENT=<status>|none`. The page never embeds or links a screenshot.

### 6. Publish (private artifact) or print the path

- `--no-publish` → skip; print `REPORT=`.
- Otherwise, **only when this host exposes an artifact tool** (Claude
  Code's `Artifact`): publish `report.html` as a **private** artifact and
  print its URL beside the path. The page contains selectors and short text
  samples from the app, so it is never made public and never pasted into an
  issue, PR or comment; the file stays in the run directory either way.
- No artifact tool (Codex, Pi, Copilot, a headless run) → print the path
  and stop; do not look for another way to host it.

### 6b. File — the managed issue (dry run unless `file` was passed)

```
... design_review file <RUN_DIR> [--file]
```

Always run it; pass `--file` only when the invocation carried `file`.
The helper merges this run's **app-owned** failing rules into the target
repo's one managed `design-review` issue (`audit_issue.py`, kind and label
`design-review`, title `design-review: rendered findings`, one per repo,
strays collapsed — the `design-drift` mechanics): each line is
`- [ ] **<rule id>** (<severity>, app) — <title> — <n> screens, worst
<screen id> · <standard>`; a re-run updates the line in place and keeps
its checkbox; a rule that passes again keeps its line, unticked, tagged
`_(fixed — passes since <date> @ <sha>)_`; an unmeasured one is
`_(carried — unmeasured this run)_`; uncatalogued findings (judgment
status `ok` or `partial`) are their own list keyed by question + title, titles and
severities only; a dated bullet lands in `## Review run log`. **Spec- and
scaffold-owned rules are not filed on the app** — they print as `SPEC=`
/ `SCAFFOLD=` here and reach fleet-config / project-scaffolding through
the fleet digest — **except** one whose fix already ships: a rule with an
`adopt` hint in the rubric (TYPE-02: vendor `_vendored/base`; A11Y-02:
vendor `_vendored/text-size`; COLOR-03: the `control-border` token) is
also filed as an app checkbox labelled `spec → app`, naming that fix, and
prints as `ADOPT=` (photo-ocr#129: an app sat at B with three such rules
and nothing app-owned to fix; fleet-config#1163). It stays on its owner's
list too. Rules with no existing fix (TYPE-01) stay off the app. A `[[design.accepted]]` entry with `rule = "<id>"` and a
`reason` in the target's `.fleet.toml` suppresses that rule (listed under
`## Accepted`); one that fails nowhere prints `PROBLEM=`.

Read `FILE=dry-run|filed`, `REPO=`, `ISSUE=<n>|none`, `CHANGED=yes|no`
(Findings / Uncatalogued / Accepted differ from the issue — the run log
alone is not a change), `FILED=<n> <ids>`, `UNCATALOGUED=`, `ACCEPTED=`,
`SPEC=`, `SCAFFOLD=`, `ADOPT=` (`FILED=` counts these too), `BODY=` (the merged body, `<RUN_DIR>/issue-body.md`,
written on a dry run too), `URL=`. Never `gh issue create` or `edit` by
hand, never paste the body yourself, never tick or close anything, never
add `Closes #`.

### 7. Final summary

Print one block and stop:

```
/design-review summary — <target> @ <commit, 12 chars>

  overall:    <grade>  <score> / 100   <(partly unmeasured)>
  categories: typography <G> · color <G> · touch <G> · navigation <G> · layout <G> · components <G> · a11y <G>
  rules:      <n> fail · <n> pass · <n> unmeasured   (rubric v<version>)
  screens:    <ok>/<total> measured   (<UNMEASURED reason | none>)
  since:      <PREVIOUS run id | first recorded run> · <n> fixed <ids> · <n> regressed <ids> · <n> new <ids> · <n> unchanged · <n> unmeasured
              <rubric changed <from> → <to>>   · commit <sha7> · live build <sha7 | unknown>
  judgment:   <ok | partial (<n> dropped) | not_confirmed | unmeasured> · <yes>/<no>/<na> · <n> uncatalogued · <n> judge(s)
              | skipped (--no-judgment) | not run (no fresh-context spawn on this host)
  mock-ups:   <template ids drawn | none>
  filed:      <REPO>#<ISSUE> <URL> · <n> app-owned · <n> uncatalogued · accepted <ids | none> · changed <yes | no>
              | dry run: <n> app-owned would be filed on <REPO> (<changed | unchanged>; pass `file` to upsert) · spec <ids> · scaffold <ids> (fleet digest)
  run dir:    <RUN_DIR>
  report:     <REPORT>   <artifact URL | not published: --no-publish | no artifact tool on this host>
```

Every line comes from the `measure` / `evaluate` / `judge-merge` /
`ledger` / `render` / `file` output, never from reading the page, a judge's
reply or the issue body. An uncatalogued finding is reported as a title in
this block and in the report — never re-typed into an issue or PR with the
judge's evidence text (it quotes the page).

## Fleet mode (`/design-review fleet [--devices ...] [file]`)

One command replaces steps 1–5 and 6b for every declared web app:

```
... design_review fleet [--devices iphone,desktop,android] [--file]
```

It walks every `hooks/projects.toml` table with a `webapp_port` in file
order, **one app at a time** — probe, measure, evaluate, ledger + diff,
render — so there is never more than one browser open. An app whose port
is `NOT_LISTENING` or `TIMEOUT` gets an `unmeasured` run and an `unmeasured`
ledger entry and is **never started, restarted or killed**; the sweep goes
on. The judgment stage is **skipped** in fleet mode (the checklist is a
per-app, on-demand step) and the digest says so. Budget: about four
minutes and ~144 PNGs per listening app, serially — run it once, never in
a loop, and never in a session that cannot stay for the whole sweep.

Read `FLEET_DIR=`, `APPS=`, one `APP=<name> probe= unmeasured= grade= score=
failed= fixed= regressed= new= run=` line per app, `MEASURED=`,
`UNMEASURED_APPS=<name:reason,...>|none`, `SPEC=`, `SCAFFOLD=`,
`PROMOTED=` (an **app-owned rule failing in two or more apps** is promoted
to the scaffold list once and left off every app issue), `JUDGMENT=skipped`,
`FILING=dry-run|filed`, `ISSUE=<repo> <url | body path>` per repo,
`DIGEST=` / `DIGEST_HTML=` — `fleet-digest.json` + `fleet-digest.html`
under `~/.claude/hooks/state/design-review/_fleet/<stamp>/`, the apps
table, the spec-owned list (fleet-config's issue), the scaffold-owned +
promoted list (project-scaffolding's issue), the filing outcome. Without
`file` every would-be body is written beside the digest and nothing
reaches GitHub; with `file` the app issues and the two digest issues are
upserted through the same merge as step 6b. Publish `fleet-digest.html` as
a private artifact where the host has one, else print the path.

Summary block for a sweep:

```
/design-review fleet summary — <stamp> (rubric v<version>)

  apps:       <n> declared · <n> measured · <n> unmeasured (<name:reason, …> | none)
  <app>:      <grade> <score> · <n> app-owned · <n> promoted · <n> fixed / <n> regressed / <n> new   (per measured app)
  <app>:      unmeasured — <reason>                                                                   (per unmeasured app)
  spec:       <ids | none>   → fleet-config
  scaffold:   <ids | none>   → project-scaffolding   (promoted: <ids | none>)
  judgment:   skipped in fleet mode — run /design-review <app> for the checklist
  filed:      <repo#N …> | dry run: <n> bodies at <FLEET_DIR> (pass `file` to upsert)
  digest:     <DIGEST_HTML>   <artifact URL | path only>
```

**The ratchet.** An uncatalogued finding the user accepts becomes rubric
data by PR with a `[meta].version` bump: a `[[rules]]` entry when it can be
measured (`measure.py` grows the metric; the question then leaves the
checklist), else a `[[judgment]]` question. Nothing automates that step.

## Full-app mode (`/design-review <repo> full-app`)

The whole-app redesign review. It investigates every tab of one app,
proposes **one shared component system** for the whole app, keeps
**everything reachable**, ships the result as **Step N/M issues** one PR at
a time with a **phone check after each**, and ends by **writing what it
learned back into the design system**. Each round starts from the standard
and leaves it better, so every app stays on one system and none forks a
look of its own. The principles it applies are `design.md`'s contracts
(page-header, glance card, action-row, avatar, status chip, meter, modal,
settings group); their rationale is `docs/design-system.md` › Round 8.
Worked example: app-launcher#1432 (two rounds, steps #1433–#1439).

Run it when an app reads as crowded or dated on the phone although the
measured review and `/design-sync` are mostly green (composition, not
tokens), or when it is the app's turn in the fleet rollout. Review one app
at a time, because each round starts from the previous round's write-back.
Unlike steps 3–6, this mode **authors** its investigation, which is its
purpose. The rest of the hard rules still bind it.

**A. Load the standard and the facts.** Read `docs/design-system.md` (the
latest Round entries) and `design.md` / `design.dark.md`. The review applies
them and finds what is missing; it does not re-derive them. Run steps 1–5
(judgment included), plus `--synthetic` where the target declares it, and
treat the failing rule ids and uncatalogued findings as evidence. Read the
target's `CLAUDE.md`, `README.md`, the source of every tab, sheet and
Settings section (read-only), and its open `design-review` /
`design-drift` issues, so known findings are not re-proposed as new.

**B. The investigation issue.** File one in the target repo through
`/issue-add` (or reuse an existing one), stating it is an investigation,
not a build: no code change, no PR, no restart. Acceptance: the inventory,
the mockup page with an everything-still-here table per tab, and a
comparison and recommendation as a text-only comment.

**C. The investigation, in this order** (it drives both the mockup page and
the comment):

1. **Inventory**: every fact and action on every tab, including drill-ins,
   menus, sheets and Settings. Give where each lives today and how often it
   is needed (`Many / day` · `Daily` · `Weekly` · `Rare` · `Once` ·
   `Debugging`). Frequency is a hypothesis the owner corrects.
2. **Diagnosis**: a numbered list of what makes each tab crowded or dated,
   each item tied to a contract or rubric rule id. Check at least: distinct
   treatments before the first useful row, status colour used as decoration,
   "normal" badges repeated on every row, duplicated state, an ellipsis that
   cuts the useful part at 390px, nested or tinted surfaces, permanent
   footnotes, mixed concerns on one card, and row height.
3. **Patterns borrowed**: the apps and patterns studied, and what each lends.
4. **The shared system**: the contracts applied to this app. Give the
   header's exceptions line per tab, the glance card per tab (write the
   tab's main question first), the row anatomy, the avatar badge, the status
   chip tone map as a table of this app's states, and the components to
   build once. A needed departure from `design.md` is flagged as a
   **proposed spec change** for step F, never invented for one app.
5. **One home per fact**: a table of fact, home, and elsewhere (a smaller
   form of the same component, or none).
6. **Per tab**: what is wrong today; the frames (today, proposed, each sheet
   or menu open); and the **everything is still here** table: item, today,
   proposed, lands (`First screen` · `One tap` · `Moved` · `Unchanged` ·
   `Duplicate removed` · `New`). Nothing reachable today becomes
   unreachable, and only duplicates are removed.
7. **What moves between tabs**: only the moves.
8. **Build plan**: Step N/M, **shared parts first** so later steps are
   mostly markup, then one tab per step. Each step is one issue, one branch
   and one PR, and ships alone. Give its size, files and e2e churn.
9. **Decisions for the owner**: numbered, each with a recommendation and the
   alternative. Any one can flip without changing the rest.

A large app may take two rounds. Round 1 draws 3–4 options for the
most-used tab and compares them (taps to the key tasks, new components,
build cost, e2e churn, recommendation). Round 2 extends the chosen option to
every tab, and round 1 moves to a collapsed archive on the page.

**D. The mockup page.** One self-contained local HTML page with inline CSS
and JS and no external request. It is never committed and never attached to
GitHub. Publish it as a **private** artifact where the host has one,
otherwise print its path.

- **Colours**: the spec's tokens as CSS custom properties, copied from
  `design.md` / `design.dark.md`.
- **Frames**: each screen is a **390px phone frame** (about 780px tall,
  scrolling inside), with a caption naming the state shown. A tab's frames
  form one strip that swipes on a phone and sits side by side on a desktop.
- **Theme**: light and dark with **one theme switch for every frame**, set
  by a pre-paint `data-theme` boot script from a page-local storage key,
  falling back to `prefers-color-scheme`.
- **Data**: **invented only** (names, numbers, sessions, hosts, projects),
  with stand-in brand marks. The lede says so.
- **Order**: the C outline, after the lede and a short version, with the
  kit drawn as one frame and the archive last.

Then post the **text-only record** on the investigation issue: the inventory
summary, the diagnosis, the options or the system, the one-home table, each
tab's headline, the build plan and the decisions. Include no screenshot and
no captured text.

**E. Decisions, build issues and the delivery loop.**

1. **Record the decisions.** The owner answers them, and a dated
   **decision-log comment** on the investigation issue records the answers.
   A session that cannot ask prints the decisions with their options and
   stops: no build issue is filed before they are answered.
2. **File the steps** through `/issue-add`, in order, each titled "Step k/M
   of #N" and self-contained: the current state with `file:line`, the
   target as drawn, the decisions it carries, acceptance (the gate plus the
   phone check after merge), out of scope, and one **constraints** block
   repeated on every step. That block names the contracts in play, the tone
   map, the header rule, the gate, the restart plus a force-reload of the
   installed PWA, the public-repo rule, and one step per PR.
3. **Upstream first.** A vendored component change gets its
   `project-scaffolding` issue first, and the step references it.
4. **Build one step at a time** through `/issue-start` → `/issue-finish`.
   After each merge, run the repo's restart recipe; then the owner
   force-reloads the installed PWA and checks light and dark. The next step
   starts only after "looks right", because desktop projections are not
   authoritative for the installed shell.
5. **Log every refinement.** When the owner refines a step, post a dated
   decision-log comment on that step's issue that names what it supersedes
   and restates the changed acceptance lines.
6. **Close the round.** Re-run `/design-review <repo>` after the last step
   (the ledger diff is the measured record), then close the umbrella with
   its steps and PRs.

**F. Write-back (mandatory closing step).** A round is not done until its
learnings reach the standard.

1. **List the learnings**: every principle that proved out, every owner
   refinement that generalises, every spec rule found wrong or missing.
2. **Route each one to the single place it belongs.** If `design.md` or
   `docs/design-system.md` already says it, or nearly, edit that line rather
   than adding a second statement.
   - The **rule** goes to `design.md` **and** `design.dark.md` in one PR: a
     frontmatter token, a contract, a Do/Don't line.
   - The **reason** goes to a new dated Round entry in
     `docs/design-system.md`, citing the umbrella as the worked example.
   - Anything **measurable** goes into the rubric through **the ratchet**
     (above).
   - A **component** change goes to `project-scaffolding`'s vendored copy,
     then `/propagate-vendored`.
3. **Ship it** as one fleet-config issue through `/issue-add`, then the normal
   PR. Run `design_lint all <root> --spec <old|new> --spec-dark <old|new>`
   (`/design-sync`'s helper) on the reviewed app and at least one other web
   app. It must show no new drift, or name the drift a new rule creates on
   purpose and file it on the apps it hits.
4. **Keep it portable**: no app-specific names in the rule text, and no
   person, host or device anywhere. The app appears only as the cited
   example.
5. **Nothing learned?** Say so on the umbrella ("write-back: no new
   principle; the standard held").

## Hard rules

- **Never start, restart or kill anything.** A dead port ends the run at
  step 1 with the target `CLAUDE.md`'s restart pointer; the user runs it.
- **Read-only walk, loopback only.** No form submits, no destructive
  clicks beyond the target's own opt-in `extra_steps` and the one built-in
  click, the header **Settings gear** (#1217: the `home-head` button named
  "Settings", found by role and accessible name; it only switches a view
  and writes nothing; Roberto's decision, 2026-10-04); a `no_go` selector
  list in `.fleet.toml` is honoured on every click of a step and on the
  gear, including anything inside a `no_go` element. An app with no such
  button is `SETTINGS_GEAR_ABSENT`, its own state in the report (A11Y-02
  stays `unmeasured`, never a pass); a pane the gear opens without the
  text-size control fails A11Y-02.
- **Embedded content is the target's to declare.** A preview themed by
  something other than the app (a session-themed slide) is named in
  `[design.review] exclude_selectors`; nothing inside it is measured or
  scored, and `metrics.json` records the exclusion (#1185). Never exclude
  something by judgment during a run, and never put a failing region of the
  app's own UI on the list: that hides a defect instead of fixing it.
- **Nothing is authored here** (steps 1–7 and fleet mode; full-app mode
  authors its investigation, never a finding or a grade). Sentences are `fix_template` + `standard`;
  grades are the rubric's arithmetic; mock-ups are library templates filled
  from measurements. Where a value is missing the report says so — no
  improvised number, no improvised mock-up.
- **Judgment is bounded and never a grade input.** The checklist is rubric
  data; the judge is a fresh context that sees only this run's screenshots
  and `metrics.json` (never `evaluate.json`, `report.html`, a previous run
  or an expected answer); its reply is schema-validated per answer (a malformed answer is dropped and reported, the valid ones kept; an unreadable reply is `unmeasured`); a
  `no` lands on a rule id or is an uncatalogued finding outside the grade.
  This session never answers a question itself and never edits a reply.
- **Screenshots and captured page text stay in the run directory.** Never
  attached, never committed, never quoted into GitHub — that includes a
  judge's `note` and `detail`, which describe what the page shows, and the
  evidence items' selectors and text samples. An issue body, a ledger entry
  and a digest carry rule ids, titles, severities, owners, counts, screen
  ids and the `standard` citation, nothing else.
- **Filing is opt-in and machine-composed.** Nothing reaches GitHub without
  the `file` argument; the body is always the helper's merge, never typed
  here; one managed issue per repo; spec- and scaffold-owned rules on an
  app only as `adopt` items (their fix already ships); never tick, close or `Closes #`.
- **Full-app mode never changes the app.** The investigation edits no code
  and restarts nothing; mockups stay local, GitHub gets text with invented
  examples; every item stays reachable; no build issue before the owner
  answers the decisions; a needed spec departure goes to the write-back,
  never into one app.
- **Fleet mode is serial and never restarts.** One browser at a time, an
  app not listening is `unmeasured`, judgment skipped, promotion decided by
  the helper (two or more apps), run once.
- **Unknown is a state.** `unmeasured` rules and categories are printed as
  such; a category flagged `unmeasured` is never read as passing, and an
  `unmeasured` or `not_confirmed` judgment is printed as such, never as
  "no findings".
- **One run per review.** The matrix is minutes and hundreds of files; the
  determinism contract (same build → identical findings, severities, grades
  and mock-up set) is proven in the package tests, not by re-running here.
  Judges are spawned once per review (`--judges N`), never re-run to get a
  different answer.

## Notes

- Deterministic core: `skills/_lib/design_review/` — `plan` (target/matrix),
  `capture` (run dir, probe, spawn), `walk` (the Playwright child), `measure`
  (the in-page script), `rubric`, `evaluate`, `judgment` (prompt, schema,
  merge — spawns nothing), `ledger` (entry, previous, diff), `filing`
  (accepted rules, routing, body merge, upsert), `fleet` (the serial sweep,
  promotion, digest), `mockups`, `report`, `cli`. Reference:
  `docs/skills.md` "Design review core".
- Rubric: `design.rubric.toml` at the repo root, versioned on its own; a
  rule's `mockup` id must exist in the library and a question's `maps_to`
  ids must be rules (validation refuses otherwise). Bump `[meta].version`
  on any rule or question change.
- Run directory contents: `metrics.json`, `shots/`, `evaluate.json`,
  `judge-prompt.md`, `judge-<n>.json`, `report.html`, `issue-body.md`. The
  per-target ledger is `<state>/design-review/<target>/ledger.json`; a
  fleet sweep is `<state>/design-review/_fleet/<stamp>/`.
- `[[design.accepted]]` (`architecture/README.md`): `rule = "<rubric id>"`
  + `reason` (+ optional `record`) suppresses a rule for that repo; the
  `check`-keyed entries are `/design-sync`'s and each loader ignores the
  other's.
- `/design-sync` is the static, authored-CSS sibling (tokens, contracts,
  vendored bytes); this skill is the rendered leg it reports as
  `unmeasured`. Each files its own managed issue (`design-drift` /
  `design-review`) through the same `audit_issue.py` identity.
