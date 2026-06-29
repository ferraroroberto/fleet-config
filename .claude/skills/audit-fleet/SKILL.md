---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass and emit one weekly digest (GitHub comment + Slack ping). Use when the user wants a whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos". Also runs unattended on a weekly schedule.
---

# audit-fleet

**Goal:** A fleet-wide, idempotent, scatter-gather wrapper around `/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip the ones that haven't changed since their last audit, audit the changed ones **through a bounded window of up to 3 concurrent sub-agents** (one per repo, the global Opus concurrency cap) that each run the full `/codebase-audit` procedure, then collect the results into **one diff-based digest** posted as a GitHub comment on the `audit-fleet digest state` ledger issue in `fleet-config` (the running log) and printed to stdout (so a scheduled run captures it in history). A Slack ping with the comment link is sent deterministically via `notify_complete.py --kind audit`.

**Scope boundary — source code, not context.** This audits *project source code* quality. The fleet's *always-on context surface* (CLAUDE.md token budgets, skill-description word counts, single-home violations) is a separate lens: `/context-audit`.

**This skill files no issues itself.** The only writes are (a) the audit issues that each sub-agent's `/codebase-audit` files, (b) the per-repo `audit-meta` ledger those audits update, (c) one `audit-fleet digest state` ledger issue in `fleet-config` for week-over-week deltas, (d) the digest comment on that issue, and (e) one cross-fleet `fleet practices ledger` issue in `project-scaffolding` cataloguing reusable solutions. It never edits source, commits, pushes, or restarts anything.

**Designed for unattended runs.** A weekly app-launcher job invokes this via
`claude -p "/audit-fleet" --model claude-opus-4-7 --effort high
--permission-mode bypassPermissions`. Every step must therefore degrade
gracefully rather than block on a prompt.

## Arguments

- No argument → the whole fleet, **as a fresh run** (resets the retry chain — see
  the rate-limit-resilience design below).
- One argument that looks like a repo name (e.g. `/audit-fleet app-launcher`) →
  restrict to that single repo. Match the bare repo name.
- `resume` → this run is a **scheduled-retry continuation** of a prior run that
  was cut short by a session rate limit. It audits the whole fleet exactly like
  the no-argument case (the ledger gate already skips repos audited last time);
  the only difference is it **continues the existing retry chain** instead of
  resetting it. Set by the self-relaunch task (`run-weekly.bat resume`); a human
  may also pass it to force-continue a chain.

Anything else → treat as no argument (fresh run).

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

## Surviving session rate limits (read before steps 1, 4, 6, 7)

A full fleet sweep (this orchestrator's own turns plus the 3-wide sub-agent
window) can exhaust the rolling **5-hour session rate limit** mid-run. When that
happens the sub-agents 429 or this process dies outright, and the rest of the
fleet is silently dropped until next week (the failure this design fixes,
fleet-config#222). **You cannot read your live session %** to pre-empt it: Claude
Code feeds it to the statusline via stdin JSON only at TUI render time, never
persists it to disk, and the statusline does not render under headless
`claude -p` — so there is nothing to poll. Instead this skill leans on two
mechanisms, wired into the steps below:

- **Idempotent resume.** Every audited repo updates its per-repo ledger (step 3),
  so a re-run skips already-done repos for free — a resume costs almost nothing.
- **Dead-man's switch.** At the start of the heavy phase (step 4) you *arm* a
  one-shot Windows scheduled task ~4h out via
  `py C:/Users/rober/.claude/skills/_lib/audit_retry.py arm …`. If this process
  dies of a session limit, the task still fires and re-launches the audit as
  `/audit-fleet resume`, which resumes via the ledger gate. A **clean finish
  disarms it** (step 6/7 `… clear`). A retry-count guard (default 3 launches → 2
  retries) caps the chain so a persistently-limited window can't loop forever.

The helper owns all of this (state file + task registration); you only call
`arm` / `clear` and read the printed `ATTEMPT=n/M` + `ARMED=yes|no`. Never try to
schedule tasks or track the count yourself.

## Steps

Run in order. A failure on one repo is reported and skipped; it does not abort
the whole run. Only a pre-flight failure (step 1) stops everything.

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. If not, stop:
  "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.
- No need to read the global `~/.claude/CLAUDE.md` here: the step-3 gate hashes
  each repo's **own** project CLAUDE.md, not the global file, so a global edit
  never busts a cache. Sub-agents still read the global rubric when they grade
  (`/codebase-audit` step 3).
- **Reset the retry chain on a fresh run.** If the argument is **not** `resume`
  (a normal weekly run or a manual `/audit-fleet`), zero any stale chain left by
  a prior cut-off and cancel a stale pending relaunch task:
  `py C:/Users/rober/.claude/skills/_lib/audit_retry.py clear`. This guarantees a
  fresh run starts attempt 1, never inheriting a capped chain. **Skip this when
  the argument is `resume`** — that run must keep counting the existing chain.

### 2. Enumerate fleet repos

List local git repos under the fleet root that have a `ferraroroberto` remote:

```
for d in /e/automation/*/; do
  [ -d "$d/.git" ] || continue   # skip linked worktrees (<repo>-wt-N): their .git is a FILE, not a dir
  url=$(git -C "$d" remote get-url origin 2>/dev/null) || continue
  case "$url" in *ferraroroberto/*) echo "$(basename "$d")";; esac
done
```

The `.git`-is-a-directory guard excludes the transient `<repo>-wt-<N>` worktrees
that `/issue-start`'s concurrency path leaves around mid-flight — a linked
worktree shares the repo's `ferraroroberto` remote, so without the guard it
would surface in the digest as a spurious off-branch repo.

If the single-repo argument was passed, keep only that name. Hold the resulting
list of `(name, path)` pairs.

### 3. Cheap gate per repo (orchestrator — no sub-agent yet)

For each repo, decide **skip** vs **audit** without reading source files. This
applies the **exact same skip condition as `/codebase-audit` step 2** (the
ledger is the single source of truth; this is just an optimization so an
unchanged repo never costs a sub-agent spawn):

1. **Dirty / wrong branch → skip + report.** `git -C <path> status --porcelain`
   non-empty, or current branch is not the default branch
   (`git -C <path> symbolic-ref --short HEAD` vs the `origin/HEAD` target) →
   record as `skipped (dirty / off-branch)` and move on. Never sync over it.
2. **Sync.** `git -C <path> fetch origin` then `git -C <path> pull --ff-only`.
   If the pull is not a fast-forward → record `skipped (non-ff)` and move on.
3. **Ledger gate.** Read the ledger by its marker (not the bare `audit-meta`
   label, which also tags the digest issue):
   `py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/<name> --kind ledger`.
   - `number` is `null` → **audit** (first run).
   - Else parse `last-audited-sha` + `rubric-sha`. Compute the repo's current
     `rubric-sha` = sha256 of `<path>/CLAUDE.md` alone (empty string if absent) —
     the project rubric only, **not** the global CLAUDE.md, so a global-file edit
     never busts this cache. If
     `git -C <path> rev-list <last-audited-sha>..HEAD --count` is `0` **and**
     `rubric-sha` is unchanged → record `unchanged (skipped)`. Otherwise →
     **audit**.

Print a one-line plan before dispatch, e.g.:

```
Fleet audit plan — 3 to audit, 24 unchanged, 2 skipped (dirty)
  audit:     app-launcher, photo-ocr, local-llm-hub
  skipped:   reporting (dirty), website (off-branch)
```

If nothing is to be audited, jump to step 6 with an empty result set (the digest
still goes out so the weekly run always produces a record).

### 4. Audit each repo — a bounded window of up to 3 sub-agents

**First, arm the dead-man's switch** (only if the to-audit list is non-empty —
nothing to be cut short otherwise). Before dispatching any sub-agent:

```
py C:/Users/rober/.claude/skills/_lib/audit_retry.py arm \
  --hours 4 --max 3 \
  --bat "E:\automation\fleet-config\.claude\skills\audit-fleet\run-weekly.bat"
```

Read its output: `ATTEMPT=<n>/<max>` and `ARMED=<yes|no>`. Hold `IS_FINAL =
(ARMED == no)`. `ARMED=yes` means a one-shot relaunch is now scheduled ~4h out
as a safety net should this process die mid-sweep; `ARMED=no` means the retry cap
is reached and this is the **final** attempt (no further retry will fire). This
arming is what makes the session-limit-kills-the-process case recoverable — do
not skip it.

Then process the to-audit list through a **bounded concurrency window of up to 3
sub-agents** (the global Opus concurrency cap — see `~/.claude/CLAUDE.md`,
"Spawning sub-agents — cap concurrent Opus at 3"). Dispatch up to 3 background
`Agent` calls (`run_in_background: true`, `subagent_type: "general-purpose"`,
`model: "opus"`); each time one returns and its report is recorded, dispatch the
next repo from the to-audit list — never more than **3 in flight**. Fewer than 3
repos left → dispatch just that many. No git worktrees: `/codebase-audit` is
read-only and only files issues, so agents in different repo directories cannot
collide.

**Never fan out the whole fleet at once.** A single-message parallel spawn of
one Opus sub-agent per repo (~27 at once) trips Anthropic's server-side burst
limit (`Server is temporarily limiting requests · Rate limited`) — the
2026-06-03 run completed only 3 of 27 repos for exactly this reason (see the
digest comment on the ledger issue). The 3-wide window stays under the
documented 3–4 burst ceiling (anthropics/claude-code#53922) while still auditing
up to 3 repos in parallel — trading a little wall-clock for reliability, the
right trade for an unattended weekly job: the whole fleet gets audited instead
of ~3 random repos.

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
  - Filed: <bucket → issue URL, one per line; omit if none>
  - Skipped-as-dupe: <count>
  - Files inspected: <count>
  - Promotion candidates: <the `promotion candidates spotted:` block from
    /codebase-audit's final report — asset/convention lines, verbatim; omit if none>
  - Note: <one line if anything surprising came up>
```

Keep the window full: each time a sub-agent returns and its report is recorded,
immediately dispatch the next pending repo (up to the 3-in-flight cap). Print a
one-line progress marker per repo as it completes (e.g.
`[3/12] photo-ocr — AUDITED`) so a scheduled run's console shows forward
motion. Do **not** sleep between dispatches — refill the window the moment a
slot frees.

**Rate-limit cut-off — stop dispatching, defer the rest.** A sub-agent failure
whose error or report carries a session-rate-limit signature — "Server is
temporarily limiting requests", "usage limit", "rate limit", "429", or
"Overloaded" — means the shared 5h session budget is spent; **every remaining
dispatch will fail the same way**. So on the **first** such failure: stop
dispatching, and mark that repo **plus all not-yet-completed repos** as `DEFERRED
(rate-limited)`. Do not burn the rest of the budget on dispatches that will all
429 — the deferred repos are what the step-6/7 retry path resumes. A failure
*without* a rate-limit signature stays an ordinary per-repo `ERROR` (a real
single-repo problem, not retried) exactly as before, and the window keeps
refilling.

### 5. Collect results

Hold each sub-agent's structured report as it returns. When the run reaches its
end — either the to-audit list is drained with no agent still in flight, **or**
dispatch was stopped early by a rate-limit cut-off (step 4) — proceed to the
practices ledger (5b) then the digest. Track three terminal buckets so step 6/7
can branch:

- A sub-agent that errors out **without** a rate-limit signature is recorded as
  `ERROR` for its repo (a genuine single-repo failure); it does not block the
  others and the window refills as normal.
- Repos left unaudited by the rate-limit cut-off are `DEFERRED (rate-limited)` —
  these are what the retry path resumes, **not** failures.
- Everything else is its normal `AUDITED` / `CLEAN` / `SKIPPED-BY-LEDGER` result.

Let **`DEFERRED`** = the set of rate-limited repos; step 6/7 keys the whole
clean-vs-cut-short decision off whether it is empty.

### 5b. Upsert the fleet practices ledger

Collect the `Promotion candidates` lines from every sub-agent report. If **all**
were empty, skip this step (the digest still notes "no new assets"). Otherwise
maintain one living catalog issue in **`ferraroroberto/project-scaffolding`** —
the cross-fleet "things that work" ledger. It is the inverse of the audit
issues (assets to remember, not rot to fix), so it lives outside the per-repo
flow and is labelled `audit-meta` so `/issue-triage` filters it out.

Read the existing ledger, then merge — same discipline as `/codebase-audit`
step 8:

```
py C:/Users/rober/.claude/skills/_lib/audit_issue.py get \
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
py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/project-scaffolding --kind practices --label audit-meta \
  --title "fleet practices ledger" --body-file <tmpfile>
```

Capture the printed URL as `PRACTICES_LEDGER_URL` for the digest. If the upsert
fails (e.g. no access to `project-scaffolding`), note `practices: skipped
(<reason>)` and carry on — **never fail the run over the ledger.**

### 6. Decide the outcome, then build the digest

Branch on `DEFERRED` (from step 5) and `IS_FINAL` (from step 4's `arm`):

1. **Clean completion** — `DEFERRED` is empty (the fleet was fully swept).
   **Disarm** the switch and reset the chain, then build + deliver the full
   digest exactly as below / step 7:
   `py C:/Users/rober/.claude/skills/_lib/audit_retry.py clear`.
2. **Cut short, retry pending** — `DEFERRED` is non-empty **and** `IS_FINAL` is
   false (a relaunch is armed and will fire ~4h out). Go **quiet** to avoid
   multiplying the weekly Slack/comment: do **not** upsert the digest-state
   ledger (leave it untouched so the eventual completing run still diffs against
   the true prior week), do **not** post the GitHub comment or Slack ping. Print
   one stdout line for the job history — e.g. `audit-fleet cut short by session
   rate limit: audited X/Y, N deferred — retry armed (attempt n/max), ~4h.` —
   then **skip to step 8**. Do not call `clear` (the armed task must survive).
3. **Cut short, cap reached** — `DEFERRED` is non-empty **and** `IS_FINAL` is
   true (no further retry will fire). Give up gracefully: `clear` the chain, then
   build + deliver a **partial** digest (header flags `M repos deferred (session
   limit) after the retry cap — not retried`, and include a `Deferred
   (rate-limited)` section listing them so they are visible, not silently
   missed). The Slack ping still goes out so you know the weekly audit landed
   partial.

For paths 1 and 3 only, continue building the digest now.

Read the digest-state ledger first so the recap is week-over-week, not a
re-list:
`py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/fleet-config --kind digest`.
Parse the `<!-- audit-fleet-digest -->` block from the returned `body`:

```
<!-- audit-fleet-digest -->
last-run-at: <YYYY-MM-DD>
<name>: <open-audit-issue-count>
...
```

Compose the digest as markdown (single long lines per paragraph, no hard
wraps). This markdown is the canonical artifact: it goes to stdout verbatim and
is attached to the email as a `.md` file; step 7 also renders it to HTML for the
email body. Structure it so the per-repo results form a clean table when
rendered:

- **Header:** date, counts — `N repos audited, M issues filed, K unchanged, J skipped`.
- **Per audited repo:** result line + the issues filed this run (bucket → URL),
  and the **delta vs last week** (`+2 since last week` from the digest-state
  counts). Repos that came back CLEAN or SKIPPED-BY-LEDGER get a one-liner.
- **Skipped section:** repos skipped for dirty / off-branch / non-ff, so the
  user knows they were intentionally left out (not silently missed).
- **Deferred (rate-limited) section** *(partial digest only — path 3)*: repos
  left unaudited when the session limit was hit and the retry cap was reached, so
  they are visibly outstanding rather than silently dropped.
- **What's new this week:** the issues filed *this run* are by definition the
  delta — list them at the top so the email leads with what changed, not
  standing backlog.
- **New fleet assets this week:** the promotion candidates added to the practices
  ledger this run (asset/convention one-liners), with the `PRACTICES_LEDGER_URL`.
  If none were added, one line: `No new fleet assets catalogued this week.`

Then upsert the digest-state ledger issue with today's date and the current
per-repo open-audit-issue counts, so next week can diff. The helper handles
create-vs-edit, collapses strays, and stamps the marker (keep the
`<!-- audit-fleet-digest -->` block intact):

```
py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/fleet-config --kind digest --label audit-meta \
  --title "audit-fleet digest state" --body-file <tmpfile>
```

Capture its printed URL as `DIGEST_ISSUE_URL` and use that for the comment in
step 7 — never a hardcoded issue number.

### 7. Deliver the digest

*(Paths 1 and 3 only — a path-2 "retry pending" run already printed its one-line
stdout note in step 6 and skipped straight to step 8, so it never reaches here.)*

Two channels. stdout is the reliable one (a scheduled run captures it in app-launcher's job history); the GitHub comment is the durable record that the Slack ping links to.

- **stdout:** print the full markdown digest. Always.
- **GitHub comment:** post the digest as a comment on the `audit-fleet digest state` issue in `ferraroroberto/fleet-config` — the one step 6 upserted (`DIGEST_ISSUE_URL`), never a hardcoded id — turning that issue into a running log of every weekly run. Use the `gh issue comment` output URL:

  ```bash
  COMMENT_URL=$(gh issue comment "$DIGEST_ISSUE_URL" --repo ferraroroberto/fleet-config --body "$DIGEST_MARKDOWN")
  # gh issue comment prints the URL of the created comment on stdout
  ```

  If `gh` fails or the URL is empty, note `comment: skipped (<reason>)` and carry on. **Never fail the run over the comment.**

- **Slack ping:** call `notify_complete.py --kind audit` with the captured comment URL and a one-line summary. This is deterministic — the skill hands the hook exact structured args; the hook assembles the message:

  ```
  py C:/Users/rober/.claude/hooks/notify_complete.py \
    --kind audit \
    --comment-url "$COMMENT_URL" \
    --summary "<N> audited, <M> issues filed, <K> unchanged"
  ```

  If `COMMENT_URL` is empty (comment was skipped), omit `--comment-url` so the ping still goes out link-less. This call is a silent no-op if no `slack_notify_channel` is configured; it always exits 0 and can never block or delay the finish.

### 8. Final report

One concise block: the plan line from step 3, per-repo results, where the digest went (stdout always; comment URL or skipped reason; Slack pinged or no-op), and the digest-state issue URL. Stop.

## Hard rules

- **The ledger is the source of truth for "changed".** The orchestrator's cheap
  gate (step 3) must apply the identical skip condition to `/codebase-audit`
  step 2 — HEAD count `0` **and** rubric-sha unchanged, where `rubric-sha` is the
  sha256 of the **project CLAUDE.md only** (not the global file) in both skills.
  If you change one, change both, or they will disagree and either re-audit
  needlessly or skip wrongly.
- **Read-only on source.** This skill and its sub-agents never edit code,
  commit, push, or restart. The only writes are audit issues, the per-repo
  ledger, the digest-state issue, the digest comment, and the cross-fleet
  practices ledger in `project-scaffolding`. The practices ledger is the one
  write target outside `fleet-config` — still an issue, never source.
- **Never disturb in-progress work.** Dirty or off-default-branch repos are
  skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, opus, through a ≤3 sliding window.** Keep at most 3
  Opus sub-agents in flight (the global Opus concurrency cap — see
  `~/.claude/CLAUDE.md`); refill the window as each returns. Never a
  single-message parallel fan-out across the fleet: a simultaneous ~27-wide Opus
  spawn trips Anthropic's server-side burst limit and leaves most repos
  un-audited (the 2026-06-03 incident; ceiling is 3–4 per
  anthropics/claude-code#53922). #57 first fixed this by going strictly
  sequential (1 at a time) — the window of 3 refines that, restoring throughput
  while staying under the burst ceiling. No worktrees (audits don't collide).
  Don't read repo source in the orchestrator.
- **Degrade, don't block.** Built for unattended `claude -p`. A per-repo failure
  is reported and skipped; only a pre-flight failure stops the whole run. Never
  wait on an interactive prompt.
- **Never poll your own session %; never sleep waiting for a reset.** The live
  rate-limit % is not readable headless (it is fed to the statusline only, never
  persisted). Survive limits the idempotent way: arm the dead-man's switch
  (step 4), stop dispatching on the first rate-limit signature (step 4), and let
  the bounded self-relaunch resume the rest. A clean run disarms; a fresh run
  resets the chain (step 1). All retry state and task scheduling live in
  `audit_retry.py` — never hand-roll `schtasks` or a counter, and never block the
  process for hours waiting on a window.
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
- **Per-category trend data lives in the per-repo ledger.** Each whole-repo
  audit posts a counts-only `<!-- audit-snapshot -->` comment on that repo's
  `codebase-audit ledger` issue (see `/codebase-audit` step 9). Open a repo's
  ledger to read its findings trajectory over time; this fleet digest stays
  aggregate (per-repo totals, week-over-week) by design.
- **The weekly job** that schedules this lives in app-launcher
  (`config/jobs.json`, a `weekly` schedule, `visible: true` console) and calls a
  thin `skills/audit-fleet/run-weekly.bat` wrapper in this repo. See that repo
  for the trigger; this skill is the work.
- **Why a dead-man's switch and not a session-% gate** (fleet-config#222): the
  obvious "read my session % and wait" cannot work headless — Claude Code feeds
  `rate_limits.five_hour.used_percentage` to the statusline via stdin JSON only
  at TUI render time and never persists it, and the statusline does not render
  under `claude -p`, so there is no value to poll in the run that actually hits
  the limit. Arming the relaunch *before* the heavy phase (rather than scheduling
  it *after* detecting a 429) is deliberate: the worst case is the orchestrator
  process dying outright on a session limit, which leaves no chance to run a
  "schedule a retry" step at the end — so the safety net must already be set, and
  a clean finish simply cancels it. The ~4h delay lets the rolling 5h window
  recover; if a retry still lands too early it re-arms and the staircase
  converges within the cap. The retry chain self-relaunches as `resume` so the
  ledger gate skips repos already audited earlier in the chain — a resume is
  near-free, which is what makes a multi-attempt chain cheap.
