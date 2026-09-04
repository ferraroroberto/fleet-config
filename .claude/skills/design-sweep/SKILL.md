---
name: design-sweep
description: Run /design-sync across every FastAPI + static-PWA web app in the E:\automation fleet in one pass, emitting one combined digest (stdout + Telegram ping). The fleet-wide, unattended half of /design-sync — e.g. "/design-sweep", "sweep the fleet for design drift", "weekly design sync across all apps". Also runs weekly unattended.
---

# design-sweep

**Goal:** a fleet-wide, idempotent, scatter-gather wrapper around `/design-sync` (`fleet-config#178`). Deterministically gate the fleet down to its **token-styled web apps** (skip non-web repos and Streamlit POC spikes), run the per-repo `/design-sync` logic against each through a bounded window of **Sonnet** sub-agents, then collect the results into **one combined digest**: stdout (so a scheduled run captures it) + a Telegram ping via `notify_complete.py --kind design`.

## Arguments

- No argument → the whole fleet.
- One argument that looks like a repo name (e.g. `/design-sweep app-launcher`) → restrict to that single repo. Match the bare repo name.
- Anything else → treat as no argument (whole fleet).

## Execution rules (read before running any command)

- **The orchestrator only does cheap, safe work:** the deterministic web-app gate (one Python sweep), windowed dispatch, collection, the digest. **All CSS reading and lint happens inside sub-agents** — keeps weekly token spend low enough for an all-app sweep.
- **Never disturb in-progress work.** `/design-sync` is read-only on source (files issues, never switches branches or edits code) — safe on a repo in any state, but never pass `apply`, never touch a repo's tree.
- **Never background a tool call in this skill.** This orchestrator runs headless via `run-weekly.bat`'s one-shot Claude process — no persistent turn loop, no human, **no wake-up mechanism**. Launching any command (the step-2 gate sweep, a sub-agent dispatch) with `run_in_background: true` and ending the turn to "wait for it" silently kills the run: the CLI exits on that clean turn-end reporting `exit_code: 0` (false success) while nothing past that point happened (`fleet-config#314`). Every command — including waiting on in-flight sub-agents — must run synchronously (foreground) or poll to completion within the same turn (e.g. the `Monitor` tool's until-loop) — never fire-and-forget.

## Steps

Run in order. A failure on one repo is reported and skipped; it does not abort
the whole run. Only a pre-flight failure (step 1) stops everything.

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. If not, stop:
  "Not authenticated — run `gh auth login`."
- Confirm `E:\automation\` exists (the fleet root). Else stop.

### 2. One Python sweep: gate the fleet to its web apps

Enumeration + the web-app gate is **one deterministic Python sweep** — a single
tool call whose JSON the orchestrator reads, never a per-repo LLM loop:

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/design_sweep_scan.py --root E:\automation [--only <repo-name>]
```

The script (`skills/_lib/design_sweep_scan.py`) reuses `/audit-fleet`'s filesystem crawl (every `ferraroroberto` repo under the root, linked worktrees skipped) and the *same* web-app detection `/design-sync`'s step 2 uses (`design_lint.repo_files` + `parse_custom_props`) — a repo counts as a web app iff it has a tracked, non-spike stylesheet defining `--custom-props` in a `:root`/dark block; Streamlit-only POCs (a `streamlit_app.py` with no FastAPI signal) are skipped.

It prints one JSON object:

```
{"web_apps": [{"repo": "...", "path": "..."}, ...],
 "skipped_non_web": [{"repo": "...", "reason": "..."}, ...],
 "skipped_streamlit": [{"repo": "...", "reason": "..."}, ...],
 "errors": [{"repo": "...", "reason": "..."}, ...]}
```

Print a one-line plan from the JSON, e.g.:

```
Design sweep plan — 8 web apps, 26 non-web skipped, 1 Streamlit skipped
  sweep: app-launcher, grocery-shopping-automation, home-automation, local-llm-hub, photo-ocr, voice-transcriber, whatsapp-radar, github-copilot-usage
```

