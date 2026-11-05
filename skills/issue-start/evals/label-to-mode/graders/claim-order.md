---
type: regex
pattern: '^(?:[^`]|`(?!``))*```[^\n]*\n(?:[ \t]*(?:#[^\n]*|[^\n]*(?:APP_LAUNCHER_SESSION_ID|workflow-capabilities)[^\n]*)?\n)*[^\n]*worktree_claim\.py[^\n]*acquire[^\n]*--force-worktree'
match: contains
---

The claim is the first real action: in the answer's first fenced command block, the only lines before `worktree_claim.py acquire … --force-worktree` are the `APP_LAUNCHER_SESSION_ID` check, the capability-preflight read of `workflow-capabilities.md`, blank lines and comments. Any `git`, `gh` or file read before it, or no claim at all, fails. A regex, not an LLM judge: the judge split PASS/FAIL on identical correct orderings, which read as a skill regression that was not one.
