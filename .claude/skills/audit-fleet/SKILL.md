---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass and emit one weekly digest (GitHub comment + Slack ping). Use when the user wants a whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos". Also runs unattended on a weekly schedule.
---

# audit-fleet

**Goal:** A fleet-wide, idempotent, scatter-gather wrapper around `/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip the ones that haven't changed since their last audit, audit the changed ones **through a bounded window of up to 3 concurrent sub-agents** (one per repo — a session-token-budget pacing default, see below) that each run the full `/codebase-audit` procedure, then collect the results into **one diff-based digest** posted as a GitHub comment on the `audit-fleet digest state` ledger issue in `fleet-config` (the running log) and printed to stdout (so a scheduled run captures it in history). A Slack ping with the comment link is sent deterministically via `notify_complete.py --kind audit`.

**Scope boundary — source code, not context.** This audits *project source code* quality. The fleet's *always-on context surface* (CLAUDE.md token budgets, skill-description word counts, single-home violations) is a separate lens: `/context-audit`.

**This skill files no issues itself.** The only writes are (a) the audit issues that each sub-agent's `/codebase-audit` files, (b) the per-repo `audit-meta` ledger those audits update, (c) one `audit-fleet digest state` ledger issue in `fleet-config` for week-over-week deltas, (d) the digest comment on that issue, and (e) one cross-fleet `fleet practices ledger` issue in `project-scaffolding` cataloguing reusable solutions. It never edits source, commits, pushes, or restarts anything.

**Designed for unattended runs.** A weekly app-launcher job invokes this via
`claude -p "/audit-fleet" --model claude-sonnet-5 --effort high
--permission-mode bypassPermissions`. Every step must therefore degrade
gracefully rather than block on a prompt. (The orchestrator itself only does
cheap enumeration/gating/dispatch — see "Execution rules" below — so it runs at
`hard` tier, not `extreme`; see `docs/model-tiers.md`.)

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

## Self-pacing against the live session budget (read before steps 1, 3, 4, 5)

A full fleet sweep (this orchestrator's own turns plus the sub-agent window) can
exhaust the rolling **5-hour session rate limit** mid-run. `statusline-command.ps1`
now caches the live `rate_limits.five_hour` usage % (plus `resets_at`) to
`~/.claude/hooks/state/rate-limits.json` on every statusline render
(fleet-config#259), so this skill reads it and **pauses dispatch proactively,
before hitting the wall, then waits in place and resumes** — all within the same
run, no relaunch required. This replaces the older dead-man's-switch design
(fleet-config#222's original fix, retired in fleet-config#261) now that a live
reading is actually available. Full design: `docs/rate-gate.md`.

Mechanics, wired into step 3:

- Before each dispatch/refill of the sub-agent window, call
  `C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70`.
  `DECISION=OK` or `UNKNOWN` → dispatch as normal. `DECISION=PAUSE` → stop
  dispatching new sub-agents (let in-flight ones finish), wait via the `Monitor`
  tool's until-loop pattern against the printed `WAIT_SECONDS`/`RESETS_AT`, then
  re-check and resume.
- **Reactive fallback:** if a sub-agent failure still carries a rate-limit
  signature ("Server is temporarily limiting requests", "usage limit", "rate
  limit", "429", "Overloaded") despite the proactive gate — the cache was stale
  or missing — handle it the same way: pause and wait-until-reset, then resume.
- **Bounded safety net:** cap this run at **3 pause cycles**. If a 4th pause
  would be needed, stop waiting — mark every not-yet-completed repo
  `SKIPPED (session limit — exceeded pause retries)` and proceed to the digest
  (step 5) with that noted. The per-repo ledger (step 2) makes next week's run
  pick these up for free, exactly as before — idempotency is what makes this
  safe to bound rather than wait forever.

This is simpler than the old design: no OS-level scheduling, no `resume`
argument, no partial-digest branching — a run always ends by building and
delivering one full digest.

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

Steps that used to be "enumerate the fleet" and "cheap gate per repo" (a
per-repo LLM-driven loop of `git`/`gh` tool calls) are now **one deterministic
Python sweep** — the orchestrator makes a single tool call and reads its JSON
output instead of looping over repos itself:

```
C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/fleet_audit_scan.py --root E:\automation [--only <repo-name>]
```

This one script (`skills/_lib/fleet_audit_scan.py`, built on
`audit_issue.py`'s `evaluate_repo`) does everything steps 2+3 used to make the
orchestrator do by hand: walks `E:\automation\*\`, skips linked worktrees
(`<repo>-wt-<N>`: their `.git` is a file, not a dir — a linked worktree shares
its repo's `ferraroroberto` remote, so without this guard it would surface as
a spurious off-branch repo), filters to repos with a `ferraroroberto` remote,
skips dirty/off-branch repos, syncs the rest (`fetch` + `pull --ff-only`), and
runs the **same ledger-gate + self-fix-churn decision `/codebase-audit` step 2
uses** (`evaluate_repo` — there is exactly one implementation, not two prose
copies to keep in sync) per repo.

It prints one JSON object:

```
{"to_audit": [{"repo": "...", "path": "..."}, ...],
 "unchanged": ["repo1", "repo2", ...],
 "self_fix": [{"repo": "...", "path": "...", "decision": "SKIP_SELF_FIX", "closed_issues": [...], ...}, ...],
 "skipped": [{"repo": "...", "reason": "dirty"|"off-branch"|"non-ff"}, ...],
 "errors": [{"repo": "...", "reason": "..."}, ...]}
