# Digest fields

What each field of `digest.py`'s JSON measures, and which finding it usually points at. Every value is a count, a name, an error class or a masked path; the helper never copies prompt text or tool output.

| Field | Measures | Usually points at |
| --- | --- | --- |
| `agent` | The harness that wrote the log: `claude`, `codex` or `pi` | Which harness's tooling a finding belongs to |
| `repo` | The fleet repo the session ran in, from its working folder with a `-wt-<N>` worktree suffix folded in; `<private>` for a private repo, `unknown` with no working folder | Where to file a finding |
| `unparsed_lines` | Non-blank log lines that were not a JSON object | A log the digest only partly read |
| `owner_prompts` | Prompts the owner typed, skill bodies, commands and tool results excluded | Missing information, when high for a run meant to be unattended |
| `turn_seconds` | Summed turn time the harness recorded; `null` when it recorded none (Pi) | Expensive calls |
| `skills` | Skill invocations by name | Which workflow the findings belong to |
| `tool_calls` | Calls per tool | Navigation friction, expensive calls |
| `tool_errors` | Failed results per tool, by class | Mistakes a check could catch |
| `error_signatures` | Failed results per signature, `tool`, `class` and `template` joined by a pipe (below) | The same mistake recurring, within and across sessions |
| `ratchet_rules` | `<name>_ratchet:<rule>` per failing ratchet a failed result's output names; rule `-` when it names none | A convention the session broke, and the check that caught it |
| `hook_refusals` | Hook refusals by event, tool and hook module, `unknown` when the text names none; a call two hooks refused counts under each | A guard that fired, and a rule the session did not follow |
| `interrupts` | Owner interrupts, and the tool called just before each | The strongest single signal |
| `repeated_identical_calls` | Calls with the same tool and input as an earlier one | Retry loops, wasted calls |
| `lookups_before_first_edit` | Read, Grep and Glob calls before the first edit; `null` when nothing was edited | Navigation friction |
| `repeat_reads` | Files read three or more times, top five | Navigation friction, a missing map or pointer |
| `large_results` | The five largest tool results by size | Expensive calls |
| `private_session` | The session ran inside a private repo; every path is withheld | Private content handling |

## Error classes

`user_rejected`, `hook_refusal`, `edit_mismatch`, `file_state`, `timeout`, `missing_path`, `permission`, `exit_nonzero`, `other`. The first matching class wins, so a timed-out command is `timeout`, not `exit_nonzero`.

## Error signatures

A signature is `tool|class|template`. The template is taken only from text a harness, hook or ratchet writes itself: the refusing hook for a `hook_refusal`, the first ratchet rule for a gate failure, a harness's own error wording on the result's first line (`<tool_use_error>...`, Pi's `Could not find the exact text in ...`), or a fixed exit line anywhere in it (`Exit code 2`, `Command exited with code 1`). Otherwise it is the span the error class matched, or `-` for `other`. Paths, numbers, ids and quoted strings are masked, so two errors that differ only there share a signature. Free model or user text never enters it.

## Paths

A path inside the session's repo or a sibling repo under the same parent folder is shown repo-relative (`app-launcher/app/server.py`). A path elsewhere keeps only its file name under `<outside>/`. A path in a private repo, `life-os` by default and more with `--private <repo>`, is `<private>`.

## Harnesses

The helper reads Claude Code, Codex and Pi session logs into this one shape. `--current` finds only a running Claude Code session; pass a Codex or Pi log by path. A log no adapter recognises reports `NOT_CONFIRMED` with reason `unmeasured: ...`; a running session the helper cannot read is reviewed from its own context with the same six categories.
