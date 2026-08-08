---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass and emit one weekly digest (GitHub comment + Slack ping). Use when the user wants a whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos". Also runs unattended on a weekly schedule.
---

# audit-fleet

**Goal:** A fleet-wide, idempotent, scatter-gather wrapper around `/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip the unchanged ones, audit the changed ones through a **bounded window of up to 3 concurrent sub-agents** (one per repo), then collect the results into **one diff-based digest**: a GitHub comment on the `audit-fleet digest state` ledger issue in `fleet-config` + stdout (so a scheduled run captures it), with a Slack ping via `notify_complete.py --kind audit`.

**Scope boundary — source code, not context.** This audits *project source code* quality. The fleet's *always-on context surface* (CLAUDE.md token budgets, skill-description word counts, single-home violations) is a separate lens: `/context-audit`. Web-app *visual conformance* to the fleet design system is a third lens, swept by `/design-sweep` (which files the `design-drift`/`cert-drift` issues); this digest only *reports* that bucket's open counts week-over-week (step 5), it never runs the design lint itself.

**This skill files no issues itself.** The only writes *this orchestrator* makes are (a) the audit issues that each sub-agent's `/codebase-audit` files, (b) the per-repo `audit-meta` ledger those audits update, (c) one `audit-fleet digest state` ledger issue in `fleet-config` for week-over-week deltas, (d) the digest comment on that issue, and (e) one cross-fleet `fleet practices ledger` issue in `project-scaffolding` cataloguing reusable solutions. The orchestrator itself never edits source, commits, pushes, or restarts anything. **One narrow exception lives inside the sub-agents:** `/codebase-audit`'s step 8b self-heals a **security** finding in place (redacted issue + auto-fix + auto-merge), the only code-writing path in the whole audit flow — scoped to security, gated on its own safety rules (claim, mandatory regression test, generic artifacts, green-gate-only merge). See that skill's step 8b; this orchestrator just carries the counts-only result into the digest.

**Designed for unattended runs.** A weekly app-launcher job invokes the co-located `run-weekly.bat`, which routes `/audit-fleet` plus its Sonnet/high-effort/bypass-permissions flags through the shared `claude_progress.py` stream adapter. Every step must therefore degrade
gracefully rather than block on a prompt. (The orchestrator itself only does
cheap enumeration/gating/dispatch — see "Execution rules" below — so it runs at
`easy` tier, not `hard`; the per-repo sweep sub-agents dispatched in step 3 are
the ones that run at `hard` tier. See `docs/model-tiers.md`.)

## Arguments

- No argument → the whole fleet.
- One argument that looks like a repo name (e.g. `/audit-fleet app-launcher`) →
  restrict to that single repo. Match the bare repo name.

Anything else → treat as no argument (whole fleet).

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. `gh` and `git` work
  identically in it. Do not use PowerShell syntax (`&`, `$env:`, here-strings)
  in Bash. Windows paths map as `/e/automation/...`.
- **The orchestrator only does cheap, safe work:** enumeration, the per-repo
  ledger gate, fast-forward syncs, windowed dispatch, collection, the digest. **All file
  reading happens inside sub-agents** — this is what keeps the orchestrator's
  context (and the weekly token spend) bounded.
- **Never disturb in-progress work.** A repo that is dirty or not on its default
  branch is skipped and reported — never stashed, never force-switched.
- **Never background a tool call in this skill.** This orchestrator runs
  headless via `run-weekly.bat`'s one-shot Claude process, with no persistent
  turn loop and no human attending it. There is
  no wake-up mechanism to resume the session after a turn ends, so launching
  any command (the step 2 `fleet_audit_scan.py` sweep, a sub-agent dispatch,
  the rate-gate wait) with `run_in_background: true` and then ending the turn
  to "wait for it" silently kills the entire run: the CLI exits immediately on
  that clean turn-end, reporting `exit_code: 0` (false success) while nothing
  past that point ever happened (`fleet-config#314`). Every command here —
  including the step 3 rate-limit pause — must run synchronously (foreground)
  or poll to completion within the same turn (e.g. the `Monitor` tool's
  until-loop pattern), never fire-and-forget.

## Self-pacing against the live session budget