```

For every `self_fix` entry, the script has **already** advanced that repo's
ledger (HEAD sha + today's date, same rubric-sha) and posted a
`<!-- audit-self-fix -->` comment on its ledger issue — no further write
needed here. If the single-repo argument was passed, `--only <name>` restricts
the whole sweep to it.

Print a one-line plan from the JSON, e.g.:

```
Fleet audit plan — 3 to audit, 24 unchanged, 1 self-fix, 2 skipped (dirty)
  audit:     app-launcher, photo-ocr, local-llm-hub
  self-fix:  website (closed #71, #64 — ledger advanced, no organic change)
  skipped:   reporting (dirty), site (off-branch)
```

If `to_audit` is empty, jump to step 5 with an empty result set (the digest
still goes out so the weekly run always produces a record).

### 3. Audit each repo — a bounded window, self-paced against the live session budget

Process the to-audit list through a **bounded concurrency window of up to 3
sub-agents** — a session-token-budget pacing default and a natural cadence for
re-checking the live rate-limit % (see "Self-pacing against the live session
budget" above), not an Opus-burst-limiter workaround: audit sub-agents run at
**`hard` tier** (bounded-but-judgment-heavy grading work — not `extreme`; see
`docs/model-tiers.md`), which resolves to `model: "sonnet"` on Claude Code today.

Before each dispatch/refill, call
`C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70`
and branch on `DECISION`:

- **`OK` / `UNKNOWN`** → dispatch the next repo. Dispatch up to 3 background
  `Agent` calls (`run_in_background: true`, `subagent_type: "general-purpose"`,
  `model: "sonnet"`); each time one returns and its report is recorded, dispatch
  the next repo from the to-audit list — never more than **3 in flight**. Fewer
  than 3 repos left → dispatch just that many. No git worktrees: `/codebase-audit`
  is read-only and only files issues, so agents in different repo directories
  cannot collide.
- **`PAUSE`** → stop dispatching new sub-agents (let in-flight ones finish),
  then wait via the `Monitor` tool's until-loop pattern against the printed
  `WAIT_SECONDS`/`RESETS_AT`, then re-check and resume. Count this as one pause
  cycle for this run (see the bounded-safety-net rule above — cap at 3 pause
  cycles per run, after which mark the remaining repos `SKIPPED (session limit
  — exceeded pause retries)` and move on to the digest).

**Reactive fallback.** If a sub-agent failure still carries a rate-limit
signature ("Server is temporarily limiting requests", "usage limit", "rate
limit", "429", "Overloaded") despite the proactive gate — e.g. the cache was
stale — treat it the same as a `PAUSE`: stop dispatching, wait (re-running
`rate_gate.py check` for a fresh `WAIT_SECONDS`, or a conservative fallback wait
if it still reads `UNKNOWN`), then resume. A failure *without* a rate-limit
signature stays an ordinary per-repo `ERROR` (a genuine single-repo problem, not
a pause trigger) exactly as before, and the window keeps refilling.

Prompt template (substitute `<name>` / `<path>`):

```
Run a resting-state codebase audit on the <name> repo.

1. cd to <path>.
2. Execute the procedure in
   E:\automation\fleet-config\skills\codebase-audit\SKILL.md against this repo,
   whole-repo scope. That skill files at most 6 GitHub issues bucketed by
   finding type (one bucket reviews README/docs quality), dedupes against open
   issues, and updates the repo's audit-meta
   ledger. Follow it exactly — including its own ledger gate (step 2): if it
   decides nothing changed, that is a valid result, report it.
3. Do NOT edit source, commit, push, or restart anything. Filing issues and
   updating the ledger are the only writes.

Report back in this exact shape so the orchestrator can build the digest:
  - Repo: <name>
  - Result: AUDITED (<N> issues filed) | CLEAN (no findings) | SKIPPED-BY-LEDGER
  - Filed: <bucket → issue URL (<new> new, <carried> carried, <stale> not re-surfaced), one per line; omit if none>
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
`OK` — refill the window the moment a slot frees.

### 4. Collect results

Hold each sub-agent's structured report as it returns. When the to-audit list is
drained with no agent still in flight (whether or not one or more pause cycles
happened along the way), proceed to the practices ledger (4b) then the digest.
Track two terminal buckets, plus the `self_fix` and `skipped` buckets already
decided by step 2's Python sweep (carried through unchanged — no sub-agent
touches those repos):

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
C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get \
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
C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/project-scaffolding --kind practices --label audit-meta \
  --title "fleet practices ledger" --body-file <tmpfile>
