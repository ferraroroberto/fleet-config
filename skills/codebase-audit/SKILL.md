---
name: codebase-audit
description: Audit a codebase's resting state against its CLAUDE.md and senior-dev standards — duplication, stale/dead code, convention drift, bugs, AI-slop bloat, doc problems — bundled into at most 7 self-assigned GitHub issues, with security gaps self-healed via a redacted issue + auto-fix. E.g. "/codebase-audit", "/codebase-audit app/", "audit the codebase", "find duplication and stale code", "check the docs against its CLAUDE.md", "review the codebase for slop", "find security gaps".
---

# codebase-audit

**Goal:** Read the codebase (or a scoped subtree) as a senior perfectionist
developer would and surface the resting-state quality problems the diff-scoped
reviewers (`/code-review`, `/simplify`, `/security-review`, ultrareview) never
see — bundled into **at most 7 GitHub issues per run** for `/issue-start`.

**Issues, not code edits — with exactly one exception.** For the seven finding
buckets below, never edit files, commit, push, or restart anything — filing
issues is the only side effect. The **sole** exception is a **security** finding:
self-healed in place (redacted issue + auto-fix), because a security gap sitting
in a public issue body until someone gets to it is itself a disclosure. That path
is step 8b and its Hard Rule; it is scoped to security only and is never license
to edit code for any other bucket.

**The seven finding buckets.** Every non-security finding belongs to exactly one
of (security is not a checklist bucket — see step 8b):

1. **Duplication** — repeated logic, parallel implementations, copy-pasted
   blocks, two helpers doing the same thing under different names.
2. **Stale / dead code** — unused exports, orphaned files, references to
   things that were removed, outdated comments, half-finished implementations,
   `// removed` placeholders, dead feature flags.
3. **CLAUDE.md drift** — concrete violations of conventions stated in the
   global `~/.claude/CLAUDE.md` or the project's own `CLAUDE.md`. Cite the
   rule that was broken.
4. **Maintainability** — modularity, naming, structure, "slop": over-
   abstraction beyond what the task required, dead error handling for
   scenarios that can't happen, planning-doc clutter, comments that explain
   *what* instead of *why*, long files that should be split, identifiers that
   lie about what they hold.
5. **Bugs** — actual correctness issues spotted while reading. Off-by-one,
   wrong default, race condition, missing await, wrong type, broken
   invariant. Only file what you'd bet money on — speculation goes nowhere.