A full fleet sweep can exhaust the rolling **5-hour session rate limit**
mid-run. `statusline-command.ps1` caches the live `rate_limits.five_hour`
usage % (+ `resets_at`) to `~/.claude/hooks/state/rate-limits.json` on every
render (fleet-config#259), so this skill pauses dispatch proactively, waits in
place, and resumes within the same run — no relaunch, no OS-level scheduling,
no `resume` argument; a run always ends by delivering one full digest. The
concrete gate/pause/fallback mechanics are wired into step 3. Full design:
`docs/rate-gate.md`.

## Steps

Run in order. A failure on one repo is reported and skipped; it does not abort
the whole run. Only a pre-flight failure (step 1) stops everything.

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. If not, stop:
  "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.
- No need to read the global `~/.claude/CLAUDE.md` here: the step-2 gate hashes
  each repo's **own** project CLAUDE.md, not the global file, so a global edit
  never busts a cache. Sub-agents still read the global rubric when they grade
  (`/codebase-audit` step 3).

### 2. One Python sweep: enumerate, sync, and gate every repo

Enumeration + per-repo gating is **one deterministic Python sweep** — a single
tool call whose JSON the orchestrator reads, never a per-repo LLM loop:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/fleet_audit_scan.py --root E:\automation [--only <repo-name>]
```

The script (`skills/_lib/fleet_audit_scan.py`, built on `audit_issue.py`'s
`evaluate_repo`) walks `E:\automation\*\`, skips linked worktrees
(`<repo>-wt-<N>`: `.git` is a file, not a dir — without this guard a worktree
surfaces as a spurious off-branch repo), filters to repos with a
`ferraroroberto` remote, skips dirty/off-branch repos, syncs the rest (`fetch`
+ `pull --ff-only`), and runs the **same ledger-gate + self-fix-churn decision
`/codebase-audit` step 2 uses** (`evaluate_repo` — exactly one
implementation) per repo.

It prints one JSON object:

```
{"to_audit": [{"repo": "...", "path": "...", "reason": "unresolvable-baseline"?, "baseline_sha": "..."?}, ...],
 "unchanged": ["repo1", "repo2", ...],
 "self_fix": [{"repo": "...", "path": "...", "decision": "SKIP_SELF_FIX", "closed_issues": [...], ...}, ...],
 "below_threshold": [{"repo": "...", "path": "...", "decision": "SKIP_BELOW_THRESHOLD", "significance": N, "threshold": M, ...}, ...],
 "skipped": [{"repo": "...", "reason": "dirty"|"off-branch"|"non-ff"}, ...],
 "errors": [{"repo": "...", "reason": "..."}, ...],
 "enumerated": N,
 "accounting": {"enumerated": N, "bucketed": N, "unaccounted": 0, "balanced": true}}
```

`enumerated` counts the repos the walk *found*, before any decision; the
`accounting` block asserts the six buckets sum back to it. A repo that lands in
no bucket shows up as a non-zero `unaccounted` / `balanced: false` — the exact
shape of the 2026-07/08 failure where an unresolvable ledger baseline threw past
every branch and two repos vanished from three consecutive runs
(fleet-config#567). Never report counts that don't add up as a healthy run.

A `to_audit` entry carrying `"reason": "unresolvable-baseline"` is **not**
organic change: its recorded `last-audited-sha` (`baseline_sha`) resolves to
nothing in the checkout — almost always a squash-merged, deleted feature-branch
tip. Auditing whole-repo is the safe answer, so it is correctly in `to_audit`,
but it also means that repo's ledger is broken and its true audit range is
unknown. Surface it by name in the plan line and the digest.

For every `self_fix` entry, the script has **already** advanced that repo's
ledger (HEAD sha + today's date, same rubric-sha) and posted a
`<!-- audit-self-fix -->` comment on its ledger issue — no further write
needed here. A `below_threshold` entry is the opposite of a silent skip: the
repo has real organic (non-self-fix) commits since its last audit, but their
weighted-LOC significance (`skills/_lib/audit_issue.py`'s
`unexplained_weighted_loc` — feature/refactor commits count fully,
docs/test count nothing, fix/chore count partially) hasn't crossed the
threshold yet. Its ledger sha is **not** advanced, so next week's check
covers the same (growing) commit range plus whatever lands meanwhile — small
changes accumulate quietly until the total is enough to justify a real
whole-repo audit, at which point it moves to `to_audit` and that audit covers
everything back to the ledger sha, so nothing is ever lost. If the
single-repo argument was passed, `--only <name>` restricts the whole sweep
to it.

Print a one-line plan from the JSON, e.g.:

```
Fleet audit plan — 32 repos enumerated, 3 to audit, 24 unchanged, 1 self-fix, 2 below-threshold, 2 skipped (dirty)
  audit:            app-launcher, photo-ocr, local-llm-hub
  broken-baseline:  grocery-shopping-automation (ledger sha 99100ac unresolvable — auditing whole repo)
  self-fix:         website (closed #71, #64 — ledger advanced, no organic change)
  below-threshold:  accounting-quarterly (591/1000), pvgis (85/1000)
  skipped:          reporting (dirty), site (off-branch)
```

Lead the line with `accounting.enumerated` and print a `broken-baseline:` line
naming every `to_audit` entry whose `reason` is `unresolvable-baseline`. If
`accounting.balanced` is `false`, print `WARNING: <N> repos in no bucket` on its
own line — the sweep lost repos and the run must not read as healthy.

If `to_audit` is empty, jump to step 5 with an empty result set (the digest
still goes out so the weekly run always produces a record).

### 3. Audit each repo — a bounded window, self-paced against the live session budget

Process the to-audit list through a **bounded concurrency window of up to 3
sub-agents** — a session-token-budget pacing default and a natural rate-limit
re-check cadence, which now doubles as the live Opus burst-limiter cap: audit
sub-agents run at **`hard` tier** (see `docs/model-tiers.md`), which resolves to
`model: "opus"` on Claude Code today.

Before each dispatch/refill, call
`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70`
and branch on `DECISION`:

- **`OK` / `UNKNOWN`** → dispatch the next repo. Dispatch up to 3 background
  `Agent` calls (`run_in_background: true`, `subagent_type: "general-purpose"`,
  `model: "opus"`) to fill the window, then **stay in this same turn and block
  on `TaskOutput` (`block: true`) for every task now in flight** — do not end
  the turn to "wait for it". This orchestrator runs headless via
  `run-weekly.bat` with no wake-up mechanism (see "Never background a tool
  call in this skill" above); a background task nobody is polling in-turn just
  gets silently killed at the CLI's 600s background-task ceiling, and the run
  reports a false `exit 0` success — exactly what happened on the 2026-07-30
  11:00 scheduled run (0/10 repos actually audited, `fleet-config#506`; the
  same class of gap `fleet-config#314` closed for this skill's own
  Bash/Monitor calls, just never stated for this specific `Agent`-dispatch
  loop). If a `TaskOutput` call times out before a task finishes, re-issue the
  same blocking call — never move on with a task still unresolved. As each
  task returns, record its report and immediately dispatch the next repo from
  the to-audit list to refill the window — never more than **3 in flight**,
  and the turn must never end while any task is still dispatched. Fewer than 3
  repos left → dispatch just that many. No git worktrees: `/codebase-audit` is
  read-only and only files issues, so agents in different repo directories
  cannot collide.
- **`PAUSE`** → stop dispatching new sub-agents (let in-flight ones finish),
  wait via the `Monitor` tool's until-loop pattern against the printed
  `WAIT_SECONDS`/`RESETS_AT`, then re-check and resume. **Cap: 3 pause cycles
  per run** — a 4th would-be pause instead marks every remaining repo
  `SKIPPED (session limit — exceeded pause retries)` and moves on to the
  digest; the per-repo ledger makes next week's run pick these up for free.

**Reactive fallback.** A sub-agent failure carrying a rate-limit signature
("Server is temporarily limiting requests", "usage limit", "rate limit",
"429", "Overloaded") despite the proactive gate — e.g. a stale cache — is
treated as a `PAUSE`: stop dispatching, wait (re-run `rate_gate.py check` for
a fresh `WAIT_SECONDS`, conservative fallback wait if still `UNKNOWN`), then
resume. A failure *without* a rate-limit signature stays an ordinary per-repo
`ERROR` and the window keeps refilling.

Prompt template (substitute `<name>` / `<path>`):

```
Run a resting-state codebase audit on the <name> repo.

1. cd to <path>.
2. Execute the procedure in
   E:\automation\fleet-config\skills\codebase-audit\SKILL.md against this repo,
   whole-repo scope. That skill files at most 7 GitHub issues bucketed by
   finding type (one bucket reviews README/docs quality, one flags AI-slop
   bloat), dedupes against open issues, and updates the repo's audit-meta
   ledger. Follow it exactly — including its own ledger gate (step 2): if it
   decides nothing changed, that is a valid result, report it.
3. Do NOT edit source, commit, push, or restart anything — with the SINGLE
   exception that skill spells out in its step 8b: a SECURITY finding is
   self-healed in place (redacted issue + auto-fix + auto-merge, or escalate on
   failure). That is the only code-writing path; obey step 8b's rules exactly
   (claim the repo, mandatory regression test, generic commit/PR/test text,
   auto-merge only on a green gate, fire the private --kind security alert).
   Never name the vulnerability in any public text. For every non-security
   bucket, filing issues and updating the ledger remain the only writes.

Report back in this exact shape so the orchestrator can build the digest:
  - Repo: <name>
  - Result: AUDITED (<N> issues filed) | CLEAN (no findings) | SKIPPED-BY-LEDGER
  - Filed: <bucket → issue URL (<new> new, <carried> carried, <stale> not re-surfaced), one per line; omit if none>
  - Security: <NONE | HEALED (<count> gap(s), PR merged, private alert sent) |
    ESCALATED (<count>, branch left for manual /issue-finish)> — a bare count +
    disposition, NEVER any detail, file, or vulnerability class
  - Skipped-as-dupe: <count>
  - Files inspected: <count>
  - Promotion candidates: <the `promotion candidates spotted:` block from
    /codebase-audit's final report — asset/convention lines, verbatim; omit if none>
  - Note: <one line if anything surprising came up>
```

The `new`/`carried`/`stale` counts per bucket come straight from
`/codebase-audit` step 10's final report table (itself sourced from step 8's
run-log counts, never recomputed) — this is what lets the digest (step 5)
separate genuinely new findings from standing backlog instead of treating
"an issue got upserted this run" as the unit of "new."

