---
name: context-audit
description: Audit the fleet's always-on context surface — CLAUDE.md token budgets, skill-description word counts, and single-home-by-altitude violations (universal directives leaking into project files) — flagging drift. E.g. "/context-audit", "audit my always-on context", "context budget audit". Also runs unattended weekly.
---

# context-audit

**Goal:** keep the **always-on context surface** lean and well-layered. Every `CLAUDE.md` and every skill *description* loads on every session of every project, so bloat there is a fleet-wide, every-session tax. Weekly (or on demand): measure that surface, flag violations of the standard below, record the trend.

**Home of the context-efficiency standard** (ferraroroberto/project-scaffolding#68) — lives *here*, in a skill body loaded only on invocation, deliberately **not** as prose in any `CLAUDE.md` (a standard governing the always-on surface must not itself bloat it).

## The standard — single-home by altitude

Every directive lives in **exactly one place**, chosen by two axes:

1. **Universal vs shape-specific.** *Universal* = true for **every** repo, including a one-off with no UI/tray/launcher. *Shape-specific* = only meaningful for a given shape (Streamlit, tray/daemon, e2e UI, GitHub-Actions CI). Test: *"would this still apply to a bare repo with no app?"* Yes → universal. No → shape-specific.
2. **Directive vs reference.** *Directive* = needed in (nearly) every session → always-on. *Reference* = only when doing X → an on-demand doc (`docs/<topic>.md`) or a skill body. **Exception:** trap-prevention *gotchas* earn their always-on slot anyway — the agent can't know to load them *before* hitting the bug.

The layering that falls out:

- **Universal directives → `global-CLAUDE.md`** (one home), inherited by every session including shapeless one-offs.
- **Shape-specific directives → the `project-scaffolding` master `CLAUDE.md`**, each gated `*apply only if this project…*`.
- **A project's own `CLAUDE.md`** carries *only* its project-specific instances (real ports, script names, the restart recipe) — it must **never restate a universal directive** (that's a single-home violation).
- **Skill `description:`** states only *what it does* + *when to trigger* (keyword/phrase cues), target **≤ ~50 words of prose** (quoted trigger examples are exempt — they must stay verbatim so routing never regresses). The *how it works* lives in the `SKILL.md` body.

## Lens separation

Three fleet audit lenses, kept distinct:

- `/audit-fleet` + `/codebase-audit` → **project source code** quality.
- `/learning-log` → the **GitHub work stream** (PRs / issues), no source.
- `/context-audit` (this) → the **always-on context surface** — instruction/config files, not source.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **The helper measures; the orchestrator judges.** `audit.py` produces exact counts — never invent or round them. Judgment (real universal-directive leak vs. legitimate project instance; true drift vs. expected shape) is the orchestrator's job.
- **Read-only except three writes:** the `kind=context-audit` ledger issue (upsert), its weekly comment, and the Telegram ping. Never edits a `CLAUDE.md`, commits, pushes, or restarts. Fixes are *separate* issues/PRs (route through `/cleanup-fleet` or file them).
- **Degrade gracefully, never block on a prompt** (runs unattended): a missing file is reported and skipped; a quiet week still records the run so the ledger keeps cadence.
- **No AI attribution; no hard-wrapped paragraphs** (global `CLAUDE.md`).

## Steps

### 1. Measure the surface

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/context-audit/audit.py
```

Prints a `MANIFEST:` line (skills / compliant / over-cap / unmeasured / repos / claude_mds / leaks / total_est_tokens) then six blocks — skill-description prose counts vs the cap (labelled `<repo>/<skill>`), the unmeasured list, the per-repo roll-up, the always-on token budget per `CLAUDE.md` (+ fleet total), single-home leaks (project lines duplicated verbatim from `global-CLAUDE.md`), and header overlap with the scaffold master. Capture it. `--json` emits the full structured report; `--cap N` overrides the word cap.

**Scope of the cap gate (fleet-config#626).** Measures **every fleet repo's** `.claude/skills/*/SKILL.md` — membership from `fleet_repos()` (`hooks/projects.toml`), same list `/system-map` and `/config-map` read, so a new repo is covered the day it's added — plus fleet-config's junctioned `skills/` tier (always-on in every repo's sessions). Measurement lives in `skills/_lib/skill_description.py`, shared with `/context-purge`'s `check.py`.

**`unmeasured` is not `compliant`.** A `SKILL.md` that cannot be read, carries no `description:`, or belongs to a missing repo checkout reports as `unmeasured`, excluded from both compliant and over-cap counts. Treat non-zero `unmeasured` as a finding in its own right — a gate that silently shrinks its own working set is what made `over_cap=0` technically true and completely false.

### 2. Judge + narrate

Read the manifest and classify, concisely:

- **Over-cap descriptions** — genuinely too verbose vs. merely example-heavy (prose already lean, only the exempt quoted triggers push the total up — fine). Name the repo, not just the skill.
- **Unmeasured descriptions** — report each one and why. Never round `unmeasured` into the compliant count or the narrative.
- **Single-home leaks** — real universal-directive restatements (→ delete from the project `CLAUDE.md`, inherit from global) vs. coincidental short matches. Big clusters = the fleet dedupe backlog.
- **Header drift** — projects whose shape-sections diverge from the scaffold master (excluding ignored one-offs).
- **Budget trend** — compare total + per-file tokens against the previous run recorded in the ledger; call out the largest files and any growth. **Ledger rows dated before 2026-08-15 are contaminated**: until fleet-config#629 the budget scan counted transient `<repo>-wt-<N>` worktree siblings as fleet projects, inflating `Total tok`/`CLAUDE.mds` (6.5% when measured). Don't rewrite them; don't read a drop across that boundary as a real saving.

### 3. Upsert the ledger + record the week

Build a short markdown digest (single long lines): the manifest totals, the top offenders per category, the week-over-week budget delta. Durable archive (per-run totals) in the body; weekly narrative in a comment — same shape as `/audit-fleet` and `/learning-log`.

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert --repo ferraroroberto/fleet-config \
  --kind context-audit --label audit-meta \
  --title "context-audit — always-on surface" --body-file <digest path>
```

Then add the weekly narrative as a comment on the returned issue.

### 4. Ping Telegram

Activity-log traffic → `--category log` (the resolver picks the channel from `hooks/projects.toml`; never hardcode an id). Caption = the TL;DR (top numbers + biggest offender), with the digest attached:

```
cat <<'EOF' | E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --title "context-audit — always-on surface <YYYY-MM-DD>"
🧮 Weekly context-audit — <total>k always-on tokens, <N> over-cap descriptions (<U> unmeasured), <M> single-home leaks
<the TL;DR>
EOF
```

### 5. Report

Print: the manifest totals, the biggest offender per category, the budget delta vs last run, the ledger issue URL, and the Telegram result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) running weekly — same executor as `/insights-weekly` and `/audit-fleet` — targeting `.claude/skills/context-audit/run-weekly.bat`; it preserves `/context-audit` plus bypass permissions and streams filtered milestones through `claude_progress.py`.

cwd = `E:/automation/fleet-config`. The skill handles measure + judge + ledger + ping itself.
