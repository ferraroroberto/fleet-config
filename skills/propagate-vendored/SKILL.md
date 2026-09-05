---
name: propagate-vendored
description: Fan out a byte-for-byte re-vendor of one project-scaffolding component to every adopter in its [vendored] manifest — copy, hash-verify, bump the sha, run the gate, open an auto-merging PR, no per-repo issue. Dry-run reports who is behind, touching nothing. Invoke explicitly, never per scaffold commit. E.g. "/propagate-vendored nav", "/propagate-vendored nav a1b2c3d", or "/propagate-vendored nav --dry-run".
---

# propagate-vendored

**Capability preflight:** read [workflow-capabilities](../../docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional Claude examples; the contract governs adaptation. Keep this skill’s worktree, independent-review, human-review and shipping gates.

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
per scaffold commit. Four propagation waves in one day is the anti-pattern
however cheap each wave is — collect scaffold changes, propagate once. Nothing
here schedules itself; there is no `run-weekly.bat` by design.

## Arguments

`/propagate-vendored <component> [scaffold-sha] [--dry-run | check]`

- **`<component>`** — required. The manifest key (`nav`, `tray_lifecycle`, …)
  — same name adopters use in their `[vendored].<component>` entry.
- **`[scaffold-sha]`** — optional. The scaffold commit to propagate to.
  Default: `project-scaffolding`'s current `origin/HEAD` (its default branch
  tip) — i.e. "bring every adopter to what's on scaffold `main` right now."
- **`--dry-run`** (or the bare word `check`) — report-only. Runs the drift
  scan, prints who's behind, touches nothing: no branch, no clone, no PR. Safe
  to run any time.

No component argument → stop: "Pass a component name, e.g.
`/propagate-vendored nav` or `/propagate-vendored nav --dry-run`."

## Rules (read before running anything)

- **Model tier: `easy`** (per `docs/model-tiers.md`) — narrow, mechanical,
  zero design decisions; full-autonomy execution shape. On Claude Code today
  that's Sonnet at high effort, fanned out **all at once** (Sonnet is exempt
  from the Opus concurrency cap — see `~/.claude/CLAUDE.md`).
- **One sub-agent per adopter repo**, never two against the same checkout.
  Respect `skills/_lib/worktree_claim.py` exactly as `/issue-batch` does — a
  repo another session is actively working gets `MODE=worktree`, never a
  collision on `main`.
- **No per-repo issues, ever.** The scaffold issue/PR is the single record;
  see the README's "Why this skill never files a per-repo issue."
- **`Part of …`, never `Closes …`** in a generated PR body — GitHub's
  closing-keyword parser matches substrings anywhere in the text, so a
  literal `Closes #N` in a distribution PR risks closing the scaffold issue
  on the very first adopter merge (the substring-match gotcha in
  `~/.claude/CLAUDE.md`).
- **Adopt before re-vendor.** A repo with no `[vendored].<component>` entry
  yet is not skipped — see step 4a's "adopt" sub-step. This is how the manifest
  grows to cover the real nav + tray consumers.
- **State coverage; a partial wave must never read as a complete one.** The
  adopter list comes from the adopters' own `[vendored]` entries, so a repo
  carrying the component without declaring it is invisible to it. Always report
  both numbers — declared adopters propagated *and* undeclared carriers found —
  and report a repo whose manifest could not be read, or a scaffold with no
  `[components]` catalog, as its own **unknown** rather than folding either into
  the clean count (project-scaffolding#230).
- **Vendor verbatim.** Never hand-edit a copied file — a re-vendor that needs
  a local tweak means the tweak belongs upstream in `project-scaffolding`
  first, not in the adopter's copy (`_vendored/README.md`'s rule).
