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
       max_age_seconds: int = 1800, unattended: bool = False) -> Decision
```

- **No signal** — the cache file is missing or unreadable (`cache_missing`), its
  `captured_at` is older than `max_age_seconds` (`cache_stale`: nothing rendered
  a statusline recently), or it carries no `five_hour.used_percentage`
  (`usage_missing`). What happens next depends on who is attending:
  - **Interactive → `UNKNOWN`, proceed.** Someone is watching the session and
    the statusline is usually rendering, so this is a transient gap.
  - **Unattended → `PAUSE`, never proceed.** `USED_PCT=null`,
    `RESETS_AT=null`, `WAIT_SECONDS=600` (`UNKNOWN_WAIT_SECONDS`): the caller
    waits and re-reads on its normal pause path, and its 3-cycle cap ends the
    run as `SKIPPED (session limit — exceeded pause retries)` if a signal never
    appears. See "Unattended runs" below.
- **`OK`** (`below_threshold`) — `five_hour.used_percentage` is present, fresh
  and below `threshold_pct`.
- **`PAUSE`** (`over_threshold`) — `five_hour.used_percentage >= threshold_pct`.
  Carries the window's `resets_at` and a computed `wait_seconds` (`resets_at − now`
  plus a small buffer; a bounded fallback wait if `resets_at` is missing).
  Unattended, that wait is capped under the stall watchdog; see "Unattended
  waits vs the stall watchdog" below.

CLI: `rate_gate.py check [--threshold 70] [--state-dir <path>] [--unattended]`
prints `DECISION=OK|PAUSE|UNKNOWN`, `REASON=<reason above>`,
`MODE=interactive|unattended`, `USED_PCT=<n|null>`, `RESETS_AT=<iso|null>`,
`WAIT_SECONDS=<n|null>`, `WATCHDOG_SECONDS=<n|off|unknown|null>` for the
calling skill to branch on and name in its digest. Mode is unattended when `FLEET_SCHEDULED_RUN=1` is in the environment
(`scheduled_runner.py` exports it to every child it launches, so every
`run-weekly.bat` job gets it) or `--unattended` is passed. No flag makes an
unattended run less cautious.

## Unattended runs: fail-open re-decided (fleet-config#825)

The original design (#261) treated `UNKNOWN` as OK everywhere, reasoning that
no signal is no evidence of a problem. That tradeoff was tested in production
and lost on **2026-09-10**: the scheduled `codebase-audit-fleet` run ran 54
minutes on `UNKNOWN`, hit the 5-hour session limit mid-flight, lost 13
sub-agents in the same second, delivered no digest, and took
`design-sweep-fleet` and `cleanup-fleet-all-weekly` down behind it in the chain.
The reactive rate-limit-signature fallback didn't save it either, because the
limit killed the sub-agents outright rather than surfacing a per-repo failure.

Roberto re-decided it on 2026-09-13, for unattended runs only:

1. **Does the #751 shared quota snapshot give a headless run a fresh number?
   No.** Its Claude producer (`quota_sources.py claude`) is fed by
   `statusline-command.ps1`, the same render that writes `rate-limits.json`.
   A `claude -p` process never renders a statusline, so with no interactive
   session open both caches age out identically (`docs/quota-snapshots.md`).
   There is nothing fresher to wire the gate to.
2. **So unattended no-signal routes to the bounded `PAUSE` path.** It
   re-reads every 600 s, under `scheduled_runner.py`'s 45-minute stall
   watchdog, so a silent wait is never killed as a hung job. The caller's
   3-cycle cap then ends the run in its session-limit skip bucket instead of
   dispatching blind. Each caller's report names the gate decision
   (`DECISION`/`REASON`/`MODE`).
3. **Interactive behaviour is unchanged**, and the 70% threshold stays until a
   real number flows in unattended runs.

In practice an unattended run only dispatches while an interactive Claude Code
session on the machine is keeping the cache fresh, for example the standing
chief. A scheduled run on an otherwise idle machine skips its fan-out and says
so, rather than spending the window on work it cannot finish.

A fresh interactive `DECISION=OK` doesn't prove the headless case. A scheduled-style
run printed one on 2026-09-13 only because interactive sessions were open.
Replay the headless case against a deliberately stale or absent cache in a temp
dir instead:

```
FLEET_SCHEDULED_RUN=1 rate_gate.py check --threshold 70 --state-dir <empty temp dir>
```

The cache path resolves via `CLAUDE_HOOKS_STATE_DIR` first, falling back to
`~/.claude/hooks/state` — the same resolution `hooks/session_state.py`'s
`state_file()` uses, so both stay overridable the same way in tests.

## Unattended waits vs the stall watchdog (fleet-config#891)

`scheduled_runner.py` kills a child whose stream has been silent for its stall
timeout (45 min by default, exit 124). A `Monitor` wait is silent. Probed on
2026-09-13 with Claude Code 2.1.270: a synthetic `claude_progress.py` run with
`--stall-timeout 120` started a `Monitor` until-loop that printed nothing for
5 minutes, and the watchdog killed it at `02:10` with `⏱ stalled · exit 124`.
The CLI emitted no `task_progress` or any other record while the monitor ran.
An over-threshold wait of up to 5 hours was therefore always going to be
killed as a hung job, with no digest.

The fix keeps the watchdog exactly as strict and makes the wait fit it:

- The runner exports the child's effective watchdog as
  `FLEET_STALL_TIMEOUT_SECONDS` (`0.0` when the watchdog is off).
- Unattended, every `PAUSE` wait (over-threshold, fallback and no-signal) is
  capped at half that value, 1350 s under the default. The caller re-checks
  after each capped wait, and that re-check is stream activity. The caller's
  3-cycle cap still bounds the total, so a reset further out than about 3 × the
  cap ends the run in its session-limit skip bucket instead of being killed.
- `WATCHDOG_SECONDS=unknown` means the variable was missing or unreadable, for
  example `--unattended` passed outside the runner. That is never read as "no
  watchdog": the cap falls back to `UNKNOWN_WAIT_SECONDS` (600 s).
- Interactive runs have no watchdog (`WATCHDOG_SECONDS=null`) and are not capped.

## How a skill waits on `PAUSE`

Chained short `sleep` calls are explicitly disallowed (they're a workaround for
the same thing a real polling primitive should do). The sanctioned mechanism is
the `Monitor` tool's until-loop pattern, polling against a wall-clock target of
now + `WAIT_SECONDS` — e.g. `until [ "$(date -u +%s)" -ge <target_epoch> ]; do
sleep 60; done`. Wait on `WAIT_SECONDS`, never on `RESETS_AT`: only
`WAIT_SECONDS` carries the unattended watchdog cap above. After the wait, re-run
`rate_gate.py check` and resume dispatch; if it still reads `PAUSE` (the reset
has not landed yet, or another process consumed the fresh window first), loop
again.

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
  in one message" fan-out; on `PAUSE`, wait and re-check (3-cycle cap) before
  firing the batch.
- **`/cleanup-fleet-all`** and **`/prompt-audit`** — one gate before their
  worker dispatch, same wait/re-check loop and 3-cycle cap. Both run weekly
  unattended, and each prints `SCHEDULED-RUN-FAILED` when the cap is exhausted.

## What this deliberately does not change

Widening `/audit-fleet`'s ≤3 concurrency window is out of scope here — that's a
separate, empirically-driven follow-up once someone actually observes a run's
wall-clock cost under the new self-pacing design and decides it's worth testing
a wider window.
