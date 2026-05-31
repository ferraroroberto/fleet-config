---
name: issue-add
description: Turn a rough idea, brain-dump, or transcript into a well-formed, self-contained GitHub issue — researches the codebase for context, drafts it the way a senior developer would, labels it, self-assigns, and creates it. Use when capturing new work, e.g. "/issue-add <paste your idea or transcript>" or "/issue-add now <idea>" to file the issue and start building it in one shot. Pairs with /issue-start and /issue-finish.
---

# issue-add

**Goal:** Take whatever the user pastes — a clean idea, a rambling brain-dump, a
raw voice transcript — and file one well-formed GitHub issue that a senior
developer would be happy to have written: self-contained, researched, correctly
scoped, and ready to hand off cold to an LLM or a human.

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
- Read the project's `CLAUDE.md` and `README.md` — layout, conventions, what the
  project is and how the change being filed actually interacts with the code.
- `gh label list` — compare against the canonical type set in step 7; any
  missing label gets created there.

Do **not** scan past issues to "learn the house style" — the canonical style is
defined in this skill (step 6) and that's the only source of truth. Reading
prior issues adds context noise and drifts toward whatever was filed last
instead of the intended format.

### 2. Extract the real intent

The pasted text may be messy or a garbled dictation. Work out what the user
actually wants — the underlying feature, bug, or change — not the literal words.
Don't ask a question yet; research usually resolves apparent ambiguity.

### 3. Research the codebase

This is the core of the skill. Find and read the code the idea touches:
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
change what gets built — ask one sharp question (AskUserQuestion). Otherwise
proceed. Never ask about anything research already answered.

### 6. Draft the issue

Write it the way a senior developer would — proportionate to the work, no
over-engineering, no padding.

- **Title:** `<Area>: <concise description>` — e.g.
  `Coding tab: rename a running session from the app`,
  `audio/transcribe: handle empty whisper response`. Lowercase verb after the
  colon, no trailing period, ≤72 chars. This is the canonical style — don't
  imitate older issues if they diverge.
- **Body:** self-contained and LLM-handoff-ready. Use as many of these sections
  as the issue genuinely needs — a tiny issue needs only the first two:
  - **What & why** — the goal in clean prose, and the motivation.
  - **Current state** — how it works today, with concrete `file:line`
    references from step 3.
  - **Proposed approach** — a concrete, sensible direction; note real
    alternatives only when they matter. Don't design the whole implementation.
  - **Acceptance criteria** — a short checklist of "done".
  - **Out of scope** — only if needed to head off scope creep.
- Keep it tight. A one-line fix gets a few sentences, not a template dump.

### 7. Label

Every issue gets **exactly one type label** from this canonical set — the
industry-standard minimal taxonomy. First ensure each label exists in the repo;
create any that's missing with `gh label create` (idempotent — skip the ones
already present):

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

Write the body to a temp file (or use a here-string) so multi-line markdown
isn't mangled by shell escaping.

### 9. Report

Print the new issue number and URL, a one-line summary of what was filed, and
the label applied.

- **Default:** mention that `/issue-start <N>` will pick it up. Then fire the
  completion ping (canonical format, real issue link, suppresses the follow-up
  idle ping) and stop:

  ```
  py C:/Users/rober/.claude/hooks/notify_complete.py --kind add --issue <N>
  ```

  Pass only the issue number; the helper pulls the title + URL from `gh`. Silent
  no-op if no channel is configured; always exits 0.
- **One-shot mode (`now`):** do **not** stop and do **not** fire the add ping —
  immediately proceed to the `/issue-start <N> now` flow on the same turn
  (pre-flight, sync main, cut branch, build straight away, per that skill's
  steps 1–6). Skip the plan-approval gate regardless of label, since `now` was
  explicit. Only pause if a step fails or a genuinely expensive/ambiguous
  decision surfaces. The start ping fires at the end of that flow instead.
