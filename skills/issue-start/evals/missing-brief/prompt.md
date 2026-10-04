---
description: "A --brief path that does not exist stops the run before any branch."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - `echo "${APP_LAUNCHER_SESSION_ID:-unset}"` prints `unset`.
  - `worktree_claim.py acquire . --issue 35` prints `MODE=primary`.
  - Reading `E:/automation/app-launcher/webapp/briefs/missing.md` fails: the file does not exist.
---

/issue-start 35 --brief E:/automation/app-launcher/webapp/briefs/missing.md