Keep the window full: each time a sub-agent returns and its report is recorded,
immediately dispatch the next pending repo (up to the 3-in-flight cap, subject
to the `rate_gate.py check` above). Print a one-line progress marker per repo as
it completes (e.g. `[3/12] photo-ocr — AUDITED`) so a scheduled run's console
shows forward motion. Do **not** sleep between dispatches when the gate reads
`OK` — refill the window the moment a slot frees. This entire loop runs inside
one turn: block on `TaskOutput` for the in-flight window, refill on each
return, repeat until the to-audit list is drained — the turn never ends with a
sub-agent still dispatched (`fleet-config#506`).

### 4. Collect results

Hold each sub-agent's structured report as it returns. When the to-audit list is
drained with no agent still in flight (whether or not one or more pause cycles
happened along the way), proceed to the practices ledger (4b) then the digest.
Track two terminal buckets, plus the `self_fix`, `below_threshold`, and
`skipped` buckets already decided by step 2's Python sweep (carried through
unchanged — no sub-agent touches those repos):

- A sub-agent that errors out **without** a rate-limit signature is recorded as
  `ERROR` for its repo (a genuine single-repo failure); it does not block the
  others and the window refills as normal.
- Everything else is its normal `AUDITED` / `CLEAN` / `SKIPPED-BY-LEDGER` result
  — or, only if the 3-pause safety net (step 3) was hit, `SKIPPED (session
  limit — exceeded pause retries)` for whatever repos were left.

