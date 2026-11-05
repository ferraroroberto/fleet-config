# `/retro`

`/retro` looks back over one agent session and turns what went wrong into enforcement proposals. The fleet's rule is mechanism before prose: a mistake a program could catch becomes a hook, ratchet or test, and only a judgement call becomes an instruction line. `/insights-weekly` diffs a weekly aggregate and `/prompt-audit` lints instruction text; neither reviews one session's mistakes, which is the gap this skill fills.

It runs on the current session (`/retro`) or on a past transcript (`/retro <path>`). The skill carries `disable-model-invocation: true`, so it costs no always-on context and runs only when asked by name; Pi honours the same key.

## How it works

1. **Digest.** `skills/_lib/retro/digest.py` reduces the transcript to counts, tool and skill names, error classes and repo-relative paths: tool calls and errors per tool, hook refusals by event, owner interrupts and the tool before each, repeated identical calls, repeat reads, lookups before the first edit, and the largest results. It copies no prompt text and no tool output, so nothing a person typed can reach a filed issue. A path in a private repo (`life-os` by default) is `<private>`, and a session run inside one withholds every path. A transcript it cannot find, read or recognise prints `RETRO_DIGEST=NOT_CONFIRMED` and exits 2 rather than an empty digest that would read as a clean session. The field reference is `skills/retro/digest-fields.md`.
2. **Find.** The skill reads the digest, then the session where a signal needs its cause, looking for navigation friction, mistakes a check could catch (and whether such a check exists but is not wired into the gate), standards the review missed, steering text with no effect, expensive calls, and missing information.
3. **Classify and rank.** Each finding is `mechanical` or `judgement`. A mechanical finding names a concrete check home; a judgement finding names a `docs/recurring-gotchas.md` entry or one line in the narrowest owning instruction file. Evidence is counts and paths only.
4. **Approve, then file.** The owner picks which findings to file; each approved one goes through `/issue-add`, which handles duplicates and the issue template. Unattended, or with no answer, it stops at the table and files nothing. It never edits code, config or instruction files itself.

The helper reads Claude Code's JSONL transcripts. A Codex, Pi or other session it cannot read is reviewed from the running session's own context with the same categories.

Tests: `tests/audit/test_retro_digest.py` (synthetic transcripts; privacy, error classes, path masking, not-confirmed states).
