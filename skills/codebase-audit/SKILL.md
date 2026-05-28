---
name: codebase-audit
description: Audit the resting state of a codebase against its CLAUDE.md and senior-dev maintainability standards — looks for duplication, stale/dead code, drift from CLAUDE.md conventions, sloppy/unmaintainable patterns, and bugs spotted while reading. Bundles findings into at most 5 GitHub issues (one per fixed bucket), self-assigned, deduped against open issues. Use when the user wants a whole-repo quality sweep — e.g. "/codebase-audit", "/codebase-audit app/", "audit the codebase", "find duplication and stale code", "check this repo against its CLAUDE.md", "review the codebase for slop".
---

# codebase-audit

**Goal:** Walk the codebase (or a scoped subtree), read it the way a senior
perfectionist developer would, and surface the resting-state quality problems
that the diff-scoped reviewers (`/code-review`, `/simplify`,
`/security-review`, ultrareview) never see. Bundle the findings into a small,
predictable set of GitHub issues — **at most 5 per run** — so `/issue-start`
can chew through them later.

**This skill produces GitHub issues, not code edits.** Never edit files in the
working tree. Never commit, push, or restart anything. Filing issues is the
only side effect.

**The five fixed buckets.** Every finding belongs to exactly one of:

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

One issue per non-empty bucket. **Hard cap: 5 issues per run.** Empty buckets
are simply skipped. Findings inside an issue go on a checklist with
`file:line` citations and a one-line fix shape.

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

**Whole-repo audits only.** If a scope path was passed (step "Arguments"),
skip this entire step *and* step 9 — the ledger tracks whole-repo audits, so a
scoped run always executes and never reads or writes the ledger.

Before reading a single source file, check whether this repo changed since the
last audit. The ledger is a per-repo cache: the commit SHA is the key, a hash
of the rubric busts it when the grading criteria change.

The ledger lives in **one issue per repo** — title `codebase-audit ledger`,
label `audit-meta`, `--assignee @me`, never closed. Its body carries a
machine-readable block:

```
<!-- audit-ledger -->
last-audited-sha: <full HEAD sha at last audit>
last-audited-at: <YYYY-MM-DD>
rubric-sha: <sha256 of global + project CLAUDE.md concatenated>
```

Steps:

- Find the ledger: `gh issue list --label audit-meta --state open --json number,body`.
  If none exists, this is a first run — skip the gate (the ledger is created in
  step 9) and continue to step 3.
- Compute the current `rubric-sha`: sha256 over the concatenation of the global
  CLAUDE.md and the project CLAUDE.md (the same two files step 3 loads); a
  missing file contributes the empty string.
- Read `last-audited-sha` from the block and run
  `git rev-list <last-audited-sha>..HEAD --count`.
- **Skip condition:** the count is `0` **and** the current `rubric-sha` equals
  the stored one. When it holds, stop immediately:
  `No changes since last audit (<short-sha> on <date>) — skipped.` Read no
  files, file nothing. A re-run over an unchanged repo then costs one `gh` call
  and one `git` call — that is the efficiency win the ledger exists for.
- Otherwise continue to step 3.

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

Read each file in the inventory. As you go, maintain a working list keyed by
bucket. For every finding capture:
- **Bucket** (one of the 5)
- **File:line** (or file range)
- **What's wrong** (one sentence, concrete)
- **Fix shape** (one sentence — what the patch would do, not the patch itself)
- For bucket 3 (CLAUDE.md drift): **which rule** was broken (quote it)

When you see the same pattern twice in two files, that's bucket 1
(duplication), not two separate bucket-4 findings.

**Apply the materiality bar (see Hard rules) to every finding as you take
it.** When in doubt, leave it out — across all five buckets. For bucket 5
specifically the bar is "I'd bet money on this"; speculation pollutes the
issue. For buckets 1–4 the bar is "a senior developer would agree this is
worth a future developer's time to fix." If you can already imagine the
user reading the finding and going "...so?", drop it before it gets
written down.

