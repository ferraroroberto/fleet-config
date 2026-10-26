---
name: propagate-vendored
description: Fan out a byte-for-byte re-vendor of project-scaffolding components to every adopter in their [vendored] manifest — one PR per repo carrying every component it is behind on; copy, hash-verify, bump the sha, run the gate, auto-merge, no per-repo issue. Dry-run reports per repo what is behind, touching nothing. Invoke explicitly, never per scaffold commit. E.g. "/propagate-vendored nav", "/propagate-vendored nav,usage_log", "/propagate-vendored --all-behind", or "/propagate-vendored nav --dry-run".
---

# propagate-vendored

**Capability preflight:** read [workflow-capabilities](../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional examples (Claude Code only); the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

**Goal:** Turn a `project-scaffolding` component fix into a one-command,
Dependabot-style distribution wave instead of N hand-filed issues + N hand-built
PRs (`project-scaffolding#144–#150`). The scaffold change carries the decision;
a byte-for-byte re-vendor carries none — so this skill never files a per-repo
issue, only opens auto-merging PRs linking back to the scaffold record.

Companion doc: `skills/propagate-vendored/README.md` (manifest-schema decision
writeup). Schema reference: `architecture/README.md`'s "Optional per-repo
`[vendored]` table" section.

## Cadence — explicit or batched, never reflex-fired

Invoke **by hand**, or as a periodic batch (e.g. weekly), never automatically
per scaffold commit. Four waves in one day is the anti-pattern — collect
scaffold changes, propagate once. Nothing here schedules itself; there is no
`run-weekly.bat` by design.

## Arguments

`/propagate-vendored <component>[,<component>…] | --all-behind [scaffold-sha] [--dry-run | check]`

- **`<component>[,<component>…]`** — the manifest keys (`nav`, `tray_lifecycle`, …)
  — same names adopters use in their `[vendored].<component>` entry. Several
  names, comma-separated, make one wave: each repo gets **one PR** carrying every
  named component it is behind on.
- **`--all-behind`** — every component, instead of a list. Same one-PR-per-repo wave.
- **`[scaffold-sha]`** — optional. The scaffold commit to propagate to.
  Default: `project-scaffolding`'s current `origin/HEAD` (default branch tip).
- **`--dry-run`** (or the bare word `check`) — report-only. Runs the drift
  scan, prints who's behind, touches nothing: no branch, no clone, no PR. Safe
  to run any time.

Neither a component nor `--all-behind` → stop: "Pass a component name, e.g.
`/propagate-vendored nav`, a list (`nav,usage_log`), or `--all-behind`."

## Rules (read before running anything)

- **Model tier: `easy`** (per `docs/model-tiers.md`) — narrow, mechanical,
  zero design decisions. Claude Code only: Sonnet at high effort, fanned
  out **all at once** (Sonnet is exempt from the Opus concurrency cap).
- **One PR per repo per wave.** A repo behind on several components in the wave
  gets one branch, one gate run and one PR carrying all of them, hash-verified per
  component; never one PR per component. The branch, commit text and PR body come
  from `vendored_wave/plan.py` (step 2), not from the agent.
- **One sub-agent per adopter repo**, never two against the same checkout.
  Respect `skills/_lib/worktree_claim.py` exactly as `/issue-batch` does — a
  repo another session is actively working gets `MODE=worktree`, never a
  collision on `main`.
- **No per-repo issues, ever.** The scaffold issue/PR is the single record;
  see the README's "Why this skill never files a per-repo issue."
- **`Part of …`, never `Closes …`** in a generated PR body — GitHub's
  closing-keyword parser matches substrings, so a literal `Closes #N` risks
  closing the scaffold issue on the first adopter merge.
- **Adopt before re-vendor.** A repo with no `[vendored].<component>` entry
  yet is not skipped — see step 4, item 2a (ADOPT). This is how the manifest
  grows to cover the real nav + tray consumers.
- **State coverage; a partial wave must never read as a complete one.** The
  adopter list comes from the adopters' own `[vendored]` entries, so a repo
  carrying the component without declaring it is invisible to it. Report
  both numbers on every run — declared adopters propagated *and* undeclared carriers found —
  and report a repo whose manifest could not be read, or a scaffold with no
  `[components]` catalog, as its own **unknown** rather than folding either into
  the clean count (project-scaffolding#230).
- **Vendor verbatim.** Never hand-edit a copied file — a re-vendor that needs
  a local tweak means the tweak belongs upstream in `project-scaffolding`
  first, not in the adopter's copy (`_vendored/README.md`'s rule).
- **Vendoring standardizes whatever you vendor, including mistakes — review
  the component's user-facing wording as carefully as its mechanism before
  propagating it fleet-wide.** A locally-softened copy registers as *drift*,
  so a bad default reads as the safe choice everywhere it lands. Before a real
  (non-`--dry-run`) wave, confirm the scaffold source's wording matches its own
  cited reference implementation, not just that the bytes hash-match.
- **Hash-verify before bumping the manifest sha.** A copy that doesn't
  byte-match the scaffold source is a bug in this skill, not something to
  paper over by writing the sha anyway.
- **Degrade, don't block.** A per-adopter failure (gate red, merge conflict,
  CI red) is reported and left for a human; it never stops the rest of the
  wave.
