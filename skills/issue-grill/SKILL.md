---
name: issue-grill
description: Interview the owner in rounds of numbered decisions, each with a recommended answer, until every decision for a large or fuzzy idea is settled, then show the draft issue for one OK and file it the /issue-add way. Explicit invocation only, e.g. "/issue-grill <idea>", "grill me on this idea before filing".
disable-model-invocation: true
---

# issue-grill

**Capability preflight:** read [workflow-capabilities](E:/automation/fleet-config/docs/workflow-capabilities.md) and bind dispatch, results, waits and questions to this session's actual tools before proceeding.

**Goal:** the thorough sibling of `/issue-add`. Where `/issue-add` files without stopping and asks at most one question, this skill settles every decision for a large or fuzzy idea with the owner first, then files the same well-formed issue. `/issue-add` itself stays unchanged: unattended skills call it.

The round format, the decision-versus-fact split and the recommended-answer rule are in [interview rounds](E:/automation/fleet-config/docs/skills/interview-rounds.md). Read it before the first round.

## Input

Everything after `/issue-grill` is the raw idea, transcript or brain-dump. Nothing pasted → ask for it and stop.

**No human present** (a scheduled or unattended run, or no callable question channel and no reply) → stop before the first round and say so. This skill never files on its own judgement.

## 1. Research, as `/issue-add` does

Run [`/issue-add`](E:/automation/fleet-config/skills/issue-add/SKILL.md) steps 1–4 as written: repo and convention context, the real intent, codebase research, duplicate check. A clear duplicate stops the run: report the existing issue and file nothing.

Facts are your job. Look them up yourself, or with read-only sub-agents on the easy tier (`docs/model-tiers.md`) when the lookup is broad; every sub-agent brief says read-only. Never ask the owner a fact.

## 2. Build the decision tree

From the research, list every open decision the issue body needs, with its prerequisites. Cover each template section from `/issue-add` step 6 that the idea genuinely needs:

- **Scope** and **out of scope**
- **Data shape** or schema, and the data source
- **Error and empty states**
- **Test level** (unit, acceptance, e2e, none) and where the tests go
- **Enforcement**, when the issue changes agent behaviour: the hook, lint, test or code path that makes it hold
- **Owner validation**: whether Roberto must try it, and what he does
- **Acceptance criteria**, each decidable pass/fail
- **Placement**: the target folder from `docs/layout.md` when new files appear
- **Split**: one PR, or Step N/M issues

A section research already settles is not a decision; state it in the draft.

**Terms.** Use the repo's own words for things, taken from its `README.md` and `docs/`. A term in the idea that clashes with the repo's word, or names two things the repo keeps apart, becomes a decision. No glossary file is written.

**Architecture decisions.** A decision that is hard to reverse, would surprise a reader without context, and came out of a real trade-off is a candidate for project-scaffolding's register (`docs/architecture-decisions.md` there). Mark it; step 4 offers it.

## 3. Rounds

Ask every ready decision per round, numbered, each with a recommended answer that "yes" accepts, exactly as the shared reference describes. Look up any fact a reply raises before the next round. Repeat until nothing is open.

## 4. Draft, one OK, file

Write the draft with `/issue-add` step 6 (title style, canonical sections, the `owner_check` classify helper for the owner-validation line). Work that cannot be one PR becomes Step N/M issues, each drafted. Show every draft plus any marked architecture decision as a proposed `adr` issue for project-scaffolding (Context / Decision / Consequences / Status), and ask for one OK.

- **OK** → file each draft with `/issue-add` steps 7–8 (one type label, `--assignee @me`), Step N/M in order, then any approved `adr` issue in project-scaffolding with the `adr` label. Report every number and URL, and that `/issue-start <N>` picks it up. An `adr` issue's register entry is added later, in the PR that applies it.
- **Changes** → apply them, show the changed draft, ask again.
- **No answer** → stop; nothing is filed.

No completion ping: the owner is present for every step.
