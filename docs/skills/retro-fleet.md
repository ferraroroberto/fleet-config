# `/retro-fleet`

`/retro` reviews one session. `/retro-fleet` looks across every session on the machine for agent errors that keep recurring (failed tool calls, hook refusals, gate and ratchet failures, edit mismatches) and proposes how to stop each one, mechanism before prose: a check a program can run (ratchet rule, hook, test) before a line of instructions. Nothing is filed or built: the output is the shared [scored proposal page](../proposal-reports.md) and one Board ask per proposal, and the owner decides each one.

## How it works

1. **Mine** (`skills/_lib/retro/fleet.py scan`). Every `.jsonl` under `~/.claude/projects` (subagent logs included), `~/.codex/sessions` and `~/.pi/agent/sessions` goes through `retro/digest.py`, which keeps only error signatures (`tool|class|template`, built from harness, hook and ratchet text with paths, numbers, ids and quotes masked). `retro/miner.py` caches one row per log in `retro-fleet-cache.json` in the hooks state dir: agent, repo, last-change date and signature counts. The cache is the watermark: a log whose size and modification time match its row is not read again, so the first run is a full backfill and later runs read only changed logs. A deleted log keeps its row, marked gone. A log that cannot be read or that no adapter recognises is cached `unmeasured` and counted by reason, never as a clean session.
2. **Aggregate.** Per signature: errors and sessions all time and in the last 30 days, sessions per repo and per agent, first and last seen, and a trend (sessions in the newer half of the window against the older half). A signature hit in at least 3 sessions in 30 days is recurring. Hook refusals are `enforced` (the check exists and fired) and owner rejections are `owner`: both are reported only, never proposed. The top 15 recurring candidates go to `candidates.json` in `retro-fleet/` under the hooks state dir.
3. **Classify.** A read-only Sonnet pass judges each candidate: already enforced, mechanical but unenforced (names the check and its target file), a check that exists but is unwired, or judgement only (one line in the narrowest instruction file). A signature seen in two or more repos is homed in `project-scaffolding`, otherwise in its one repo.
4. **Render** (`fleet.py report`). `retro/proposals.py` joins each verdict to its counts and writes the `proposal_report` input with report id `retro-fleet`. The evidence line is generated from counts and the signature only, and a verdict naming a key the mine did not produce is refused, so no transcript text reaches the page or the asks. Each suggestion id is `sig-<10 hex>` of its signature, so its ask id is the same every week and an answered ask is never repeated.
5. **Ask.** `proposal_report/cli.py ask` posts one Board ask per proposal against the published page.

## Running it

On demand: `/retro-fleet`. Scheduled: `.claude/skills/retro-fleet/run-weekly.bat` (Sonnet, through `claude_progress.py`); it is not wired to an app-launcher Job until the owner asks.

Tests: `tests/audit/test_retro_fleet.py` (synthetic logs for all three agents; watermark, window, unmeasured, enforced, report build and privacy).