- **`--dry-run` never writes anything** — no clone, no branch, no file, no PR,
  no manifest bump. It only runs `vendored_drift.py` and reports.
- **No AI attribution; no hard-wrapped PR-body paragraphs** (global CLAUDE.md).
- **Shell:** Bash tool here is Git Bash — plain `git`/`gh`/the resolved Python
  path only, no PowerShell syntax.

## Steps

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. Else stop.
- `E:\automation\project-scaffolding` must exist and be a git repo (the
  canonical source). Else stop.
- Resolve `<scaffold-sha>`: if omitted, `git -C E:\automation\project-scaffolding
  fetch origin` then resolve `origin/HEAD`'s tip
  (`skills/_lib/vendored_drift.py`'s `resolve_ref_sha` does this same
  resolution — reuse it rather than re-deriving with raw `git` calls). If
  given, verify it resolves (`git -C ... rev-parse --verify <sha>`); else
  stop: "`<sha>` doesn't resolve in project-scaffolding."
- Real runs only: mint the wave id every agent records its gate time under —
  `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_wave/gate_ledger.py wave-id --sha <scaffold-sha>`
  — and keep it for the agent prompts and the step-6 summary.

### 2. Run the drift scan (always — dry-run and real runs both start here)

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_drift.py scan --component <component>[,<component>…]
```

Omit `--component` for `--all-behind`.

Prints one JSON object: `adopters` (every fleet repo with a
`[vendored].<component>` entry, each carrying `local_drift` and `behind_head`
booleans + diff file lists), `no_manifest` (every other fleet repo — the
"hasn't adopted yet" bucket), and `errors`. This is the **only** source of
truth for who's behind; never eyeball repos by hand.

For the **adopt** discovery — a repo that already carries the component's files
but has no manifest entry — `no_manifest` alone doesn't distinguish "never
touched this component" from "has it, unlabeled." **The scan answers that; never
sweep for it by hand** (project-scaffolding#230). `undeclared_carriers` names
every repo holding a catalogued component's files at the scaffold's own path
with no manifest entry, each carrying:

- `matches_head: true` — byte-identical to the scaffold tip. Nothing to decide:
  the repo simply never recorded what it copied. An **adopt** candidate (step 4, item 2a (ADOPT)).
- `matches_head: false` — present but different, with `diff_files`. "Never
  declared it" and "deliberately forked it" are indistinguishable from the
  bytes, so **this reports; it does not overwrite.** Never fold one of these
  into the wave silently: name it, say what differs, and let the human decide
  whether it is a stale copy to re-vendor or a deliberate divergence to leave
  alone.

`coverage` carries the two numbers this skill must always state, plus
`carriers_unknown` (repos whose `.fleet.toml` could not be parsed — their carrier
status is *unestablished*, never "clean") and `catalog_known`. **If
`catalog_known` is `false` the scaffold checkout has no `[components]` table and
carrier detection did not run at all** — say so; do not report "no undeclared
carriers", which is a different and unearned claim.

**Contract-change check — a removed selector stops the wave** (fleet-config#1227).
Every `behind_head` adopter carries `contract`: the component's CSS selectors and
README compared between that adopter's pinned `sha` and the scaffold tip.
`removed_selectors` are selectors the pin has and the tip does not; `readme_changed`
and `readme_removed_lines` report the README. A byte-for-byte re-vendor is
hash-verified, so nothing else notices a split like project-scaffolding#300, which
moved the footer button and the input/select recipe out of `modal.css` and left
two apps unstyled in live builds. `coverage.contract_breaking` lists every adopter
whose `contract.breaking` is true (a selector was removed). A README change on its
own is reported but never stops the wave. The comparison is whole selectors, so a
reworded selector reads as one removed: it stops for a human look, never the reverse.

A repo appearing in neither list carries nothing catalogued and is genuinely
not an adopter — skip it, it is out of scope for this component.

**`classify_e2e` only — check each adopter's consumer.** Re-vendoring the
classifier never checked the gates that read it (a new classifier joined a
surface's targets with spaces; one gate passed them to pytest as one argument,
failing every branch touching two e2e modules: home-automation#784,
fleet-config#1134). For every adopter and carrier, run
`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/e2e_route.py probe E:/automation/<repo>`
and carry its `GATE_ROUTING` into the step-3 report and the step-6 summary:
`broken` (the gate never splits the target) is a gate fix the wave must not
ship past silently, so name it per repo; `not-consumed` means the re-vendor
changes nothing that repo's gate runs.

Then group the same scan by repo — read-only, the same call the real wave starts from:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_wave/plan.py plan --component <component>[,<component>…] [--allow-breaking <component>[,<component>…]] [--part-of <scaffold-issue-or-PR-url>] [--format text]
```

