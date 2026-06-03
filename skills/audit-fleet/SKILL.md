---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass — enumerate the local ferraroroberto repos, skip the ones unchanged since their last audit (per-repo ledger gate), audit the changed ones one at a time via sequential sub-agents that each run the full audit, then emit one diff-based weekly digest as a GitHub comment on the audit-fleet ledger issue + a Slack ping with the link (and to stdout). Also catalogs each repo's hard-won reusable solutions into one cross-fleet "practices ledger" issue in project-scaffolding. Built to run unattended on a weekly schedule. Use when the user wants a whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos".
---

# audit-fleet

**Goal:** A fleet-wide, idempotent, scatter-gather wrapper around `/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip the ones that haven't changed since their last audit, audit the changed ones **one at a time via sequential sub-agents** (one per repo) that each run the full `/codebase-audit` procedure, then collect the results into **one diff-based digest** posted as a GitHub comment on the `audit-fleet digest state` ledger issue in `claude-config` (the running log) and printed to stdout (so a scheduled run captures it in history). A Slack ping with the comment link is sent deterministically via `notify_complete.py --kind audit`.

**This skill files no issues itself.** The only writes are (a) the audit issues that each sub-agent's `/codebase-audit` files, (b) the per-repo `audit-meta` ledger those audits update, (c) one `audit-fleet digest state` ledger issue in `claude-config` for week-over-week deltas, (d) the digest comment on that issue, and (e) one cross-fleet `fleet practices ledger` issue in `project-scaffolding` cataloguing reusable solutions. It never edits source, commits, pushes, or restarts anything.

**Designed for unattended runs.** A weekly app-launcher job invokes this via
`claude -p "/audit-fleet" --model claude-opus-4-7 --effort high
--permission-mode bypassPermissions`. Every step must therefore degrade
gracefully rather than block on a prompt.

## Arguments

- No argument → the whole fleet.
- One argument that looks like a repo name (e.g. `/audit-fleet app-launcher`) →
  restrict to that single repo. Match the bare repo name.

Anything else → treat as no argument.

## Execution rules (read before running any command)

- **Shell:** the Bash tool here is **Git Bash**. `gh` and `git` work
  identically in it. Do not use PowerShell syntax (`&`, `$env:`, here-strings)
  in Bash. Windows paths map as `/e/automation/...`.
- **The orchestrator only does cheap, safe work:** enumeration, the per-repo
  ledger gate, fast-forward syncs, sequential dispatch, collection, the digest. **All file
  reading happens inside sub-agents** — this is what keeps the orchestrator's
  context (and the weekly token spend) bounded.
- **Never disturb in-progress work.** A repo that is dirty or not on its default
  branch is skipped and reported — never stashed, never force-switched.

## Steps

Run in order. A failure on one repo is reported and skipped; it does not abort
the whole run. Only a pre-flight failure (step 1) stops everything.

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. If not, stop:
  "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.
- Read the global rubric once: `~/.claude/CLAUDE.md` (or
  `$HOME/.claude/CLAUDE.md`). Hold its bytes for the rubric-hash in step 3.

### 2. Enumerate fleet repos

List local git repos under the fleet root that have a `ferraroroberto` remote:

```
for d in /e/automation/*/; do
  url=$(git -C "$d" remote get-url origin 2>/dev/null) || continue
  case "$url" in *ferraroroberto/*) echo "$(basename "$d")";; esac
done
```

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
     `rubric-sha` = sha256 of the global CLAUDE.md (step 1) concatenated with
     `<path>/CLAUDE.md` (empty string if absent). If
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

### 4. Audit each repo — one sub-agent at a time (sequential)

Process the to-audit list **sequentially**: dispatch one `Agent` call for the
next repo, **wait for it to return**, record its report, then dispatch the next.
Use a foreground call (`run_in_background` omitted/`false`),
`subagent_type: "general-purpose"`, `model: "opus"`. No git worktrees:
`/codebase-audit` is read-only and only files issues, so agents in different repo
directories cannot collide.

**Do not spawn them all at once.** A single-message parallel fan-out across the
whole fleet (one Opus sub-agent per repo, ~27 at once) trips Anthropic's
server-side burst limit (`Server is temporarily limiting requests · Rate
limited`) — the 2026-06-03 run completed only 3 of 27 repos for exactly this
reason (see the digest comment on the ledger issue). Sequential dispatch trades
wall-clock time for reliability, which is the right trade for an unattended
weekly job: the whole fleet gets audited instead of ~3 random repos.

Prompt template (substitute `<name>` / `<path>`):

```
Run a resting-state codebase audit on the <name> repo.

1. cd to <path>.
2. Execute the procedure in
   E:\automation\claude-config\skills\codebase-audit\SKILL.md against this repo,
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

Dispatch the next repo only once the current sub-agent has returned and its
report is recorded. Print a one-line progress marker per repo as you go (e.g.
`[3/12] photo-ocr — AUDITED`) so a scheduled run's console shows forward
motion. Do **not** sleep between repos — start the next dispatch immediately
after the previous returns.

### 5. Collect results

Hold each sub-agent's structured report as it returns. When the **last** repo
in the sequential loop has returned, proceed to the practices ledger (5b) then
the digest. (A sub-agent that errors out — including a rate-limit error — is
recorded as `ERROR` for its repo and does not block the remaining repos; the
loop simply moves on to the next.)

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

### 6. Build the diff-based digest

Read the digest-state ledger first so the recap is week-over-week, not a
re-list:
`py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/claude-config --kind digest`.
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
  --repo ferraroroberto/claude-config --kind digest --label audit-meta \
  --title "audit-fleet digest state" --body-file <tmpfile>
```

Capture its printed URL as `DIGEST_ISSUE_URL` and use that for the comment in
step 7 — never a hardcoded issue number.

### 7. Deliver the digest

Two channels. stdout is the reliable one (a scheduled run captures it in app-launcher's job history); the GitHub comment is the durable record that the Slack ping links to.

- **stdout:** print the full markdown digest. Always.
- **GitHub comment:** post the digest as a comment on the `audit-fleet digest state` issue in `ferraroroberto/claude-config` — the one step 6 upserted (`DIGEST_ISSUE_URL`), never a hardcoded id — turning that issue into a running log of every weekly run. Use the `gh issue comment` output URL:

  ```bash
  COMMENT_URL=$(gh issue comment "$DIGEST_ISSUE_URL" --repo ferraroroberto/claude-config --body "$DIGEST_MARKDOWN")
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
  step 2 — HEAD count `0` **and** rubric-sha unchanged. If you change one, change
  both, or they will disagree and either re-audit needlessly or skip wrongly.
- **Read-only on source.** This skill and its sub-agents never edit code,
  commit, push, or restart. The only writes are audit issues, the per-repo
  ledger, the digest-state issue, the digest comment, and the cross-fleet
  practices ledger in `project-scaffolding`. The practices ledger is the one
  write target outside `claude-config` — still an issue, never source.
- **Never disturb in-progress work.** Dirty or off-default-branch repos are
  skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, sequential (one at a time), opus.** Dispatch the
  next only after the current returns — never a single-message parallel
  fan-out across the fleet. A simultaneous ~27-wide Opus spawn trips Anthropic's
  server-side burst limit and leaves most repos un-audited (the 2026-06-03
  incident). No worktrees (audits don't collide). Don't read repo source in the
  orchestrator.
- **Degrade, don't block.** Built for unattended `claude -p`. A per-repo failure
  is reported and skipped; only a pre-flight failure stops the whole run. Never
  wait on an interactive prompt.
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
  the rubric hash busts the cache when the grading criteria themselves change.
- **The weekly job** that schedules this lives in app-launcher
  (`config/jobs.json`, a `weekly` schedule, `visible: true` console) and calls a
  thin `audit-fleet.bat` wrapper in this repo. See that repo for the trigger;
  this skill is the work.
