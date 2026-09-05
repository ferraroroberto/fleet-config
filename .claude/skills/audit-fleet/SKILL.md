---
name: audit-fleet
description: Run /codebase-audit across every repo in the E:\automation fleet in one pass, emitting one weekly digest (GitHub comment + Telegram ping). Whole-fleet quality sweep — e.g. "/audit-fleet", "audit the whole fleet", "weekly codebase audit across all repos". Also runs unattended weekly.
---

# audit-fleet

**Capability preflight:** read [workflow-capabilities](../../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional Claude examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** fleet-wide, idempotent, scatter-gather wrapper around `/codebase-audit`. Walk every repo under `E:\automation\`, cheaply skip unchanged ones, audit changed ones through a **bounded window of up to 3 concurrent sub-agents** (one per repo), collect results into **one diff-based digest**: GitHub comment on the `audit-fleet digest state` ledger issue in `fleet-config` + stdout, with a Telegram ping via `notify_complete.py --kind audit`.

**Scope boundary — source code, not context.** Audits *project source code* quality only. Fleet *context surface* is `/context-audit`'s lens; web-app *visual conformance* is `/design-sweep`'s (files `design-drift`/`cert-drift` issues). This digest only *reports* that bucket's open counts week-over-week (step 5); never runs the design lint itself.

**Files no issues itself.** Only writes: (a) audit issues each sub-agent's `/codebase-audit` files, (b) per-repo `audit-meta` ledger, (c) one `audit-fleet digest state` ledger issue in `fleet-config`, (d) the digest comment on it, (e) one `fleet practices ledger` issue in `project-scaffolding`. Orchestrator never edits source, commits, pushes, or restarts anything. **One exception inside sub-agents:** `/codebase-audit` step 8b self-heals a **security** finding in place (redacted issue + auto-fix + auto-merge) — scoped to security, gated on its own rules (claim, mandatory regression test, generic artifacts, green-gate-only merge).

**Unattended.** Weekly app-launcher job invokes co-located `run-weekly.bat`, routing `/audit-fleet` + Sonnet/high-effort/bypass-permissions flags through `claude_progress.py`. Every step must degrade gracefully, never block on a prompt. Orchestrator runs `easy` tier; step-3 sweep sub-agents run `hard` tier (`docs/model-tiers.md`).

## Arguments

- No argument → whole fleet.
- One argument matching a repo name (e.g. `/audit-fleet app-launcher`) → restrict to that repo (bare name match).
- Anything else → treat as no argument.

## Execution rules (read before running any command)

- **Shell:** Bash tool here is **Git Bash**. `gh`/`git` work identically. No PowerShell syntax (`&`, `$env:`, here-strings). Windows paths map as `/e/automation/...`.
- **Orchestrator only does cheap, safe work:** enumeration, per-repo ledger gate, fast-forward syncs, windowed dispatch, collection, digest. **All file reading happens inside sub-agents** — keeps orchestrator context/token spend bounded.
- **Never disturb in-progress work.** Dirty or off-default-branch repo → skip and report, never stash or force-switch.
- **Never end the turn to "wait for it."** Headless one-shot process via `run-weekly.bat` — no turn loop, no human, no wake-up after a turn ends. `run_in_background: true` + ending the turn = false success (`exit_code: 0`) while nothing past that point happens (`fleet-config#314`).
  - Extends to the harness auto-backgrounding a call past its own timeout ceiling: never let a single call span the sweep's real runtime (`fleet-config#609` — step 2 sweep ran 345s–1460s+, past the Bash tool's 600s ceiling; see step 2).
  - Every command — including step 3's rate-limit pause — runs synchronously (foreground) or polls to completion within the same turn against a concrete, externally observable condition (e.g. `Monitor`'s until-loop for step 3's rate-gate pause). Never fire-and-forget.
  - A model-composed `Monitor` wait once returned without blocking and the turn ended awaiting a notification that never came (`fleet-config#609`, reopened) — step 2 therefore uses a foreground blocking helper script, not a model-composed wait.

## Self-pacing against the live session budget

