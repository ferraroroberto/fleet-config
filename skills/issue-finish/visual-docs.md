# Visual-docs refresh (`/issue-finish` step 2b, non-empty `STALE`)

Loaded on demand from `/issue-finish` step 2b when `docs_shots_plan.py check` printed a `MANIFEST=<path>` with a non-empty `STALE`. Do this inside the finish run, then return to step 3.

A non-empty `STALE` → run the full `/docs-shots` judgment + propose-then-capture
flow (`skills/docs-shots/SKILL.md`) **inside this run**: show the stale set +
reasons, wait for the user's explicit OK, and on approval invoke the repo's own
doc-capture engine for just those features, fold the result into the finish
summary. Also surface any `UNMAPPED` changed files for the user to add a
manifest entry for — never guess one. On decline, note the refresh was skipped
and continue to step 3 — never block the finish over it.
