---
name: propagate-vendored
description: Fan out a byte-for-byte re-vendor of one project-scaffolding component to every adopter in its [vendored] manifest — copy, hash-verify, bump the sha, run the gate, open an auto-merging PR, no per-repo issue. Dry-run reports who is behind, touching nothing. Invoke explicitly, never per scaffold commit. E.g. "/propagate-vendored nav", "/propagate-vendored nav a1b2c3d", or "/propagate-vendored nav --dry-run".
---

# propagate-vendored

**Goal:** Turn a `project-scaffolding` component fix into a one-command,
Dependabot-style distribution wave instead of N hand-filed issues + N
hand-built PRs (the 2026-07-09/10 cascade: ~40 mechanical byte-copy PRs across
6–7 repos for two components — `project-scaffolding#144–#150`). The scaffold
change already carries the decision; a byte-for-byte re-vendor carries none —
so this skill never files a per-repo issue, only opens auto-merging PRs that
link back to the scaffold record.

Companion doc: `skills/propagate-vendored/README.md` (the manifest-schema
decision writeup — `.fleet.toml [vendored]` vs a separate `VENDORED.lock`, and
the concrete `build_data.py` conflict check that was run and came back clean).
Schema reference: `architecture/README.md`'s "Optional per-repo `[vendored]`
table" section.

## Cadence — explicit or batched, never reflex-fired

Invoke this skill **by hand**, or as a periodic batch (e.g. weekly), never
automatically per scaffold commit. Four propagation waves in one day (the
cascade this skill exists to fix) is the anti-pattern regardless of how cheap
each wave is — collect scaffold changes, propagate once. Nothing in this
skill schedules itself; there is no `run-weekly.bat` here by design.

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

## Execution rules (read before running anything)

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
  yet is not skipped — see step 3's "adopt" sub-step. This is how the manifest
  goes from zero adopters (today) to covering the real nav + tray consumers,
  without this PR touching a single sister repo.
- **Degrade, don't block.** A per-adopter failure (gate red, merge conflict,
  CI red) is reported and left for a human; it never stops the rest of the
  wave.
- **`--dry-run` never writes anything** — no clone, no branch, no file, no PR,
  no manifest bump. It only runs `vendored_drift.py` and reports.
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

For the **adopt** discovery — finding a repo that already carries the
component's files but has no manifest entry recording it — `no_manifest`
alone doesn't distinguish "never touched this component" from "has it,
unlabeled." Resolve that with one targeted sweep, scoped to the component's
conventional path (UI components: `app/webapp/static/_vendored/<component>/`;
tray primitives: the specific file, e.g. `app/tray/tray_lifecycle.ps1`):

```
git -C E:\automation\<repo> ls-files -- "<component-path>"
```

run once per `no_manifest` repo (or, cheaper, one `Glob`/`git grep`-style
sweep across `E:/automation/*/<component-path>` first, then confirm per hit).
A repo with files at that path and no manifest entry is an **adopt**
candidate (step 4a); a repo with nothing there is genuinely not an adopter —
skip it, it is out of scope for this component.

### 3. `--dry-run` / `check` stops here

Print the report and stop — no clone, no branch, no PR, nothing written:

```
/propagate-vendored <component> --dry-run — scaffold @ <short-sha> (<ref>)

  adopters (declared in [vendored]):
    <repo>   pinned <short-sha>   local: OK|DRIFT (<n> files)   vs HEAD: current|BEHIND (<n> files)
    …
  adopt candidates (component present, no manifest entry yet):
    <repo>   <component-path> found, undeclared
    …
  no signal (<n> repos): <comma-separated, or "none">

Run `/propagate-vendored <component>` (no --dry-run) to fan out the real wave.
```

### 4. Real run: fan out one sub-agent per target repo

