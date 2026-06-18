---
name: context-audit
description: Audit the fleet's always-on context surface — CLAUDE.md token budgets, skill-description word counts, and single-home-by-altitude violations (universal directives leaking into project files) — flagging drift, weekly. Use when the user wants to check the always-on context budget — e.g. "/context-audit", "audit my always-on context", "context budget audit". Also runs unattended on a weekly schedule.
---

# context-audit

**Goal:** Keep the **always-on context surface** lean and well-layered over time. Every `CLAUDE.md` and every skill *description* loads on every session of every project, so bloat and duplication there is a fleet-wide, every-session tax. Once a week (or on demand) measure that surface, flag where it violates the standard below, and record the trend — so drift is caught and corrected, not discovered years later.

**This is the home of the context-efficiency standard** (ferraroroberto/project-scaffolding#68). The standard lives *here*, in a skill body loaded only on invocation — deliberately **not** as prose in any `CLAUDE.md`, because a standard governing the always-on surface must not itself bloat it.

## The standard — single-home by altitude

Every directive lives in **exactly one place**, chosen by two axes:

1. **Universal vs shape-specific.** *Universal* = true for **every** repo on the machine, including a one-off with no UI, no tray, no launcher. *Shape-specific* = only meaningful for a project of a given shape (Streamlit, tray/daemon, e2e UI, GitHub-Actions CI). The test: *"would this still apply to a bare repo with no app?"* Yes → universal. No → shape-specific.
2. **Directive vs reference.** *Directive* = needed in (nearly) every session → stays always-on. *Reference* = only when doing X → belongs in an on-demand doc (`docs/<topic>.md`) or a skill body. **Exception:** trap-prevention *gotchas* earn their always-on slot even though they're "only when doing X", because the agent can't know to load them *before* hitting the bug.

The layering that falls out:

- **Universal directives → `global-CLAUDE.md`** (one home). Inherited by every session, including shapeless one-offs, with zero shape noise.
- **Shape-specific directives → the `project-scaffolding` master `CLAUDE.md`**, each gated `*apply only if this project…*`, inherited only by projects of that shape.
- **A project's own `CLAUDE.md`** carries *only* its project-specific instances (real ports, script names, the restart recipe) — it must **never restate a universal directive** (that's a single-home violation).
- **Skill `description:`** states only *what it does* + *when to trigger* (keyword/phrase cues), target **≤ ~50 words of prose** (quoted trigger examples are exempt — they must stay verbatim so routing never regresses). The *how it works* lives in the `SKILL.md` body.

## Lens separation

Three fleet audit lenses, kept distinct so each stays sharp:

- `/audit-fleet` + `/codebase-audit` → **project source code** quality.
- `/learning-log` → the **GitHub work stream** (PRs / issues), no source.
- `/context-audit` (this) → the **always-on context surface** — instruction/config files, not source.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **The helper measures; the orchestrator judges.** `audit.py` produces exact counts — never invent or round them. The *judgment* (is a flagged duplication a real universal-directive leak or a legitimate project instance? is a header divergence true drift or expected shape?) is the orchestrator's job.
- **Read-only except three writes:** the `kind=context-audit` ledger issue (upsert), its weekly comment, and the Slack ping. Never edits a `CLAUDE.md`, commits, pushes, or restarts. Fixes are *separate* issues/PRs (route them through `/cleanup-fleet` or file them).
- **Degrade gracefully, never block on a prompt** (this runs unattended): a missing file is reported and skipped; a quiet week still records the run so the ledger keeps cadence.
- **No AI attribution; no hard-wrapped paragraphs** (global `CLAUDE.md`).

## Steps

### 1. Measure the surface

```
py skills/context-audit/audit.py
```

Prints a `MANIFEST:` line (skills / over-cap / claude_mds / leaks / total_est_tokens) then four blocks — skill-description word counts vs the cap, the always-on token budget per `CLAUDE.md` (+ fleet total), single-home leaks (project lines duplicated verbatim from `global-CLAUDE.md`), and header overlap with the scaffold master. Capture it. `--json` emits the full structured report; `--cap N` overrides the word cap.

### 2. Judge + narrate

Read the manifest and classify, concisely:

- **Over-cap descriptions** — which are genuinely too verbose vs. merely example-heavy (prose already lean, only the exempt quoted triggers push the total up — those are fine).
- **Single-home leaks** — which duplicated lines are real universal-directive restatements (→ should be deleted from the project `CLAUDE.md`, inherited from global instead) vs. coincidental short matches. The big clusters are the fleet dedupe backlog.
- **Header drift** — projects whose shape-sections diverge from the scaffold master (excluding the ignored one-offs).
- **Budget trend** — compare the total + per-file tokens against the previous run recorded in the ledger; call out the largest files and any growth.

### 3. Upsert the ledger + record the week

Build a short markdown digest (single long lines): the manifest totals, the top offenders per category, and the week-over-week budget delta. Keep the durable archive (per-run totals) in the body; put the weekly narrative in a comment — same shape as `/audit-fleet` and `/learning-log`.

```
py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert --repo ferraroroberto/fleet-config \
  --kind context-audit --label audit-meta \
  --title "context-audit — always-on surface" --body-file <digest path>
```

Then add the weekly narrative as a comment on the returned issue.

### 4. Ping Slack

Activity-log traffic → `--category log` (the resolver picks the channel from `hooks/projects.toml`; never hardcode an id). Caption = the TL;DR (top numbers + biggest offender), with the digest attached:

```
cat <<'EOF' | py hooks/slack_notify.py --category log \
   --title "context-audit — always-on surface <YYYY-MM-DD>"
🧮 Weekly context-audit — <total>k always-on tokens, <N> over-cap descriptions, <M> single-home leaks
<the TL;DR>
EOF
```

### 5. Report

Print: the manifest totals, the biggest offender per category, the budget delta vs last run, the ledger issue URL, and the Slack result. A few lines.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) running weekly — same executor as `/insights-weekly` and `/audit-fleet`:

```
claude -p "/context-audit" --permission-mode bypassPermissions
```

cwd = `E:/automation/fleet-config`. The skill handles measure + judge + ledger + Slack itself.
