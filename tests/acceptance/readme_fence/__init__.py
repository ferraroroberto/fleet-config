"""The fenced-block half of the README/docs history check (fleet-config#1697).

`scripts/docs_ratchet.py` is vendored byte-for-byte from project-scaffolding
and by design never scans inside a fenced code block. `readme_fence_refs`
is the local, non-vendored complement: it scans a Markdown file's fenced
blocks for issue-number references, the exact gap the README's `## Layout`
tree fell into.
"""
