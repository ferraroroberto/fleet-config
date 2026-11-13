---
name: upstream-sync
description: Bring an outside source repo's merged work into the local copy built from it — read the `[upstream]` watermark in `.fleet.toml`, survey the source's merged items since then (apply / adapt / skip), and port each one through `/issue-add` + an `/issue-yolo` lane that reuses the source code and fixes it forward until the gate passes. One shot by default; `--dry-run` only surveys. E.g. "/upstream-sync", "/upstream-sync https://github.com/o/r sonnet", "/upstream-sync --dry-run".
---

# upstream-sync

**Capability preflight:** read [workflow-capabilities](E:/automation/fleet-config/docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session's actual tools before proceeding. Every port runs as a launcher lane through `chief_ops.py dispatch`, an HTTP call any harness can make, so this skill needs no in-session sub-agent.

**Goal:** the local repo gains each feature the source merged since the watermark, working here, through the normal issue flow. This skill orchestrates `/issue-add`, `/issue-yolo` and `/issue-finish`; it never re-implements them, and it writes nothing to the local tree itself. Every write, the watermark included, lands through a lane's PR.

Helper (all commands print `KEY=value`; exit 0 done, 1 held, 2 refused, 3 unknown):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/upstream_sync/cli.py <init|status|survey|advance> --repo .
```

Contract and table schema: [upstream-sync](E:/automation/fleet-config/docs/skills/upstream-sync.md).

## Arguments

- `<source-url>`: needed only on the first run, when the repo has no `[upstream]` table. `--branch B` names the source branch (default `main`).
- Model, one per run: `sonnet`, `opus`, `luna` or `grok`. Every item's lane runs on it. Default `opus`, the dispatched-lane default in [model-tiers](E:/automation/fleet-config/docs/model-tiers.md).
- `--dry-run`: survey and report only. Lanes, issues and `.fleet.toml` are left alone; the only side effect is fetching the source into the fetch-only `upstream-sync` remote.

## Hard rules

- **Merged work only.** The source's open issues and PRs are never read.
- **No provenance trail.** No "ported from" line, source name, source sha or source PR number in any issue, PR body, comment or commit message, and no `cherry-pick -x`. The `[upstream]` watermark and skip ledger are the only record. Issue and PR text describes the feature itself, in public-safe words.
- **The source is read-only.** Never push to it; the helper sets its push url to one git cannot reach. Never force-push the local repo.
- **Licence first.** The survey prints `LICENSE=<name> REUSE=<yes|copyleft|no|unknown>`. Anything but `yes` stops the run before any port, and the report names the licence: the owner decides.
- **Nothing may break.** An item that cannot pass the local gate is not shipped. Fixing forward never weakens a test, a ratchet or a baseline.

## Steps

### 1. Read the watermark

Run from inside the local repo. `git fetch origin`, then `status --ref origin/<default-branch>`, so the answer is what is merged, not what this checkout happens to hold.

- `UPSTREAM=ok` → step 2.
- `UPSTREAM=absent` and no `<source-url>` → stop: "no watermark yet; pass the source url".
- `UPSTREAM=absent` with a url → **first run**. `--dry-run`: `init <url> --branch B --dry-run` and report `SOURCE_HEAD` and the licence line, then stop. Otherwise run one bookkeeping lane (step 3) whose lane runs `init <url> --branch B` and ships the table. Report the merged `SYNCED_SHA` and stop: this run only sets the watermark, and the next run ports what the source merges after it.
- `UPSTREAM=invalid` or exit 3 → stop and report; never repair the table by hand.

### 2. Survey

`survey --against origin/<default-branch>`. It fetches the source and prints the table, `LICENSE=`, `PENDING=<n>` and one `ITEM` line per source item after the watermark, oldest first (`sha`, `pr`, `merge`, `present`, `skipped`, `files`, `subject`). `WATERMARK=unknown` (the source rewrote its history) stops the run. `PENDING=0` → report "up to date" and stop.

Read each item's diff (`git show <sha>`, or `git diff <sha>^1 <sha>` for a merge) against the local tree and classify it:

- **apply**: the code fits as it is.
- **adapt**: it fits after a named local change (a different path, config shape, dependency or convention). Name the change.
- **skip**: it does not belong here. Give the reason. `present=yes` is a skip with the reason "already present"; `skipped=yes` is already in the ledger and needs nothing.

The order is the source's order: the watermark advances only over a contiguous run, and source order puts each change after what it builds on.

Print the survey as a table (item, PR, class, reason or adaptation). With `--dry-run`, stop here.

### 3. Port, one item at a time

Walk the items in order. Collect the skips in front of each port; that lane records them, so its PR moves the watermark over them and onto its own item in one merge. Any skips left after the last port go to one bookkeeping lane at the end.

For each **apply** or **adapt** item:

1. **File the issue** through the `/issue-add` flow (its own steps, not a paraphrase): a feature title and body in the local repo's terms, the adaptation as an acceptance criterion, a type label. No provenance (Hard rules).
2. **Write the lane brief** to a file in this session's scratch or temp dir, containing:
   - the source item: remote `upstream-sync`, the sha, and whether it is a merge;
   - how to bring it in: `git fetch upstream-sync`, then `git cherry-pick --no-commit <sha>` (`-m 1` for a merge), or `git diff <sha>^1 <sha> | git apply -3` when the cherry-pick cannot start; the lane's own commit message describes the feature, never the source;
   - the adaptation from the survey;
   - the skips to record first, one `advance <skip-sha> --outcome skipped --reason "<reason>"` each, then `advance <sha> --outcome merged`, both run on the lane's branch before `/issue-finish`, so the merged PR carries the watermark (an exit 1 `WATERMARK=held` there means an earlier item is missing: stop and report);
   - fix forward: when the source code does not work here as it is, fix it in the same run until the gate passes, reusing as much of it as possible; stop only on a third red on this item or a change that would break existing behaviour;
   - no provenance anywhere, and ship only on `GATE-RESULT: PASS` through `/issue-finish` with its independent review.
3. **Dispatch** through the one path every model uses:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/chief_ops.py dispatch <repo> <N> --mode yolo --yolo-confirmed --model <model> --brief-file <brief>
   ```
   A refusal (repo occupied, at the worker cap, launcher unreachable, a model the launcher does not offer, any other) stops the run, and the report quotes the refusal line as the reason. Never fall back to another model or path.
4. **Wait in this turn**: nothing wakes this session. Every few minutes, `git fetch origin` and `status --ref origin/<default-branch>`, and `chief_ops.py sessions` for the lane. Each wait ends in exactly one state:
   - **merged**: `SYNCED_SHA` equals the item's sha. Go to the next item.
   - **failed**: the lane's session has ended or gone idle and the watermark has not reached the item. Stop the run.
   - **in flight past timeout**: 120 minutes without either. Stop the run, and say the lane may still be working.

**Bookkeeping lane** (the first-run `init`, or trailing skips): the same issue, brief, dispatch and wait, with a brief that only runs the helper commands and ships the `.fleet.toml` change.

### 4. Report

One line per item: `merged` (with its PR), `skipped` (reason), `failed` or `in flight` (with the lane and its last state), or `not started`. Then the watermark before and after, read from `origin/<default-branch>`, and what the next run will pick up. A stopped run leaves the watermark before the item that stopped it; re-running resumes there.
