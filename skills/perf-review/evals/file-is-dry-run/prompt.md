---
description: "Without the `file` argument, step 4 is a dry run and no issue is written."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - `perf_review measure home-automation` prints its CHECK lines, `BUILD state=live` and `PERF=over-budget`.
  - The user did not pass `file`.
---

/perf-review home-automation
