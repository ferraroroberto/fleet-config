# codebase-audit — step reference

Loaded on demand from the `/codebase-audit` skill (`SKILL.md`, steps 8, 9 and 10). "Hard rules" and step numbers refer to that file.

## Step 8 — fresh issue body shape and title style

**Body shape** for a fresh issue (no hard wraps in paragraphs — the global
CLAUDE.md "Markdown that will be rendered" rule applies; the helper prepends the
marker, don't write it yourself):

```markdown
Surfaced by `/codebase-audit`, kept up to date across runs. Scope: <whole repo | path>.

## Findings

- [ ] **<file>:<line>** — <what's wrong>. Quote: `<verbatim line(s)>`. Fix: <fix shape>.
- [ ] **<file>:<line>** — <what's wrong>. Quote: `<verbatim line(s)>`. Fix: <fix shape>.
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

## Step 9 — ledger write and snapshot comment

Upsert the per-repo ledger issue so the next run can short-circuit at step 2:

- One command does the whole write — **never hand-author the ledger block, and
  never record the working checkout's `HEAD`**:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py ledger-write \
    --repo <OWNER/REPO> --repo-path <REPO_PATH>
  ```

  It composes the `<!-- audit-ledger -->` block itself — sha, today's date, and
  the `rubric-sha` (sha256 of the project CLAUDE.md alone) — then creates,
  edits, or collapses strays and ensures the `audit-meta` label, printing the
  ledger issue URL. Capture that URL; the snapshot comment below posts to it.

  Two things not to do by hand, both of which have already cost real audits:

  - **Don't write the marker.** Hand-authoring drifts to an *open* comment block
    (`<!-- audit-ledger` … `-->`) the step-2 gate cannot read, buying a full Opus
    whole-repo audit every week (fleet-config#566). The parser reads both forms
    and the helper normalizes back to the closed one, but the tool owns the
    delimiter.
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

## Step 10 — final report shape

Print one summary table and stop. Exact shape:

```
/codebase-audit summary — <repo>  (scope: <whole repo | path>)

  bucket             findings  new  carried  stale*  resurfaced  filed
  -----------------  --------  ---  -------  ------  ----------  --------------------------------------------
  duplication              3    1        2       0           0   https://github.com/<owner>/<repo>/issues/<N>
  stale                    0    0        0       0           0   (no findings)
  claude-md-drift          2    0        2       0           0   https://github.com/<owner>/<repo>/issues/<N>
  maintainability          5    2        2       1           1   https://github.com/<owner>/<repo>/issues/<N>
  slop                     2    2        0       0           0   https://github.com/<owner>/<repo>/issues/<N>
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

  unverified (not filed — the quote check did not return VERIFIED):
    - <file>:<line> (MISMATCH | NO_QUOTE | UNREADABLE)

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

## Step 8 — re-verification tags on a merged item

The four tags and the escalation rule step 8.2 applies when it merges this run's findings into the existing body ("Tag each item's re-verification status inline").

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

## Step 8 — quote-check statuses (2b)

What each `audit_quote.py check` status means and why only `VERIFIED` findings are filed.

Every finding line prints `VERIFIED`, `MISMATCH`, `NO_QUOTE` or `UNREADABLE`
(ticked and `_(carried …)_` items are skipped). **Only `VERIFIED` findings are
filed**: remove every other one from the body before step 3 and list it in
step 10's report as `unverified: <file>:<line> (<status>)`. A `file:line` can
be hallucinated and still look valid; a quote that exists in the file can't —
that matters most in the unattended `/audit-fleet` run, where nobody reads the
findings before they are filed.

## Step 2 — the ledger issue

What the per-repo ledger issue holds and what `evaluate_repo` compares.

The ledger lives in **one issue per repo** — title `codebase-audit ledger`,
label `audit-meta`, `--assignee @me`, never closed, with a hidden identity
marker and a machine-readable `<!-- audit-ledger -->` block (`last-audited-sha`,
`last-audited-at`, `rubric-sha` — sha256 of the project CLAUDE.md **alone**;
the global `~/.claude/CLAUDE.md` is deliberately excluded so an edit to that
shared file never busts every repo's cache at once). `evaluate_repo` computes
and compares all of this internally.
