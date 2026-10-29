"""Instruction-line delta: the net growth of the instruction files a branch carries.

`delta` measures it from `git diff --numstat` against the merge-base with the default
branch and renders the one `INSTRUCTION_DELTA=` line the acceptance gate prints and
`/issue-finish` pastes into the PR body. Report only. stdlib + the shared git wrapper only.
"""
