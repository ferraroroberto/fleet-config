---
description: "A bug-labelled issue in a launcher session: claim with --force-worktree, fix/ branch in a worktree, fast mode."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - `echo "${APP_LAUNCHER_SESSION_ID:-unset}"` prints `9f1c0a2b`.
  - `worktree_claim.py acquire . --issue 35 --force-worktree` prints `MODE=worktree`.
  - `git rev-parse --is-inside-work-tree` prints `true`.
  - `gh issue view 35` shows an open issue titled "Terminal reconnect drops the session" with the label `bug`.
  - `worktree_claim.py setup-worktree . 35 <branch>` prints `WORKTREE=E:/automation/demo-wt-35`.
  - `active_issue.py add` prints `ACTIVE_ISSUE=added`.
---

/issue-start 35
