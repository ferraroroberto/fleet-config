---
name: downstream-sync
description: Hand a local copy's own features back to the outside source repo it was built from, as specs — list the local work the source lacks (read-only), let the owner pick, write one code-free, public-safe feature spec per pick, and file each on the source only after a per-item OK. Sibling of /upstream-sync; shares its `[upstream]` table. E.g. "/downstream-sync", "/downstream-sync --since abc123", "what have we built that the source doesn't have".
---

# downstream-sync

**Capability preflight:** read [workflow-capabilities](E:/automation/fleet-config/docs/workflow-capabilities.md) and bind questions, dispatch, results and waits to this session's actual tools before proceeding. The one write to the local tree goes through a launcher lane started by `chief_ops.py dispatch`, an HTTP call any harness can make, so this skill needs no in-session sub-agent.

**Goal:** the source repo receives a description of each chosen local feature, detailed enough to rebuild it, and the local repo records what it offered. When the source later ships its own version, the next `/upstream-sync` marks that item **supersedes local** and aligns this repo to it. This skill never ports, never pushes and never writes the local tree itself.

Helper (shared with `/upstream-sync`; every command prints `KEY=value`; exit 0 done, 1 held, 2 refused, 3 unknown):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/upstream_sync/cli.py <status|compare|lint-spec|offer> --repo .
```

Contract and table schema: [upstream-sync](E:/automation/fleet-config/docs/skills/upstream-sync.md).

## Arguments

- `--since REF`: list only local work after this commit. Default: every local commit the source branch does not reach.
- Model for the one bookkeeping lane: `sonnet`, `opus`, `luna` or `grok`. Default `sonnet`, since that lane only runs helper commands ([model-tiers](E:/automation/fleet-config/docs/model-tiers.md)).

## Hard rules

- **Nothing reaches the source without a per-item OK.** Filing a spec publishes to someone else's repo. The owner approves each spec by name, after reading it; one OK covers one item, never "all".
- **No code when the source is private.** `gh repo view <source owner/repo> --json visibility -q .visibility` printing `PUBLIC`, and only that, allows `lint-spec --allow-code`. Anything else, an error included, means no code.
- **No provenance and no private detail.** A spec never names this repo, its owner, a local path, host, address, email or secret. `lint-spec` refuses those, and its refusal is fixed by rewording, never by a flag.
- **The source is read-only.** Issues are the only thing filed there: no comments on other threads, no PRs, no pushes.
- **A spec is a suggestion.** The source may reject it or build it differently, and its version wins when it returns.

## Steps

### 1. Read the table

Run from inside the local repo: `git fetch origin`, then `status --ref origin/<default-branch>`. `UPSTREAM=ok` → step 2. `UPSTREAM=absent` → stop: "no `[upstream]` table yet; run `/upstream-sync <source-url>` once to set it". `invalid` or exit 3 → stop and report.

### 2. Compare (read-only)

`compare --against origin/<default-branch> [--since REF]`. One `LOCAL` line per local first-parent commit the source branch does not reach, oldest first: `synced=yes` (it moved the `[upstream]` table, so it came in through a sync lane), `present=yes` (the source has the same patch), `offered=yes` (already handed back), and `candidate=yes` for the rest. `OFFERED` lines list the open offers.

For each candidate, read its diff and the source tree at `upstream-sync/<branch>`. Drop a candidate whose behaviour the source already has, naming where. Group commits that are one feature. Print the list: letter, feature, one-line value statement, the local commits. `CANDIDATES=0`, or nothing left → report "nothing to hand back" and stop.

### 3. Pick

Ask the owner which features to hand back (A, B, C), through the session's question channel. No channel (a headless run) → the list is the report; stop.

### 4. Write the specs

One Markdown file per pick, in this session's scratch or temp dir, never in the repo. A cold implementer must be able to build from it alone, with these sections:

- `## Problem`: who needs it and why.
- `## Behaviour`: what it does, edge and empty states included.
- `## Interface`: routes, config keys, CLI flags and UI, in the source's own terms.
- `## Acceptance criteria`: a checklist.
- `## Tests`: what proves each criterion.

Describe the feature as it would fit the source, not this repo. Then run `lint-spec <file>` (plus `--allow-code` only under the visibility rule above, and `--forbid <word>` for any further name the spec must not carry). `SPEC=refused` → reword and re-run until `SPEC=ok`.

### 5. Hand back, one OK at a time

Show the owner each spec in full with its path. For each item they approve by name, re-run `lint-spec` on the file as it is now, then file it:

```
gh issue create -R <source owner/repo> --title "<the feature, public-safe>" --body-file <spec>
```

Record the issue number it prints. A source that is not on GitHub, or does not take issues → give the owner the file to hand over, and record the item with no issue number. Items not approved are left alone: they stay candidates for the next run.

### 6. Record the offers

One bookkeeping lane writes the `[[upstream.offered]]` entries. File its issue through the `/issue-add` flow (title and body describe recording offered features, no source names), write a brief containing one `offer <local-sha> --feature "<name>" [--source-issue <N>]` per handed-back item, to run on the lane's branch and ship through `/issue-finish`, then dispatch it:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/chief_ops.py dispatch <repo> <N> --mode yolo --yolo-confirmed --model <model> --brief-file <brief>
```

A refusal stops the run and the report quotes it. Wait in this turn, nothing wakes this session: every few minutes `git fetch origin`, `compare --against origin/<default-branch>`, and `chief_ops.py sessions` for the lane. It ends **merged** (every handed-back sha shows `offered=yes`), **failed** (the lane ended or went idle first) or **in flight past timeout** (120 minutes; say the lane may still be working).

### 7. Report

One line per candidate: `handed back` (source issue or "file given to owner"), `not picked`, `already in the source` (where), or `failed` (why). Then the offers recorded on `origin/<default-branch>`. When the source ships its version, `/upstream-sync` marks that item `supersedes local` and replaces the local implementation with it.
