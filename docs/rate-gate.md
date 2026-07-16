# Session-rate-limit self-pacing (`rate_gate.py`)

## Why this replaced the dead-man's switch

`/audit-fleet` used to survive the rolling 5-hour session rate limit with a
"dead-man's switch" (`skills/_lib/audit_retry.py`): before the heavy dispatch
phase it armed a one-shot Windows Scheduled Task ~4h out, so if the process died
mid-sweep from hitting the limit, the task relaunched the audit as a `resume`
continuation, and the idempotent per-repo ledger gate skipped work already done.
That design existed because there was genuinely no way to read the live session
usage % from a headless `claude -p` run — Claude Code fed
`rate_limits.five_hour.used_percentage` to the statusline via stdin JSON only at
TUI render time, and never persisted it to disk.

That constraint no longer holds. `statusline-command.ps1` now caches
`rate_limits.five_hour` / `seven_day` (`used_percentage` + `resets_at`) plus a
`captured_at` stamp to `~/.claude/hooks/state/rate-limits.json` on **every**
statusline render (fleet-config#259 / app-launcher#326), specifically so a
non-statusline process can read current usage without being the statusline
itself. `rate_gate.py` (fleet-config#261) reads that cache and lets a skill
**self-pace proactively** — pause before hitting the wall, wait in place, resume
— instead of dying and hoping a scheduled relaunch picks up the pieces next week.

## `skills/_lib/rate_gate.py` contract

Pure logic, unit-tested in `tests/test_rate_gate.py` (same discipline as
`audit_issue.py` / the retired `audit_retry.py`: correctness-critical decisions
live in Python, not the model):

```
decide(cache: dict, now: datetime, threshold_pct: float = 70.0,
       max_age_seconds: int = 1800) -> Decision
```

- **`UNKNOWN`** — the cache file is missing, unreadable, or its `captured_at` is
  older than `max_age_seconds` (nothing rendered a statusline recently — the
  normal case for an unattended overnight run with no interactive session open).
  **Treated as OK, proceed** — there's no signal of a problem, so don't invent one.
- **`OK`** — `five_hour.used_percentage` is present and below `threshold_pct`.
- **`PAUSE`** — `five_hour.used_percentage >= threshold_pct`. Carries the
  window's `resets_at` and a computed `wait_seconds` (`resets_at − now` plus a
  small buffer; a bounded fallback wait if `resets_at` is missing).

CLI: `rate_gate.py check [--threshold 70] [--state-dir <path>]` prints
`DECISION=OK|PAUSE|UNKNOWN`, `USED_PCT=<n|null>`, `RESETS_AT=<iso|null>`,
`WAIT_SECONDS=<n|null>` for the calling skill to branch on.

The cache path resolves via `CLAUDE_HOOKS_STATE_DIR` first, falling back to
`~/.claude/hooks/state` — the same resolution `hooks/session_state.py`'s
`state_file()` uses, so both stay overridable the same way in tests.

## How a skill waits on `PAUSE`

Chained short `sleep` calls are explicitly disallowed (they're a workaround for
the same thing a real polling primitive should do). The sanctioned mechanism is
the `Monitor` tool's until-loop pattern, polling against the wall-clock
`resets_at` target — e.g. `until [ "$(date -u +%s)" -ge <target_epoch> ]; do
sleep 300; done`. After the wait, re-run `rate_gate.py check` and resume
dispatch; if it still reads `PAUSE` (the reset landed later than expected, or
another process consumed the fresh window first), loop again.

## Who calls it

- **`/audit-fleet`** — before each dispatch/refill of its ≤3-wide sub-agent
  window (a check that's a session-budget pacing default and, since `hard`
  tier resolves to Opus again, also the live Opus-burst-limiter cap — see
  `docs/model-tiers.md`). On `PAUSE`, stop
  dispatching new sub-agents (let in-flight ones finish), wait via the pattern
  above, then resume. The same handling applies **reactively** if a sub-agent
  failure still carries a rate-limit signature despite the proactive check (a
  stale or missing cache) — pause and wait-until-reset, then resume, rather than
  deferring the rest of the fleet to next week. A run therefore always completes
  to one full digest in a single (possibly paused-and-resumed) execution — there
  is no more `IS_FINAL`/`DEFERRED`/partial-digest branching, no scheduled
  relaunch, no `resume` argument.
- **`/cleanup-fleet`** — a single check before its "spawn every easy-tier agent
  in one message" fan-out; on `PAUSE`, wait once before firing the batch.

## What this deliberately does not change

Widening `/audit-fleet`'s ≤3 concurrency window is out of scope here — that's a
separate, empirically-driven follow-up once someone actually observes a run's
wall-clock cost under the new self-pacing design and decides it's worth testing
a wider window.
