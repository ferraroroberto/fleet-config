---
name: design-review
description: Review a running fleet web app's rendered design — measure the live app in real browsers (iPhone/Android/desktop × light/dark), score it against the versioned design rubric, have a fresh-context judge answer the rubric's bounded checklist from the local screenshots, diff the run against the last one from a local ledger, and render one self-contained HTML report with now-vs-proposed mock-ups; `file` upserts the findings into the repo's one managed issue, `fleet` sweeps every declared web app serially. Never starts, restarts or kills the app. E.g. "/design-review app-launcher", "/design-review app-launcher file", "/design-review fleet", "/design-review app-launcher --judges 2", "review the rendered design", "how does the app score against the rubric".
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
`JUDGMENT=ok|unmeasured|not_confirmed`, `ANSWERS=<yes>/<no>/<na>`,
`UNCATALOGUED=<n>`, `ERRORS=<n>` (+ one `ERROR_DETAIL=` per violation, one
`NOT_CONFIRMED=<id>:<votes>` per disagreement, and `RUBRIC_MISMATCH=` when
`evaluate.json` was scored under another rubric version than the checklist
— carry that line into the summary). A malformed or partial
reply makes the **whole** judgment `unmeasured` — the report says so; it is
a result, exit 0, and the run continues. Never re-prompt a judge to "fix"
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
status `ok` only) are their own list keyed by question + title, titles and
severities only; a dated bullet lands in `## Review run log`. **Spec- and
scaffold-owned rules are never filed on the app** — they print as `SPEC=`
/ `SCAFFOLD=` here and reach fleet-config / project-scaffolding through
the fleet digest. A `[[design.accepted]]` entry with `rule = "<id>"` and a
`reason` in the target's `.fleet.toml` suppresses that rule (listed under
`## Accepted`); one that fails nowhere prints `PROBLEM=`.

Read `FILE=dry-run|filed`, `REPO=`, `ISSUE=<n>|none`, `CHANGED=yes|no`
(Findings / Uncatalogued / Accepted differ from the issue — the run log
alone is not a change), `FILED=<n> <ids>`, `UNCATALOGUED=`, `ACCEPTED=`,
`SPEC=`, `SCAFFOLD=`, `BODY=` (the merged body, `<RUN_DIR>/issue-body.md`,
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
  judgment:   <ok | not_confirmed | unmeasured> · <yes>/<no>/<na> · <n> uncatalogued · <n> judge(s)
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

## Hard rules

- **Never start, restart or kill anything.** A dead port ends the run at
  step 1 with the target `CLAUDE.md`'s restart pointer; the user runs it.
- **Read-only walk, loopback only.** No form submits, no destructive
  clicks beyond the target's own opt-in `extra_steps`; a `no_go` selector
  list in `.fleet.toml` is honoured by the walk on every click of a step,
  including anything inside a `no_go` element.
- **Nothing is authored here.** Sentences are `fix_template` + `standard`;
  grades are the rubric's arithmetic; mock-ups are library templates filled
  from measurements. Where a value is missing the report says so — no
  improvised number, no improvised mock-up.
- **Judgment is bounded and never a grade input.** The checklist is rubric
  data; the judge is a fresh context that sees only this run's screenshots
  and `metrics.json` (never `evaluate.json`, `report.html`, a previous run
  or an expected answer); its reply is schema-validated all-or-nothing; a
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
  here; one managed issue per repo; spec- and scaffold-owned rules never on
  an app; never tick, close or `Closes #`.
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