A full sweep can exhaust the rolling **5-hour session rate limit** mid-run. `statusline-command.ps1` caches live `rate_limits.five_hour` usage % + `resets_at` to `~/.claude/hooks/state/rate-limits.json` on every render (`fleet-config#259`), so this skill pauses dispatch proactively, waits in place, and resumes within the same run — no relaunch, no OS-level scheduling, no `resume` argument; a run always ends by delivering one full digest. Gate/pause/fallback mechanics wired into step 3. Full design: `docs/rate-gate.md`.

## Steps

Run in order. A failure on one repo is reported and skipped, doesn't abort the run. Only a pre-flight failure (step 1) stops everything.

### 1. Pre-flight

- `gh auth status` must be authenticated as `ferraroroberto`. If not, stop: "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (fleet root). Else stop.
- No need to read global `~/.claude/CLAUDE.md`: step-2 gate hashes each repo's **own** project CLAUDE.md, not the global file, so a global edit never busts the cache. Sub-agents read the global rubric when grading (`/codebase-audit` step 3).

### 2. One Python sweep: enumerate, sync, gate — launched detached, polled to completion

Enumeration + per-repo gating is **one deterministic Python sweep**; orchestrator reads its JSON, never runs a per-repo LLM loop. Runtime (345s–1460s+, growing with the fleet) regularly crosses the Bash tool's 600s ceiling, so this step launches the sweep **detached** and polls a **sentinel file** — concrete, externally observable — instead of an opaque harness-tracked background task.

**Launch** (returns in well under a second):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/fleet_audit_scan.py --root E:\automation [--only <repo-name>] --detach
```

Prints exactly one line: `LAUNCHED pid=<pid> out=<result-path> log=<log-path>`

Capture `out=` verbatim as `SWEEP_RESULT` — never construct/guess the path; `fleet_audit_scan.py` generates a fresh unique one per invocation (`E:/tmp/fleet-audit-scan-<pid>-<ns>.json` when `E:/tmp` exists, system temp dir otherwise), so overlapping runs never collide. The detached child is the same `scan()`, spawned `CREATE_NEW_PROCESS_GROUP | NO_WINDOW` (same pattern as `hooks/restart_and_verify_webapp.py`) so it outlives this tool call. It publishes JSON to `SWEEP_RESULT` atomically (temp-file-then-rename) or an `{"error": "..."}` payload if it raises, so a crash is distinguishable from "still running".

**Wait** with a foreground blocking helper script — never `Monitor`, never a raw backgrounded Bash call (`fleet-config#609`, reopened):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/wait_for_sentinel.py --path "$SWEEP_RESULT" --timeout-seconds 560
```

**Exit 0** (`SENTINEL-READY <path>`) — sweep done: `cat "$SWEEP_RESULT"` and continue with its JSON. **Exit 2** (`SENTINEL-NOT-READY <path>`) — not a wait-for-notification signal; re-invoke the exact same command again immediately, same turn. `--timeout-seconds 560` keeps a call under the Bash tool's 600s ceiling — the retry is normal, not a failure. **Cap retries at 26** (~4 hours, above the observed 1460s outlier). If `SWEEP_RESULT` still absent after 26 exit-2 results, or carries `{"error": ...}`: treat as pre-flight-class failure — print `Fleet audit plan — sweep did not complete: <reason>` and skip to step 6's delivery assertion (prints `SCHEDULED-RUN-FAILED`); never fabricate a plan line from an unfinished scan. **Never end this turn on an exit-2 result** — only two valid stops: resolved exit 0, or exhausted retry cap.

Once read, `SWEEP_RESULT`'s content is this step's JSON output.

The script (`skills/_lib/fleet_audit_scan.py`, built on `audit_issue.py`'s `evaluate_repo`) walks `E:\automation\*\`, skips linked worktrees (`<repo>-wt-<N>`: `.git` is a file, not a dir — without this guard a worktree surfaces as a spurious off-branch repo), filters to repos with a `ferraroroberto` remote, skips dirty/off-branch repos, syncs the rest (`fetch` + `pull --ff-only`), and runs the **same ledger-gate + self-fix-churn decision `/codebase-audit` step 2 uses** (`evaluate_repo` — one implementation) per repo.

JSON shape:

```
{"to_audit": [{"repo": "...", "path": "...", "reason": "unparseable-ledger"|"unresolvable-baseline"?, "baseline_sha": "..."?, "ledger_issue": N?}, ...],
 "unchanged": ["repo1", "repo2", ...],
 "self_fix": [{"repo": "...", "path": "...", "decision": "SKIP_SELF_FIX", "closed_issues": [...], ...}, ...],
 "below_threshold": [{"repo": "...", "path": "...", "decision": "SKIP_BELOW_THRESHOLD", "significance": N, "threshold": M, ...}, ...],
 "skipped": [{"repo": "...", "reason": "dirty"|"off-branch"|"non-ff"|"index-lock in flight"}, ...],
 "stale_lock": [{"repo": "...", "path": "...", "verdict": "stale"|"stale_unconfirmed", "age_seconds": N, "size": N, "reason": "..."}, ...],
 "errors": [{"repo": "...", "reason": "..."}, ...],
 "enumerated": N,
 "accounting": {"enumerated": N, "bucketed": N, "unaccounted": 0, "balanced": true}}
