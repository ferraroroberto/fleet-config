# Debug tags (temporary debug output never merges)

A bug diagnosis in `/issue-start` adds temporary logs while it tests its ranked causes. Each one carries a single tag, `[DEBUG-<id>]` with a concrete id of letters and digits, one id per diagnosis, so cleanup is one search and nothing leaks into the merge.

`skills/_lib/debug_tags/check.py [repo]` scans the working tree against the merge-base with the default branch: committed, staged, unstaged and new untracked files all count. It prints one of three states, each with its own exit code:

- `DEBUG_TAGS=clean` (exit 0): no added line carries a tag.
- `DEBUG_TAGS=FOUND count=<n>` plus one `FOUND=<path>:<line>` per hit (exit 1): delete those lines and re-run.
- `DEBUG_TAGS=NOT_CONFIRMED reason=<text>` (exit 2): no merge-base, a git failure or an unreadable file. It is never read as clean, and the finish stops.

`/issue-finish` runs it before `gh pr create`, beside the merge-danger check. Only lines the branch adds are scanned, so a tag already on the default branch is not this branch's to remove. The placeholder `[DEBUG-<id>]` never matches, which lets docs and skill text name the convention.
