---
description: "A build behind HEAD is reported first; nothing restarts and the diff is not read as not-fixed."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - `perf_review measure home-automation` prints CHECK lines with two budgets over, `DIFF` against the last run showing no improvement, `BUILD state=behind behind_by=3`, a `BUILD_WARNING`, and `PERF=over-budget`.
  - home-automation's CLAUDE.md restart recipe is `tray.bat --restart`.
---

/perf-review home-automation