### 6. Dedupe against existing open issues

```
gh issue list --state open --limit 200 --json number,title,body
```

For each finding, scan the open issues. If a finding's substance is already
covered by an open issue (matched on title keywords + body content, not
strict string match), **drop it from the bucket** and remember it as
"skipped: dupe of #N" for the summary.

Do not file an issue that re-litigates an open one.

### 7. Ensure labels exist

The five bucket labels are: `duplication`, `stale`, `claude-md-drift`,
`maintainability`, `bug`. `bug` typically already exists. For each bucket
that has surviving findings, ensure its label exists:

```
gh label list --json name -q '.[].name'
```

For each missing label, create it (idempotent — only call for missing ones):

```
gh label create duplication       --color 'fbca04' --description 'Repeated logic across files'           || true
gh label create stale             --color 'cfd3d7' --description 'Dead/unused code or stale references'  || true
gh label create claude-md-drift   --color 'd876e3' --description 'Violates a CLAUDE.md convention'       || true
gh label create maintainability   --color 'a2eeef' --description 'Modularity / clarity / slop'           || true
```

### 8. File one issue per non-empty bucket

For each non-empty bucket (max 5 iterations), write the issue body to a
temp file and create:

```
gh issue create \
  --title "audit: <bucket> findings (<N> items)" \
  --body-file <tmpfile> \
  --label <bucket-label> \
  --assignee @me
```

**Body shape** (use this template, no hard wraps in paragraphs — the global
CLAUDE.md "Markdown that will be rendered" rule applies):

```markdown
Surfaced by `/codebase-audit` on <YYYY-MM-DD>. Scope: <whole repo | path>.

## Findings

- [ ] **<file>:<line>** — <what's wrong>. Fix: <fix shape>.
- [ ] **<file>:<line>** — <what's wrong>. Fix: <fix shape>.
- ...

## Context

<One short paragraph: the common thread across these findings, why they
matter together, anything the next `/issue-start` should know.>

<For bucket 3 (claude-md-drift), additionally list the rules that were
broken, quoting the CLAUDE.md passage.>
```

Title style — `audit: <bucket> findings (<N> items)`. Examples:
- `audit: duplication findings (3 items)`
- `audit: claude-md-drift findings (2 items)`
- `audit: maintainability findings (5 items)`

Use a temp file (`E:/tmp/audit-<bucket>.md` on Windows, `/tmp/audit-<bucket>.md`
elsewhere) so multi-line markdown isn't mangled by shell escaping.

### 9. Update the ledger

**Whole-repo audits only** — skip if a scope path was passed.

Upsert the per-repo ledger issue so the next run can short-circuit at step 2:

- Ensure the `audit-meta` label exists (idempotent):
  `gh label create audit-meta --color '5319e7' --description 'codebase-audit ledger / metadata — not actionable work' || true`
- Build the block with the current HEAD sha (`git rev-parse HEAD`), today's
  date, and the `rubric-sha` computed in step 2.
- If a ledger issue exists, `gh issue edit <N> --body-file <tmp>`; otherwise
  `gh issue create --title 'codebase-audit ledger' --body-file <tmp> --label audit-meta --assignee @me`.
- This runs on **every** non-skipped path — including a clean pass that filed
  zero issues — so an unchanged repo is correctly skipped next time.

### 10. Final report

Print one summary table and stop. Exact shape:

```
/codebase-audit summary — <repo>  (scope: <whole repo | path>)

  bucket             findings  filed
  -----------------  --------  --------------------------------------------
  duplication              3   https://github.com/<owner>/<repo>/issues/<N>
  stale                    0   (no findings)
  claude-md-drift          2   https://github.com/<owner>/<repo>/issues/<N>
  maintainability          5   https://github.com/<owner>/<repo>/issues/<N>
  bug                      0   (no findings)

  skipped as duplicates:
    - <file>:<line> — dupe of #<N>
    - <file>:<line> — dupe of #<N>

  files inspected: <count>   (prioritization: <none | recent + entry points | …>)
```

