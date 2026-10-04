---
type: llm
---

PASS if the first command is the worktree_claim acquire with `--force-worktree`, the branch is `fix/35-<slug>` created with `setup-worktree` (never `git checkout main` or `git checkout -b` in the primary), and it says it builds straight away in fast mode because of the `bug` label, without waiting for plan approval.
FAIL if it asks for plan approval, uses a `feat/` branch, or skips the claim.
