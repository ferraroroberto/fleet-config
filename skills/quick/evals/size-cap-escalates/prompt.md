---
description: "A change over the size cap escalates to the issue workflow and lands nothing."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - On the default branch `main`, working tree clean.
  - `worktree_claim.py status .` prints `CLAIM=free`.
  - The rename touches 9 files and about 140 lines; no new files.
---

/quick rename the config loader to settings loader everywhere
