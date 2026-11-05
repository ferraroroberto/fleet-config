---
name: retro
description: Reviews one agent session for mistakes and friction, labels each finding mechanical (file a check) or judgement (file a rule), proposes the cheapest enforcing home, and files approved findings through /issue-add. Explicit invocation only, e.g. "/retro" for the running session or "/retro path/to/session.jsonl" for a past one.
disable-model-invocation: true
---

# retro

**Goal:** turn one session's mistakes into enforcement. Anything a hook, ratchet or test could decide becomes a proposed check; only a real judgement call becomes a proposed rule line. This skill reads and proposes. Its one write is filing the issues the owner approves, through `/issue-add`; it edits no code, config or instruction file itself.

## Input

- No argument: the running session.
- A transcript path: that session, e.g. an interrupted `/issue-yolo` run.

## 1. Digest

```
E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/retro/digest.py --current
```

Pass the transcript path in place of `--current` for a past session. The JSON holds only counts, tool and skill names, error classes and repo-relative paths; [digest fields](digest-fields.md) says what each field measures. `RETRO_DIGEST=NOT_CONFIRMED` prints its reason: for the running session in a harness the helper does not read, review your own context in step 2 and mark the evidence `from context`; for a named path, stop and report the reason.

## 2. Find

Start from the digest, then read the session where a signal needs its cause: the running session is already in context, a named transcript is read from its file. Interrupts are the strongest signal, so start from what came just before each one. Look for six things:

1. **Navigation friction**: repeat reads, many lookups before the first edit, a file found slowly.
2. **Mistakes a check could catch**: tool errors, hook refusals, red gates. Search the repo's `hooks/`, `tests/` and ratchet config for a check that already covers it, and whether the gate runs it. An existing but unwired check is its own finding.
3. **Standards the review missed**: work that shipped, or nearly did, against the repo's `CLAUDE.md`.
4. **Steering with no effect**: an instruction in context that the session broke anyway.
5. **Expensive calls**: large results, repeated identical calls, long waits.
6. **Missing information**: a fact the owner had to supply, or a question that ended in an interrupt.

## 3. Classify and rank

For each finding:

- **Kind**: `mechanical` when a program could decide it from files, a diff or a tool payload; `judgement` when it needs intent read.
- **Home**: a mechanical finding names a concrete check, such as a hook in `hooks/` with its event and tool, a ratchet rule, a test in a named suite, or the wiring of an existing check. A judgement finding names a `docs/recurring-gotchas.md` entry or one line in the narrowest instruction file that owns the area. A mechanical finding goes to prose only with the reason no check can decide it.
- **Evidence**: digest counts and paths. No quoted prompt, command, tool output or other transcript text.

Rank by cost times recurrence, cheapest home first among equals. Present one table: rank, kind, finding (paraphrased), evidence, home, target repo.

**Private content**: when the digest reports `private_session: true` or a `<private>` path, a finding describes the tooling problem only. What the session was about stays out entirely; content appears as counts or a generic paraphrase, never names, topics or paths.

## 4. Approve, then file

Ask the owner which ranked findings to file, with the session's question tool when one is callable and in plain text otherwise. With no answer, or in an unattended run, stop at the table: nothing is filed. For each approved finding, run `/issue-add` with its table row as the input, one issue per finding, in the repo that owns the home. `/issue-add` checks for duplicates. Report the issue links.
