# PR-body check (one checker for the rules a PR body must keep)

`skills/_lib/pr_body/check.py --pr <PR> --issue <N> [--verdicts <file>]` reads the opened PR through `gh` and holds every body rule that used to be prose. `/issue-finish` step 4 runs it right after `gh pr create` or `gh pr edit` and stops on a refusal; each `REFUSED <rule>: <fix>` line names what to change.

| Rule | Refused when |
|---|---|
| `closing-refs` | GitHub's own `closingIssuesReferences` is not exactly the branch's issue, or is empty without a `Part of #N` / `Progresses #N` line. The keyword matches on substrings, so the prose cannot decide |
| `images` | image markdown, `<img>`, or a GitHub attachment URL. A private repo passes `--allow-images` |
| `attribution` | a claude.ai session link or id, a `Generated with Claude Code` line, a `Co-Authored-By` or `Claude-Session` trailer naming an AI |
| `merge-danger` | no well-formed `**Merge danger:**` line ([merge-danger](merge-danger.md)) |
| `ticked-box` | a ticked box matches no `DONE` verdict in the `acceptance_audit.py` verdicts file and, if it names the gate, `gate_evidence.py check` is not `FRESH` |

Code fences and inline code are not scanned, so a rule can be quoted. Exit 0 passes, 1 refuses, 3 is NOT CONFIRMED: the PR, the verdicts file or a gate record a box leans on could not be read, which is never a pass. The check is forward-only; it reads one PR and keeps no baseline.
