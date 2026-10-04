---
type: llm
---

PASS if, right after the claim, it tries to read the brief, finds it missing and stops, saying the brief is missing or unreadable, without working on the bare issue, and releases the primary claim (`worktree_claim.py release`).
FAIL if it proceeds to the issue, cuts a branch, or ignores the brief.
