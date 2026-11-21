Issue: __REF__, an audit-managed findings issue (__KIND__). There is no plan gate for this run: build straight away.

1. Read the issue and the repo's CLAUDE.md. Fix every unchecked finding you can fix within this repo. A finding that turns out wrong or needs the owner: say why in a comment on the issue and leave it unchecked.
2. Verify with the repo's own gate (and its e2e routing via /e2e). Where the repo declares a safe restart recipe, use it after merge and confirm the new build is live.
3. Ship through /issue-finish (squash merge on green). If every finding is fixed and proven on main, the issue may close; otherwise use "Part of" and leave it open with what remains.
4. Delete your own merged branch. No screenshots or images on GitHub. End with a one-paragraph summary; skip the Telegram ping.