- **Vendoring standardizes whatever you vendor, including mistakes — review
  the component's user-facing wording as carefully as its mechanism before
  propagating it fleet-wide.** Byte-identical + hash-verified means a
  locally-softened copy registers as *drift*, so a bad default reads as the
  safe choice everywhere it lands. Before a real (non-`--dry-run`) wave,
  confirm the scaffold source's wording actually matches its own cited
  reference implementation, not just that the bytes hash-match.
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

### 2. Run the drift scan (always — dry-run and real runs both start here)

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/vendored_drift.py scan --component <component>
```

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
  the repo simply never recorded what it copied. An **adopt** candidate (step 4a).
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

A repo appearing in neither list carries nothing catalogued and is genuinely
not an adopter — skip it, it is out of scope for this component.

### 3. `--dry-run` / `check` stops here

Print the report and stop — no clone, no branch, no PR, nothing written:

```
/propagate-vendored <component> --dry-run — scaffold @ <short-sha> (<ref>)

  adopters (declared in [vendored]):
    <repo>   pinned <short-sha>   local: OK|DRIFT (<n> files)   vs HEAD: current|BEHIND (<n> files)
    …
  undeclared carriers (component present, no manifest entry):
    <repo>   <component-path>   identical to HEAD — adopt
    <repo>   <component-path>   DIFFERS (<n> files) — stale or deliberately forked, human call
    …
  no signal (<n> repos): <comma-separated, or "none">

  coverage: <n> declared adopters, <m> undeclared carriers, <k> unknown (<repos, or "none">)
            catalog: <c> components known | NOT KNOWN (<reason>)

Run `/propagate-vendored <component>` (no --dry-run) to fan out the real wave.
```

### 4. Real run: fan out one sub-agent per target repo

Target repos = every `behind_head`/`local_drift` adopter from step 2, plus
every `matches_head: true` carrier (adopt-only — its bytes are already current).
A `matches_head: false` carrier is **not** a target by default: surface it and
ask, then include it only on an explicit yes. For each target, dispatch a background
worker through the capability contract (easy tier, bounded by available slots). Worktree
setup mirrors `/issue-batch` step 6 — pre-create the branch in the orchestrator
via `worktree_claim.py`, sequentially, before any agent launches:

```
git -C E:\automation\<repo> fetch origin
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire E:\automation\<repo>
```

`MODE=primary` → work in place on `main`. `MODE=worktree` → 
`setup-worktree E:\automation\<repo> <component>-<shortsha> chore/revendor-<component>-<shortsha>`
(same helper `/issue-batch` uses; junctions `.venv`, reparse-safe teardown).

#### Agent prompt

```
You are re-vendoring the "<component>" component from project-scaffolding
into <repo>, off scaffold commit <scaffold-sha> (short: <shortsha>).
Working tree: <path>. Branch: chore/revendor-<component>-<shortsha>.
You are the only agent touching this checkout.

1. cd to <path>. Confirm you are on the branch (cut it if MODE was primary
   and it doesn't exist yet: `git checkout -b chore/revendor-<component>-<shortsha>`
   off fresh origin/main).

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
   exactly (sha256 compare). If any mismatch, STOP and report — do not guess-fix.

4. Bump .fleet.toml: [vendored].<component>.sha = "<scaffold-sha>" (write the
   FULL sha, not the short form).

5. Run this repo's verification gate (its own CLAUDE.md / hooks/projects.toml
   gate_cmd — e.g.
   `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`,
   never bare `pwsh`: on this machine that is a 0-byte WindowsApps stub which
   fails non-interactively). A gate
   FAILURE is a genuine blocker: STOP, do not weaken or skip it, report FAILED.