JSON (default) is one entry per repo: `components` (each with `action`
`re-vendor`|`adopt`, `old_sha`, `new_sha`, `files`), plus the `branch`,
`worktree_key`, `commit_subject`, `commit_body` and `pr_body` that repo's single PR
uses, and top-level `held_contract` (components stopped for removed selectors) and
`held_carriers` (carriers that differ from the tip). A one-component plan keeps
exactly the per-component branch, commit subject and one-line PR body the skill
always produced. `--allow-breaking` is passed only for a component a human approved
in step 4. `--format text` is the per-repo block of the dry-run report below.

### 3. `--dry-run` / `check` stops here

Print the report and stop — no clone, no branch, no PR, nothing written. Lead with
the per-repo block from `plan.py plan --format text` (each repo, the components it is
behind on, the PRs the wave would open against one per component), then:

```
/propagate-vendored <component>[,<component>…] --dry-run — scaffold @ <short-sha> (<ref>)

  adopters (declared in [vendored]):
    <repo>   pinned <short-sha>   local: OK|DRIFT (<n> files)   vs HEAD: current|BEHIND (<n> files)   [classify_e2e: gate <GATE_ROUTING>]
    …
  undeclared carriers (component present, no manifest entry):
    <repo>   <component-path>   identical to HEAD — adopt
    <repo>   <component-path>   DIFFERS (<n> files) — stale or deliberately forked, human call
    …
  no signal (<n> repos): <comma-separated, or "none">

  coverage: <n> declared adopters, <m> undeclared carriers, <k> unknown (<repos, or "none">)
            catalog: <c> components known | NOT KNOWN (<reason>)
            contract: <j> adopter(s) would lose selectors (<repo> @ <short-pin>: <removed selectors>, or "none")
            README changed for: <repos, or "none">

Run `/propagate-vendored <component>` (no --dry-run) to fan out the real wave.
```

### 4. Real run: fan out one sub-agent per target repo

**Stop first on a contract change.** If `coverage.contract_breaking` is non-empty,
do not dispatch anything. Print each component, the pinned sha and the
`removed_selectors`, say which adopters it affects, and ask whether the markup of
those adopters still works without them (the scaffold's split issue says where the
selectors went). Include the component only on an explicit yes naming it; a yes for
one component never covers another. Adopters of other components are not held up.
The README note rides along in the report.

Target repos = the `repos` of the step-2 plan: every `behind_head`/`local_drift`
adopter, plus every `matches_head: true` carrier (adopt-only — its bytes are already
current), grouped so a repo appears once with all its components. Re-run the plan
with `--allow-breaking` for any component the human approved above. A
`matches_head: false` carrier is **not** a target by default: surface it and
ask, then include it only on an explicit yes. For each target **repo**, dispatch one background
worker through the capability contract (easy tier, bounded by available slots). Worktree
setup mirrors `/issue-batch` step 6 — pre-create the branch in the orchestrator
via `worktree_claim.py`, sequentially, before any agent launches:

```
git -C E:\automation\<repo> fetch origin
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire E:\automation\<repo>
```

`MODE=primary` → work in place on `main`. `MODE=worktree` → 
`setup-worktree E:\automation\<repo> <worktree_key> <branch>` (both from the repo's
plan entry; same helper `/issue-batch` uses; junctions `.venv`, reparse-safe teardown).

#### Agent prompt

```
You are re-vendoring these components from project-scaffolding into <repo>,
off scaffold commit <scaffold-sha> (short: <shortsha>), as ONE change and ONE PR:
<one line per plan component: name, action, scaffold-src, dest-path, old sha -> new sha>
Working tree: <path>. Branch: <branch>.
You are the only agent touching this checkout. Steps 2a-4 run once per component
above; steps 5-8 run once for the whole repo.

1. cd to <path>. Confirm you are on the branch (cut it if MODE was primary
   and it doesn't exist yet: `git checkout -b <branch>` off fresh origin/main).

2a. ADOPT (only if <repo> has no [vendored].<component> entry yet): add one
    to .fleet.toml's [vendored] table —
    <component> = { src = "<scaffold-src>", sha = "<scaffold-sha>", dest = "<dest-path>" }
    — dest defaults to the same relative path as src unless the component
    already lives somewhere else in this repo (check before assuming).

2b. RE-VENDOR: byte-copy every file under project-scaffolding's
    <scaffold-src> (at commit <scaffold-sha> — `git -C E:\automation\project-scaffolding
    show <scaffold-sha>:<path>` per file, or `git archive` for a directory) to
    <repo>'s <dest-path>, overwriting verbatim. Never hand-edit a copied file.

3. HASH-VERIFY: confirm every copied file's bytes match the scaffold source
   exactly (sha256 compare), component by component. If any mismatch, STOP and
   report — do not guess-fix, and do not land the other components without it.

4. Bump .fleet.toml: [vendored].<component>.sha = "<scaffold-sha>" (write the
   FULL sha, not the short form).

5. Run this repo's verification gate (its own CLAUDE.md / hooks/projects.toml
   gate_cmd — e.g.
   `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`,
   never bare `pwsh`: on this machine that is a 0-byte WindowsApps stub which
   fails non-interactively). Run it through the gate-time ledger so its
   wall-clock and result are recorded, prefixing the gate command:
   `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_wave/gate_ledger.py run --wave <wave-id> --repo <repo> -- <gate command>`
   — it exits with the gate's own exit code. A gate
   FAILURE is a genuine blocker: STOP, do not weaken or skip it, report FAILED.

6. Commit (conventional, no AI-attribution trailer) with the plan's
   `commit_subject` and `commit_body`. Push. Open the PR with the plan's
   `pr_body` verbatim, ticking a Test plan box only once you have observed it.
   It carries a one-line summary (a table of component, old sha, new sha when
   there are several), "Part of <scaffold-issue-or-PR-url>" — NEVER "Closes"
   (substring-match gotcha: GitHub's closing parser fires on
   `close(s|d)?`/`fix(es|ed)?` anywhere in the text, so a literal Closes/Fixes
   here could close the *scaffold's* tracking issue on merge) — and the Test
   plan. No paragraph hard-wraps (rendered markdown).

7. Auto-merge on green: `gh pr merge --auto --squash --delete-branch` (or the
   repo's own merge convention if its CLAUDE.md states one). Do not force-merge
   past a red gate/CI.
   **In `MODE=worktree`, drop `--delete-branch`** — from a worktree its local
   half fails (`'main' is already used by worktree at <primary>`). Once merged:
   `remove-worktree <worktree-path>`, then land the primary so the re-vendor is
   actually live there —
   `worktree_claim.py land-primary E:\automation\<repo> <worktree_key>`
   — and report its `PRIMARY=live behind=0` / `PRIMARY=stale reason=<why>` line.
   Then delete the refs explicitly: `git push origin --delete <branch>`, and the
   local ref with `-D` **only after** confirming the tip landed in
   `origin/<default>` (`git diff --quiet origin/<default> <branch>`). `-d` fails
   here by design — a squash merge rewrites the SHA (fleet-config#647).

8. If this repo has a tray (hooks/projects.toml tray_cmd/restart_cmd), restart
   it per that repo's CLAUDE.md recipe — only if the merge landed.

Report back, in this exact shape:
  - Repo: <repo>
  - Action: ADOPTED | RE-VENDORED | BOTH   (per component, when there are several)
  - Primary: PRIMARY=live behind=0 | PRIMARY=stale reason=<why> | n/a (primary mode)
  - Branch: <branch>
  - Gate: PASS | FAIL (<reason>) | NOT CONFIRMED (<reason>)   (+ the `LEDGER=recorded` line)
  - Result: MERGED (<merge-sha>) | PR-OPEN (<url>, gate/CI pending) | BLOCKED (<reason>)
  - PR: <url or n/a>

If the gate fails or a merge conflict appears, STOP and report BLOCKED — never
guess-fix, weaken the gate, or force anything.
```

