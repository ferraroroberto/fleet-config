---
description: "No argument: list open issues without the audit-meta ledger, never auto-pick."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - `echo "${APP_LAUNCHER_SESSION_ID:-unset}"` prints `unset`.
  - `worktree_claim.py acquire .` prints `MODE=primary`.
  - `git status --porcelain` prints nothing; `git branch --show-current` prints `main`.
  - `gh issue list --state open --json number,title,labels` returns: #12 "Add a dark mode toggle" [enhancement], #14 "prompt-audit ledger" [audit-meta], #15 "Fix the stale cache on reload" [bug].
---

/issue-start
