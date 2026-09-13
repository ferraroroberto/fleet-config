---
name: prompt-audit
description: Audits every fleet instruction file (CLAUDE.md, AGENTS.md, rules, SKILL.md) against the vendors' current prompting guides — first checking whether those guides changed, then linting and judging each file — posts a ledger digest and files per-repo prompt-drift cleanup issues. Also runs unattended weekly. E.g. "/prompt-audit", "/prompt-audit --dry-run", "audit our prompts against the latest guidance".
---

# prompt-audit

**Capability preflight:** read [workflow-capabilities](../../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** keep the fleet's instruction files in step with what the vendors' *current* prompting guidance says — and notice when that guidance itself moves. Three phases, in order: a **freshness gate** (did any guide change since `sources.toml`'s baselines?), a **scan** (deterministic lint + a per-repo judgment pass against `rules.md`), and **outputs** (one digest comment on the `kind=prompt-audit` ledger, plus one `audit: prompt-drift findings` issue per repo with violations). A fourth lens on the same files `/context-audit` sizes and `/context-purge` compresses: prompt-engineering quality, not token budget or layering.

The deliverables are the digest and the `prompt-drift` issues. This skill **reports**; it never edits an instruction file, never commits, and never opens a PR. Fixes are `/cleanup-fleet prompt-drift`'s job, under that skill's tier rule and preservation gate (fleet-config#833).

Files in this directory:

- `rules.md` — the rule-set (`R-NN` blocks: tags, detect, why, fix shape, source). Read it before judging anything; its sha is the ledger's `rubric-sha`.
- `sources.toml` — one block per vendor page with its baseline sha + marker, plus the audience vocabulary that decides which rules are primary for a file.
- `audit.py` — every exact step: `sources`, `diff-source`, `inventory`, `lint`, `dedup`, `state`, `ledger`, `digest`, `drift`.

`<py>` below is `E:/automation/fleet-config/.venv/Scripts/python.exe`; `<audit>` is `.claude/skills/prompt-audit/audit.py`.

## Arguments

- `--dry-run` — run every phase and print the manifest, lint hits, judgments, the digest and the `prompt-drift` issue bodies, but **write nothing**: no state marks, no ledger body, no comment, no update issue, no `prompt-drift` issue or label. Reading the ledger and the existing issues is allowed.
- `--only <repo>` — scan one fleet repo (its `hooks/projects.toml` name). The freshness gate still runs in full; the ledger keeps every other repo's entries.
- `--rescan-all` — ignore skip-unchanged (scan every file) and source cadence (fetch every source). Also the way to seed the `prompt-drift` issues: they are filed only from files judged in the run, so a run that skips everything files nothing new.

No argument → the full run.

## Execution rules (read first)

- **Run from the `fleet-config` repo root.** Put fetched pages and run files in a freshly created, uniquely named scratch directory outside the repo (e.g. `<session temp>/prompt-audit-<date>-<time>`). Never delete through a variable-built path — a harness prompts on `rm` against a variable that could be empty, which blocks an unattended run; a new directory per run needs no cleanup.
- **Writes are exactly these:** `~/.claude/prompt-audit/state.json`; the ledger issue body and one digest comment (through `audit.py ledger`, which goes through `skills/_lib/audit_issue.py` — never `gh issue create` a ledger by hand); in scan mode, one `kind=prompt-drift` issue per repo with something to say plus its `prompt-drift` label (through `audit.py drift`, same helper); and, in update mode only, one rule-set update issue. Nothing else, and none of it under `--dry-run`.
- **Unknown is never a pass.** A page that could not be fetched is `not-checked`, never `unchanged`. A file whose judgment did not come back is `unmeasured`, never `compliant`. A skipped file is listed as skipped, so "not in the findings" cannot read as "not looked at".
- **Degrade one item, never the run.** A failed fetch degrades that source; a failed judgment agent degrades that repo's files; the run still posts its digest (`status=partial` when any planned file ended unmeasured).
- **Poll to completion in this turn** (fleet-config#314). Any background agent or command is collected before moving on; never end the turn expecting to be resumed.
- **Numbers come from `audit.py`.** Counts, verdicts, plan actions and the digest's tallies are its output — copy them, never estimate them.
- **Unattended runs** (the weekly `run-weekly.bat` job, fleet-config#834) follow the same steps plus two gates: the rate gate before step 6's fan-out, and step 9's delivery assertion. Nobody can answer a question, so never ask one.

## Steps

### 1. Cadence state and sources

```
<py> <audit> state show
<py> <audit> sources
```

`SOURCE_STATE=<id>|due=yes|no|…` per source. A source is fresh only when it was checked within `check_every_days` **and** last seen `unchanged`; for each fresh source `state show` also prints a ready `VERDICT=unchanged|…|sha=cached|…` line — keep those lines for the digest. With `--rescan-all`, treat every source as due.

### 2. Freshness gate — fetch and diff each due source

Fetch the page's **verbatim bytes** with the session's shell HTTP client, following redirects, into the scratch dir:

```
curl -sSL --max-time 60 -o <scratch>/<id>.part -w "%{http_code} %{url_effective}" "<url>"
```

Only when curl exits 0 **and** the status is `200`, promote it: `mv <scratch>/<id>.part <scratch>/<id>.md`. A failed or truncated download stays a `.part` file that `diff-source` never reads, so it reports `not-checked` instead of hashing partial bytes into a false `changed`. A web tool that summarises or converts pages cannot establish a hash; if verbatim fetching is unavailable, skip the fetch and let `diff-source` report `not-checked` (the digest says so, and the scan still runs against the current `rules.md`). Independent fetches may run in parallel. Then, per due source:

```
<py> <audit> diff-source --id <id> --file <scratch>/<id>.md --final-url <url_effective>
```

One `VERDICT=unchanged|changed|new-guide|not-checked|id=…|sha=…|marker=…|reason=…` line each; keep them all. Unless `--dry-run`, record every verdict except `not-checked` (which was never checked):

```
<py> <audit> state mark --source <id> --verdict <verdict>
```

**Any `changed` or `new-guide` → step 3 (update mode).** Otherwise → step 4.

### 3. Update mode — the rule-set is stale

Do not scan the fleet against a rule-set now known to be out of date.

1. Read each changed guide's fetched file in full, alongside `rules.md` and `sources.toml`.
2. Draft the delta: the exact new, changed or retired `R-NN` blocks (same format, `Why:` quoting the new text, `Source:` with the section name), any "Recorded as rejected" entry, and the new `sources.toml` baseline values for every changed source (`baseline_sha`, `baseline_marker`, `baseline_final_url` from its `VERDICT` line, today's `baseline_date`). A `new-guide` adds a `[sources.*]` block for the new page. Say plainly when the change is cosmetic and the rules need no edit — the new baselines alone are then the delta.
3. Dedupe by exact title against open issues:
   ```
   gh issue list --repo ferraroroberto/fleet-config --state open --search "prompt-audit: vendor guidance changed in:title" --json number,title,url
   ```
   An open issue titled exactly `prompt-audit: vendor guidance changed — update rule-set` → add the draft as a comment there. None → create it (`--label enhancement --assignee @me --body-file <draft>`). Never both; under `--dry-run`, print the draft and file nothing.
4. Continue at step 7 with `scan_ran: false` and `update_issue: "#<N>"`. The run is still `status=complete`: the update issue is the delivery. Once a human merges the rule-set PR, `rules.md`'s hash changes and the next run rescans everything.

### 4. Plan the scan

```
<py> <audit> ledger plan [--only <repo>] [--rescan-all]
```

`PLAN=<path>|action=scan|skip|unmeasured|reason=…|sha=…` per file, then `LEDGER=#<n>|rubric=…|ledger_rubric=…|scan=…|skip=…|unmeasured=…`. A file is skipped only when its sha matches the ledger **under the same rubric**; a `rules.md` edit (new rubric) or a missing ledger plans every file. Keep every `PLAN=` line.

Nothing to scan (`scan=0`) → skip steps 5–6; the digest still posts and lists the skipped files.

### 5. Lint the planned files

```
<py> <audit> lint --all --changed-only --detail [--only <repo>] [--rescan-all]
<py> <audit> inventory [--only <repo>]
```

`HIT=<path>:<line>|rule=…|cap=violation|consider|count=…|text=…` per candidate and `HITS=<path>|audience=…|kind=…|lines=…|size=…|hits=…|neg=…|desc_words=…` per file; `inventory` adds each file's `audience` and any `SECTION=` scoped to one vendor's agent. A hit is a **candidate** — the judgment pass confirms or rejects it. `cap` is the strongest verdict the rule can reach for that reader (from the rule's tag and the file's audience); a judgment never exceeds it.

### 6. Judgment pass

**Rate gate first** (every run that dispatches workers):

```
<py> C:/Users/rober/.claude/skills/_lib/rate_gate.py check --threshold 70
```

`DECISION=OK` or `UNKNOWN` (interactive-only: a scheduled run with no fresh signal reads `PAUSE`, fleet-config#825) → dispatch. `PAUSE` → dispatch nothing yet; wait in place against the printed `WAIT_SECONDS` / `RESETS_AT` with a foreground, bounded wait (`Monitor`'s until-loop, never a backgrounded sleep), then re-check. **Cap: 3 pause cycles.** Still `PAUSE` after the third → judge nothing: every planned file is `null` in `judgments` (unmeasured, so the digest still posts as `status=partial` and names them), and step 9 prints the failure marker. Record the `DECISION=` / `REASON=` / `MODE=` lines for the report.

Group the planned files by repo. Split `fleet-config` into three groups (the global file and its always-on files, `skills/`, `.claude/skills/`); repos with at most two small files may share one worker, up to eight repos per worker. Dispatch one **easy-tier** worker per group through the capability contract (tier intent: [`docs/model-tiers.md`](../../../docs/model-tiers.md); stay within host slots and that doc's concurrency caps). No reliable spawn-and-collect → judge the groups serially in this session and say so in the report. Collect every worker's terminal result before step 7.

Brief each worker with exactly this, filled in:

> Read-only task: do not edit, create, move or delete any file, and do not run git, gh or any command that changes state. Read `<absolute path of this checkout>/.claude/skills/prompt-audit/rules.md` first, then each file below in full.
> Files, with audience/kind and scoped sections: `<inventory FILE= / SECTION= lines>`.
> Lint candidates: `<HIT= and HITS= lines for these files>`.
> For every file, assess every rule whose `file:` scope covers the file's kind and whose tag applies to its reader (a single-vendor rule does not apply inside a section scoped to the other vendor; a `[conflict]` rule applies only to neutral readers). Every lint candidate gets a verdict: `violation` or `consider` (never stronger than its `cap`) when the rule's `Why:` genuinely applies, `compliant` with a one-clause note when it is a false positive. Judgment rules get `violation`, `consider` or `compliant` from your reading; a rule you could not establish is `unmeasured`, never `compliant`. `text` is the offending line copied verbatim.
> Return only one fenced `json` block: a list of `{"path": "<path>", "findings": [{"rule": "R-NN", "verdict": "…", "line": <n or null>, "text": "<verbatim line or empty>", "note": "<one clause>"}]}` — one object per file, every file present. List only `violation`, `consider` and `unmeasured` verdicts; a rule you assessed and found compliant is left out, and a file with nothing to report has `"findings": []`.

Validate each result: valid JSON, every briefed path present, verdicts from the allowed values. Keeping compliant verdicts out of the payload is what makes a fleet-wide result small enough to carry into `run.json` intact; a file's presence is what marks it judged. A missing, malformed or failed result → each of that worker's files is `null` (unmeasured). Never re-spawn a worker to fill a gap in an unattended run; report it.

### 7. Render the digest

Write `<scratch>/run.json`:

```json
{
  "date": "<YYYY-MM-DD>",
  "dry_run": false,
  "sources": ["<every VERDICT= line from steps 1-2>"],
  "update_issue": null,
  "scan_ran": true,
  "plan": ["<every PLAN= line from step 4>"],
  "judgments": {"<path>": [<findings>] }
}
```

`judgments` holds each judged file's findings list, or `null` for an unmeasured one; a planned file left out of `judgments` is treated as unmeasured. Then:

```
<py> <audit> digest --run <scratch>/run.json > <scratch>/digest.md
```

The helper prints `DIGEST=status=complete|partial` on stderr. The digest carries `status`, `guides`, the rubric, per-source outliers, scanned/skipped/unmeasured counts, findings by rule, findings shared with the scaffolding master collapsed to one entry with a `propagate to:` list (`audit.py dedup` logic), repo-local findings, the unmeasured list, and the skipped list.

`--dry-run` → print `digest.md`, then `<py> <audit> drift --run <scratch>/run.json --dry-run` (the issue bodies that would be filed), and stop here.

### 8. Record and post

1. Write the ledger body (creates the `prompt-audit ledger` issue, label `audit-meta`, on the first run). It records, from `run.json` itself, each file judged with no `unmeasured` rule at its `PLAN=` sha (the bytes that were actually judged); an unmeasured file or rule is left out so it is rescanned next run. Nothing is recorded in update mode or when nothing was scanned.
   ```
   <py> <audit> ledger write --run <scratch>/run.json
   ```
2. Post the digest:
   ```
   <py> <audit> ledger comment --body-file <scratch>/digest.md
   ```
3. Scan mode only — file the cleanup issues:
   ```
   <py> <audit> drift --run <scratch>/run.json
   ```
   One `DRIFT=<repo>|issue=<url>|tier=easy|hard|none|open=N|new=…|matched=…|kept=…|not_resurfaced=…` line per repo touched. Only `violation` verdicts are filed; `consider` stays advisory in the digest. A line shared with the scaffolding master is filed once, on `project-scaffolding`, with `Propagate to:` naming every sister carrying it, and is left off the sisters' issues; `global-CLAUDE.md` and `skills/` findings file on `fleet-config`. Each item is tagged `· easy` / `· hard` by `/cleanup-fleet`'s prompt-drift tier rule and the body states the issue's tier. Re-runs merge (the living-backlog rules of `/codebase-audit` step 8): a ticked box is never touched, a re-matched item refreshes its line, an item for a file this run did not rescan is kept as-is, and an item gone from a rescanned file is tagged `not re-surfaced`, never ticked or deleted. A repo this run had nothing new to say about is left untouched. A `DRIFT=<repo>|error=…` line is that repo's failure (exit 1), reported, never folded into success.
4. Scan mode only: `<py> <audit> state mark --scan`.

A failed write is reported with its error; the run does not claim delivery without the `LEDGER_COMMENT=` URL.

### 9. Delivery assertion and report

Before the report, check what this run actually delivered. **Delivered** means all of: a `LEDGER_COMMENT=` URL from step 8.2; `DIGEST=status=complete`; and either a scan (step 8.3 ran with no `DRIFT=…|error=` line — zero findings is still a delivery) or, in update mode, the rule-set update issue filed or commented (`update_issue` is `#N`). Anything else — no comment URL, a `partial` digest, a drift error, update mode with no issue, the rate gate's pause cap — prints this literal line in the report, one reason, ASCII after the dash:

```
SCHEDULED-RUN-FAILED — <what was not delivered, one line>
```

The scheduled adapter maps that marker to exit `123`; the scheduled job's `delivery_check.py` independently re-reads the ledger for the same facts from the digest's `<!-- prompt-audit-digest … -->` stamp. Never print the marker on a run that delivered, and never on `--dry-run` (it writes nothing by design; the job's post-condition still fails it, which is correct).

A few lines: `status`, `guides`, sources checked/not-checked, files scanned/skipped/unmeasured, violation/consider totals, the update issue (if any), the ledger comment URL, and each `DRIFT=` line (repo, issue, tier).

## Notes

- **Where fixes go.** Violations become `prompt-drift` issues, the ninth `/cleanup-fleet` bucket (fleet-config#833): easy-tier issues ship through `/issue-yolo`, hard-tier ones stop for review, and every lane must pass `/context-purge`'s preservation harness (`check.py --base`). Considers are advisory and stay in the digest — the first fleet run put 152 of 255 of them on a single rule, far too noisy to become lanes. It runs weekly unattended (`run-weekly.bat`, an app-launcher Job slotted before `cleanup-fleet-all`, #834) so the bucket is fresh when cleanup starts.
- **Why a guide change stops the scan.** Scanning against rules known to be stale produces findings that the next rule-set would contradict. The update issue is `enhancement`, never a cleanup bucket, so a human always reviews the rule-set change (#831 decision log, 2026-09-11).
- **Why audience, not host.** The same `rules.md` runs on any agent; which vendor's rules are primary is decided by who reads the file (`sources.toml` `[audiences.*]`), so a neutral file read by several agents gets single-vendor advice as `consider`, never `violation`.
- **Shared text is filed once.** A finding whose line also sits in `project-scaffolding/CLAUDE.md` belongs to the master, with the repos that inherited it listed — never N copies for N divergent fixes. Same for `global-CLAUDE.md` text that also lives in the lite port's global instructions.
- **Neighbouring lenses.** `/context-audit` measures size and single-home altitude; `/context-purge` compresses losslessly; `/sota-watch` watches adopted tooling choices. None of them reads vendor prompting guidance.
