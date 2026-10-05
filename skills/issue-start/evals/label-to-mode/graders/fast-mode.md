---
type: llm
---

Judge only the mode and the branch; the order of the claim is graded elsewhere.
PASS if it chooses fast mode because of the `bug` label, with no plan gate and no wait for approval, and the branch is `fix/35-<slug>`, created with `setup-worktree`. In this dry run there is nothing to implement, so ending once setup is done is correct and is not a wait for approval.
FAIL if it asks for or waits on plan approval, uses a `feat/` branch, or runs `git checkout main` or `git checkout -b` in the primary instead of `setup-worktree`.