6. **Documentation** — the content, structure, and coverage of `README.md`
   and `docs/`, judged as documentation. Three sub-checks: (a) **CLAUDE.md
   compliance** — the docs break a doc-discipline rule in the global or
   project `CLAUDE.md` (e.g. a dated `docs/YYYY-MM-DD-*.md` retrospective the
   doc-lifecycle rules forbid, hard-wrapped paragraphs in rendered markdown,
   `docs/` content that's a changelog rather than durable reference); (b)
   **stale / duplicated sections** — a section documents a removed feature,
   wrong command, or outdated config/port, or the same content is duplicated
   across `README` and `docs/` (or within one file) and has begun to diverge;
   (c) **missing crucial features** — a shipped, user-facing feature / command
   / config knob with no documentation a new reader could find. Cite the rule
   (sub-check a) or the feature + where it should be documented (sub-check c).

   **Boundary against buckets 1–3 (read this — it's the part that goes wrong):**
   anything whose *subject* is `README.md` / `docs/` prose goes here, in bucket
   6 — including a doc that violates a CLAUDE.md doc rule, a duplicated doc
   section, or a stale doc section. `duplication`, `stale`, and
   `claude-md-drift` stay about **code/config/workflow**. Don't double-file a
   doc problem into both a code bucket and this one.
7. **Slop** — AI-generated *bloat*: code whose sheer volume doesn't earn its
   keep. Redundant scaffolding, a 40-line implementation of a 5-line idea,
   unused generality (a config knob / parameter / abstraction layer nothing
   exercises), belt-and-suspenders defensive handling for inputs that can't
   occur, verbose boilerplate a stdlib one-liner replaces. **The bucket-4
   boundary (read this — the two blur):** bucket 4 asks *"is this code well
   *structured*?"* (naming, modularity, a god-module); bucket 7 asks *"did this
   much code need to exist at all?"* A finding that would shrink the line count
   with no loss of behavior is slop (7); a finding that would reorganize the
   same lines is maintainability (4). When both apply, file it once, in
   whichever is the dominant fix. AI-assisted work on this fleet steadily
   accretes lines — be actively critical of volume, not just structure.

One issue per non-empty bucket. **Hard cap: 7 issues per run** (one per finding
bucket). Empty buckets are simply skipped. Findings inside an issue go on a
checklist with `file:line` citations and a one-line fix shape. A **security**
finding is *not* one of these seven — it never goes on a public checklist; it
takes the self-heal path in step 8b, which may file one extra *redacted* issue
that carries no finding detail.

## Arguments

- No argument → audit the whole repository from its root.
- One argument → treat as a path (relative to repo root or absolute). Scope
  the audit to that subtree only. The rubric (CLAUDE.md) is still read
  whole — only the *files inspected* are scoped.
- More than one argument → tell the user only one path is accepted and stop.

## Steps

Run in order. Stop on any hard failure.

### 1. Pre-flight

In parallel:
- `git rev-parse --is-inside-work-tree` — must print `true`, else stop:
  "Not inside a git repository."
- `git rev-parse --show-toplevel` — capture the repo root.
- `gh repo view --json nameWithOwner -q .nameWithOwner` — confirm a GitHub
  remote is reachable, capture `OWNER/REPO`. If this fails, stop:
  "No GitHub remote — this skill files issues, can't run without one."

If a scope path was passed, resolve it against the repo root and verify it
exists. If not, stop with a one-line error.

### 2. Ledger gate — skip if nothing changed

**Whole-repo audits only.** If a scope path was passed, skip this entire step
*and* step 9 — the ledger tracks whole-repo audits, so a scoped run always
executes and never reads or writes the ledger.

Before reading a single source file, check whether the repo changed since the
last audit — **one deterministic Python call, not LLM judgment**
(`skills/_lib/audit_issue.py`'s `evaluate_repo`, the single implementation
this skill and `/audit-fleet` share):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py gate --repo <OWNER/REPO> --repo-path <repo-root-from-step-1>
```

It prints `{"decision": "SKIP"|"AUDIT"|"SKIP_SELF_FIX"|"SKIP_BELOW_THRESHOLD", "reason": ..., ...}`.
The ledger lives in **one issue per repo** — title `codebase-audit ledger`,
label `audit-meta`, `--assignee @me`, never closed, with a hidden identity
marker and a machine-readable `<!-- audit-ledger -->` block (`last-audited-sha`,
`last-audited-at`, `rubric-sha` — sha256 of the project CLAUDE.md **alone**; the
global `~/.claude/CLAUDE.md` is deliberately excluded so an edit to that shared
file never busts every repo's cache at once). `evaluate_repo` computes and
compares all of this internally.

Branch on the decision:

- **`SKIP`** — nothing changed. Stop immediately:
  `No changes since last audit (<short-sha> on <date>) — skipped.` Read no
  files, file nothing.
- **`SKIP_SELF_FIX`** — every commit since the last audit closes only this
  repo's own audit-managed findings (merged-PR `closingIssuesReferences`,
  entirely in Python — `evaluate_repo`/`audit_only_churn`). The gate call has
  **already advanced the ledger** and posted the `<!-- audit-self-fix -->`
  comment. Stop immediately:
  `Skipped — commits since last audit only close this repo's own audit
  findings (#N, #M); ledger advanced, no organic change.` This stops a repo
  from being endlessly re-flagged for fixing its own findings.
- **`SKIP_BELOW_THRESHOLD`** — real organic commits exist, but their
  weighted-LOC significance (feature/refactor commits count fully,
  docs/test count nothing, fix/chore count partially — `audit_issue.py`'s
  `PR_TYPE_WEIGHTS`) hasn't crossed `DEFAULT_SIGNIFICANCE_THRESHOLD` (1000)
  yet. The ledger is **not** advanced, so this keeps accumulating across
  runs. Stop immediately: `Skipped — organic change since last audit is
  below the significance threshold (<significance>/<threshold> weighted
  lines); accumulating, not yet audited.` Read no files, file nothing.
- **`AUDIT`** — continue to step 3.

### 3. Load the rubric

Read both CLAUDE.md files in full:
- Global: `~/.claude/CLAUDE.md` (or `$env:USERPROFILE/.claude/CLAUDE.md` on
  Windows). If absent, note it and continue without a global rubric.
- Project: `<repo-root>/CLAUDE.md`. If absent, note it and continue — the
  audit still runs, just without project-specific drift checks.

Extract the **specific, checkable rules** from each (e.g. "no `Co-Authored-By:
Claude` trailer", "tests must hit a real database", "use `.venv` not `venv`",
"forward slashes in `settings.json` commands"). These are the inputs to
bucket 3.

### 4. Inventory the files to read

`git ls-files` (or `git ls-files <scope-path>`) to get the tracked-file list.
This automatically respects `.gitignore`.

Filter to source files worth reading — typically: `.py`, `.ts`, `.tsx`,
`.js`, `.jsx`, `.go`, `.rs`, `.ps1`, `.sh`, `.md` (only top-level docs like
`README.md`, `CLAUDE.md`, files in `docs/`), `.toml`, `.json` configs of
interest. Skip generated files, lockfiles, binary assets, `dist/`, `build/`,
test fixtures.

If the file list is large (>~150 files), prioritize:
- Entry points (`main.py`, `cli.py`, `app.py`, `index.ts`, `server.*`)
- Files modified most recently (`git log --pretty=format: --name-only --since="3 months ago" | sort -u`)
- Top-level modules of each package
- Anything `CLAUDE.md` calls out by name

State the prioritization in the final report so the user knows what was
inspected.

### 5. Read systematically and take notes by bucket

Read each file in the inventory, keeping a working list keyed by bucket. For
every finding capture:
- **Bucket** (one of the 7)
- **File:line** (or file range)
- **What's wrong** (one sentence, concrete)
- **Fix shape** (one sentence — what the patch would do, not the patch itself)
- For bucket 3 (CLAUDE.md drift): **which rule** was broken (quote it)
- For bucket 6 (documentation): **which sub-check** (CLAUDE.md compliance /
  stale-or-duplicated / missing feature) and the rule or feature it concerns

When you see the same pattern twice in two files, that's bucket 1
(duplication), not two separate bucket-4 findings.

**Security findings are captured on a *separate* private list — never in the
per-bucket notes and never in a public checklist.** A security gap (an injection
sink, a hardcoded secret, a path-traversal, a missing-authz check, an unsafe
deserialization, credentials in a committed file, etc.) is held aside for the
self-heal path (step 8b). Record only what the fix agent needs — file:line and
the concrete gap; it never leaves this run as public text. Hold a security
finding to the bug bar (would you bet money it's exploitable) — a false one
wastes an auto-fix cycle and, worse, an unnecessary public fix commit.

**Read `README.md` and `docs/` twice — once for context, once for bucket 6.**
Pass one mines them for code-side staleness leads (bucket 2). Pass two judges
them *as documentation* against bucket 6's three sub-checks: walk the shipped
user-facing surface seen in the code (commands, flags, ports, config keys,
entry points) and confirm the docs cover it, don't contradict it, and don't
repeat themselves. A feature with no mention in `README`/`docs` is the
canonical "missing crucial features" finding.

**Apply the materiality bar (see Hard rules) to every finding as you take
it.** When in doubt, leave it out — across all seven buckets. Bucket 5's bar is
"I'd bet money on this"; buckets 1–4 and 7: "a senior developer would agree this
is worth a future developer's time to fix." If you can imagine the user reading
the finding and going "...so?", drop it.

**Promotion candidates (a second lens on the same read — not a bucket).** Also
jot anything *worth preserving fleet-wide* — the inverse of a finding: (a) a
**fleet-worthy asset**, a hard-won reusable solution another repo would want to
copy, noting *where it lives*; (b) a **generalizable-convention candidate** that
ought to propagate up to `project-scaffolding` per the global CLAUDE.md rule.
Same materiality bar, even higher. These are **never issues and never a write to
another repo** — surfaced in the final report only (step 10), where
`/audit-fleet` collects them into the cross-fleet practices ledger. Most runs
have zero; that is fine.

### 6. Dedupe against existing open issues

```
gh issue list --state open --limit 200 --json number,title,body
```

This catches only **cross-issue** duplicates — a finding already tracked by a
*hand-filed* issue or a *different* bucket. Do **not** drop a finding just
because this bucket's own managed audit issue already lists it — that issue is
the one step 8 merges into. If a finding's substance is covered by an issue
that is **not** this bucket's managed issue (matched on title keywords + body
content, not strict string match), **drop it** and record it as
"skipped: dupe of #N" for the summary.

### 7. Ensure labels exist

The seven bucket labels are: `duplication`, `stale`, `claude-md-drift`,
`maintainability`, `slop`, `bug`, `documentation` — plus `security` for the
redacted self-heal issue (step 8b). `bug` and `documentation` are GitHub
defaults that typically already exist. For each bucket that has surviving
findings (and `security` if step 8b fires), ensure its label exists:

```
gh label list --json name -q '.[].name'
```

For each missing label, create it (idempotent — only call for missing ones):

```
gh label create duplication       --color 'fbca04' --description 'Repeated logic across files'           || true
gh label create stale             --color 'cfd3d7' --description 'Dead/unused code or stale references'  || true
gh label create claude-md-drift   --color 'd876e3' --description 'Violates a CLAUDE.md convention'       || true
gh label create maintainability   --color 'a2eeef' --description 'Modularity / clarity / structure'      || true
gh label create slop              --color 'e99695' --description 'AI-generated bloat — volume that does not earn its keep' || true
gh label create documentation     --color '0075ca' --description 'README / docs quality, coverage, drift' || true
gh label create security          --color 'b60205' --description 'Self-healed security gap (detail redacted)' || true
```

### 8. Upsert one issue per non-empty bucket

There is **exactly one** managed issue per (repo, bucket), reused across runs.
You never `gh issue create` directly — the helper owns identity so a re-run can
never spawn a duplicate. For each non-empty bucket (max 7 iterations —
`security` is not iterated here; it takes step 8b):

**1. Fetch the existing issue** for this bucket:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind <bucket>
```

It prints `{"number": N|null, "body": "...", "duplicates": [...]}`.

**2. Build the merged body.** If `number` is `null`, write a fresh body from the
template below. If it exists, **merge** this run's findings into the returned
body — the issue is a *living backlog*, so:

- **Preserve every already-ticked checkbox** (`- [x]`) verbatim — the user
  fixed those; never reset them.
- **Match by file path first.** A finding for a file already listed is the same
  finding even if the line number moved — update the line to this run's value
  (re-verified while reading) and keep the existing checkbox state.
- **Tag each item's re-verification status inline — don't bury it in the run
  log.** A stale checklist item must not read identically to a freshly
  discovered one:
  - **New this run** (no item for that file + problem existed before): append
    as-is, no suffix.
  - **Re-matched this run** (found again, same file + problem): silently bump
    its hidden `last-seen` date, no visible tag — it reads as a normal,
    currently-live finding.
  - **Not re-surfaced this run:** keep the line (never delete), append
    *inline on the same line* (a bare HTML comment on its own line risks
    GitHub treating it as breaking the list):
    `_(carried — not re-verified since <date>)_<!-- last-seen: <date> -->`.
  - **Escalation, free of new state:** fetch the ledger's *previous*
    `last-audited-at` (`audit_issue.py get --repo <OWNER/REPO> --kind ledger`,
    read before step 9 overwrites it this run). For an item not re-surfaced
    this run, compare its existing `last-seen` against that previous date: if
    equal, this is its first miss (use the plain tag above); if earlier, it
    already missed last run too — escalate to
    `_(carried — not re-verified since <date>; flag for pruning)_<!-- last-seen: <date> -->`.
    Two audits on the same calendar day degrade to "no escalation" — a safe
    default, not a bug. Pruning stays a human decision (never auto-close/tick);
    this only makes staleness visible on the item itself.
- **Never tick or close anything yourself**, and never add `Closes #` — multiple
  PRs may chip at one audit issue without closing it; closing is the user's call
  via `/issue-finish` once all boxes are checked.
- Append a dated bullet to the `## Audit run log` section:
  `<YYYY-MM-DD> @ <short-sha>: +A new, B carried, C not re-surfaced`.

**3. Upsert** (creates if absent, edits if present, collapses any strays):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo <OWNER/REPO> --kind <bucket> --label <bucket-label> \
  --title "audit: <bucket> findings" --body-file <tmpfile>
```

The helper stamps the `<!-- audit-managed: kind=<bucket> -->` marker, applies
the label, and prints the canonical issue URL. **Titles are stable** — no
`(N items)` count (it lives in the body), so the title never changes run to run.

**Body shape** for a fresh issue (no hard wraps in paragraphs — the global
CLAUDE.md "Markdown that will be rendered" rule applies; the helper prepends the
marker, don't write it yourself):

```markdown
Surfaced by `/codebase-audit`, kept up to date across runs. Scope: <whole repo | path>.

## Findings

- [ ] **<file>:<line>** — <what's wrong>. Fix: <fix shape>.
- [ ] **<file>:<line>** — <what's wrong>. Fix: <fix shape>.
- ...

## Context

<One short paragraph: the common thread across these findings, why they
matter together, anything the next `/issue-start` should know.>

<For bucket 3 (claude-md-drift), additionally list the rules that were
broken, quoting the CLAUDE.md passage.>

## Audit run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Title style — stable, no count: `audit: <bucket> findings`. Examples:
`audit: duplication findings`, `audit: claude-md-drift findings`,
`audit: maintainability findings`, `audit: slop findings`,
`audit: documentation findings`.

Use a **repo-scoped, unique** temp file so multi-line markdown isn't mangled
by shell escaping *and* concurrent audits never clobber each other's scratch:
`E:/tmp/audit-<owner>-<repo>-<short-sha>-<bucket>.md` (slash in `OWNER/REPO` →
hyphen; `<short-sha>` = `git rev-parse --short HEAD`). **Never** a fixed
`E:/tmp/audit-<bucket>.md` — `/audit-fleet`'s parallel sub-agents share
`E:/tmp`, and a fixed name is a race.

### 8b. Security findings — redacted issue + immediate self-heal

**Only runs when step 5 held aside one or more security findings.** No security
findings → skip this entire step. This is the one place the skill writes code;
everything about it is scoped to security and gated on the safety rules below.

**One repo → one branch → one PR → one redacted issue, no matter how many gaps** —
tracked by the single `audit: security findings` issue, never N public security
commits.

Do this in order; **run it inline (synchronously) in your own agent context — do
NOT spawn a nested background sub-agent for the fix.** A nested background agent
does not get an auto-resume wake-up (global CLAUDE.md, "A sub-agent does not
self-resume"), so under `/audit-fleet` it would silently stall.

1. **Claim the repo in forced worktree mode** (the collision primitive — same one
   `/issue-start` uses), so a concurrent `/cleanup-fleet` / human session on this
   repo can't clobber you and vice-versa. `--force-worktree` skips the primary
   claim entirely: this is unattended fleet-wide dispatch, and a *running* app or
   a live junction is not a claim holder, so an ordinary `acquire` would hand you
   `MODE=primary` and have you edit files a live process is serving
   (fleet-config#515). Then `cd` into the printed `WORKTREE=` path — everything
   after this step happens there, never in the primary checkout:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire <repo-root> --issue <security-issue-or-0> --force-worktree
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py setup-worktree <repo-root> <security-issue-or-0> <branch>
   ```
   A live-e2e guard refusal is a hard STOP — report it and stop; setting
   `E2E_LIVE=1` or any equivalent override is forbidden.

2. **File the redacted issue** via the helper — **no vulnerability detail, ever**:
   not the class, not the file, not the line, not a description. Title exactly
   `audit: security findings`, label `security`. Body is only:
   `A security gap was detected by /codebase-audit and is being self-healed in
   this run. Detail is deliberately omitted from this public issue; see the
   private security alert for the fix PR.` — nothing more.
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind security --label security \
     --title "audit: security findings" --body-file <tmpfile>
   ```

3. **Fix + prove it, on one branch.** Run the `/issue-yolo <N>` flow against that
   issue (branch off fresh `main`, patch every held-aside gap), with **two
   non-negotiable additions**:
   - **A regression test per gap is mandatory.** The fix ships with a test that
     exercises the specific gap — fails before the patch, passes after. This
     test is the coverage that makes unattended auto-merge safe: it catches a
     wrong fix, so on a repo with a thin suite the fix is never resting on a
     bare byte-compile. (Global CLAUDE.md: "Reproduce before fixing" / empirical
     proof.)
   - **Every artifact stays generic.** Commit message, PR title, PR body, the
     test name and any comment — none may name the vulnerability class (no "SQL
     injection", "XSS", "hardcoded credential", "path traversal", …). Use
     `fix: harden input handling in <module>` shapes. The public diff already
     reveals the fix to anyone who reads it — unavoidable on a public repo — so
     the mitigation is a *short exposure window + a private review*, not secret
     text; don't add a neon label on top.
   - Run the repo's **own verification gate** (per its CLAUDE.md) — the new
     regression test included — as the hard pass/fail.

4. **Auto-merge on green** (green = gate passes *including* the new test), exactly
   like `/cleanup-fleet`'s easy tier: PR, wait for CI per `/issue-yolo`'s rules,
   `merge --delete-branch`, land on `main`, **tear the worktree down and release
   the claim** (`worktree_claim.py remove-worktree <worktree-path>` then
   `release <repo>` — verify `CLAIM=free` and that `git worktree list` shows the
   primary only; never `rm -rf` a worktree, its `.venv` junction would take the
   primary's real venv with it). Tray restart
   follows `/issue-yolo`'s safety rule: a detach-compliant tray restarts; an
   unsafe/silent tray is **not** restarted unattended — note "tray not restarted,
   still on old build" in the alert instead.

5. **Close the redacted issue**, referencing the merged PR by number only (still
   no vuln detail in the close comment).

6. **Fire the private security alert** — the review channel the public issue
   deliberately lacks, so you can inspect the actual fix and revert if it's
   wrong. Routes to the attention channel, not the log:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
     --kind security --issue <N> --pr <PR> --pr-url <PR_URL> --summary "auto-merged, review the diff"
   ```

**Escalate instead of merging blind when the safety net is absent.** If the
repo has **no test surface at all** to add a regression test to, or the
verification gate / added test does **not** pass, or `/issue-yolo`'s validation
fails for any reason: **do not merge.** Leave the branch in place, leave the
redacted issue **open**, and fire the same `--kind security` alert with
`--summary "escalated - needs manual /issue-finish"` (drop `--pr`/`--pr-url` if
no PR was opened). Never retry a failed security fix by guessing, and never
force-merge one. Half-healing a security gap unreviewed is worse than leaving it
for the human the alert just pinged.

### 9. Update the ledger

**Whole-repo audits only** — skip if a scope path was passed.

Upsert the per-repo ledger issue so the next run can short-circuit at step 2:

- One command does the whole write — **never hand-author the ledger block, and
  never record the working checkout's `HEAD`**:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py ledger-write \
    --repo <OWNER/REPO> --repo-path <REPO_PATH>
  ```

  It composes the `<!-- audit-ledger -->` block itself — sha, today's date, and
  the `rubric-sha` (sha256 of the project CLAUDE.md alone) — then creates, edits,
  or collapses strays and ensures the `audit-meta` label, printing the ledger
  issue URL. Capture that URL; the snapshot comment below posts to it.

  Two things you must not do by hand, both of which have already cost real
  audits:

  - **Don't write the marker.** Hand-authoring it drifts — an agent naturally
    writes an *open* comment block (`<!-- audit-ledger` … `-->`) the step-2 gate
    cannot read, so the repo bought a full Opus whole-repo audit every week
    while reporting as legitimately changed (fleet-config#566). The parser now
    reads both forms and the helper normalizes back to the closed one, but the
    tool owns the delimiter.
  - **Don't record `HEAD`.** The helper records the repo's **default-branch**
    commit, re-confirmed reachable from that branch. An audit off a feature
    branch (or in a worktree) recording the checkout tip writes a commit the
    fleet's squash-merge + delete-branch pipeline is *guaranteed* to destroy;
    `rev-list <sha>..HEAD` then fails and the repo drops out of every later
    sweep (fleet-config#567).

  If the helper exits non-zero it could not verify a commit or read what it was
  asked to write — **leave the ledger unchanged**, and say so in the run report.
  A stale-but-valid baseline costs one wider audit next week; a poisoned one
  costs every audit.

- This runs on **every** non-skipped path — including a clean pass that filed
  zero issues — so an unchanged repo is correctly skipped next time.

Then **post one per-category snapshot comment** on the ledger issue —
append-only telemetry showing the findings *trajectory* per repo. **Counts
only** (never finding text — the bucket issues are the single source of truth
for *what*; this is *how many*). Living in a comment keeps it off the step-2
gate's hot path, which only reads the ledger *body*:

- Use the per-bucket **findings-surfaced-this-run** counts — the exact same
  numbers as the step-10 summary table's `findings` column. No recomputation.
- Build a small **standalone** markdown table (header + separator + one data
  row, so it renders on its own), prefixed with the hidden `<!-- audit-snapshot -->`
  marker so a later LLM/tool can filter snapshot comments from other ledger
  comments. Shape (`<sha>` is `git rev-parse --short HEAD`; `total` is the sum
  of the seven finding buckets; `sec` is the **count** of security gaps
  self-healed this run — a bare count, never any detail, and deliberately *not*
  folded into `total` since it's a fix count, not a standing-backlog count):

  ```markdown
  <!-- audit-snapshot -->
  | run | sha | dup | stale | drift | maint | slop | bug | doc | total | sec |
  |-----|-----|-----|-------|-------|-------|------|-----|-----|-------|-----|
  | <YYYY-MM-DD> | <sha> | 3 | 0 | 2 | 5 | 2 | 0 | 4 | 16 | 1 |
  ```

- Write it to a repo-scoped temp file (same convention as step 8, e.g.
  `E:/tmp/audit-<owner>-<repo>-snapshot.md`) — never a fixed shared name — and
  post it to the captured ledger URL:

  ```
  gh issue comment <ledger-url> --repo <OWNER/REPO> --body-file <tmpfile>
  ```

- **Posting the comment must never fail the run.** If `gh issue comment` errors,
  note `snapshot: skipped (<reason>)` and carry on — the ledger body upsert
  above is what the gate depends on; the snapshot is telemetry on top.

### 10. Final report

Print one summary table and stop. Exact shape:

```
/codebase-audit summary — <repo>  (scope: <whole repo | path>)

  bucket             findings  new  carried  stale*  filed
  -----------------  --------  ---  -------  ------  --------------------------------------------
  duplication              3    1        2       0   https://github.com/<owner>/<repo>/issues/<N>
  stale                    0    0        0       0   (no findings)
  claude-md-drift          2    0        2       0   https://github.com/<owner>/<repo>/issues/<N>
  maintainability          5    2        2       1   https://github.com/<owner>/<repo>/issues/<N>
  slop                     2    2        0       0   https://github.com/<owner>/<repo>/issues/<N>
  bug                      0    0        0       0   (no findings)
  documentation            4    1        1       2   https://github.com/<owner>/<repo>/issues/<N>

  security (self-healed):  1  — PR merged, private alert sent; or "escalated" / "none"

  * stale = carried from an earlier run, not re-verified this pass — kept on
    the checklist and flagged for review, not deleted.
  The security line is a count + disposition only — no finding detail (it never
  appears in this report, the issue, or any commit).

  skipped as duplicates:
    - <file>:<line> — dupe of #<N>
    - <file>:<line> — dupe of #<N>

  files inspected: <count>   (prioritization: <none | recent + entry points | …>)

  promotion candidates spotted:    (omit the block entirely if none)
    - asset:      <repo-relative path / module> — <one-line capability>
    - convention: <convention> — generalizable because <…>
```

The `new`/`carried`/`stale` columns are the **same counts** step 8 computed
for the `## Audit run log` bullets — never recomputed here. `findings` is the
total surviving-after-dedup count (step 9's snapshot comment reads this
column). `/audit-fleet`'s digest uses the breakdown to separate genuinely new
findings from standing backlog. The `promotion candidates spotted:` block is
the only place those surface (no issue, no writes) — `/audit-fleet` reads it
for the practices ledger; omit when none.

If every bucket was empty after dedupe, say so explicitly: `No actionable
findings. Codebase passes the audit.` — and stop.

## Hard rules

- **Materiality bar — applies to ALL SEVEN buckets.** Before filing, ask:
  *"Would a senior, perfectionist developer agree this is worth a future
  developer's time to fix?"* Hesitate more than a second → drop it. Empty
  buckets are the **right answer** when there's no material rot —
  `No actionable findings. Codebase passes the audit.` is a successful run.
  **Do not file findings to look thorough**; bias toward *fewer*. Bucket 5
  (bugs): only what you'd bet money on — false positives erode trust in the
  whole skill. Bucket 6 (documentation): only *headline, user-facing*
  surfaces — a shipped command, config knob, or setup step a new reader would
  hit — not an internal helper, a single stale sentence, or a section already
  changing from in-flight work. Bucket 7 (slop): only bloat a senior dev would
  actually delete — a materially oversized implementation or a whole unused
  abstraction, not "this could be two lines shorter." Bugs and documentation
  historically re-surface low-value findings, so hold both to a stricter bar
  than the others.
- **Never edit files — except the security self-heal (step 8b).** For the seven
  finding buckets this skill files issues and does not patch code. The **sole**
  code-editing path is step 8b, gated on its own rules (claim the repo, mandatory
  regression test, generic artifacts, auto-merge only on a green gate, escalate
  rather than merge blind). Never a reason to patch a duplication, slop, bug, or
  any other bucket's finding.
- **Promotion candidates never become issues or foreign-repo writes.** They are
  the inverse of a finding (an asset to preserve, not rot to fix), surfaced in
  the final report only. Filing or cataloguing them is `/audit-fleet`'s job.
- **Cap is 7 issues per run, period** (one per finding bucket). Don't split a
  bucket into multiple issues. A bucket with 30 findings → one issue with 30
  checklist items; the user triages via `/issue-start`. The step-8b redacted
  `security` issue is separate from this cap (it carries no findings and closes
  as soon as its fix merges) and is rare.
- **Security is self-healed, never publicly detailed (step 8b).** A security
  finding never goes on a public checklist. It gets a redacted issue (no class,
  file, line, or description), one branch fixing every gap in the repo with a
  mandatory regression test, generic commit/PR/test text, auto-merge only on a
  green gate (the regression test included), a private `--kind security` review
  alert, and escalation-not-blind-merge on any failure. The public fix commit is
  an unavoidable disclosure on a public repo — the mitigations are a short window
  and the private review, not secret text.
- **One managed issue per (repo, bucket) — the helper owns identity.**
  Never `gh issue create` / `gh issue edit` a managed issue by hand; always go
  through `skills/_lib/audit_issue.py` (`get` then `upsert`). It reuses the one
  issue, merges into it, and collapses strays. Hand-rolling a create is what
  spawned duplicates.
- **Never auto-close or auto-tick an audit issue.** It's a living backlog;
  multiple PRs may chip at it. Closing and checking boxes are the user's call.
- **The ledger snapshot comment is counts-only telemetry.** Step 9's
  per-category *count* row (`<!-- audit-snapshot -->`) must **never** carry
  finding text, file paths, or fix shapes — those live in the bucket issues, the
  single source of truth for *what* was found. Counts are derived (recomputed
  each run, append-only, never hand-edited), so the snapshot can't drift into a
  second authoritative store. A comment-post failure is non-fatal.
- **Cross-issue dedupe still applies.** Drop a finding already covered by a
  *different* (hand-filed or other-bucket) open issue; record it as
  "skipped: dupe of #N".
- **Citations or it didn't happen.** Every finding must point at a real
  `file:line`. "Lots of duplication in the auth module" is not a finding.
- **Don't audit `node_modules/`, `.venv/`, `dist/`, generated code, or
  vendored third-party trees.** `git ls-files` already excludes most of
  this, but double-check.
- **One label per issue** (the bucket label). Don't stack multiple type
  labels.
- **No AI attribution in the issue body or any commit.** (Per global
  CLAUDE.md.)
- **No hard-wrap in issue body paragraphs.** (Per global CLAUDE.md —
  rendered markdown.)

## What's NOT a finding

Concrete anti-examples. If a candidate finding looks like a **no**,
**drop it** — don't try to find a way to make it count:

- **Duplication.** No: three lines copied once between two files; a constant
  repeated in two places (local clarity beats premature abstraction). **Yes:**
  a 50-line block copied four times; two parallel implementations of the same
  workflow under different names.
- **Stale / dead code.** No: one slightly outdated comment, a six-month-old
  `TODO`, an unused import (a linter catches the import; the comment doesn't
  materially mislead). **Yes:** an entire orphaned module no caller references;
  a removed feature's scaffolding still imported on startup; a
  `# removed in v2` block shipped in v5.
- **CLAUDE.md drift.** No: a typo in a rule's prose, one instance of slightly
  inconsistent phrasing (the rule still reads correctly). **Yes:** a rule
  violated systematically (CLAUDE.md says "use `.venv`" and three modules use
  `venv/`); a hard rule contradicted by actual shipped behavior.
- **Maintainability.** No: a function name that could be slightly more
  descriptive, a 30-line function that could be 25, a *what* comment on already
  obvious code. **Yes:** a 1500-line god module mixing four unrelated concerns;
  a public API whose identifiers actively mislead about what they return;
  copy-pasted error handling 12 times in one file.
- **Bugs.** No: "this *might* race under high concurrency" without a concrete
  scenario; a bug in code already superseded by other in-flight work; one you
  can't point to a *currently reachable* call path for from a real entry point
  — reachability from something that actually runs today is required, not just
  "the line looks wrong." **Yes:** "this will mis-handle empty input because
  line N reads `xs[0]` with no guard" — name the input, the line, the failure.
- **Documentation.** No: a slightly stale README sentence, a flag described in
  fractionally outdated wording, a missing entry for a trivial internal or
  dev-only helper, a single outdated example a reader would self-correct in
  context. **Yes:** a whole README section documenting a removed subsystem; a
  headline user-facing command/feature absent from the docs entirely; the same
  setup steps duplicated across `README` and a `docs/` file that now disagree
  on the port; a dated `docs/2026-…-retrospective.md` the project's own
  doc-lifecycle rule forbids — name the file/section and the rule or missing
  feature. Bucket 6 is reserved for headline surfaces a new user/dev would
  actually go looking for and not find.
- **Slop.** No: a function a few lines longer than strictly necessary, one
  extra helper, a single defensive `if` for an unlikely-but-possible input
  (local clarity and honest guarding beat golf). **Yes:** a 40-line hand-rolled
  reimplementation of a stdlib one-liner; an entire configurable abstraction
  (strategy class, plugin registry, options dict) with exactly one hard-coded
  caller and no second use in sight; three parallel error-handling arms for
  exceptions the call can't raise; a generated-looking wall of boilerplate that
  collapses to a fraction of the lines — name the span and the line count it
  would shed. (If the fix is *reorganize* rather than *delete*, it's
  maintainability, not slop.)
- **Security.** No: "this input *could* be unsafe somewhere" with no reachable
  sink — the bug bar applies, name the exploitable path. **Yes:** a
  user-controlled value flowing unsanitized into a shell/SQL/eval sink; a
  secret or credential committed in source; a missing authz check on a
  state-changing route; `pickle.loads`/`yaml.load` on untrusted bytes — and it
  takes the step-8b self-heal path, never a public checklist item.

The pattern across all seven: **scale and impact matter**. One-off cosmetic
blemishes are not findings. Systematic problems, structural rot, or concrete
failure modes are.

## Notes

- Read-only by design for the seven buckets — "find problems" (this skill) stays
  separate from "fix problems" (`/issue-start` → `/simplify` / manual).
- The project's own CLAUDE.md is the rubric for bucket 3; no file → bucket 3
  usually empty, that's fine.
- **Not a deep security audit or pentest, and not a performance audit** — don't
  expand scope into either. Security here is limited to gaps that surface
  naturally during a resting-state read (an obvious injection sink, a committed
  secret, a missing authz check); anything found is self-healed via step 8b, not
  filed as a public finding. `/security-review` remains the diff-scoped
  reviewer; this is the whole-repo resting-state lens.
- Layered idempotency (step 2): unchanged → `SKIP` at one `gh` + one `git`
  call; self-fix-only churn → `SKIP_SELF_FIX`, ledger auto-advanced;
  below-threshold organic churn → `SKIP_BELOW_THRESHOLD`, ledger not advanced so
  it accumulates; even on `AUDIT`, dedupe prevents re-filing. All one Python
  function (`evaluate_repo`; unit-tested in `tests/test_audit_issue.py`), not
  LLM judgment.
- The ledger is labelled `audit-meta` so it never shows up as actionable —
  `/issue-triage` and `/issue-start` filter it out.