```

Capture the printed URL as `PRACTICES_LEDGER_URL` for the digest. If the upsert
fails (e.g. no access to `project-scaffolding`), note `practices: skipped
(<reason>)` and carry on — **never fail the run over the ledger.**

### 5. Build the digest

A run always reaches this step with a complete result set — every repo is
`AUDITED` / `CLEAN` / `SKIPPED-BY-LEDGER` / `SELF-FIX` / `ERROR`, or (only if
the step-3 3-pause safety net was hit) `SKIPPED (session limit — exceeded
pause retries)`. `SELF-FIX` repos were decided entirely by step 2's Python
sweep — no sub-agent ran for them, and their ledger was already advanced by
the sweep itself. There is no separate "cut short, retry pending" path to
branch on any more — a run that had to pause for the session budget just took
longer wall-clock, resumed, and finished normally. Build and deliver the full
digest below / step 6 in every case; if any repos were skipped for the
session-limit safety net, the digest header simply flags it (see the
"Skipped" bullet below) so they're visible, not silently dropped — next
week's ledger gate picks them up for free.

Read the digest-state ledger first so the recap is week-over-week, not a
re-list:
`C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/fleet-config --kind digest`.
Parse the `<!-- audit-fleet-digest -->` block from the returned `body`:

```
<!-- audit-fleet-digest -->
last-run-at: <YYYY-MM-DD>
<name>: <open-audit-issue-count>
...
```

Compose the digest as markdown (single long lines per paragraph, no hard
wraps). This markdown is the canonical artifact: it goes to stdout verbatim and
is attached to the email as a `.md` file; step 6 also renders it to HTML for the
email body. Structure it so the per-repo results form a clean table when
rendered:

- **Header:** date, counts — `N repos audited, M issues filed, K unchanged, L self-fix, J skipped`.
- **Per audited repo:** result line + the issues filed this run (bucket → URL),
  and the **delta vs last week** (`+2 since last week` from the digest-state
  counts). Repos that came back CLEAN or SKIPPED-BY-LEDGER get a one-liner.
- **Self-fix section** *(only when non-empty)*: repos step 2's sweep classified
  `SELF-FIX` — one line each naming the closed issue numbers (`website:
  closed #71, #64 — ledger advanced, no organic change`), so it's visible that
  these repos were deliberately *not* re-audited rather than silently skipped.
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

Then upsert the digest-state ledger issue with today's date and the current
per-repo open-audit-issue counts, so next week can diff. The helper handles
create-vs-edit, collapses strays, and stamps the marker (keep the
`<!-- audit-fleet-digest -->` block intact):

