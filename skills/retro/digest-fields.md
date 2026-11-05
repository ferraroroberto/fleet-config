# Digest fields

What each field of `digest.py`'s JSON measures, and which finding it usually points at. Every value is a count, a name, an error class or a masked path; the helper never copies prompt text or tool output.

| Field | Measures | Usually points at |
| --- | --- | --- |
| `owner_prompts` | Prompts the owner typed, skill bodies, commands and tool results excluded | Missing information, when high for a run meant to be unattended |
| `turn_seconds` | Summed turn time the harness recorded | Expensive calls |
| `skills` | Skill invocations by name | Which workflow the findings belong to |
| `tool_calls` | Calls per tool | Navigation friction, expensive calls |
| `tool_errors` | Failed results per tool, by class | Mistakes a check could catch |
| `hook_refusals` | Hook refusals by event and tool, plus the hook module when the shim names it | A guard that fired, and a rule the session did not follow |
| `interrupts` | Owner interrupts, and the tool called just before each | The strongest single signal |
| `repeated_identical_calls` | Calls with the same tool and input as an earlier one | Retry loops, wasted calls |
| `lookups_before_first_edit` | Read, Grep and Glob calls before the first edit; `null` when nothing was edited | Navigation friction |
| `repeat_reads` | Files read three or more times, top five | Navigation friction, a missing map or pointer |
| `large_results` | The five largest tool results by size | Expensive calls |
| `private_session` | The session ran inside a private repo; every path is withheld | Private content handling |

## Error classes

`user_rejected`, `hook_refusal`, `edit_mismatch`, `file_state`, `timeout`, `missing_path`, `permission`, `exit_nonzero`, `other`. The first matching class wins, so a timed-out command is `timeout`, not `exit_nonzero`.

## Paths

A path inside the session's repo or a sibling repo under the same parent folder is shown repo-relative (`app-launcher/app/server.py`). A path elsewhere keeps only its file name under `<outside>/`. A path in a private repo, `life-os` by default and more with `--private <repo>`, is `<private>`.

## Harnesses

The helper reads Claude Code's JSONL transcripts. A Codex, Pi or other session the helper cannot read reports `NOT_CONFIRMED`; the running session is then reviewed from its own context with the same six categories.
