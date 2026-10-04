---
description: "A one-line typo goes through the claim check, an ephemeral branch, the gate and a fast-forward."
max_turns: 8
allowed_tools: [Skill]
append_system_prompt: |
  Procedure eval, dry run. No shell, file or network tool is available, so run nothing. Follow the skill's instructions as if you were executing them, using the facts below as the output each command would print. Answer with: (1) the exact commands you would run, in order, one per line inside a single fenced code block; (2) after the block, each decision you made and the instruction that drove it, one line each. Stop where the skill says to stop.
  
  Facts:
  - On the default branch `main`, working tree clean.
  - `worktree_claim.py status .` prints `CLAIM=free`.
  - `untrack_guard.py fast-forward .` prints `FF=done`.
  - The fix is one line in README.md. The repo's CLAUDE.md gate is `scripts/verify.ps1`; it passes.
  - The /e2e evaluation routes `skip` (docs-only diff).
  - `git diff main --stat` shows 1 file changed, 1 insertion, 1 deletion.
  - The repo has no tray or web surface.
---

/quick fix the typo 'recieve' in README.md
