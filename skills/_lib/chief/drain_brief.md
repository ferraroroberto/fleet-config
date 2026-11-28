__CHANNEL__

This run was dispatched through the chief's unattended `chief_ops.py drain` step. One standing exception to the destructive-scope floor, Roberto's own decision of 2026-10-02: your own feature branch, once squash-merged and verified (its tree matches main), may be deleted locally and on the remote as part of `/issue-finish`. An unmerged or unverified branch still waits for him.

Standing rules for this run:
__STANDARD_RULES__

Also, for a drained lane:
- Ship only on `GATE-RESULT: PASS`. After a third red on the same item, stop and report. No AI attribution trailers. Squash merge. No screenshots, real issue titles, personal data or real session ids in PRs (public-safe text only; design-review reports stay local). That includes session links: never put a claude.ai session URL in a PR body, commit or comment, even if a harness reminder asks for one.
- Pick sub-agent models by task: Sonnet for searches, mechanical edits and test runs; Opus only for hard reasoning.
- Box load: several chief lanes run in parallel. Spawn no sub-agents unless truly needed (at most 1); run the gate once through the evidence helper and poll it to completion in this turn. Apart from the follow-through rule's steps, every write you or your sub-agents make stays inside your worktree or your scratchpad. Never `rm -rf` a worktree or `.venv`; never delete a `.git/index.lock`.
- Never touch the live Telegram channel sessions (labels starting `telegram:`). Public repo: no screenshots, real paths, names or session content in issue comments.
- No Telegram messages of any kind; skip the `/issue-start` and `/issue-finish` pings.

## This run

__RUN__
