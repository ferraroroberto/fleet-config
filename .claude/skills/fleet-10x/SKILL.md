---
name: fleet-10x
description: Ask every active fleet repo "how could this app be 10x better, even at twice the effort?" — one read-only Sonnet researcher per repo, an Opus synthesis, and a dated machine-local report with an executive summary, one impact-vs-cost table across the fleet, per-repo sections and a build sequence. E.g. "/fleet-10x", "/fleet-10x --repos task-os,life-os", "how could the fleet be 10x better".
---

# fleet-10x

**Capability preflight:** read [workflow-capabilities](../../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** one report that someone opening it cold in three months can act on: *this is the fleet, this is what would make each app 10x better, ranked by impact for the cost, and this is the order we'd build it in.* Each run is dated, so runs can be compared (fleet-config#1253).

**The helper owns everything that must not drift between runs:** which repos are selected, the brief each researcher gets, the recommendation schema, the ranking and the footer. All of it lives in `.claude/skills/fleet-10x/fleet10x.py`. The model owns the judgment: the research and the synthesis.

## Rules

- **Read-only, all the way down.** No sub-agent edits, commits, branches, files or comments on issues or PRs, or restarts anything in any repo, and none calls a live app, port, job or session. The rendered brief restates this in full, and every researcher gets that brief verbatim. The orchestrator writes only inside the run folder.
- **Machine-local output, never committed.** Everything goes to `~/.claude/hooks/state/fleet-10x/<YYYY-MM-DD>/`. The report holds personal workflow detail: never paste it into an issue, a PR or any repo. Publishing a private page from it is the operator's call, never this skill's.
- **Scoped reading.** Researchers read their own repo, the global `CLAUDE.md` and the system map. They never run an unscoped filesystem walk. `gh` goes through the Issues API, never search.
- **Models:** researchers on the easy tier, synthesis on the hard tier (`docs/model-tiers.md`). At most 3 hard-tier (Opus) agents in flight. Keep researchers to about 6 at a time on one box.
- **Poll to completion in-turn.** Never end a turn waiting on a background researcher; nothing wakes this session.

## Arguments

- none → the default selection: `.fleet.toml` repos with at least 20 commits on their default branch in the last 60 days.
- `--repos a,b,c` → exactly those repos (each must have a `.fleet.toml`).
- `--min-commits N` / `--days N` → move the threshold.

## Steps

`$H` below is `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-10x/fleet10x.py`, run from the fleet-config root.

### 1. Open the run and select repos

```
$H rundir
$H select --run-dir <RUN_DIR> [--repos …] [--min-commits N] [--days N]
```

`rundir` prints `RUN_DIR=` and stamps `run.json` with the start time and the Claude quota reading. A second run on the same day reuses the folder: researcher files already there count as done, so a crashed run resumes. Pass a different `--date` to keep a separate run. `select` prints one `ACTIVE=` line per selected repo, one `QUIET=` line per quieter `.fleet.toml` repo, and `SELECTED=…|RULE=…`. It writes `selection.json`. An `ERROR=` line means an unknown `--repos` name: stop and report it.

### 2. Render the briefs

```
$H brief <RUN_DIR>
```

This writes `briefs/<repo>.md` for each active repo. Each one is `research-brief.md`, filled in for that repo.

### 3. Research, one easy-tier agent per repo

For each active repo that has no valid `repos/<repo>.json` yet, dispatch a fresh read-only sub-agent on the easy tier. Its prompt is the full text of `briefs/<repo>.md`, unchanged, followed by one line: *"You are read-only: write only the one JSON file named above."* Keep about 6 in flight and refill as each returns.

Collect every result. Then run `$H validate <RUN_DIR>/repos/<repo>.json` yourself; never trust the agent's own `VALID=` line. On `INVALID=`, re-dispatch that repo once, with the error lines added to its brief. A second failure is recorded as `research failed: <reason>` and the run continues. Keep a ledger of dispatched, collected, valid and failed repos, plus each agent's reported tokens.

### 4. Rank

```
$H rank <RUN_DIR>
```

This writes `ranked.md`, every recommendation across the fleet sorted by impact over cost (H/M/L = 3/2/1, S/M/L/XL = 1/2/3/4). Ties go to the higher impact. `file:line` and `#N` evidence becomes links. It prints `RANKED=…|INVALID=…|WALL=…|QUOTA=…`.

### 5. Synthesize, one hard-tier agent

Dispatch one fresh sub-agent on the hard tier. Tell it that it is read-only except for `<RUN_DIR>/report.md`, and ask it to read `selection.json`, `ranked.md` and every `repos/*.json`, then write `report.md` with exactly these sections:

1. **Executive summary.** What the fleet is and how it is worked: phone-first through the launcher, the local LLM hub, the chief and its lanes. Ground this in the global `CLAUDE.md` and the researchers' summaries, never invention. Then 3-5 cross-fleet themes: patterns several repos share, and where one fix in a shared layer (scaffolding, the hub, the launcher) lifts many apps. Then the **top 10 bets**, chosen by judgment from the ranked table, not copied from its first ten rows. Favour cross-repo leverage, and say why each one made the list.
2. **The ranked table.** `ranked.md`, verbatim.
3. **How we'd do it.** A build sequence of waves, each a handful of issue-sized steps. Name dependencies, and anything only the owner can do (a signup, a device test). The waves respect the two-lane cap.
4. **Per-repo sections.** One per active repo in selection order: its summary, then its recommendations with impact, cost, what changes, why it's 10x, the first step and linked evidence. A repo whose research failed gets one line saying so.
5. **Quiet repos.** One line each, from `selection.json`'s `quiet` list.

Tell it to write so a reader with no context can follow: no session jargon, every repo and issue linked, and no secrets, hostnames or other people's personal data.

### 6. Footer and report back

```
$H footer <RUN_DIR> --agents <researchers + synthesis> [--tokens <sum of reported tokens>]
```

Omit `--tokens` when any agent's count is unknown; the footer then says unknown rather than guessing. Report the run folder, `report.md`'s path, the selected repos, the wall time, the quota line and the top 10 bets as a short list. Name any repo whose research failed.
