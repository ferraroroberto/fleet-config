# Instruction delta (prose growth as a reported fact)

An agent-behaviour change that lands as new instruction text grows every session's context, and a model can skip it. The enforcing mechanism (a hook refusal, a lint, a test, a code path) holds regardless. `/issue-add` asks a behaviour-changing issue for an **Enforcement** section naming that mechanism, and `/issue-start` builds it ahead of prose.

`skills/_lib/instruction_delta/delta.py` makes the cost of prose visible. It sums `git diff --numstat` against the merge-base with the default branch over the instruction files (`CLAUDE.md`, `AGENTS.md`, `global-CLAUDE.md`, `skills/*/SKILL.md`, `.claude/skills/*/SKILL.md`, `.claude/rules/*.md`), counts new untracked ones by line, and prints one line: `INSTRUCTION_DELTA=+<added> -<removed> net=<n> files=<count>`. A base it cannot resolve prints `INSTRUCTION_DELTA=UNKNOWN reason=...`, never `+0 -0`.

The acceptance run prints the same line beside the instruction ratchet and fails it as NOT CONFIRMED when the base cannot be resolved. `/issue-finish` pastes the line into the PR body. It reports growth and sets no limit: the ratchet already caps rule hits and size per file.