```
C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
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

- **Slack ping:** call `notify_complete.py --kind audit` with the captured comment URL and a one-line summary. This is deterministic — the skill hands the hook exact structured args; the hook assembles the message:

  ```
  C:/Users/rober/AppData/Local/Python/bin/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
    --kind audit \
    --comment-url "$COMMENT_URL" \
    --summary "<N> audited, <M> issues filed, <K> unchanged"
  ```

  If `COMMENT_URL` is empty (comment was skipped), omit `--comment-url` so the ping still goes out link-less. This call is a silent no-op if no `slack_notify_channel` is configured; it always exits 0 and can never block or delay the finish.

### 7. Final report

One concise block: the plan line from step 2, per-repo results, where the digest went (stdout always; comment URL or skipped reason; Slack pinged or no-op), and the digest-state issue URL. Stop.

## Hard rules

- **The ledger gate is one shared Python implementation, not prose.** Step 2's
  `fleet_audit_scan.py` and `/codebase-audit`'s own step 2 both call
  `audit_issue.py`'s `evaluate_repo` — there is exactly one implementation of
  the skip/audit/self-fix decision, so there is nothing left for two prose
  copies to disagree about. Unit-tested independent of `gh`/`git` in
  `tests/test_audit_issue.py`.
- **Read-only on source.** This skill and its sub-agents never edit code,
  commit, push, or restart. The only writes are audit issues, the per-repo
  ledger, the digest-state issue, the digest comment, and the cross-fleet
  practices ledger in `project-scaffolding`. The practices ledger is the one
  write target outside `fleet-config` — still an issue, never source.
- **Never disturb in-progress work.** Dirty or off-default-branch repos are
  skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, `hard` tier (Sonnet on Claude Code today), through a
  ≤3 sliding window.** Keep at most 3 sub-agents in flight, refilling the window
  as each returns — a session-token-budget pacing default and a natural cadence
  for re-checking the live rate-limit %, not an Opus-burst-limiter workaround
  (see `docs/model-tiers.md` — `hard` no longer resolves to Opus, so the
  historical 2026-06-03 burst-limit incident doesn't directly apply to the
  default path any more, but the window stays as a conservative default; a wider
  window is a separate, empirically-driven follow-up). No worktrees (audits
  don't collide). Don't read repo source in the orchestrator.
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

- **Why scatter-gather:** orchestrator → N stateless workers → one aggregator.
  Each repo's file reading is isolated in its own sub-agent context, so the
  orchestrator never holds the whole fleet's source at once. That bounded
  context is what makes a weekly all-repo sweep cheap.
- **Why a ledger gate and not "just re-audit":** most weeks most repos are
  unchanged. The gate turns an unchanged repo into one `gh` + one `git` call
  instead of a full read + a sub-agent spawn. The commit SHA is the cache key;
  the rubric hash (sha256 of the repo's **own** project CLAUDE.md) busts that
  one repo's cache when its grading criteria change. The shared global
  `~/.claude/CLAUDE.md` is deliberately excluded from the hash — folding it in
  re-audited the entire fleet on every edit to that frequently-touched file
  (the 2026-06-06 incident); a deliberate fleet-wide re-grade is now an explicit
  act (clear the ledgers' `last-audited-sha`), not an accidental side effect.
- **Self-fix-only churn gets the same treatment as unchanged:** a repo whose
  only commits since the last audit are `/cleanup-fleet` (or manual) fixes for
  its own audit findings also skips the full read — `evaluate_repo` detects
  this via merged-PR `closingIssuesReferences` against the repo's managed
  bucket issues, entirely in Python, and advances the ledger itself. Without
  this, a repo that only ever gets fixed via `/cleanup-fleet` would be
  re-audited (and often re-flagged) every single week purely because
  *something* landed since last time — the exact "rediscovering the same
  issues" complaint fleet-config#251 was filed over.
- **A known, deliberate edge: a mixed PR fails closed to AUDIT, on purpose.**
  If a PR closes a hand-filed issue alongside (or instead of) an audit-managed
  one — e.g. something spotted incidentally while fixing an audit finding, then
  filed and fixed in the same pass — that commit is correctly *not* recognized
  as self-fix, so the whole repo re-audits. This was evaluated deliberately
  (fleet-config#251 decision log) and kept as-is: reliably telling "discovered
  incidentally during audit work" apart from "the user's own unrelated fix"
  would need provenance tagging at issue-*filing* time (e.g. `/cleanup-fleet`
  or `/issue-yolo` linking a mid-session discovery back to the audit issue it
  came from) — a materially bigger change across multiple skills, not a
  ledger-gate tweak. Occasionally re-auditing a repo that only needed a
  self-fix skip is a much safer failure mode than the reverse, so this stays a
  known limitation rather than a bug to chase.
- **Per-category trend data lives in the per-repo ledger.** Each whole-repo
  audit posts a counts-only `<!-- audit-snapshot -->` comment on that repo's
  `codebase-audit ledger` issue (see `/codebase-audit` step 9). Open a repo's
  ledger to read its findings trajectory over time; this fleet digest stays
  aggregate (per-repo totals, week-over-week) by design.
- **The weekly job** that schedules this lives in app-launcher
  (`config/jobs.json`, a `weekly` schedule, `visible: true` console) and calls a
  thin `.claude/skills/audit-fleet/run-weekly.bat` wrapper in this repo. See that repo
  for the trigger; this skill is the work.
- **Why a proactive gate now, and not a dead-man's switch** (fleet-config#222
  originally, redesigned in fleet-config#261): the original "read my session %
  and wait" was impossible headless, because Claude Code fed
  `rate_limits.five_hour.used_percentage` to the statusline via stdin JSON only
  at TUI render time and never persisted it — hence the arm-a-scheduled-task
  workaround. That constraint is gone: `statusline-command.ps1` now caches the
  same numbers to `~/.claude/hooks/state/rate-limits.json` on every render
  (fleet-config#259), so this skill can read them directly and pause-then-wait
  in place instead of dying and hoping an OS-level relaunch resumes the rest.
  See `docs/rate-gate.md` for the full design and `rate_gate.py`'s contract.
