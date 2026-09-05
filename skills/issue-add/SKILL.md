---
name: issue-add
description: Turn a rough idea, brain-dump, or transcript into a well-formed GitHub issue — researches the codebase, drafts it as a senior developer would, labels it, self-assigns, creates it. E.g. "/issue-add <paste your idea or transcript>" or "/issue-add now <idea>" to file and start building in one shot. Pairs with /issue-start and /issue-finish.
---

# issue-add

**Capability preflight:** read [workflow-capabilities](../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional Claude examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** Take whatever the user pastes — clean idea, rambling brain-dump, raw
voice transcript — and file one well-formed GitHub issue a senior developer
would be happy to have written: self-contained, researched, correctly scoped,
ready to hand off cold to an LLM or a human.

The issue is **created directly** once drafted — no approval checkpoint.

## Arguments

Everything after `/issue-add` is the raw input. If nothing was pasted, ask the
user to paste the idea/transcript and stop until they do.

The word `now` anywhere in the args (`/issue-add now <text>`, `/issue-add <text> now`)
→ **one-shot mode**: after the issue is created, immediately proceed to the
`/issue-start <N> now` flow (sync main, cut the branch, build straight away)
without a stop in between. Strip the `now` token before treating the rest as
the issue text.

## Steps

Run in order. If a step fails, print a short error and stop.

### 1. Repo + convention context

In parallel:
- `git rev-parse --is-inside-work-tree` — must be `true`, else stop:
  "Not inside a git repository."
- Read the project's `CLAUDE.md` and `README.md` — layout, conventions, how the
  change being filed interacts with the code.
- `gh label list` — compare against the canonical type set in step 7; any
  missing label gets created there.

Do **not** scan past issues to "learn the house style" — step 6 is the only
source of truth; reading prior issues adds noise and drifts toward whatever was
filed last.

### 2. Extract the real intent

The pasted text may be messy or a garbled dictation. Work out what the user
actually wants — not the literal words. Don't ask a question yet; research
usually resolves apparent ambiguity.

### 3. Research the codebase

The core of the skill. Find and read the code the idea touches:
- Which files / modules / functions are involved, and how they behave now.
- Constraints, conventions, and patterns the change must respect.
- Anything that makes the idea harder or different than it first sounds.

Gather enough that the issue can be picked up **cold** — no tribal knowledge.

### 4. Check for duplicates

Scan open issues (`gh issue list --state open`) for one already covering the
same thing. If there's a clear duplicate, **don't create** — tell the user the
existing issue number and stop.

### 5. Decide if a question is needed

Only if a **substantive** ambiguity remains after research — one that would
change what gets built — ask one sharp question (the contract’s available user-input channel). Never ask
about anything research already answered.

### 6. Draft the issue

Write it the way a senior developer would — proportionate, no over-engineering,
no padding.

- **Title:** `<Area>: <concise description>` — e.g.
  `Coding tab: rename a running session from the app`,
  `audio/transcribe: handle empty whisper response`. Lowercase verb after the
  colon, no trailing period, ≤72 chars. This is the canonical style — don't
  imitate older issues if they diverge.
- **Body:** self-contained and LLM-handoff-ready. This is the **one canonical
  section list** for an issue body fleet-wide (fleet-config#446 reconciled a
  second, conflicting list that used to live in `global-CLAUDE.md` — that file
  now only points here). Use as many of these as the issue genuinely needs — a
  tiny issue needs only the first two:
  - **What & why** (or **Symptom** + **Root cause** for a bug) — the goal in
    clean prose, and the motivation.
  - **Current state** — how it works today, with concrete `file:line`
    references from step 3.
  - **Scope** — what's included, when that isn't obvious from "what & why"
    alone.
  - **Proposed approach** — a concrete, sensible direction; note real
    alternatives only when they matter. Don't design the whole implementation.
  - **Acceptance criteria** — a short checklist of "done".
  - **How to verify** — the concrete steps/commands that prove the acceptance
    criteria hold, when they aren't self-evident from the checklist alone.
  - **Out of scope** — only if needed to head off scope creep.
  - **Constraints worth knowing** — non-obvious limits, conventions, or
    gotchas a cold implementer needs to not violate.
- Keep it tight. A one-line fix gets a few sentences, not a template dump.

### 7. Label

Every issue gets **exactly one type label** from this canonical set — the
industry-standard minimal taxonomy. First ensure each exists in the repo;
create any missing ones with `gh label create` (idempotent — skip existing):

| Label           | Color    | For                                            |
|-----------------|----------|------------------------------------------------|
| `bug`           | `d73a4a` | a defect or regression                         |
| `enhancement`   | `a2eeef` | a new feature or an improvement                |
| `documentation` | `0075ca` | documentation-only work                        |
| `chore`         | `c5def5` | build, CI, dependencies, refactor, maintenance |

Example for a missing label:
`gh label create chore --color c5def5 --description "Build, CI, dependencies, refactor, maintenance"`

Pick the one type label that fits the issue. You may additionally add a single
GitHub-default **meta** label when clearly warranted (`good first issue`,
`help wanted`, `question`) — but never more than one type label, and **never
invent a label outside this canonical set**.

### 8. Create

Create the issue directly, self-assigned to the user:

```
gh issue create --title "<title>" --body-file <tmpfile> --label <label> --assignee @me
```

Write the body to a temp file (or a here-string) so multi-line markdown isn't
mangled by shell escaping. Capture the repo the issue actually landed in right
here — `gh repo view --json nameWithOwner -q .nameWithOwner` — rather than
assuming it later; that value feeds the `--repo` flag in step 9 so the
completion ping can't drift to a different repo than the one just filed into
(fleet-config#497).

### 9. Report

Print the new issue number and URL, a one-line summary of what was filed, and
the label applied.

- **Default:** mention that `/issue-start <N>` will pick it up. Then fire the
  completion ping (canonical format, real issue link) and stop:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind add --issue <N> --repo <owner/name>
  ```

  `<owner/name>` is the value captured in step 8, never re-derived from CWD
  (which may be elsewhere by the time this fires). The helper pulls the title
  + URL from `gh -R <owner/name>`. Silent no-op if no chat is configured;
  always exits 0.
- **One-shot mode (`now`):** do **not** stop and do **not** fire the add ping —
  immediately proceed to the `/issue-start <N> now` flow on the same turn
  (pre-flight, sync main, cut branch, build straight away, per that skill's
  steps 1–6). Skip the plan-approval gate regardless of label, since `now` was
  explicit. Only pause if a step fails or a genuinely expensive/ambiguous
  decision surfaces. The start ping fires at the end of that flow instead.
