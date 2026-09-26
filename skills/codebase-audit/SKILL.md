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
self-healed in place (redacted issue + auto-fix) — a security gap sitting in a
public issue body until someone gets to it is itself a disclosure. That path is
step 8b and its Hard Rule; it is scoped to security only and is never license
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

   **Boundary against buckets 1–3 (this is the part that goes wrong):**
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
   boundary (the two blur):** bucket 4 asks *"is this code well structured?"*
   (naming, modularity, a god-module); bucket 7 asks *"did this much code need
   to exist at all?"* A finding that would shrink the line count with no loss
   of behavior is slop (7); one that would reorganize the same lines is
   maintainability (4). Both apply → file once, in whichever is the dominant
   fix. AI-assisted work on this fleet steadily accretes lines — be actively
   critical of volume, not just structure.

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
`last-audited-at`, `rubric-sha` — sha256 of the project CLAUDE.md **alone**;
the global `~/.claude/CLAUDE.md` is deliberately excluded so an edit to that
shared file never busts every repo's cache at once). `evaluate_repo` computes
and compares all of this internally.

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
per-bucket notes and never in a public checklist.** A security gap (an
injection sink, a hardcoded secret, a path-traversal, a missing-authz check, an
unsafe deserialization, credentials in a committed file, etc.) is held aside
for the self-heal path (step 8b). Record only what the fix agent needs —
file:line and the concrete gap; it never leaves this run as public text. Hold
it to the bug bar (would you bet money it's exploitable) — a false one wastes
an auto-fix cycle and, worse, an unnecessary public fix commit.

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
  fixed those; never reset them — **except when this run finds the same
  problem again** (below): a ticked item hides a regression, so it is
  un-ticked and tagged, never left silently ticked (fleet-config#960 B).
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
  - **Re-surfaced this run** (found again, and its box is `[x]`): the fix did
    not hold or regressed. Un-tick it (`- [ ]`), bump `last-seen`, and append
    inline `_(re-surfaced <date>)_`. Count it separately from new and carried
    (the `resurfaced` column in step 10's table and the run-log bullet).
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
    default, not a bug. Pruning stays a human decision (never auto-tick);
    this only makes staleness visible on the item itself.
- **Never tick anything yourself**, and never add `Closes #` — multiple PRs may
  chip at one audit issue without closing it. Closing is the user's call via
  `/issue-finish` once all boxes are checked; a lane may close it only under
  the proven-landed bar in **Hard rules** below.
- Append a dated bullet to the `## Audit run log` section:
  `<YYYY-MM-DD> @ <short-sha>: +A new, B carried, C not re-surfaced, D re-surfaced`.

**2b. Verify every quote before filing** (fleet-config#960 A). Each new and
re-matched finding carries ``Quote: `<verbatim text of the motivating line(s)>` ``
(one to three lines copied from the file, joined with spaces, no backticks
inside — see the body shape in reference.md). Write the merged body to the temp file, then:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_quote.py check \
  --repo-path <repo root> --body-file <tmpfile>
```

Every finding line prints `VERIFIED`, `MISMATCH`, `NO_QUOTE` or `UNREADABLE`
(ticked and `_(carried …)_` items are skipped). **Only `VERIFIED` findings are
filed**: remove every other one from the body before step 3 and list it in
step 10's report as `unverified: <file>:<line> (<status>)`. A `file:line` can
be hallucinated and still look valid; a quote that exists in the file can't —
that matters most in the unattended `/audit-fleet` run, where nobody reads the
findings before they are filed.

**3. Upsert** (creates if absent, edits if present, collapses any strays):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
  --repo <OWNER/REPO> --kind <bucket> --label <bucket-label> \
  --title "audit: <bucket> findings" --body-file <tmpfile> --verify-quotes <repo root>
```

`--verify-quotes` is the backstop for 2b: the helper re-runs the quote check
and refuses the whole write (`QUOTES=unverified`, nothing written) if an
unverified finding is still in the body.

The helper stamps the `<!-- audit-managed: kind=<bucket> -->` marker, applies
the label, prints the canonical issue URL. **Titles are stable** — no `(N
items)` count (lives in the body), so the title never changes run to run.

**Body shape** for a fresh issue and the stable title style (`audit: <bucket>
findings`, never a count) are in [reference.md](reference.md), step 8. No hard
wraps in paragraphs; the helper prepends the marker, don't write it yourself.

Use a **repo-scoped, unique** temp file so multi-line markdown isn't mangled
by shell escaping *and* concurrent audits never clobber each other's scratch:
`E:/tmp/audit-<owner>-<repo>-<short-sha>-<bucket>.md` (slash in `OWNER/REPO` →
hyphen; `<short-sha>` = `git rev-parse --short HEAD`). **Never** a fixed
`E:/tmp/audit-<bucket>.md` — `/audit-fleet`'s parallel sub-agents share
`E:/tmp`, and a fixed name is a race.

### 8b. Security findings — redacted issue + immediate self-heal

**Only runs when step 5 held aside one or more security findings.** No security
findings → skip this entire step. This is the one place the skill writes code,
scoped to security and gated on the rules below.

When it runs, open [security-self-heal.md](security-self-heal.md) and follow its
six steps in order, **inline in your own agent context** — never a nested
background sub-agent, which gets no auto-resume wake-up and would silently stall
under `/audit-fleet`. In outline: claim the repo with `worktree_claim.py acquire
<repo-root> --force-worktree` and work only in the printed `WORKTREE=`; file the
redacted `audit: security findings` issue; fix every gap on one branch via
`/issue-yolo` with a mandatory regression test per gap and generic artifact
text; auto-merge only on a green gate; close the redacted issue; fire the private
`--kind security` alert. No test surface, or a red gate → escalate, never merge
blind. The invariants are restated under **Hard rules**.

### 9. Update the ledger

**Whole-repo audits only** — skip if a scope path was passed. It runs on
**every** non-skipped path, including a clean pass that filed zero issues, so an
unchanged repo is correctly skipped next time.

One command does the whole write — **never hand-author the ledger block, and
never record the working checkout's commit**:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py ledger-write \
  --repo <OWNER/REPO> --repo-path <REPO_PATH>
```

Capture the ledger issue URL it prints. A non-zero exit → leave the ledger
unchanged and say so in the run report. Then post the counts-only
`<!-- audit-snapshot -->` comment to that URL (a post failure is non-fatal). Why
hand-authoring and recording the checkout tip each broke real audits, and the
snapshot table's exact shape: [reference.md](reference.md), step 9.

### 10. Final report

Print one summary table and stop — its exact shape, and what each column means
to `/audit-fleet`, is in [reference.md](reference.md), step 10. The
`new`/`carried`/`stale` columns reuse step 8's counts, never recomputed; the
security line is a count + disposition only, never finding detail.

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
- **Never edit files — except the security self-heal (step 8b)**, gated on its
  own rules (claim the repo, mandatory regression test, generic artifacts,
  auto-merge only on a green gate, escalate rather than merge blind). Never a
  reason to patch a duplication, slop, bug, or any other bucket's finding.
- **Promotion candidates never become issues or foreign-repo writes.** They are
  the inverse of a finding (an asset to preserve, not rot to fix), surfaced in
  the final report only. Filing or cataloguing them is `/audit-fleet`'s job.
- **Cap is 7 issues per run, period** (one per finding bucket). Don't split a
  bucket into multiple issues. A bucket with 30 findings → one issue with 30
  checklist items; the user triages via `/issue-start`. The step-8b redacted
  `security` issue is separate from this cap (it carries no findings and closes
  as soon as its fix merges) and is rare.
- **Security is self-healed, never publicly detailed (step 8b).** A security
  finding never goes on a public checklist. Invariants: a redacted issue (no
  class, file, line, or description); one branch fixing every gap in the repo;
  a mandatory regression test; generic commit/PR/test text; auto-merge only on
  a green gate including that test; a private `--kind security` review alert;
  escalation-not-blind-merge on any failure.
- **One managed issue per (repo, bucket) — the helper owns identity.**
  Never `gh issue create` / `gh issue edit` a managed issue by hand; always go
  through `skills/_lib/audit_issue.py` (`get` then `upsert`). It reuses the one
  issue, merges into it, and collapses strays. Hand-rolling a create is what
  spawned duplicates.
- **Never auto-tick an audit issue; close one only on proof.** It's a living
  backlog; multiple PRs may chip at it. Checking boxes stays the user's call.
  A lane **may** close an audit issue (Roberto's standing authorization,
  2026-09-12) only when **every** finding is proven already landed on `main`
  by directly reading the code at a named SHA — never from a PR description, a
  changelog, or an earlier session's log — with that proof recorded per finding
  in the close comment. One unproven or still-open finding means do not close:
  fix it, or report it and leave the issue open. Never manufacture a change to
  justify a close, and never hand-edit the managed body's checkboxes (the body
  belongs to `audit_issue.py`; hand edits spawn duplicates).
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
  `file:line` **and quote the line(s) it is about**, verbatim, checked by
  `audit_quote.py` in step 8 (2b). A finding that cannot quote is reported as
  unverified and not filed. "Lots of duplication in the auth module" is not a
  finding.
- **Don't audit `node_modules/`, `.venv/`, `dist/`, generated code, or
  vendored third-party trees.** `git ls-files` already excludes most of
  this.
- **One label per issue** (the bucket label). Don't stack multiple type
  labels.
- **No AI attribution in the issue body or any commit.** (Per global
  CLAUDE.md.)
- **No hard-wrap in issue body paragraphs.** (Per global CLAUDE.md —
  rendered markdown.)

## What's NOT a finding

Concrete per-bucket anti-examples — a **no** and a **yes** for every bucket,
security included — live in [not-a-finding.md](not-a-finding.md); read it
before step 5's notes become findings. If a candidate finding looks like a
**no**, **drop it** — don't try to find a way to make it count. The pattern
across all seven: **scale and impact matter**. One-off cosmetic blemishes are
not findings. Systematic problems, structural rot, or concrete failure modes
are.

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
- Step 2's four decisions plus step 6's dedupe are layered idempotency, all
  decided by one Python function (`evaluate_repo`; unit-tested in
  `tests/test_audit_issue.py`), never LLM judgment.
- The ledger is labelled `audit-meta` so it never shows up as actionable —
  `/issue-triage` and `/issue-start` filter it out.
