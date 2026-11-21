Issue: __REF__, a red-main issue (__KIND__): the repo's nightly gate failed on `main`. There is no plan gate for this run: build straight away.

1. Read the issue and its newest comment for the failing run, then the repo's CLAUDE.md. Read the failed step with `gh run view <run-id> --log-failed` (no run id in the comment: read the failing step and test names it quotes).
2. Fix the cause in this repo, with a regression test proven to fail on the pre-fix code where the failure is a code bug. If the cause is in project-scaffolding's workflow rather than this repo, change nothing here: comment on the issue naming the failing step and why, then stop.
3. Never re-run a workflow and never mark the issue fixed on a local pass alone: the next nightly run is the proof. Verify with the repo's own gate (and its e2e routing via /e2e).
4. Ship through /issue-finish (squash merge on green) with "Part of" in the PR body, not "Closes": the red-main issue stays open until a nightly run passes. Where the repo declares a safe restart recipe, use it after merge and confirm the new build is live.
5. Delete your own merged branch. No screenshots or images on GitHub. End with a one-paragraph summary; skip the Telegram ping.