If every bucket was empty after dedupe, say so explicitly: `No actionable
findings. Codebase passes the audit.` — and stop.

## Hard rules

- **Materiality bar — applies to ALL FIVE buckets.** Before filing a
  finding, ask: *"Would a senior, perfectionist developer reading this
  agree it's worth a future developer's time to fix?"* If you hesitate
  for more than a second, drop it. Empty buckets are the **right answer**
  when there's no material rot — `No actionable findings. Codebase passes
  the audit.` is a successful run, not a failed one. **Do not file
  findings to look thorough.** The user would rather get zero issues from
  a clean codebase than five issues full of noise they have to triage
  out. Bias is toward filing *fewer*, not more. For bucket 5 (bugs)
  specifically the bar is even higher — only file what you'd bet money
  on; false-positive bug reports erode trust in the whole skill.
- **Never edit files.** This skill files issues; it does not patch code.
- **Cap is 5 issues per run, period.** Don't split a bucket into multiple
  issues. If a bucket has 30 findings, file one issue with 30 checklist
  items — the user can triage which to fix via `/issue-start`.
- **Dedupe is not optional.** Always check open issues first. Re-filing
  the same finding every audit run defeats the purpose.
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

Concrete anti-examples. If a candidate finding looks like any of these,
**drop it** — don't try to find a way to make it count:

- **Duplication.** Three lines copied once between two files: not a
  finding. A constant repeated in two places: not a finding (might even
  be the right shape — local clarity beats premature abstraction). A
  50-line block copied four times, or two parallel implementations of
  the same workflow under different names: **yes**, that's a finding.
- **Stale / dead code.** One slightly outdated comment, a `TODO` from
  six months ago, an unused import: not a finding individually (a linter
  catches the import, and the comment doesn't materially mislead). An
  entire orphaned module no caller references, a removed feature's
  scaffolding still imported on startup, a `# removed in v2` block
  shipped in v5: **yes**.
- **CLAUDE.md drift.** A typo in a prose paragraph of one CLAUDE.md
  rule, a single instance of slightly inconsistent phrasing: not a
  finding (the rule still reads correctly). A rule violated
  systematically across the codebase (e.g. CLAUDE.md says "use `.venv`"
  and three modules use `venv/`), or a hard rule contradicted by actual
  shipped behavior: **yes**.
- **Maintainability.** One function name that could be slightly more
  descriptive, a 30-line function that could be 25, a comment that
  explains the *what* but the code is already obvious: not a finding.
  A 1500-line god module mixing four unrelated concerns, a public API
  whose identifiers actively mislead about what they return,
  copy-pasted error handling 12 times in one file: **yes**.
- **Bugs.** "This *might* race under high concurrency" without a
  concrete scenario: not a finding. "This will mis-handle empty input
  because line N reads `xs[0]` with no guard": **yes** — name the
  input, name the line, name the failure.

The pattern across all five: **scale and impact matter**. One-off
cosmetic blemishes are not findings. Systematic problems, structural
rot, or concrete failure modes are.

## Notes

- The audit is intentionally **read-only and bundled**. The split between
  "find problems" (this skill) and "fix problems" (`/issue-start` →
  `/simplify` / manual) keeps both stages reviewable.
- Each project's own CLAUDE.md is the rubric for bucket 3. If a project
  doesn't have one, bucket 3 will usually be empty — that's fine.
- This skill is **not** a security audit (`/security-review` covers that)
  or a performance audit (different rubric, different tooling). Don't
  expand scope into either.
- If the user reruns the skill the next day after fixing nothing, the
  **ledger gate (step 2)** short-circuits before any files are read — HEAD
  is unchanged, so the run stops at one `gh` + one `git` call. Even when the
  gate is bypassed (first run, scoped run, or the rubric changed), the dedupe
  step still prevents re-filing the same findings. That layered idempotency
  is the intended behavior.
- The ledger issue is labelled `audit-meta` precisely so it never shows up as
  actionable work — `/issue-triage` and `/issue-start` filter that label out.