### 4b. Upsert the fleet practices ledger

Collect the `Promotion candidates` lines from every sub-agent report. If **all**
were empty, skip this step (the digest still notes "no new assets"). Otherwise
maintain one living catalog issue in **`ferraroroberto/project-scaffolding`** —
the cross-fleet "things that work" ledger. It is the inverse of the audit
issues (assets to remember, not rot to fix), so it lives outside the per-repo
flow and is labelled `audit-meta` so `/issue-triage` filters it out.

Read the existing ledger, then merge — same discipline as `/codebase-audit`
step 8:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get \
  --repo ferraroroberto/project-scaffolding --kind practices
```

Merge this run's candidates into the returned body: **preserve every existing
entry verbatim** (the catalog is durable memory), **dedupe by repo + capability**
(don't re-add an asset already listed; refresh its `Where:` path if it moved),
and append a dated `## Ledger run log` bullet. Sort candidates into the two
sections — **Capabilities** (fleet-worthy assets) and **Convention candidates**
(nominations for `project-scaffolding`). The ledger only *nominates* conventions;
actually filing one is a manual `/issue-add` call, so the weekly run never
auto-spams `project-scaffolding`. Body shape (no hard wraps; the helper prepends
the `kind=practices` marker — keep the `<!-- fleet-practices -->` block intact):

```
<!-- fleet-practices -->
## Capabilities
- **<repo>** — <capability one-liner>. Where: `<path/module>`. Reach for this when ...
## Convention candidates (nominate to project-scaffolding)
- **<repo>** — <convention>. Generalizable because ... → /issue-add if adopted.
## Ledger run log
- <YYYY-MM-DD>: +N capabilities, +M candidates from <repos>.
```

Write to a repo-scoped temp file (never a fixed shared name — see the global
tmp-file gotcha; e.g. `E:/tmp/audit-practices-ledger.md`) and upsert:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/project-scaffolding --kind practices --label audit-meta \
  --title "fleet practices ledger" --body-file <tmpfile>
