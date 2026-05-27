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

### 2. Load the rubric

Read both CLAUDE.md files in full:
- Global: `~/.claude/CLAUDE.md` (or `$env:USERPROFILE/.claude/CLAUDE.md` on
  Windows). If absent, note it and continue without a global rubric.
- Project: `<repo-root>/CLAUDE.md`. If absent, note it and continue — the
  audit still runs, just without project-specific drift checks.

Extract the **specific, checkable rules** from each (e.g. "no `Co-Authored-By:
Claude` trailer", "tests must hit a real database", "use `.venv` not `venv`",
"forward slashes in `settings.json` commands"). These are the inputs to
bucket 3.

### 3. Inventory the files to read

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

### 4. Read systematically and take notes by bucket

Read each file in the inventory. As you go, maintain a working list keyed by
bucket. For every finding capture:
- **Bucket** (one of the 5)
- **File:line** (or file range)
- **What's wrong** (one sentence, concrete)
- **Fix shape** (one sentence — what the patch would do, not the patch itself)
- For bucket 3 (CLAUDE.md drift): **which rule** was broken (quote it)

When you see the same pattern twice in two files, that's bucket 1
(duplication), not two separate bucket-4 findings.

When in doubt about a bucket-5 (bug) finding, leave it out. The bar is "I'd
bet money on this." Speculation pollutes the issue.

### 5. Dedupe against existing open issues

```
gh issue list --state open --limit 200 --json number,title,body
```

For each finding, scan the open issues. If a finding's substance is already
covered by an open issue (matched on title keywords + body content, not
strict string match), **drop it from the bucket** and remember it as
"skipped: dupe of #N" for the summary.

Do not file an issue that re-litigates an open one.

### 6. Ensure labels exist

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

### 7. File one issue per non-empty bucket

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

### 8. Final report

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

- **Never edit files.** This skill files issues; it does not patch code.
- **Cap is 5 issues per run, period.** Don't split a bucket into multiple
  issues. If a bucket has 30 findings, file one issue with 30 checklist
  items — the user can triage which to fix via `/issue-start`.
- **Dedupe is not optional.** Always check open issues first. Re-filing
  the same finding every audit run defeats the purpose.
- **Citations or it didn't happen.** Every finding must point at a real
  `file:line`. "Lots of duplication in the auth module" is not a finding.
- **Bucket 5 (bugs) is high-bar.** If you wouldn't bet money on it, leave
  it out. False-positive bug reports erode trust in the whole skill.
- **Don't audit `node_modules/`, `.venv/`, `dist/`, generated code, or
  vendored third-party trees.** `git ls-files` already excludes most of
  this, but double-check.
- **One label per issue** (the bucket label). Don't stack multiple type
  labels.
- **No AI attribution in the issue body or any commit.** (Per global
  CLAUDE.md.)
- **No hard-wrap in issue body paragraphs.** (Per global CLAUDE.md —
  rendered markdown.)

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
  dedupe step means no new issues get filed — the open issues from the
  previous run still cover the findings. That's the intended behavior.
