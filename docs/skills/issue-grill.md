# `/issue-grill`

`/issue-grill` is the thorough way to file an issue. `/issue-add` researches and files in one pass and asks at most one question, which suits a clear idea and unattended callers. A large or fuzzy idea needs more: every decision settled with the owner before the issue is written. `/issue-grill` runs that interview, then files the same well-formed issue. It is adapted from the public "grilling" pattern in mattpocock/skills.

The skill carries `disable-model-invocation: true`, so it costs no always-on context and runs only when the owner names it. `/issue-add` is unchanged, because `/issue-yolo`, `/retro` and the scheduled skills call it unattended.

## How it works

1. **Research.** It runs `/issue-add`'s context, intent, research and duplicate steps. A duplicate stops the run.
2. **Decision tree.** Every open decision the issue needs (scope, out of scope, data shape, error and empty states, test level, enforcement, owner validation, acceptance criteria, placement, split) is listed with its prerequisites. Facts are looked up, read-only, never asked. A term that clashes with the repo's README or docs becomes a decision; no glossary file is written.
3. **Rounds.** Each round asks every decision whose prerequisites are settled, numbered, each with a recommended answer phrased so "yes" accepts it. Dependent questions wait for a later round. The format is the shared [interview rounds](interview-rounds.md) reference, which other skills reuse.
4. **One OK.** With nothing open it shows the draft (or the Step N/M drafts) and asks for one OK, then files through `/issue-add`'s label and create steps. A decision that is hard to reverse, surprising without context and the result of a real trade-off is offered as an `adr` issue for project-scaffolding's register.

With no human present it stops before asking anything; silence never files an issue.