Target repos = every `behind_head`/`local_drift` adopter from step 2, plus
every adopt-candidate from step 2's sweep. For each, dispatch a background
sub-agent (`run_in_background: true`, easy tier per the execution rules
above). Worktree setup mirrors `/issue-batch` step 6 — pre-create the branch
in the orchestrator via `worktree_claim.py`, sequentially, before any agent
launches:

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
   gate_cmd — e.g. `pwsh -File scripts/verify-before-ship.ps1`). A gate
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

8. If this repo has a tray (hooks/projects.toml tray_cmd/restart_cmd), restart
   it per that repo's CLAUDE.md recipe — only if the merge landed.

Report back, in this exact shape:
  - Repo: <repo>
  - Action: ADOPTED | RE-VENDORED | BOTH
  - Branch: chore/revendor-<component>-<shortsha>
  - Gate: PASS | FAIL (<reason>)
  - Result: MERGED (<merge-sha>) | PR-OPEN (<url>, gate/CI pending) | BLOCKED (<reason>)
  - PR: <url or n/a>

If the gate fails or a merge conflict appears, STOP and report BLOCKED — never
guess-fix, weaken the gate, or force anything.
```

### 5. Confirm fan-out and stand by

Print a confirmation block listing every dispatched agent (repo, action,
branch). Then stop — do not poll. The harness re-invokes as each returns.

### 6. Aggregate, then final report

As each agent returns, surface its report with a status mark (`✅ merged` /
`⚠️ PR open, pending` / `❌ blocked`). After all have returned:

```
/propagate-vendored <component> @ <shortsha> — wave complete

  ✅ merged:  <repo> <pr-url>, …
  ⚠️ open:    <repo> <pr-url> (<reason>), …
  ❌ blocked: <repo> — <reason>, …

  no per-repo issues filed (by design — see the scaffold record).
```

## Hard rules

- **No per-repo issues, ever.** The scaffold issue/PR is the sole record.
- **`Part of …`, never `Closes …`** in a generated PR body.
- **Vendor verbatim.** Never hand-edit a copied file — a re-vendor that needs
  a local tweak means the tweak belongs upstream in `project-scaffolding`
  first, not in the adopter's copy (`_vendored/README.md`'s rule).
- **Hash-verify before bumping the manifest sha.** A copy that doesn't
  byte-match the scaffold source is a bug in this skill, not something to
  paper over by writing the sha anyway.
- **`--dry-run` writes nothing.** Not a manifest bump, not a branch, nothing.
- **One agent per repo/checkout**, worktree-claimed exactly like `/issue-batch`.
- **Degrade, don't block.** A blocked adopter is reported and left for a
  human; the rest of the wave proceeds.
- **No AI attribution; no hard-wrapped PR-body paragraphs** (global CLAUDE.md).

## Notes

- **This PR (fleet-config#338) ships the skill + manifest schema + drift
  helper only — it never edits a sister repo.** The nav + tray adopters
  (app-launcher, home-automation, local-llm-hub, photo-ocr, voice-transcriber,
  whatsapp-radar, grocery-shopping-automation) get their `[vendored]` entries
  written by step 4a ("ADOPT") the first time this skill actually runs against
  them — not hand-added here. That first run is this skill's own acceptance
  test.
- **Quality gate is upstream, not here.** `project-scaffolding#152` (source
  behavioral tests + a same-day-second-bug freeze rule) is what keeps a
  defective component from ever reaching this skill's fan-out — propagation
  distributes whatever quality ships, including defects, so it deliberately
  does not re-review the component's correctness.
- **If `project-scaffolding#153` (de-vendor the tray) lands,** tray components
  drop out of this skill's scope — a shared junctioned call replaces
  per-repo vendoring for machine-local infrastructure. The UI components
  (`_vendored/`) remain vendored + propagated exactly as here.
- **Drift-history lens is a natural follow-up, not built here:** a future
  `/audit-fleet`-style weekly sweep could call `vendored_drift.py scan` with
  no `--component` filter and report the whole fleet's drift in one digest —
  today it's invoked per component, on demand.
