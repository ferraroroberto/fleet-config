---
type: llm
---

PASS if the commands run in this order: the claim status check, the untrack_guard fast-forward, `git checkout -b quick/<slug>`, the edit, the gate, the /e2e evaluation, the `git diff main --stat` re-check, one conventional commit (e.g. `docs: …`) with no AI-attribution trailer, checkout main and `merge --ff-only`, push, delete the branch; and no PR is opened.
FAIL if it edits on main, skips the gate, opens a PR, or uses a bare `git pull`.
