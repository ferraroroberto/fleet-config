---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass — enumerate the local ferraroroberto repos, skip the ones unchanged since their last audit (per-repo ledger gate), fan the changed ones out to parallel sub-agents that each run the full audit, then emit one diff-based weekly digest as a Gmail draft (and to stdout). Built to run unattended on a weekly schedule. Use when the user wants a whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos".
---

# audit-fleet

**Goal:** A fleet-wide, idempotent, scatter-gather wrapper around
`/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip the
ones that haven't changed since their last audit, fan the changed ones out to
**parallel sub-agents** (one per repo) that each run the full `/codebase-audit`
procedure, then collect the results into **one diff-based digest** delivered as
a Gmail draft — an HTML body that renders headers and tables, with the markdown
digest attached as a file — and printed to stdout (so a scheduled run captures
it in history).

**This skill files no issues itself.** The only writes are (a) the audit issues
that each sub-agent's `/codebase-audit` files, (b) the per-repo `audit-meta`
ledger those audits update, and (c) one `audit-fleet digest state` ledger issue
in `claude-config` for week-over-week deltas, plus the Gmail draft. It never
edits source, commits, pushes, or restarts anything.

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
  ledger gate, fast-forward syncs, fan-out, collection, the digest. **All file
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
3. **Ledger gate.** Read the ledger:
   `gh issue list -R ferraroroberto/<name> --label audit-meta --state open --json number,body`.
   - No ledger issue → **audit** (first run).
   - Else parse `last-audited-sha` + `rubric-sha`. Compute the repo's current
     `rubric-sha` = sha256 of the global CLAUDE.md (step 1) concatenated with
     `<path>/CLAUDE.md` (empty string if absent). If
     `git -C <path> rev-list <last-audited-sha>..HEAD --count` is `0` **and**
     `rubric-sha` is unchanged → record `unchanged (skipped)`. Otherwise →
     **audit**.

Print a one-line plan before fan-out, e.g.:

```
Fleet audit plan — 3 to audit, 24 unchanged, 2 skipped (dirty)
  audit:     app-launcher, photo-ocr, local-llm-hub
  skipped:   reporting (dirty), website (off-branch)
```

If nothing is to be audited, jump to step 6 with an empty result set (the digest
still goes out so the weekly run always produces a record).

### 4. Fan out — one background sub-agent per repo to audit

Spawn all sub-agents in a **single message** with multiple parallel `Agent`
calls, each `run_in_background: true`, `subagent_type: "general-purpose"`,
`model: "opus"`. No git worktrees: `/codebase-audit` is read-only and only
files issues, so agents in different repo directories cannot collide.

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
  - Note: <one line if anything surprising came up>
```

Then print a confirmation block listing every sub-agent dispatched and stop
spawning. Do **not** poll or sleep — the harness re-invokes you as each
background agent completes.

### 5. Collect results

As each sub-agent returns, hold its structured report. When **all** have
returned, proceed to the digest. (A sub-agent that errors out is recorded as
`ERROR` for its repo and does not block the others.)

### 6. Build the diff-based digest

Read the digest-state ledger first so the recap is week-over-week, not a
re-list: `gh issue list -R ferraroroberto/claude-config --label audit-meta --state open --json number,title,body`,
find the issue titled `audit-fleet digest state`, parse its block:

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

Then upsert the digest-state ledger issue (create with label `audit-meta`,
title `audit-fleet digest state`, `--assignee @me` if absent) with today's date
and the current per-repo open-audit-issue counts, so next week can diff.

### 7. Deliver the digest

Two channels. stdout is the reliable one (a scheduled run captures it in
app-launcher's job history); the email is best-effort. The email itself carries
the digest as a **rich artifact** — an HTML body that renders headers and tables
in Gmail, plus the markdown digest attached as a file — never as raw markdown in
the plain-text body (Gmail shows that as literal `##`, `|`-pipes, and
`[text](url)` syntax).

- **stdout:** print the full markdown digest. Always.
- **Gmail draft:** call `mcp__claude_ai_Gmail__create_draft` with
  `to: ["roberto.ferraro@gmail.com"]`, subject
  `Fleet audit <YYYY-MM-DD> — <N> repos changed, <M> issues filed`, and:
  - **`htmlBody`** = the digest rendered as HTML — real `<h2>`/`<h3>` headers,
    the per-repo result/delta grid as a `<table>` (repo · result · filed this
    run · delta vs last week), filed-issue lists as `<ul>` with `<a>` links to
    each issue. This is the channel that makes the digest readable; build it
    deliberately, don't just wrap the markdown in `<pre>`.
  - **`body`** = the same digest as plain markdown — the plain-text alternative
    for clients without HTML.
  - **`attachments`** = the markdown digest as one file,
    `filename: "fleet-audit-<YYYY-MM-DD>.md"`, `mimeType: "text/markdown"`,
    `content` = its base64-encoded bytes. This is the annex the user can open as
    a proper file. The tool description hedges that attachments are "not
    supported yet" — so treat it as **best-effort**: if the call is rejected or
    the attachment is dropped, note `attachment: skipped (<reason>)`, keep the
    HTML body, and carry on. Never fail the run over the attachment.

  The Gmail MCP creates a **draft** (it has no auto-send) — the digest lands
  ready-to-send in Gmail, not auto-delivered. If the Gmail tool itself is
  unavailable or errors (e.g. the MCP connector isn't loaded in a headless run),
  note `email: skipped (<reason>)` and rely on stdout. **Never** fail the run
  over the email.
- **No rolling issue comment** — deliberately dropped as noise; the per-repo
  audit issues are the durable record and everything already lives in GitHub.

### 8. Final report

One concise block: the plan line from step 3, per-repo results, where the
digest went (stdout always; draft created / skipped, and whether the `.md`
attachment was accepted or skipped), and the digest-state issue URL. Stop.

## Hard rules

- **The ledger is the source of truth for "changed".** The orchestrator's cheap
  gate (step 3) must apply the identical skip condition to `/codebase-audit`
  step 2 — HEAD count `0` **and** rubric-sha unchanged. If you change one, change
  both, or they will disagree and either re-audit needlessly or skip wrongly.
- **Read-only on source.** This skill and its sub-agents never edit code,
  commit, push, or restart. The only writes are audit issues, the per-repo
  ledger, the digest-state issue, and the Gmail draft.
- **Never disturb in-progress work.** Dirty or off-default-branch repos are
  skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, parallel, background, opus.** No worktrees (audits
  don't collide). Don't read repo source in the orchestrator.
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