If `web_apps` is empty, jump to step 5 with an empty result set (the digest still goes out so the weekly run always produces a record).

### 3. Sweep each web app — a bounded window of Sonnet sub-agents

Process the `web_apps` list through a **bounded concurrency window of up to 4 Sonnet sub-agents** (one per repo). This window is a token-pacing default, not a rate limiter — Sonnet is exempt from the ≤3-Opus cap. Dispatch up to 4 background `Agent` calls (`run_in_background: true`, `subagent_type: "general-purpose"`, **`model: "sonnet"`**) to fill the window, then **stay in this same turn and block on `TaskOutput` (`block: true`) for every task now in flight** — do not end the turn to "wait for it". An unpolled background task is silently killed at the CLI's background-task ceiling and the run reports a false `exit 0` (`fleet-config#506`, same gap `fleet-config#314` closed for this skill's own Python-sweep/digest calls, just never stated for this `Agent`-dispatch loop). If a `TaskOutput` call times out before a task finishes, re-issue the same blocking call. As each task returns, record its report and immediately dispatch the next repo — never more than 4 in flight, and the turn must never end while any task is still dispatched. No git worktrees needed: `/design-sync` (report-only) never edits a tree, so agents in different repo directories cannot collide.

Prompt template (substitute `<name>` / `<path>`):

```
Run the per-repo design-drift check on the <name> repo.

1. cd to <path>.
2. Execute the procedure in
   E:\automation\fleet-config\skills\design-sync\SKILL.md against this repo,
   REPORT-ONLY (never pass `apply`). That skill runs the deterministic
   design_lint helper (tokens, adoption, contracts, vendored bytes, siblings),
   the step-1b tailnet-cert check, applies light judgment, and files exactly one
   deduped `design-drift` issue (and, independently, a `cert-drift` issue if the
   cert check trips) through the shared audit_issue.py machinery. Follow it
   exactly — including its "no drift → file nothing" rule.
3. Do NOT edit source, commit, push, restart, or run `/design-sync apply`.
   Filing/updating the design-drift and cert-drift issues are the only writes.

Report back in this exact shape so the orchestrator can build the digest:
  - Repo: <name>
  - Result: DRIFT (<n> findings) | IN-SYNC (no drift) | SKIPPED (<reason>)
  - Contracts: <p> PASS · <w> WARN · <f> FAIL (<failing ids, or "-">)
  - Adoption: color <0.xx> · font-size <0.xx> · radius <0.xx> · spacing <0.xx>
  - Rendered leg: <ran (results) | unmeasured — no rendered-geometry harness>
  - Cert: <ok | drift, filed #N>
  - Filed: <design-drift issue URL, or "none (in sync)">
  - Note: <one line if anything surprising came up>
```

Print a one-line progress marker per repo as it completes (e.g. `[3/8] home-automation — DRIFT (4)`) so a scheduled run's console shows forward motion. The whole loop runs inside one turn: block on `TaskOutput` for the in-flight window, refill on each return, repeat until `web_apps` is drained — the turn never ends with a sub-agent still dispatched (`fleet-config#506`).

### 4. Collect results

Hold each sub-agent's structured report as it returns. When the `web_apps` list is drained with no agent still in flight, proceed to the digest. A sub-agent that errors out is recorded as `ERROR` for its repo (a genuine single-repo failure); it does not block the others and the window refills as normal.

### 5. Build and deliver the digest

Compose the digest as markdown (single long lines per paragraph, no hard wraps — the global CLAUDE.md rendered-markdown rule). This markdown is the canonical artifact and goes to stdout verbatim. Structure the per-repo results as a table:

- **Header:** date, counts — `N web apps swept, D drifted, C in sync, F findings filed`, plus `X cert-drift` when any sub-agent reported a non-`ok` `Cert:` line.
- **Per-app row:** result + findings count + contract PASS/WARN/FAIL + the filed `design-drift` URL (or "in sync"). In-sync apps get a one-liner. Note the adoption ratios (trend signal) and any `unmeasured` rendered leg.
- **Cert-drift section** *(only when non-empty)*: one line per repo whose step-1b check tripped, with the filed `cert-drift` issue — points at `project-scaffolding#89`.
- **Skipped section:** the `skipped_non_web` + `skipped_streamlit` counts from step 2 (one aggregate line each) — visible which repos were intentionally left out.
- **Where the counts persist:** one line noting the fleet-wide week-over-week design-drift accounting lives in the `audit-fleet digest state` ledger (via `/audit-fleet`), not here.

Deliver on two channels:

- **stdout:** print the full markdown digest. Always.
- **Telegram ping:** call `notify_complete.py --kind design` with a one-line
  summary. Deterministic — the skill hands the hook exact structured args:

  ```
  E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
    --kind design \
    --summary "<N> swept | <D> drifted | <F> findings filed"
  ```

  Keep the summary **pure ASCII** and separate its parts with `|` — the hook
  renders that as `·` from a Python literal. A literal `·` typed into the command
  line reached the chat as `??` (fleet-config#507): a Windows command line is not a
  UTF-8-safe channel, so the separator must never travel as an argv character.

  This is a silent no-op if no `telegram_chat` is configured; it always
  exits 0 and can never block or delay the finish.

### 6. Final report

One concise block: the plan line from step 2, per-repo results, and where the
digest went (stdout always; Telegram pinged or no-op). Stop.

## Hard rules

- **The web-app gate is one shared Python implementation, not prose.** Step 2's `design_sweep_scan.py` reuses `design_lint.repo_files` / `parse_custom_props` — the *same* detection `/design-sync`'s step 2 describes — so the sweep's population can never drift from the per-repo lint. Unit-tested independent of git/gh in `tests/run_acceptance.py`.
- **Report-only on source.** This orchestrator and its sub-agents never edit code, commit, push, restart, or run `/design-sync apply`. Every write is a `design-drift` or `cert-drift` issue.
- **Opus orchestrator, Sonnet sub-agents.** The top-level orchestrator runs on **Opus** (`hard` tier — enumeration/gating/digest reasoning is where a mistake is most expensive) and is the **only** Opus session in flight; per-repo `/design-sync` is deterministic-lint-heavy with only light judgment, so it runs on **Sonnet** through a ≤4 pacing window (Sonnet is exempt from the ≤3-concurrent-Opus burst cap — `docs/model-tiers.md`, global CLAUDE.md).
- **No separate design ledger.** This skill owns no week-over-week counts: fleet's design-drift accounting lives in the single `audit-fleet digest state` ledger issue, where `/audit-fleet` tallies open `design-drift` + `cert-drift` issues per repo on its own weekly run — one place, alongside the six code buckets, never conflated with them (`fleet-config#180`). This sweep is the *doer* (files/refreshes the per-repo issues); `/audit-fleet` is the unified *reporter*, this digest is a transient this-run roll-up.
- **Degrade, don't block.** Built for unattended `claude -p`. A per-repo failure is reported and skipped; only a pre-flight failure stops the whole run. Never wait on an interactive prompt, never background a tool call and end the turn (`fleet-config#314`).
- **No AI attribution; no hard-wrapped digest paragraphs** (per global CLAUDE.md).

## Notes

- **`design-drift` and `cert-drift` are first-class audit buckets** (`audit_issue.py` `KINDS`) — `/cleanup-fleet design-drift` clears the design bucket fleet-wide and `/cleanup-fleet cert-drift` the cert bucket, both unchanged by this sweep (it only files/refreshes the issues they consume).
- **The per-repo detector is `/design-sync`** (global `skills/`); this is its fleet-wide orchestrator, kept in the fleet-only `.claude/skills/` tier so its description stays out of unrelated sessions' context (`fleet-config#161`).
- **The weekly job** lives in app-launcher (`config/jobs.json`) and calls this repo's `.claude/skills/design-sweep/run-weekly.bat`; `jobs.sample.json` carries the committed example.
