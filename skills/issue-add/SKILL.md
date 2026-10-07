---
name: issue-add
description: Turn a rough idea, brain-dump, or transcript into a well-formed GitHub issue — researches the codebase, drafts it as a senior developer would, labels it, self-assigns, creates it. E.g. "/issue-add <paste your idea or transcript>" or "/issue-add now <idea>" to file and start building in one shot. Pairs with /issue-start and /issue-finish.
---

# issue-add

**Capability preflight:** read [workflow-capabilities](../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional Claude examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** Turn whatever the user pastes (idea, brain-dump, voice transcript) into one well-formed GitHub issue: self-contained, researched, correctly scoped, ready to hand off cold to an LLM or human.

The issue is **created directly** once drafted — no approval checkpoint.

## Arguments

Everything after `/issue-add` is the raw input. Nothing pasted → ask the user to paste it and stop until they do.

The word `now` anywhere in the args (`/issue-add now <text>`, `/issue-add <text> now`) → **one-shot mode**: after creation, proceed straight to the `/issue-start <N> now` flow (claim the repo, sync main, cut the branch, build) with no stop. Strip the `now` token before treating the rest as the issue text.

## Steps

Run in order. If a step fails, print a short error and stop.

### 1. Repo + convention context

In parallel:
- `git rev-parse --is-inside-work-tree` — must be `true`, else stop: "Not inside a git repository."
- Read the project's `CLAUDE.md` and `README.md` — layout, conventions, how the change interacts with the code.
- `gh label list` — compare against the canonical type set in step 7; missing labels get created there.

Do **not** scan past issues for "house style" — step 6 is the only source of truth; prior issues drift toward whatever was filed last.

### 2. Extract the real intent

The text may be messy or garbled dictation. Work out what the user actually wants, not the literal words. Don't ask yet; research usually resolves ambiguity.

### 3. Research the codebase

Find and read the code the idea touches:
- Which files / modules / functions are involved, and how they behave now.
- Constraints, conventions, and patterns the change must respect.
- Anything that makes the idea harder or different than it first sounds.

Gather enough that the issue can be picked up **cold**.

### 4. Check for duplicates

Scan open issues (`gh issue list --state open`) for one already covering the same thing. Clear duplicate → **don't create**; tell the user the existing issue number and stop.

### 5. Decide if a question is needed

Only if a **substantive** ambiguity remains after research — one that would change what gets built — ask one sharp question (the contract’s available user-input channel). Never ask what research already answered.

### 6. Draft the issue

Write it as a senior developer would — proportionate, no over-engineering, no padding.

- **Title:** `<Area>: <concise description>` — e.g. `Coding tab: rename a running session from the app`, `audio/transcribe: handle empty whisper response`. Lowercase verb after the colon, no trailing period, ≤72 chars. Canonical style — don't imitate older issues that diverge.
- **Body:** self-contained and LLM-handoff-ready. The **one canonical section list** for an issue body fleet-wide (fleet-config#446). Use as many as the issue needs — a tiny issue needs only the first two:
  - **What & why** (or **Symptom** + **Root cause** for a bug) — goal in clean prose, and motivation.
  - **Current state** — how it works today, with concrete `file:line` references from step 3.
  - **Scope** — what's included, when not obvious from "what & why".
  - **Proposed approach** — a concrete direction; note alternatives only when they matter. Don't design the whole implementation.
  - **Acceptance criteria** — a short "done" checklist. Each criterion must be decidable pass/fail by a reader with the repo in hand; "works correctly" / "is robust" is not a criterion.
  - **How to verify** — concrete steps/commands proving the criteria hold, when not self-evident.
  - **Out of scope** — only to head off scope creep.
  - **Constraints worth knowing** — non-obvious limits, conventions, gotchas a cold implementer must not violate.
- Keep it tight. A one-line fix gets a few sentences, not a template dump.

### 7. Label

Every issue gets **exactly one type label** from this canonical set. Ensure each exists in the repo; create missing ones with `gh label create` (idempotent — skip existing):

| Label           | Color    | For                                            |
|-----------------|----------|------------------------------------------------|
| `bug`           | `d73a4a` | a defect or regression                         |
| `enhancement`   | `a2eeef` | a new feature or an improvement                |
| `documentation` | `0075ca` | documentation-only work                        |
| `chore`         | `c5def5` | build, CI, dependencies, refactor, maintenance |

Example for a missing label:
`gh label create chore --color c5def5 --description "Build, CI, dependencies, refactor, maintenance"`

Pick the one type label that fits. You may add a single GitHub-default **meta** label when clearly warranted (`good first issue`, `help wanted`, `question`) — never more than one type label, and **never invent a label outside this canonical set**.

### 8. Create

Create the issue directly, self-assigned to the user:

```
gh issue create --title "<title>" --body-file <tmpfile> --label <label> --assignee @me
```

Write the body to a temp file (or here-string) so shell escaping can't mangle the markdown. Capture the repo here — `gh repo view --json nameWithOwner -q .nameWithOwner` — it feeds step 9's `--repo` so the completion ping can't drift to another repo (fleet-config#497).

### 9. Report

Print the new issue number and URL, a one-line summary, and the label applied.

- **Default:** mention that `/issue-start <N>` will pick it up. Then fire the completion ping (canonical format, real issue link) and stop:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py --kind add --issue <N> --repo <owner/name>
  ```

  `<owner/name>` is the step-8 value, never re-derived from CWD. The helper pulls title + URL from `gh -R <owner/name>`. Silent no-op if no chat is configured; always exits 0.
- **One-shot mode (`now`):** do **not** stop and do **not** fire the add ping — proceed to the `/issue-start <N> now` flow on the same turn (claim the repo, pre-flight, sync main, cut branch, build, per that skill's steps 0–6 — step 0's `worktree_claim.py acquire` first). Skip the plan-approval gate regardless of label (`now` was explicit). Pause only if a step fails or a genuinely expensive/ambiguous decision surfaces. The start ping fires at the end of that flow instead.
