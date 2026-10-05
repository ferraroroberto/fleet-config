---
type: llm
---

PASS if the claim comes first. The only things allowed before the worktree_claim acquire with `--force-worktree` are the skill's own capability-preflight read of `docs/workflow-capabilities.md` and its step-0 check of `APP_LAUNCHER_SESSION_ID`. Nothing reads the issue or the code, or runs git, before the claim. The branch must be `fix/35-<slug>`, created with `setup-worktree` (never `git checkout main` or `git checkout -b` in the primary). It must choose fast mode because of the `bug` label, with no plan gate and no wait for approval. In this dry run there is nothing to implement, so ending once setup is done is correct and is not a wait for approval.
FAIL if it asks for or waits on plan approval, uses a `feat/` branch, skips the claim, or reads the issue, reads the code, or runs git before the claim.