6. Commit (conventional, no AI-attribution trailer):
   "chore: re-vendor <component> from project-scaffolding@<shortsha>"
   Push. Open a PR whose body:
     - Summary: one line, what changed + why (byte-for-byte re-vendor).
     - "Part of <scaffold-issue-or-PR-url>" — NEVER "Closes" (substring-match
       gotcha: GitHub's closing parser fires on `close(s|d)?`/`fix(es|ed)?`
       anywhere in the text, so a literal Closes/Fixes here could close the
       *scaffold's* tracking issue on merge).
     - Test plan: gate result, hash-verify result.
   No paragraph hard-wraps (rendered markdown).

7. Auto-merge on green: `gh pr merge --auto --squash --delete-branch` (or the
   repo's own merge convention if its CLAUDE.md states one). Do not force-merge
   past a red gate/CI.
   **In `MODE=worktree`, drop `--delete-branch`** — from a worktree its local
   half fails (`'main' is already used by worktree at <primary>`). Once merged:
   `remove-worktree <worktree-path>`, then land the primary so the re-vendor is
   actually live there —
   `worktree_claim.py land-primary E:\automation\<repo> <component>-<shortsha>`
   — and report its `PRIMARY=live behind=0` / `PRIMARY=stale reason=<why>` line.
   Then delete the refs explicitly: `git push origin --delete <branch>`, and the
   local ref with `-D` **only after** confirming the tip landed in
   `origin/<default>` (`git diff --quiet origin/<default> <branch>`). `-d` fails
   here by design — a squash merge rewrites the SHA (fleet-config#647).

8. If this repo has a tray (hooks/projects.toml tray_cmd/restart_cmd), restart
   it per that repo's CLAUDE.md recipe — only if the merge landed.

Report back, in this exact shape:
  - Repo: <repo>
  - Action: ADOPTED | RE-VENDORED | BOTH
  - Primary: PRIMARY=live behind=0 | PRIMARY=stale reason=<why> | n/a (primary mode)
  - Branch: chore/revendor-<component>-<shortsha>
  - Gate: PASS | FAIL (<reason>)
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
/propagate-vendored <component> @ <shortsha> — wave complete

  ✅ merged:  <repo> <pr-url>, …
  ⚠️ open:    <repo> <pr-url> (<reason>), …
  ❌ blocked: <repo> — <reason>, …

  coverage: propagated to <n>/<N> declared adopters
            <m> undeclared carrier(s) found: <repos, or "none">
              - adopted (identical to HEAD): <repos, or "none">
              - reported only (differ from HEAD, human call): <repos, or "none">
            <k> repo(s) unknown (manifest unreadable): <repos, or "none">
            catalog: <c> components known | NOT KNOWN — carrier detection did not run (<reason>)

  no per-repo issues filed (by design — see the scaffold record).
```

**The coverage block is not decoration — it is the finding.** A wave that
re-vendors every declared adopter and prints nothing else reads as complete
whether it covered seven repos or one — how `#228`'s fix reached `task-os` and
left six repos on the leaked-hostname copy (project-scaffolding#230). Print it
even when every number is zero, and never compress "found no carriers" and
"could not look for carriers" into the same line.

## Notes

- **fleet-config#338 ships the skill + manifest schema + drift helper only —
  it never edits a sister repo.** The nav + tray adopters (app-launcher,
  home-automation, local-llm-hub, photo-ocr, voice-transcriber, whatsapp-radar,
  grocery-shopping-automation) get their `[vendored]` entries written by step 4a
  ("ADOPT") the first time this skill actually runs against them.
- **Quality gate is upstream, not here.** `project-scaffolding#152` (source
  behavioral tests + a same-day-second-bug freeze rule) keeps a defective
  component from reaching this skill's fan-out — propagation distributes
  whatever quality ships, including defects, so it deliberately does not
  re-review the component's correctness.
- **If `project-scaffolding#153` (de-vendor the tray) lands,** tray components
  drop out of this skill's scope — a shared junctioned call replaces
  per-repo vendoring for machine-local infrastructure. The UI components
  (`_vendored/`) remain vendored + propagated exactly as here.
- **Drift-history lens is not built here:** `vendored_drift.py scan` is invoked
  per component, on demand; a future `/audit-fleet`-style weekly sweep with no
  `--component` filter (whole-fleet drift in one digest) is a possible
  follow-up.