```

Capture the printed URL as `PRACTICES_LEDGER_URL` for the digest. If the upsert
fails (e.g. no access to `project-scaffolding`), note `practices: skipped
(<reason>)` and carry on — **never fail the run over the ledger.**

### 5. Build the digest

A run always reaches this step with a complete result set — every repo is
`AUDITED` / `CLEAN` / `SKIPPED-BY-LEDGER` / `SELF-FIX` / `BELOW-THRESHOLD` /
`ERROR`, or (only if the 3-pause safety net was hit) `SKIPPED (session limit —
exceeded pause retries)`. `SELF-FIX` and `BELOW-THRESHOLD` repos were both
decided entirely by step 2's sweep — no sub-agent ran, and for
`BELOW-THRESHOLD` the ledger is deliberately **not** advanced (see step 2).
Build and deliver the full digest in every case; session-limit skips are
flagged in the digest, not silently dropped.

Read the digest-state ledger first so the recap is week-over-week, not a
re-list:
`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/fleet-config --kind digest`.
Parse the `<!-- audit-fleet-digest -->` block from the returned `body`:

```
<!-- audit-fleet-digest -->
last-run-at: <YYYY-MM-DD>
<name>: <open-audit-issue-count>
...
design-drift-last-run-at: <YYYY-MM-DD>
design-drift:<name>: <open-design-drift-count>
cert-drift:<name>: <open-cert-drift-count>
...
```

The code-bucket per-repo lines are the bare `<name>: <count>` ones; the
`design-drift:` / `cert-drift:`-prefixed keys below the
`design-drift-last-run-at:` marker are the **design bucket's own accounting**,
kept separate so a design-drift issue never inflates a repo's code-finding
count or its `+N since last week` code delta (`fleet-config#180`). Treat a
ledger with no `design-drift-last-run-at:` line (a pre-#180 state) as an empty
design baseline — every current design/cert count is then "new" only in the
sense of first-observation, not a spurious weekly spike; note it as the initial
design snapshot rather than a delta.

**Count the design-drift bucket (read-only).** `design-drift` and `cert-drift`
issues are filed by `/design-sweep` / `/design-sync`, never by an audit
sub-agent, so no sub-agent report carries them — count the open issues directly
with two fleet-wide searches (read-only, no repo reading):

```bash
gh search issues --owner ferraroroberto --label design-drift --state open --json repository --limit 200
gh search issues --owner ferraroroberto --label cert-drift  --state open --json repository --limit 200
```

Group each result by `repository.name` into per-repo open counts. This is the
one place the fleet's design-drift accounting is tallied week-over-week — one
ledger, alongside the six code buckets, never conflated with them. `/audit-fleet`
is the unified reporter; `/design-sweep` is the doer that keeps those issues
current. If either `gh search` fails, note `design-drift: count skipped
(<reason>)`, carry the last ledger snapshot forward unchanged, and never fail
the run over it.

Compose the digest as markdown (single long lines per paragraph, no hard
wraps). This markdown is the canonical artifact: it goes to stdout verbatim and
is attached to the email as a `.md` file; step 6 also renders it to HTML for the
email body. Structure it so the per-repo results form a clean table when
rendered:

- **Header:** date, counts — `E repos enumerated: N audited, M issues filed, K unchanged, L self-fix, B below-threshold, J skipped, X errors`, plus `S security fixes` when any sub-agent reported a `Security:` result other than `NONE`, and `D design-drift / C cert-drift open` from the step-5 bucket count. The per-bucket counts **must sum to `E`** (`accounting.enumerated` from step 2); when `accounting.balanced` is `false`, append `— ⚠️ <N> repos in no bucket` rather than printing counts that quietly don't add up.
- **Broken-baseline section** *(only when non-empty)*: repos step 2 routed to `to_audit` with `reason: unresolvable-baseline` — one line each naming the unresolvable `baseline_sha` (`grocery-shopping-automation: ledger sha 99100ac resolves to nothing — audited whole-repo, ledger re-anchored`). These were audited, so they also appear in the per-repo results; this section exists because a broken ledger is a distinct problem from organic change and used to be visible nowhere at all (fleet-config#567).
- **Per audited repo:** result line + the issues filed this run (bucket → URL),
  and the **delta vs last week** (`+2 since last week` from the digest-state
  counts). Repos that came back CLEAN or SKIPPED-BY-LEDGER get a one-liner.
- **Security section** *(only when any sub-agent reported a non-`NONE`
  `Security:` line)*: one line per repo, **counts + disposition only, never any
  detail** — `<repo>: 1 gap self-healed, PR merged (private alert sent)` or
  `<repo>: 1 gap ESCALATED — branch left for manual /issue-finish`. This mirrors
  the per-repo ledger's counts-only `sec` telemetry; the vulnerability, file,
  and class live nowhere in the digest, the ledger, or any commit. An
  `ESCALATED` line is the digest's flag that a security gap needs a human — the
  private `--kind security` alert already pinged, this makes it durable.
- **Self-fix section** *(only when non-empty)*: repos step 2's sweep classified
  `SELF-FIX` — one line each naming the closed issue numbers (`website:
  closed #71, #64 — ledger advanced, no organic change`), so it's visible that
  these repos were deliberately *not* re-audited rather than silently skipped.
- **Below-threshold section** *(only when non-empty)*: repos step 2's sweep
  classified `SKIP_BELOW_THRESHOLD` — one line each with the accumulated vs.
  threshold weighted-LOC (`accounting-quarterly: 591/1000 weighted lines
  since 2026-07-04 — accumulating, not yet audited`), so quiet accumulation
  stays visible instead of turning into a silent cap. These repos are NOT
  counted toward "standing backlog" below — they have no open audit findings
  from this, just unaudited organic change.
- **Skipped section:** repos skipped for dirty / off-branch / non-ff, so the
  user knows they were intentionally left out (not silently missed).
- **Session-limit section** *(only when non-empty)*: repos left unaudited
  because the step-3 3-pause safety net was hit, so they are visibly
  outstanding rather than silently dropped — next week's ledger gate picks
  them up for free.
- **New findings this week:** built strictly from the `new` counts each
  sub-agent reported (step 3's `Filed:` breakdown) — only bucket/URL pairs
  where `new > 0`. This is the actual delta, not "an issue got upserted" (most
  upserts on a re-run are carried backlog, not new findings) — list these at
  the top so the email leads with what genuinely changed.
- **Standing backlog:** a single fleet-wide count — the sum of every
  `carried` + `stale` count across every audited repo this run (not an item
  list — enumerating the same carried findings every week is exactly the
  noise this digest exists to avoid). One line, e.g. `14 standing findings
  across 5 repos, unchanged or not re-verified this run — see each repo's
  audit issue for detail.`
- **New fleet assets this week:** the promotion candidates added to the practices
  ledger this run (asset/convention one-liners), with the `PRACTICES_LEDGER_URL`.
  If none were added, one line: `No new fleet assets catalogued this week.`
- **Design & cert drift:** the `design-drift` bucket, reported *alongside* the
  six code buckets but never mixed into their counts. One line with the
  fleet-wide open total and its week-over-week delta from the
  `design-drift-last-run-at:` baseline (`6 open design-drift across 3 apps
  (+2 since last week); 1 cert-drift`), then — only for repos whose count
  **changed** since the baseline — a per-repo delta line (`home-automation: 4
  (+2)`). Steady repos are folded into the total, not enumerated (same
  anti-noise discipline as standing backlog). `/design-sweep` is what files and
  refreshes these issues; this line is the durable week-over-week record.

Then upsert the digest-state ledger issue with today's date and the current
per-repo open-audit-issue counts **plus** the design/cert bucket counts under
their `design-drift-last-run-at:` marker (stamp today's date on that marker
too), so next week can diff both. Keep the two account groups distinct in the
body — the bare `<name>: <count>` code lines and the `design-drift:` /
`cert-drift:`-prefixed lines — never fold a design count into a code line. The
helper handles create-vs-edit, collapses strays, and stamps the marker (keep the
`<!-- audit-fleet-digest -->` block intact):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/fleet-config --kind digest --label audit-meta \
  --title "audit-fleet digest state" --body-file <tmpfile>
```

Capture its printed URL as `DIGEST_ISSUE_URL` and use that for the comment in
step 6 — never a hardcoded issue number.

### 6. Deliver the digest

Two channels. stdout is the reliable one (a scheduled run captures it in app-launcher's job history); the GitHub comment is the durable record that the Slack ping links to.

- **stdout:** print the full markdown digest. Always.
- **GitHub comment:** post the digest as a comment on the `audit-fleet digest state` issue in `ferraroroberto/fleet-config` — the one step 5 upserted (`DIGEST_ISSUE_URL`), never a hardcoded id — turning that issue into a running log of every weekly run. Use the `gh issue comment` output URL:

  ```bash
  COMMENT_URL=$(gh issue comment "$DIGEST_ISSUE_URL" --repo ferraroroberto/fleet-config --body "$DIGEST_MARKDOWN")
  # gh issue comment prints the URL of the created comment on stdout
  ```

  If `gh` fails or the URL is empty, note `comment: skipped (<reason>)` and carry on. **Never fail the run over the comment.**

- **Delivery assertion — run it here, before the ping.** This is the only place the run may declare itself failed on *content* rather than on a crash, and it is the gap `fleet-config#506` left open: on 2026-07-30 the tell was entirely in the content — zero repos audited, no digest comment, no ping — while the exit code read `0`. Check all three, and treat any one you **cannot establish** as failed rather than as passing (an unresolved check is its own state, never folded into the passing one — global CLAUDE.md, "Verify before declaring done"):

  1. **≥1 repo evaluated.** Step 2's sweep placed at least one repo in some bucket (`to_audit` + `unchanged` + `self_fix` + `below_threshold` + `skipped` + `errors` > 0). An empty sweep means the fleet walk itself failed — it does not mean the fleet is clean.
  1b. **The buckets account for every repo walked.** Step 2's `accounting.balanced` is `true`. A `false` (or missing) `balanced` means repos the walk *found* landed in no bucket at all and were neither audited nor reported — the fleet-config#567 shape. Treat it exactly like the other assertions: fail the run, name the unaccounted count, never let a sweep that lost repos report success.
  2. **A digest was composed and printed.** Step 5 produced digest markdown and this step wrote it to stdout verbatim.
  3. **The digest comment resolved either way.** `COMMENT_URL` holds a real URL, *or* the comment was recorded as `comment: skipped (<reason>)` with a stated reason. "Never fail the run over the comment" holds for a *stated* failure; a comment step that silently never ran fails this assertion.

  All three hold → carry on to the Slack ping and report normally in step 7. Any one fails → do **not** report success: print this line verbatim in the step-7 final report,

  ```
  SCHEDULED-RUN-FAILED — <which assertion failed, one line>
  ```

  still send the Slack ping (a failed run must be *more* visible, not less), and state the failure plainly. `skills/_lib/claude_progress.py` detects that literal marker in the run's final report and exits `123` instead of `0`, so the weekly job shows red rather than a false green (`fleet-config#519`). Never print the marker on a run that did deliver: a sweep where every repo came back `unchanged` and `to_audit` was empty is a **successful** run — it still produces a full digest (step 2), which is exactly why assertion 1 counts `unchanged` too.

- **Slack ping:** call `notify_complete.py --kind audit` with the captured comment URL and a one-line summary. This is deterministic — the skill hands the hook exact structured args; the hook assembles the message:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
    --kind audit \
    --comment-url "$COMMENT_URL" \
    --summary "<N> audited, <M> issues filed, <K> unchanged"
  ```

  If `COMMENT_URL` is empty (comment was skipped), omit `--comment-url` so the ping still goes out link-less. This call is a silent no-op if no `slack_notify_channel` is configured; it always exits 0 and can never block or delay the finish.

### 7. Final report

One concise block: the plan line from step 2, per-repo results, where the digest went (stdout always; comment URL or skipped reason; Slack pinged or no-op), and the digest-state issue URL. If step 6's delivery assertion failed, its `SCHEDULED-RUN-FAILED — <reason>` line goes in this block verbatim and nothing in the block may describe the run as complete. Stop.

## Hard rules

- **The ledger gate is one shared Python implementation, not prose.** Step 2's
  `fleet_audit_scan.py` and `/codebase-audit`'s own step 2 both call
  `audit_issue.py`'s `evaluate_repo` — there is exactly one implementation of
  the skip/audit/self-fix decision, so there is nothing left for two prose
  copies to disagree about. Unit-tested independent of `gh`/`git` in
  `tests/test_audit_issue.py`.
- **Read-only on source — except a sub-agent's security self-heal.** This
  orchestrator never edits code, commits, pushes, or restarts. Its sub-agents
  are read-only too **except** for `/codebase-audit`'s step 8b security
  self-heal (redacted issue + auto-fix + auto-merge), the single code-writing
  path in the flow — scoped to security, gated on that skill's rules (claim,
  mandatory regression test, generic artifacts, green-gate-only merge, escalate
  on failure). Every other write is an audit issue, the per-repo ledger, the
  digest-state issue, the digest comment, or the cross-fleet practices ledger in
  `project-scaffolding` (the one issue-write target outside `fleet-config` —
  still an issue, never source).
- **Never disturb in-progress work.** Dirty or off-default-branch repos are
  skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, `hard` tier (Opus on Claude Code today), through a
  ≤3 sliding window.** Refill as each returns — a session-token-budget pacing
  default that now also doubles as the live Opus burst-limiter cap (`hard`
  resolves to Opus again as of 2026-07-16; widening the window beyond 3 would
  need to respect that cap — `docs/model-tiers.md`). No worktrees (audits
  don't collide). Don't read repo source in the orchestrator.
- **Block on `TaskOutput` for every in-flight sub-agent, same turn, always.**
  Dispatching with `run_in_background: true` and then ending the turn to "wait
  for it" is not a valid pattern anywhere in this skill — a headless `claude
  -p` run has no wake-up mechanism, so an unpolled background task is silently
  killed at the CLI's background-task ceiling and the run reports a false
  `exit 0` (`fleet-config#506`). Step 3's dispatch loop blocks in-turn on every
  task it launches and never returns control until the to-audit list is fully
  drained. `claude_progress.py` now also hands the CLI
  `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` so an in-flight task is waited on
  rather than killed (`fleet-config#519`) — that is a safety net against losing
  the work, **not** a licence to end the turn: results a turn never collected
  still never reach the digest.
- **A run that delivered nothing must exit non-zero.** Step 6's delivery
  assertion (≥1 repo evaluated, a digest composed and printed, the comment
  posted or skipped with a stated reason) runs before the Slack ping on every
  run. On failure the run prints the literal `SCHEDULED-RUN-FAILED` marker,
  which `claude_progress.py` maps to exit `123`. A green weekly job with zero
  work done is the failure mode this whole skill's incident history is made of
  — never let one report success.
- **Degrade, don't block.** Built for unattended `claude -p`. A per-repo failure
  is reported and skipped; only a pre-flight failure stops the whole run. Never
  wait on an interactive prompt.
- **Self-pace against the live session %, don't die-and-hope.** Check
  `rate_gate.py` before each dispatch/refill (step 3); on `PAUSE`, wait in place
  via the `Monitor` tool's until-loop pattern and resume — capped at 3 pause
  cycles per run (the bounded safety net). No OS-level scheduling, no `resume`
  argument — see `docs/rate-gate.md`.
- **No AI attribution; no hard-wrapped digest paragraphs.** (Per global
  CLAUDE.md.)

## Notes

- **Why scatter-gather:** each repo's file reading is isolated in its own
  sub-agent context, so the orchestrator never holds the whole fleet's source
  at once — that bounded context is what makes a weekly all-repo sweep cheap.
- **Why a ledger gate:** most weeks most repos are unchanged; the gate turns an
  unchanged repo into one `gh` + one `git` call. The commit SHA is the cache
  key; the rubric hash (sha256 of the repo's **own** project CLAUDE.md) busts
  one repo's cache when its criteria change. The global `~/.claude/CLAUDE.md`
  is deliberately excluded — folding it in once re-audited the entire fleet on
  every edit; a fleet-wide re-grade is now an explicit act (clear the ledgers'
  `last-audited-sha`), never a side effect.
- **Self-fix-only churn is treated as unchanged:** `evaluate_repo` detects it
  via merged-PR `closingIssuesReferences` against the managed bucket issues and
  advances the ledger itself — otherwise a repo fixed only via `/cleanup-fleet`
  would be re-flagged weekly (the fleet-config#251 complaint).
- **A mixed PR fails closed to AUDIT, on purpose** (fleet-config#251 decision
  log): a PR closing a hand-filed issue alongside an audit-managed one is not
  recognized as self-fix, so the repo re-audits. Telling those apart would need
  provenance tagging at issue-filing time across multiple skills; an occasional
  unnecessary re-audit is the safer failure mode. Known limitation, not a bug.
- **Not every non-self-fix commit re-audits immediately** (fleet-config#315):
  once `evaluate_repo` decides a repo isn't pure self-fix, it doesn't
  unconditionally fall to `AUDIT` — it weighs the unexplained commits'
  `additions + deletions` by conventional branch-type (`feat`/`refactor` full
  weight, `fix`/`chore` partial, `docs`/`test` none — `audit_issue.py`'s
  `PR_TYPE_WEIGHTS`) and only audits once the accumulated total crosses
  `DEFAULT_SIGNIFICANCE_THRESHOLD` (1000). Below that, `SKIP_BELOW_THRESHOLD`
  leaves the ledger sha untouched, so small organic changes batch up across
  however many weekly runs it takes rather than forcing a full re-audit over a
  single low-risk commit — proven necessary when a one-time fleet-wide
  docs-only rollout (#256) flipped 28 of 31 repos to "needs audit" in one run.
  A repo that does cross the threshold is still audited whole-repo, covering
  everything back to the ledger sha — nothing is lost, only batched.
- **Per-category trend data lives in the per-repo ledger** (`<!-- audit-snapshot -->`
  comments, `/codebase-audit` step 9); this fleet digest stays aggregate by design.
- **The weekly job** lives in app-launcher (`config/jobs.json`) and calls this
  repo's `.claude/skills/audit-fleet/run-weekly.bat`.
- **Why a proactive gate, not a dead-man's switch** (#222 → redesigned #261):
  the session % used to exist only at TUI render time; `statusline-command.ps1`
  now caches it to `~/.claude/hooks/state/rate-limits.json` on every render
  (#259), so the skill reads it directly and pause-waits in place instead of
  dying and hoping a relaunch resumes. Contract: `docs/rate-gate.md`.
