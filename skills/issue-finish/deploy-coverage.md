# Deploy-coverage check (`/issue-finish` step 6b)

Loaded on demand from the `/issue-finish` skill (`SKILL.md`, step 6b), right after step 6's restart.

A merged PR and a restarted process only prove the component the restart
actually touched is live — not every runtime component the repo owns.
Convention + declared shape: `project-scaffolding#199`/`#200`; reference
implementation: `app-launcher#615`. Trigger is deterministic:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/deploy_coverage.py check .
```

- `DECLARED=no` → **no-op** (a repo with no `## <component>` block carrying a
  `liveness signal:` bullet in its `CLAUDE.md`). Skip straight to step 7 and
  say nothing — must never get slower or noisier for a repo that hasn't
  declared anything.
- `DECLARED=yes` with every component `TOUCHED=no` → still a no-op, but
  **state it** in the step-7 summary (`deploy-coverage: N component(s)
  declared, none touched by this diff`).
- `DECLARED=yes` with a component `TOUCHED=yes` **or** `TOUCHED=unknown` (no
  parseable path in its declaration — treat as touched, never as a silent
  "no") → check that component's printed `LIVENESS` field against the actual
  running target. Where step 6 already polled a build-identity endpoint (e.g.
  `GET /api/version`) as part of the restart, read the matching sub-block out
  of that same response — don't issue a second request. Three outcomes, only
  wording changes; **never** block, hang, or prompt for confirmation, and
  never skip closing the issue or merging over this check:
  - **Live** (field confirms loaded build matches current `HEAD`) → step 7
    states `confirmed live: <component>`.
  - **Stale** (field says otherwise) → step 7 states **`merged but not yet
    live: <component> — requires <UPDATE_CMD>`**, never "shipped".
  - **Unresolvable** (endpoint unreachable, no matching field, or no local
    restart happened at all to poll from) → step 7 states **`unknown:
    <LIVENESS> could not be checked`** — never assume fine.
- Do **not** invoke `UPDATE_CMD` yourself. Where the declaration says it's
  confirmation-gated or destructive (e.g. a manual session-host restart that
  would kill live PTYs), it is a human/operator action only — report what's
  needed, don't attempt it.