### 5. Confirm fan-out and stand by

Print a confirmation block listing every dispatched agent (repo, action,
branch). Drain the contract’s dispatch/collect loop within this turn; never assume automatic reinvocation. Only terminal results proceed to aggregation.

### 6. Aggregate, then final report

As each agent returns, surface its report with a status mark (`✅ merged` /
`⚠️ PR open, pending` / `❌ blocked`). After all have returned:

```
/propagate-vendored <component>[,<component>…] @ <shortsha> — wave complete (<n> PRs for <k> component-repo pairs)

  ✅ merged:  <repo> <pr-url>, …
  ⚠️ open:    <repo> <pr-url> (<reason>), …
  ❌ blocked: <repo> — <reason>, …

  coverage: propagated to <n>/<N> declared adopters
            <m> undeclared carrier(s) found: <repos, or "none">
              - adopted (identical to HEAD): <repos, or "none">
              - reported only (differ from HEAD, human call): <repos, or "none">
            <k> repo(s) unknown (manifest unreadable): <repos, or "none">
            catalog: <c> components known | NOT KNOWN — carrier detection did not run (<reason>)
            contract: <j> adopter(s) held for removed selectors: <repo/component, or "none">

  no per-repo issues filed (by design — see the scaffold record).
```

Then print the run's gate time, built from the ledger and the step-2 plan's repos
(`--expect` makes a planned repo that recorded nothing show as unknown):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_wave/gate_ledger.py summary --wave <wave-id> --expect <repo>,<repo>,…
```

It lists each repo's gate time and result and a run total. A gate whose time could not
be read prints `unknown` and makes the total a lower bound; never restate it as 0 or as
a clean total. The records live in the machine-local `propagate-vendored/gate-ledger.jsonl`
under the hooks state directory.

**The coverage block is not decoration — it is the finding.** A wave that
prints only the re-vendored adopters reads as complete whether it covered
seven repos or one (`#228`'s fix reached `task-os` and left six repos on the
leaked-hostname copy, project-scaffolding#230). Print it even when every
number is zero, and never compress "found no carriers" and "could not look for
carriers" into the same line.

## Notes

- **fleet-config#338 ships the skill + manifest schema + drift helper only —
  it never edits a sister repo.** The nav + tray adopters (app-launcher,
  home-automation, local-llm-hub, photo-ocr, voice-transcriber, whatsapp-radar,
  grocery-shopping-automation) get their `[vendored]` entries written by step 4, item 2a
  ("ADOPT") the first time this skill actually runs against them.
- **Quality gate is upstream, not here.** `project-scaffolding#152` (source
  behavioral tests + a same-day-second-bug freeze rule) keeps a defective
  component from reaching the fan-out; this skill deliberately does not
  re-review the component's correctness.
- **If `project-scaffolding#153` (de-vendor the tray) lands,** tray components
  drop out of this skill's scope — a shared junctioned call replaces
  per-repo vendoring for machine-local infrastructure. The UI components
  (`_vendored/`) remain vendored + propagated exactly as here.
- **Drift-history lens is not built here:** `vendored_drift.py scan` is invoked
  per component, on demand.