```

`enumerated` counts repos the walk *found*, before any decision; `accounting` asserts the seven buckets sum back to it. A repo in no bucket shows nonzero `unaccounted` / `balanced: false` (fleet-config#567 — two repos vanished from three consecutive runs). Never report counts that don't add up as healthy.

A `stale_lock` entry is a repo carrying a stranded `.git/index.lock` — **the one bucket that never self-heals**: surface every entry by name, every week, until a human clears it. Invisible to every read (`status`, `fetch`, `rev-list`, an up-to-date `pull --ff-only` all exit 0) while the repo is frozen against every write (fleet-config#667 — nine locked repos filed as healthy `below_threshold` went unnoticed 15 days). `verdict: stale` = no git process running at all; `stale_unconfirmed` = past threshold but couldn't be established — both need a look, neither auto-repaired. **Never delete a lock from this skill**, and never instruct a sub-agent to — it's another process's file; fix is a human confirming the holder is dead, then removing it.

A `to_audit` entry carrying a `reason` is **not** organic change — the gate was *forced* to audit because it couldn't read the ledger (`fleet_audit_scan.broken_ledgers()` returns these; don't re-derive the filter in prose). `unresolvable-baseline` = recorded `last-audited-sha` (`baseline_sha`) resolves to nothing in the checkout (almost always a squash-merged, deleted feature-branch tip). `unparseable-ledger` = the ledger issue (`ledger_issue`) carries no readable `<!-- audit-ledger` block. Both belong in `to_audit` (safe answer), but each re-bills a full Opus whole-repo pass *every week* until the ledger is repaired — surface by name in the plan line and digest. Both self-heal once the repo's own audit reaches step 9 (every ledger write normalizes the block).

For every `self_fix` entry, the script has **already** advanced that repo's ledger (HEAD sha + today's date, same rubric-sha) and posted a `<!-- audit-self-fix -->` comment on its ledger issue — no further write needed. A `below_threshold` entry: the repo has real organic commits since last audit, but weighted-LOC significance (`skills/_lib/audit_issue.py`'s `unexplained_weighted_loc` — feature/refactor full weight, docs/test none, fix/chore partial) hasn't crossed the threshold. Its ledger sha is **not** advanced — next week covers the same growing range plus new changes, accumulating until it crosses into `to_audit` (which then covers everything back to the ledger sha). If the single-repo argument was passed, `--only <name>` restricts the whole sweep to it.

Print a one-line plan from the JSON, e.g.:

```
Fleet audit plan — 32 repos enumerated, 3 to audit, 24 unchanged, 1 self-fix, 2 below-threshold, 2 skipped (dirty)
  audit:            app-launcher, photo-ocr, local-llm-hub
  broken-ledger:    grocery-shopping-automation (baseline 99100ac unresolvable), local-llm-hub (ledger #31 unparseable)
  self-fix:         website (closed #71, #64 — ledger advanced, no organic change)
  below-threshold:  accounting-quarterly (591/1000), pvgis (85/1000)
  skipped:          reporting (dirty), site (off-branch), pvgis (index-lock in flight)
  stale-lock:       email-archiver (stale, 15.2d), algo-trading (stale, 15.2d)
```

Lead with `accounting.enumerated`; print `broken-ledger:` naming every `broken_ledgers()` entry with reason. If `accounting.balanced` is `false`, print `WARNING: <N> repos in no bucket` on its own line. Print `stale-lock:` whenever non-empty, naming every repo with verdict and age.

**Name every skipped repo with its own `reason` string, verbatim from the JSON — never a bare count, never a hardcoded list of expected reasons.** `fleet-config#642`: skipped repos dropped from the report; `#667` added `index-lock in flight` — a new reason must reach the operator by name without this file being edited again, hence rendering from the reason returned rather than a vocabulary written here.

If `to_audit` is empty, jump to step 5 with an empty result set (digest still goes out).

### 3. Audit each repo — a bounded window, self-paced against the live session budget

Process the to-audit list through a **bounded concurrency window of up to 3 sub-agents** — session-token-budget pacing default and the live Opus burst-limiter cap: audit sub-agents run at **`hard` tier** (`docs/model-tiers.md`), which resolves to `model: "opus"` on Claude Code today.

Before each dispatch/refill, call `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70` and branch on `DECISION`:

- **`OK` / `UNKNOWN`** → dispatch eligible repos through the capability contract, hard tier, up to the smaller of three workers and the available host slots. Collect each terminal result in the same turn before refilling; a timeout is still pending. No-spawn fallback is explicit serial audit execution with the same per-repo issue/report gates. Audit work in different repos does not need worktrees unless the invoked security-heal path writes code; that path retains its own isolation and shipping gates.
- **`PAUSE`** → stop dispatching new sub-agents (let in-flight ones finish), wait via `Monitor`'s until-loop pattern against the printed `WAIT_SECONDS`/`RESETS_AT`, re-check and resume. **Cap: 3 pause cycles per run** — a 4th would-be pause instead marks every remaining repo `SKIPPED (session limit — exceeded pause retries)` and moves to the digest; next week's ledger gate picks these up for free.

**Reactive fallback.** A sub-agent failure carrying a rate-limit signature ("Server is temporarily limiting requests", "usage limit", "rate limit", "429", "Overloaded") despite the proactive gate is treated as `PAUSE`: stop dispatching, wait (re-run `rate_gate.py check` for a fresh `WAIT_SECONDS`, conservative fallback wait if still `UNKNOWN`), resume. A failure *without* a rate-limit signature stays an ordinary per-repo `ERROR` and the window keeps refilling.

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

The `new`/`carried`/`stale` counts per bucket come straight from `/codebase-audit` step 10's final report table (from step 8's run-log counts, never recomputed) — lets the digest (step 5) separate genuinely new findings from standing backlog.

Keep the window full: each time a sub-agent returns and its report is recorded, immediately dispatch the next pending repo (up to the 3-in-flight cap, subject to `rate_gate.py check`). Print a one-line progress marker per repo as it completes (e.g. `[3/12] photo-ocr — AUDITED`). Do **not** sleep between dispatches when the gate reads `OK` — refill the moment a slot frees. Entire loop runs in one turn: block on `TaskOutput` for the in-flight window, refill on each return, repeat until the to-audit list is drained — turn never ends with a sub-agent still dispatched (`fleet-config#506`).

### 4. Collect results

Hold each sub-agent's structured report as it returns. When the to-audit list is drained with no agent in flight, proceed to the practices ledger (4b) then the digest. Track terminal buckets `AUDITED`/`CLEAN`/`SKIPPED-BY-LEDGER`/`ERROR` plus the `self_fix`, `below_threshold`, `skipped`, `stale_lock` buckets already decided by step 2's sweep (carried unchanged — no sub-agent touches those repos):

- A sub-agent that errors **without** a rate-limit signature is recorded `ERROR` for its repo — doesn't block others, window refills normally.
- Everything else is its normal result — or, only if the 3-pause safety net (step 3) was hit, `SKIPPED (session limit — exceeded pause retries)`.

### 4b. Upsert the fleet practices ledger

Collect `Promotion candidates` lines from every sub-agent report. If **all** were empty, skip this step (digest notes "no new assets"). Otherwise maintain one living catalog issue in **`ferraroroberto/project-scaffolding`** — the cross-fleet "things that work" ledger — labelled `audit-meta` so `/issue-triage` filters it out.

Read the existing ledger:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get \
  --repo ferraroroberto/project-scaffolding --kind practices
```

Merge this run's candidates into the returned body: **preserve every existing entry verbatim**, **dedupe by repo + capability** (refresh `Where:` path if it moved), append a dated `## Ledger run log` bullet. Sort into two sections — **Capabilities** (fleet-worthy assets) and **Convention candidates** (nominations for `project-scaffolding`). The ledger only *nominates* conventions — filing one is a manual `/issue-add` call, so the weekly run never auto-spams `project-scaffolding`. Body shape (no hard wraps; helper prepends the `kind=practices` marker — keep `<!-- fleet-practices -->` intact):

```
<!-- fleet-practices -->
## Capabilities
- **<repo>** — <capability one-liner>. Where: `<path/module>`. Reach for this when ...
## Convention candidates (nominate to project-scaffolding)
- **<repo>** — <convention>. Generalizable because ... → /issue-add if adopted.
## Ledger run log
- <YYYY-MM-DD>: +N capabilities, +M candidates from <repos>.
```

Write to a **unique** temp file — never a fixed shared name (parallel sub-agents share `E:/tmp`, same rule as `skills/codebase-audit/SKILL.md` step 8): `E:/tmp/audit-practices-ledger-<short-sha>.md`, `<short-sha>` = `git rev-parse --short HEAD` in this repo. Then upsert:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/project-scaffolding --kind practices --label audit-meta \
  --title "fleet practices ledger" --body-file <tmpfile>
```

Capture the printed URL as `PRACTICES_LEDGER_URL` for the digest. If the upsert fails (e.g. no access to `project-scaffolding`), note `practices: skipped (<reason>)` and carry on — **never fail the run over the ledger.**

### 5. Build the digest

A run always reaches this step with a complete result set — every repo `AUDITED`/`CLEAN`/`SKIPPED-BY-LEDGER`/`SELF-FIX`/`BELOW-THRESHOLD`/`ERROR`, or (only if the 3-pause safety net was hit) `SKIPPED (session limit — exceeded pause retries)`. `SELF-FIX` and `BELOW-THRESHOLD` were decided entirely by step 2's sweep — no sub-agent ran; for `BELOW-THRESHOLD` the ledger is deliberately **not** advanced. Build and deliver the full digest in every case; session-limit skips are flagged, not silently dropped.

Read the digest-state ledger first (week-over-week, not a re-list): `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo ferraroroberto/fleet-config --kind digest`. Parse the `<!-- audit-fleet-digest -->` block:

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

Code-bucket per-repo lines are bare `<name>: <count>`; `design-drift:`/`cert-drift:`-prefixed keys below `design-drift-last-run-at:` are the **design bucket's own accounting**, kept separate so a design-drift issue never inflates a repo's code-finding count or `+N since last week` delta (fleet-config#180). Treat a ledger with no `design-drift-last-run-at:` line (pre-#180) as an empty design baseline — note as initial snapshot, not a delta.

**Count the design-drift bucket (read-only).** `design-drift`/`cert-drift` issues are filed by `/design-sweep`/`/design-sync`, never by an audit sub-agent — count open issues directly via the Issues API rather than `gh search issues --owner` (Search-API-backed, observed reporting issues open for 5+ weeks after they closed — fleet-config#623):

```bash
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gh_issue_fetch.py fetch --label design-drift
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/gh_issue_fetch.py fetch --label cert-drift
```

Group each result by `repository.name` into per-repo open counts — the one place this accounting is tallied, never conflated with code buckets. `/audit-fleet` is the unified reporter; `/design-sweep` is the doer. If either fetch fails outright, note `design-drift: count skipped (<reason>)`, carry the last snapshot forward unchanged, never fail the run over it. If stderr reports per-repo `ERROR` lines but still returns a partial result, use the partial count and note which repos were skipped.

Compose the digest as markdown (single long lines per paragraph, no hard wraps) — stdout verbatim, attached to email as `.md`, rendered to HTML for the email body in step 6:

- **Header:** date, counts — `E repos enumerated: N audited, M issues filed, K unchanged, L self-fix, B below-threshold, J skipped, W stale-lock, X errors`, plus `S security fixes` when any sub-agent reported non-`NONE` `Security:`, and `D design-drift / C cert-drift open`. Per-bucket counts **must sum to `E`** (`accounting.enumerated`); if `accounting.balanced` is `false`, append `— ⚠️ <N> repos in no bucket`.
- **Broken-ledger section** *(only when non-empty)*: repos `broken_ledgers()` returns — one line each naming reason and what couldn't be read (`grocery-shopping-automation: baseline 99100ac resolves to nothing — audited whole-repo, ledger re-anchored`; `local-llm-hub: ledger #31 had no readable audit-ledger block — audited whole-repo, ledger normalized`) — a recurring weekly cost misreported as organic change if omitted (fleet-config#566, #567).
- **Per audited repo:** result line + issues filed this run (bucket → URL) + delta vs last week (`+2 since last week`). CLEAN/SKIPPED-BY-LEDGER get a one-liner.
- **Security section** *(only when any sub-agent reported non-`NONE` `Security:`)*: one line per repo, counts + disposition only, NEVER detail — `<repo>: 1 gap self-healed, PR merged (private alert sent)` or `<repo>: 1 gap ESCALATED — branch left for manual /issue-finish`.
- **Self-fix section** *(only when non-empty)*: repos classified `SELF-FIX` — one line each naming closed issue numbers (`website: closed #71, #64 — ledger advanced, no organic change`).
- **Below-threshold section** *(only when non-empty)*: repos classified `SKIP_BELOW_THRESHOLD` — one line each with accumulated vs threshold weighted-LOC (`accounting-quarterly: 591/1000 weighted lines since 2026-07-04 — accumulating, not yet audited`). NOT counted toward standing backlog.
- **Skipped section:** repos skipped for dirty/off-branch/non-ff/index-lock-in-flight.
- **Stale-lock section** *(only when non-empty)*: repos with a stranded `.git/index.lock`, one line each with verdict and age (`email-archiver: 0-byte lock 15.2d old, no git process running — repo frozen against every write; needs a human to confirm the holder is dead and remove it`). **Never** resolved by the run itself — repeats verbatim until acted on (fleet-config#667).
- **Session-limit section** *(only when non-empty)*: repos left unaudited because the step-3 3-pause safety net was hit.
- **New findings this week:** built strictly from the `new` counts each sub-agent reported (step 3's `Filed:` breakdown) — only bucket/URL pairs where `new > 0`. List at the top.
- **Standing backlog:** single fleet-wide count — sum of every `carried` + `stale` count across every audited repo, never an item list, e.g. `14 standing findings across 5 repos, unchanged or not re-verified this run — see each repo's audit issue for detail.`
- **New fleet assets this week:** promotion candidates added to the practices ledger this run, with `PRACTICES_LEDGER_URL`. If none: `No new fleet assets catalogued this week.`
- **Design & cert drift:** design-drift bucket reported alongside the six code buckets but never mixed into their counts. One line with fleet-wide open total and week-over-week delta from the `design-drift-last-run-at:` baseline (`6 open design-drift across 3 apps (+2 since last week); 1 cert-drift`), then — only for repos whose count **changed** since baseline — a per-repo delta line (`home-automation: 4 (+2)`). Steady repos fold into the total, not enumerated.

Then upsert the digest-state ledger issue with today's date, current per-repo open-audit-issue counts, **plus** the design/cert bucket counts under `design-drift-last-run-at:` (stamp today's date there too). Keep the two account groups distinct — bare `<name>: <count>` code lines vs `design-drift:`/`cert-drift:`-prefixed lines — never fold one into the other. Helper handles create-vs-edit, collapses strays, stamps the marker (keep `<!-- audit-fleet-digest -->` intact):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo ferraroroberto/fleet-config --kind digest --label audit-meta \
  --title "audit-fleet digest state" --body-file <tmpfile>
```

Capture the printed URL as `DIGEST_ISSUE_URL`, used for the comment in step 6 — never a hardcoded issue number.

### 6. Deliver the digest

Two channels: stdout (reliable, captured in app-launcher's job history) and the GitHub comment (durable record the Telegram ping links to).

- **stdout:** print the full markdown digest, always.
- **GitHub comment:** post the digest on `audit-fleet digest state` in `ferraroroberto/fleet-config` (`DIGEST_ISSUE_URL` from step 5, never a hardcoded id):

  ```bash
  COMMENT_URL=$(gh issue comment "$DIGEST_ISSUE_URL" --repo ferraroroberto/fleet-config --body "$DIGEST_MARKDOWN")
  ```

  If `gh` fails or the URL is empty, note `comment: skipped (<reason>)` and carry on — **never fail the run over the comment.**

- **Delivery assertion — run here, before the ping.** The only place the run may declare itself failed on *content* rather than a crash (fleet-config#506). Check all three; treat any one you **cannot establish** as failed, never as passing:

  1. **≥1 repo evaluated.** Step 2's sweep placed ≥1 repo in some bucket (`to_audit`+`unchanged`+`self_fix`+`below_threshold`+`skipped`+`stale_lock`+`errors` > 0). Empty sweep = the fleet walk itself failed.
  1b. **Buckets account for every repo walked.** `accounting.balanced` is `true`. `false`/missing = repos the walk found landed in no bucket at all (fleet-config#567 shape) — fail the run, name the unaccounted count.
  2. **A digest was composed and printed.** Step 5 produced markdown and this step wrote it to stdout verbatim.
  3. **The digest comment resolved either way.** `COMMENT_URL` holds a real URL, or the comment was recorded as `comment: skipped (<reason>)` with a stated reason — a comment step that silently never ran fails this assertion.

  All three hold → carry on to the Telegram ping, report normally in step 7. Any one fails → do **not** report success: print verbatim in the step-7 final report,

  ```
  SCHEDULED-RUN-FAILED — <which assertion failed, one line>
  ```

  still send the Telegram ping (a failed run must be *more* visible), state the failure plainly. `skills/_lib/claude_progress.py` detects that literal marker and exits `123` instead of `0` (fleet-config#519). Never print the marker on a run that did deliver — a sweep where every repo came back `unchanged` and `to_audit` was empty is a **successful** run (it still produces a full digest per step 2, which is why assertion 1 counts `unchanged` too).

- **Telegram ping:** call `notify_complete.py --kind audit` with the captured comment URL and a one-line summary (deterministic — skill hands the hook exact structured args):

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
    --kind audit \
    --comment-url "$COMMENT_URL" \
    --summary "<N> audited, <M> issues filed, <K> unchanged"
  ```

  If `COMMENT_URL` is empty, omit `--comment-url` so the ping still goes out link-less. Silent no-op if no `telegram_chat` configured; always exits 0, never blocks/delays the finish.

### 7. Final report

One concise block: the plan line from step 2, per-repo results, where the digest went (stdout always; comment URL or skipped reason; Telegram pinged or no-op), the digest-state issue URL. If step 6's delivery assertion failed, its `SCHEDULED-RUN-FAILED — <reason>` line goes in this block verbatim and nothing may describe the run as complete. Stop.

## Hard rules

- **The ledger gate is one shared Python implementation, not prose.** Step 2's `fleet_audit_scan.py` and `/codebase-audit`'s own step 2 both call `audit_issue.py`'s `evaluate_repo` — exactly one implementation of the skip/audit/self-fix decision. Unit-tested independent of `gh`/`git` in `tests/test_audit_issue.py`.
- **Read-only on source — except a sub-agent's step-8b security self-heal** (redacted issue + auto-fix + auto-merge, gated: claim, mandatory regression test, generic artifacts, green-gate-only merge, escalate on failure). Every other write is an audit issue, the per-repo ledger, the digest-state issue, the digest comment, or the cross-fleet practices ledger in `project-scaffolding` (the one issue-write target outside `fleet-config` — still an issue, never source).
- **Never disturb in-progress work.** Dirty or off-default-branch repos are skipped and reported, never stashed or force-switched.
- **One sub-agent per repo, `hard` tier (Opus on Claude Code today), through a ≤3 sliding window.** Refill as each returns. No worktrees (audits don't collide). Don't read repo source in the orchestrator.
- **Block on `TaskOutput` for every in-flight sub-agent, same turn, always.** `run_in_background: true` + ending the turn is never valid here (`fleet-config#506`); step 3's loop never returns control until the to-audit list is fully drained. `claude_progress.py` hands the CLI `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` so an in-flight task is waited on rather than killed (`fleet-config#519`) — not a licence to end the turn.
- **A run that delivered nothing must exit non-zero.** Step 6's delivery assertion runs before the Telegram ping on every run; on failure it prints the literal `SCHEDULED-RUN-FAILED` marker, mapped by `claude_progress.py` to exit `123`.
- **And the assertion can't save a run that never reaches it.** An orchestrator cut off mid-flight never prints the marker and the job records `success`/exit 0 with zero repos audited. The launcher therefore also passes `--delivery-check .claude/skills/audit-fleet/delivery_check.py`, an outer post-condition `claude_progress.py` runs *after* the child exits: resolves this skill's digest ledger issue the same way step 5 does (never a hardcoded id), fails the job (exit `121`) unless a digest comment landed within the last 12 hours (fleet-config#560). If step 6's shape changes, that check changes with it.
- **Degrade, don't block.** Built for unattended `claude -p`. A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. Never wait on an interactive prompt.
- **Self-pace against the live session %, don't die-and-hope.** Check `rate_gate.py` before each dispatch/refill (step 3); on `PAUSE`, wait via `Monitor`'s until-loop pattern and resume — capped at 3 pause cycles per run. No OS-level scheduling, no `resume` argument. `docs/rate-gate.md`.
- **No AI attribution; no hard-wrapped digest paragraphs.** (Per global CLAUDE.md.)

## Notes

- **Why scatter-gather:** each repo's file reading is isolated in its own sub-agent context, so the orchestrator never holds the whole fleet's source at once.
- **Why a ledger gate:** most weeks most repos are unchanged; the gate turns an unchanged repo into one `gh` + one `git` call. Commit SHA is the cache key; rubric hash (sha256 of the repo's **own** project CLAUDE.md) busts one repo's cache when its criteria change. Global `~/.claude/CLAUDE.md` is deliberately excluded — a fleet-wide re-grade is an explicit act (clear the ledgers' `last-audited-sha`), never a side effect.
- **Self-fix-only churn is treated as unchanged:** `evaluate_repo` detects it via merged-PR `closingIssuesReferences` against managed bucket issues and advances the ledger itself — otherwise a repo fixed only via `/cleanup-fleet` would be re-flagged weekly (fleet-config#251).
- **A mixed PR fails closed to AUDIT, on purpose** (fleet-config#251): a PR closing a hand-filed issue alongside an audit-managed one is not recognized as self-fix, so the repo re-audits. Known limitation, not a bug.
- **Not every non-self-fix commit re-audits immediately** (fleet-config#315): `evaluate_repo` weighs unexplained commits' `additions + deletions` by conventional branch-type (`feat`/`refactor` full weight, `fix`/`chore` partial, `docs`/`test` none — `audit_issue.py`'s `PR_TYPE_WEIGHTS`), audits once the total crosses `DEFAULT_SIGNIFICANCE_THRESHOLD` (1000). Below that, `SKIP_BELOW_THRESHOLD` leaves the ledger sha untouched (#256).
- **Per-category trend data lives in the per-repo ledger** (`<!-- audit-snapshot -->` comments, `/codebase-audit` step 9); this fleet digest stays aggregate by design.
- **The weekly job** lives in app-launcher (`config/jobs.json`), calls this repo's `.claude/skills/audit-fleet/run-weekly.bat`.
- **Why a proactive gate, not a dead-man's switch** (#222 → redesigned #261): session % used to exist only at TUI render time; `statusline-command.ps1` now caches it to `~/.claude/hooks/state/rate-limits.json` on every render (#259), so the skill reads it directly and pause-waits in place instead of dying and hoping a relaunch resumes. Contract: `docs/rate-gate.md`.
