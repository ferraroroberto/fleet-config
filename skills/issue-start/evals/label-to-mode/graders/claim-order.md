---
type: llm
---

Judge only the order of the first steps; the mode and the branch are graded elsewhere.
PASS if the `worktree_claim.py acquire` with `--force-worktree` is the first real action: the only things before it are the check of `APP_LAUNCHER_SESSION_ID` and the skill's own capability-preflight read of `docs/workflow-capabilities.md`. Nothing in the answer reads the issue, the code, `CLAUDE.md` or `README.md`, or runs git, before the claim. Reads that come after the claim are fine.
FAIL if the answer says it reads the issue, the code, `CLAUDE.md` or `README.md` before the claim, runs git or `gh` before it, or omits the claim.
