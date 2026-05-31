---
name: codebase-audit
description: Audit the resting state of a codebase against its CLAUDE.md and senior-dev maintainability standards — looks for duplication, stale/dead code, drift from CLAUDE.md conventions, sloppy/unmaintainable patterns, bugs spotted while reading, and documentation problems (README/docs that drift from CLAUDE.md, repeat themselves, go stale, or omit a shipped feature). Bundles findings into at most 6 GitHub issues (one per fixed bucket), self-assigned, deduped against open issues. Use when the user wants a whole-repo quality sweep — e.g. "/codebase-audit", "/codebase-audit app/", "audit the codebase", "find duplication and stale code", "check the docs against its CLAUDE.md", "review the codebase for slop".
---

# codebase-audit

**Goal:** Walk the codebase (or a scoped subtree), read it the way a senior
perfectionist developer would, and surface the resting-state quality problems
that the diff-scoped reviewers (`/code-review`, `/simplify`,
`/security-review`, ultrareview) never see. Bundle the findings into a small,
predictable set of GitHub issues — **at most 6 per run** — so `/issue-start`
can chew through them later.

**This skill produces GitHub issues, not code edits.** Never edit files in the
working tree. Never commit, push, or restart anything. Filing issues is the
only side effect.

**The six fixed buckets.** Every finding belongs to exactly one of:

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

One issue per non-empty bucket. **Hard cap: 6 issues per run.** Empty buckets
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
label `audit-meta`, `--assignee @me`, never closed. Its body carries a hidden
identity marker on the first line **and** a machine-readable block:

```
<!-- audit-managed: kind=ledger -->
<!-- audit-ledger -->
last-audited-sha: <full HEAD sha at last audit>
last-audited-at: <YYYY-MM-DD>
rubric-sha: <sha256 of global + project CLAUDE.md concatenated>
```

Steps:

- Find the ledger by its marker (not the bare `audit-meta` label, which also
  tags the `audit-fleet digest state` issue):
  `py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind ledger`.
  It prints `{"number": N|null, "body": "...", "duplicates": [...]}`. If
  `number` is `null`, this is a first run — skip the gate (the ledger is created
  in step 9) and continue to step 3.
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
- **Bucket** (one of the 6)
- **File:line** (or file range)
- **What's wrong** (one sentence, concrete)
- **Fix shape** (one sentence — what the patch would do, not the patch itself)
- For bucket 3 (CLAUDE.md drift): **which rule** was broken (quote it)
- For bucket 6 (documentation): **which sub-check** (CLAUDE.md compliance /
  stale-or-duplicated / missing feature) and the rule or feature it concerns

When you see the same pattern twice in two files, that's bucket 1
(duplication), not two separate bucket-4 findings.

**Read `README.md` and `docs/` twice — once for context, once for bucket 6.**
The first pass mines them for code-side staleness (a doc that references a
removed module is a lead for bucket 2). The second pass judges them *as
documentation* against bucket 6's three sub-checks: walk the shipped,
user-facing surface you saw in the code (commands, flags, ports, config keys,
entry points) and confirm the docs cover it, don't contradict it, and don't
repeat themselves. A feature in the code with no mention in `README`/`docs` is
the canonical "missing crucial features" finding.

**Apply the materiality bar (see Hard rules) to every finding as you take
it.** When in doubt, leave it out — across all six buckets. For bucket 5
specifically the bar is "I'd bet money on this"; speculation pollutes the
issue. For buckets 1–4 the bar is "a senior developer would agree this is
worth a future developer's time to fix." If you can already imagine the
user reading the finding and going "...so?", drop it before it gets
written down.

### 6. Dedupe against existing open issues

```
gh issue list --state open --limit 200 --json number,title,body
```

This catches only **cross-issue** duplicates — a finding already tracked by a
*hand-filed* issue or a *different* bucket. Re-filing the same bucket every run
is no longer a risk: step 8 reuses the one managed issue per bucket and merges
into it, so do **not** drop a finding just because last run's audit issue for
*this* bucket already lists it — that issue is the one you're about to update.

For each finding, scan the open issues. If a finding's substance is already
covered by an issue that is **not** this bucket's managed audit issue (matched
on title keywords + body content, not strict string match), **drop it** and
remember it as "skipped: dupe of #N" for the summary.

### 7. Ensure labels exist

The six bucket labels are: `duplication`, `stale`, `claude-md-drift`,
`maintainability`, `bug`, `documentation`. `bug` and `documentation` are
GitHub defaults that typically already exist. For each bucket that has
surviving findings, ensure its label exists:

```
gh label list --json name -q '.[].name'
```

For each missing label, create it (idempotent — only call for missing ones):

```
gh label create duplication       --color 'fbca04' --description 'Repeated logic across files'           || true
gh label create stale             --color 'cfd3d7' --description 'Dead/unused code or stale references'  || true
gh label create claude-md-drift   --color 'd876e3' --description 'Violates a CLAUDE.md convention'       || true
gh label create maintainability   --color 'a2eeef' --description 'Modularity / clarity / slop'           || true
gh label create documentation     --color '0075ca' --description 'README / docs quality, coverage, drift' || true
```

### 8. Upsert one issue per non-empty bucket

There is **exactly one** managed issue per (repo, bucket), reused across runs.
You never `gh issue create` directly — the helper owns identity so a re-run can
never spawn a duplicate. For each non-empty bucket (max 6 iterations):

**1. Fetch the existing issue** for this bucket:

```
py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind <bucket>
```

It prints `{"number": N|null, "body": "...", "duplicates": [...]}`.

**2. Build the merged body.** If `number` is `null`, write a fresh body from the
template below. If it exists, **merge** this run's findings into the returned
body — the issue is a *living backlog*, so:

- **Preserve every already-ticked checkbox** (`- [x]`) verbatim — the user
  fixed those; never reset them.
- **Match by file path first.** A finding for a file already listed is the same
  finding even if the line number moved — update the line to this run's value
  (re-verified while reading) and keep the existing checkbox state. Append a
  finding as new only when no item for that file + problem already exists.
- **Keep items this run didn't re-surface** (don't delete them); flag them in
  the run log as "not re-surfaced (verify)".
- **Never tick or close anything yourself**, and never add `Closes #` — multiple
  PRs may chip at one audit issue without closing it; closing is the user's call
  via `/issue-finish` once all boxes are checked.
- Append a dated bullet to the `## Audit run log` section:
  `<YYYY-MM-DD> @ <short-sha>: +A new, B carried, C not re-surfaced`.

**3. Upsert** (creates if absent, edits if present, collapses any strays):

```
py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
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
`audit: maintainability findings`, `audit: documentation findings`.

Use a **repo-scoped, unique** temp file so multi-line markdown isn't mangled
by shell escaping *and* concurrent audits never clobber each other's scratch:
`E:/tmp/audit-<owner>-<repo>-<short-sha>-<bucket>.md` on Windows
(`/tmp/audit-<owner>-<repo>-<short-sha>-<bucket>.md` elsewhere), where
`<owner>-<repo>` is the `OWNER/REPO` from step 1 with the slash replaced by a
hyphen, and `<short-sha>` is `git rev-parse --short HEAD`. **Do not** use a
fixed `E:/tmp/audit-<bucket>.md` — when `/audit-fleet` fans many repos out to
parallel sub-agents at once they share `E:/tmp`, and a fixed name is a race
where one agent overwrites another's body mid-run (see the global CLAUDE.md
tmp-file-reuse gotcha).

### 9. Update the ledger

**Whole-repo audits only** — skip if a scope path was passed.

Upsert the per-repo ledger issue so the next run can short-circuit at step 2:

- Build the body: the `<!-- audit-ledger -->` block with the current HEAD sha
  (`git rev-parse HEAD`), today's date, and the `rubric-sha` from step 2. The
  helper prepends the `<!-- audit-managed: kind=ledger -->` marker — don't write
  it yourself. Keep the `<!-- audit-ledger -->` block intact; the step-2 gate
  parses it.
- Write the body to a repo-scoped temp file (same convention as step 8, e.g.
  `E:/tmp/audit-<owner>-<repo>-ledger.md`) — never a fixed shared name.
- Upsert via the helper (creates, edits, or collapses strays — and ensures the
  `audit-meta` label):

  ```
  py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
    --repo <OWNER/REPO> --kind ledger --label audit-meta \
    --title "codebase-audit ledger" --body-file <tmpfile>
  ```

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
  documentation            4   https://github.com/<owner>/<repo>/issues/<N>

  skipped as duplicates:
    - <file>:<line> — dupe of #<N>
    - <file>:<line> — dupe of #<N>

  files inspected: <count>   (prioritization: <none | recent + entry points | …>)
```

If every bucket was empty after dedupe, say so explicitly: `No actionable
findings. Codebase passes the audit.` — and stop.

## Hard rules

- **Materiality bar — applies to ALL SIX buckets.** Before filing a
  finding, ask: *"Would a senior, perfectionist developer reading this
  agree it's worth a future developer's time to fix?"* If you hesitate
  for more than a second, drop it. Empty buckets are the **right answer**
  when there's no material rot — `No actionable findings. Codebase passes
  the audit.` is a successful run, not a failed one. **Do not file
  findings to look thorough.** The user would rather get zero issues from
  a clean codebase than six issues full of noise they have to triage
  out. Bias is toward filing *fewer*, not more. For bucket 5 (bugs)
  specifically the bar is even higher — only file what you'd bet money
  on; false-positive bug reports erode trust in the whole skill.
- **Never edit files.** This skill files issues; it does not patch code.
- **Cap is 6 issues per run, period.** Don't split a bucket into multiple
  issues. If a bucket has 30 findings, file one issue with 30 checklist
  items — the user can triage which to fix via `/issue-start`.
- **One managed issue per (repo, bucket) — the helper owns identity.**
  Never `gh issue create` / `gh issue edit` a managed issue by hand; always go
  through `skills/_lib/audit_issue.py` (`get` then `upsert`). It reuses the one
  issue, merges into it, and collapses any strays. Hand-rolling a create is what
  spawned duplicates in the first place.
- **Never auto-close or auto-tick an audit issue.** It's a living backlog;
  multiple PRs may chip at it. Closing and checking boxes are the user's call.
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
- **Documentation.** One sentence in the README that's slightly stale, a
  flag the docs describe in fractionally outdated wording, a missing entry
  for a trivial internal helper: not a finding. A whole README section
  documenting a removed subsystem, a headline user-facing command/feature
  absent from the docs entirely, the same setup steps duplicated across
  `README` and a `docs/` file that now disagree on the port, or a dated
  `docs/2026-…-retrospective.md` the project's own doc-lifecycle rule
  forbids: **yes** — name the file/section and the rule or missing feature.

The pattern across all six: **scale and impact matter**. One-off
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
