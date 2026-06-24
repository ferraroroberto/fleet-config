# fleet-config

Versioned home for my user-scope [Claude Code](https://docs.claude.com/en/docs/claude-code) configuration — the hooks, helpers, skills, slash commands, statusline, and global `CLAUDE.md` that live in `~/.claude/` and shape how Claude behaves across every project on my machine.

The hooks here are project-aware via a single `hooks/projects.toml` registry: generic at code level, per-project nuance (ports, pre-ship gate triggers, "never kill these ports") in one TOML file.

## Why this repo exists

`~/.claude/` is a kitchen sink — cache, transcripts, plans — so it can't all be a git repo. But the *config* inside it (hooks, skills, the global `CLAUDE.md`) is real source code: it shapes every Claude session, breaks silently when typoed, and needs to be reviewed, diffed, and reverted like any other code. Before this repo, edits to `~/.claude/hooks/*` and friends were unversioned. Now they aren't.

## What's in here today

11 hooks under `hooks/` that enforce the rituals I kept correcting Claude on, across the home-stack fleet:

| Hook | Event | What it does |
|---|---|---|
| `pre_commit_no_ai_trailer.py` | `PreToolUse` on `Bash` | Blocks `git commit` messages that include `Co-Authored-By: Claude`, `Generated with Claude Code`, etc. |
| `secret_scan_guard.py` | `PreToolUse` on `Bash` | Blocks `git commit` when a live credential is staged — scans `git diff --cached` and the command string for real Slack bot tokens (`xoxb-…`); placeholder forms (`xoxb-…`, `xoxb-<token>`) in docs are deliberately allowed and never trip it. |
| `gh_body_file_guard.py` | `PreToolUse` on `Bash` | Non-blocking nudge: an inline `gh issue/pr create\|comment\|edit --body` carrying a heredoc/backtick (Bash mangles it) → use `--body-file <tmp>`; a PowerShell here-string (`@'…'@`) run through the Bash tool → wrong shell. `--body-file`/`-F` and plain `gh` reads pass silently. |
| `safe_kill_guard.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks blanket `Stop-Process -Name python(w)?` (would nuke sister hubs), `git push --force` to main, `--no-verify`. Port-scoped kills against the project's own webapp port pass through. |
| `venv_discipline.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks `python -m venv venv` (the user's canonical name is `.venv`), `.\.venv\Scripts\activate`, bare `python`/`pip` when a project `.venv` exists. |
| `py_syntax_check.py` | `PostToolUse` on `Edit` / `Write` for `*.py` | Runs `py_compile` against the project's `.venv` and surfaces syntax errors inline. ~50 ms per edit. |
| `docs_dated_filename_guard.py` | `PreToolUse` on `Write` | Blocks a `Write` of a `YYYY-MM-DD-`prefixed file under a `docs/` directory — `docs/` is durable reference, not dated retrospectives (the issue + PR + `git log` are the changelog). Override with `CLAUDE_HOOKS_ALLOW_DATED_DOCS=1`. |
| `hub_bypass_warn.py` | `PostToolUse` on `Edit` / `Write` for `*.py` | Non-blocking nudge when a `*.py` outside the LLM-hub repo spawns an inline `claude -p` subprocess → route through the local hub at `127.0.0.1:8000` via the standard SDKs instead. |
| `browser_stealth_lint.py` | `PostToolUse` on `Edit` / `Write` | Non-blocking nudge when a browser-launch file (`chrome_launch.py` / `browser.py` / `*_session.py`) launches Chrome but is missing a stealth marker (`--enable-automation` strip, `navigator.webdriver` init, `channel="chrome"`, `AutomationControlled`) → import the project's single-source launch helper. |
| `restart_and_verify_webapp.py` | slash command `/restart-webapp` | Project-aware: looks up the webapp port from `projects.toml`, kills only that PID, then brings the webapp back — via the project's `restart_cmd` (a `WebappManager` respawn the tray adopts, for tray-owned apps) or `tray.bat` — and polls `/api/version` until `git_sha == HEAD`, reporting the new `asset_hash`. Never touches the `:8446` session-host. |
| `notify_on_idle.py` | `Notification` | Opt-in (off unless a project/`[global]` `slack_notify_channel` is set): pings Slack via `slack_notify` when a live session is **blocked** on input (a permission gate or `AskUserQuestion`), so an AFK human gets a phone notification — routed to the `#attention` channel (issue #139). No-ops on the 💤 idle nag. |

`hooks/pi_usage_stats.py` is the Pi usage bridge for Coding stats. It reads Pi's native JSONL sessions under `~/.pi/agent/sessions/` and emits content-free telemetry — agent=`pi`, cwd/project, provider, model, message/tool-call counts, and token/cost totals from each assistant message's `usage` block. Example: `py hooks/pi_usage_stats.py --json --include-sessions`. This is the source app-launcher (or any fleet stats job) should ingest for Pi instead of scraping terminal transcripts; prompt/response text is deliberately omitted.

Alongside the hooks, `hooks/slack_notify.py` is a shared **Slack-notify transport** (importable + CLI, stdlib-only) any skill / hook / unattended job can call to fire a real bot-identity notification — zero install via the `hooks/` junction. It posts text (`chat.postMessage`) or **uploads a file** (`--file`, via Slack's external-upload flow) — e.g. `/system-map` posts its rendered PNG with it. On top of it, `hooks/notify_complete.py` is the **deterministic skill-completion ping** the `issue-*` / fleet skills call (`--kind add|start|finish|yolo|batch|audit|cleanup|recap|finish-batch|learning`): it builds the one canonical message and pulls the real GitHub link from `gh` in Python rather than letting the model paraphrase. Every machine→me ping carries an **intent category** routed to a dedicated channel (issue #139) — `#attention` for act-now pings (blocked-on-input, `start`/`batch`/`cleanup`-with-review) vs `#log` for the activity record (filed/shipped/merged, the `/system-map` image, the `/insights-weekly` digest) — single-sourced in `_lib.resolve_slack_target(cwd, category=…)`, which falls back to `slack_notify_channel` when a category channel is unset. The PR-shipping kinds (`finish` / `yolo`) append a **work-summary roll-up** under the canonical line — the file/LOC shape of the merged change (`📊 +N −M · K files` + new/changed/deleted buckets) — computed deterministically by `hooks/work_summary.py` from `gh pr view` (no LLM). Its CLI (`py hooks/work_summary.py --pr <N|url>`) additionally prints a churn-sorted **per-file table** that `/issue-finish` and `/issue-yolo` echo into the chat report; the table is chat-only (Slack mrkdwn has no tables). Both degrade to nothing on any `gh` error, so a stats hiccup never blocks a finish. The mention decision is single-sourced in `slack_notify.notify()` and defaults off (the `[global] slack_notify_mention` toggle). The full Slack story (bot helper vs session hook vs the native "Claude in Slack" remote control, plus one-time setup) is in [`docs/slack-workflow.md`](docs/slack-workflow.md).

Two more **project-wired** hooks (opt-in per project via `projects.toml` `capture = true`; currently just life-os, and wired from that project's own `settings.json` rather than user-scope) form a **conversation-memory engine**. `conversation_capture.py` (Stop) writes each finished session to markdown; `session_index.py` (SessionStart) lazily runs `conversation_index.py` to digest *settled* captures — once per conversation, after it ends — into a per-folder `index.md` (topic / decisions / open loops, pointing back at the raw capture), so a consumer knows what happened recently without bulk-loading transcripts. The digest goes through `hooks/hub_client.py`, the shared stdlib-`urllib` client for the local LLM hub (OpenAI-shape, fail-open — mirrors `slack_notify`). Routing is config-driven: `capture_routing = "flat"` (one `conversations/` + `index.md` per project, the default) or `"skills"` (per-skill dirs, routed by an `active_marker`; life-os's setup). Generic by design — any repo opts in with one `projects.toml` block.

The original Tier 2 / Tier 3 follow-up plan was triaged once Tier 1 had burned in (fleet-config#158): the three hooks that still earned their slot — `docs_dated_filename_guard`, `hub_bypass_warn`, `browser_stealth_lint` — shipped into the table above; the rest were dropped as low-signal or deferred (session-start fleet status → the Fleet Board work in #91).

### Skills

Skills live in **two tiers**, both versioned in this repo:

- **Global** (`skills/`, junctioned → `~/.claude/skills` + `~/.agents/skills`) — load in every session in every repo: the issue-workflow set (`/issue-add`, `/issue-start`, `/issue-finish`, `/issue-yolo`, `/issue-triage`, `/issue-batch`, `/issue-finish-batch`), `/handoff-commit`, `/screen`, `/codebase-audit`, `/design-sync`, and the shared `_lib/` helpers. `/design-sync` is global (not fleet-only) because it operates on *sister* web-app repos and must load there.
- **Fleet-only** (`.claude/skills/`, project-scoped — Claude Code loads it only when cwd is `fleet-config`) — the whole-fleet orchestrators that never make sense from a single sister repo: `/audit-fleet`, `/cleanup-fleet`, `/context-audit`, `/insights-weekly`, `/learning-log`, `/system-map`, `/config-map`. Living under `.claude/skills/` instead of the junctioned `skills/` keeps their descriptions out of every *unrelated* session's always-on context (fleet-config#161) while still loading them here. `/codebase-audit` deliberately stays **global** even though it's an audit — `/audit-fleet` fans out sub-agents that `cd` into each repo and invoke `/codebase-audit` there, so it has to be available everywhere. One caveat: project-scoped `.claude/skills/` is a Claude Code concept; Codex reads `~/.agents/skills` (the global junction) only, so these seven fleet skills don't reach Codex — acceptable, since they run via `claude -p` on a schedule.

Beyond the issue-workflow trio (`/issue-add`, `/issue-start`, `/issue-finish`) and `/handoff-commit`, this repo also ships:

- **`/codebase-audit`** — read-only quality sweep of the resting codebase against its `CLAUDE.md` and senior-dev standards. Bundles findings into at most 6 GitHub issues (one per fixed bucket: duplication, stale/dead code, CLAUDE.md drift, maintainability, bugs, and documentation — README/`docs/` that drift from `CLAUDE.md`, repeat themselves, go stale, or omit a shipped feature), self-assigned, deduped against open issues. Complements the diff-scoped `/code-review` / `/simplify` / `/security-review`. Idempotent: a per-repo `audit-meta` **ledger issue** records the audited commit SHA + rubric hash, so a re-run over an unchanged repo short-circuits before reading any files. Each whole-repo audit also posts a counts-only `<!-- audit-snapshot -->` comment to that ledger issue — a per-category findings table (date · sha · the six buckets · total) — so opening a repo's ledger shows its findings *trajectory* over time, kept off the gate's hot path (which only reads the ledger body). Each managed issue (ledger + one per bucket) carries a hidden `<!-- audit-managed: kind=… -->` marker, and the skill upserts through `skills/_lib/audit_issue.py` — so a re-run reuses and merges into the one issue per type rather than ever filing a duplicate.
- **`/design-sync`** — the **web-app design-conformance** detector. Reads the fleet design system (`~/.claude/design.md` light + `~/.claude/design.dark.md` dark — Google's `design.md` schema, Vercel's two-file light/dark convention, GitHub-mobile palette), maps its tokens onto a target app's CSS custom properties (light + dark), and surfaces the values that have **drifted** from the spec — plus whether the app honors the spec's navigation/interaction contract (the floating bottom-tab pill). It files exactly one deduped `design-drift` issue per repo through the *same* `skills/_lib/audit_issue.py` upsert machinery as `/codebase-audit`, so `design-drift` is a first-class audit bucket: `/cleanup-fleet design-drift` fans out fixers and `/issue-triage` treats it like any other. Default mode is read-only on code (reports + files the issue); `/design-sync apply` writes the aligned token values into the working tree for review. Skips Streamlit POC spikes and never re-authors nav/components (those are vendored verbatim from `project-scaffolding`). The fleet-wide weekly sweep + `/audit-fleet` digest integration are a tracked follow-up — until then, run it per repo.
- **`/audit-fleet`** — scatter-gather wrapper that runs `/codebase-audit` across every `ferraroroberto` repo under `E:\automation\`, skipping the unchanged ones via the ledger gate, auditing the changed ones through a bounded window of up to 3 concurrent sub-agents (a single-message parallel fan-out across the whole fleet trips Anthropic's burst rate limit; the 3-wide cap is the fleet-wide Opus concurrency window defined in the global `CLAUDE.md`), and emitting one diff-based weekly digest (GitHub comment on the digest-state issue + Slack ping + stdout). Built to run unattended on a weekly app-launcher job. Alongside the rot it files, it runs a **second lens on the same read** — a *fleet practices ledger*: each sub-agent surfaces hard-won reusable solutions and generalizable-convention candidates (the inverse of a finding — an asset to remember, not rot to fix), and the orchestrator merges them into one living catalog issue in `project-scaffolding` so the best of each repo is discoverable fleet-wide.
- **`/cleanup-fleet <bucket>`** — the fix-half of the audit loop. Takes one bucket label (`documentation`, `claude-md-drift`, `bug`, …), gathers every open issue carrying it across the fleet, scores each for complexity, and fans out **one background agent per repo** sized to the work: Sonnet for easy issues (full `/issue-yolo` → merged, each firing its own PR-link ping) and Opus for complex ones (build → stop for your review, ≤3 Opus agents in flight via the fleet-wide Opus concurrency window). One agent per repo by construction (the audit files one issue per repo per bucket). `hard` mode (default) presents the plan for approval and runs both tiers; `easy`/`silent` mode runs only the Sonnet tier unattended and never auto-merges hard-scored work — safe to schedule after `/audit-fleet`. Once you've reviewed the Opus branches, `/issue-finish-batch` ships them in parallel (below).
- **`/issue-finish-batch <branches>`** — the parallel-finish step after `/cleanup-fleet` or `/issue-batch` build-and-stop. Given a set of already-reviewed branches, it fans out **one background Sonnet finisher per branch** (all at once — Sonnet is exempt from the Opus cap), each running the full `/issue-finish` flow one-shot (push, PR, CI-advisory, merge, delete branch, tray restart) and reporting back **only** on a genuine blocker a human must resolve. User-triggered, never automatic; per-branch `✅ Done` pings are kept and a single `🏁 Finished batch` roll-up closes the run. Manual `/issue-finish` per branch stays the always-available fallback.
- **`/system-map`** — regenerate the fleet **architecture map**: crawl `hooks/projects.toml` (minus `[global] architecture_ignore`), reconcile it against the single source of truth (`architecture/fleet.data.js`) and the narrative doc (`architecture/ARCHITECTURE.md`), render the light, horizontal, Janis-style infographic to `architecture/system-map.png` with headless Chrome (placeholder specs only — real hardware specs stay in the gitignored `system-map.local.js`), commit the diff when the map changed, and post the fresh image to Slack on every run — with a one-line week-over-week **what-changed** summary (`+whatsapp-radar, −suna, 3 repos updated`, from `.claude/skills/system-map/whatchanged.py` diffing the working `fleet.data.js` against the last commit). The acceptance matrix asserts the fleet ⇄ data file ⇄ doc never drift. Built to run unattended on a weekly app-launcher job.
- **`/config-map`** — regenerate the fleet **config & convention map**: the cross-agent sibling of `/system-map`. Where the architecture map answers *"what runs in the fleet?"*, this answers *"what configuration does each coding agent get?"* — the per-agent capability matrix (Claude Code · Codex · Pi · Copilot · Antigravity), the skill inventory (universal / fleet-orchestration / repo-specific), the hook inventory (blocking vs nudge, the Claude-full vs Codex-subset split), and the convention surface (`global-CLAUDE.md`, the design system, single-home-by-altitude). **Derived, not declared** (config is centralized, not per-repo): `.claude/skills/config-map/build_data.py` *introspects* `install.ps1`'s link table + the `skills/`/`hooks/` dirs + `settings.template.json`/`codex-hooks.json` + a per-repo git sweep into the generated `architecture/config.data.js`, overlaying only the non-derivable bits from `architecture/config.residual.json`. Renders the light Janis-style infographic to `architecture/config-map.png`, commits the diff when it changed, and posts to Slack with a week-over-week what-changed line (`whatchanged.py`) — same plumbing as `/system-map`. The dataset holds only wiring/structure, never a secret. The **descriptive** companion to `/context-audit` (which is prescriptive — it flags drift); the acceptance matrix asserts `config.data.js` can't go stale. Built to run unattended on a weekly app-launcher job, staggered off `/system-map`'s slot. Built in #207.
- **`/insights-weekly`** — turn Claude Code's built-in **`/insights`** report into a week-over-week signal. `/insights` already writes a dated, self-contained `report-<timestamp>.html` into `~/.claude/usage-data/` on every run, so that series *is* the history; this skill refreshes it, hands the **newest two reports to the local LLM hub** (`127.0.0.1:8000`, via `report.py` + `extract.py`) to narrate what changed — no raw-JSON re-aggregation, and the orchestrator doesn't write the narrative itself. The result is saved as a dated, traceable note under `~/.claude/usage-data/weekly/` (user-local, never committed) and a concise digest is posted to Slack. First run captures a baseline instead of a diff; model is `INSIGHTS_DIFF_MODEL`-overridable (default `claude_sonnet`). Built to run unattended on a weekly app-launcher job (first run targeted for a Friday).
- **`/learning-log`** — the **journey + productivity lens**: turn the fleet's *GitHub work stream* (merged PRs + closed issues across every `ferraroroberto` repo since the last run) into a weekly **learning log + productivity stats + forward horizon**. Scatter-gather like `/audit-fleet`: `gather.py` reads each repo's merged PRs + closed issues **per repo** (`gh pr list` / `gh issue list` — REST, so the whole window is covered with no cap and no search rate-limit), buckets every item by work type (PRs by conventional-commit prefix, issues by type label), computes **exact productivity tables** (PRs / issues / LOC by project and by work-type bucket — Python over `gh` JSON, never model-invented), and partitions the items into one file per bucket. The orchestrator then fans out **one Sonnet sub-agent per bucket** (Sonnet is exempt from the Opus concurrency cap, so they run in parallel), each returning a **fixed format** that *extracts insights* — recurring root-causes, decisions, durable lessons — not a restatement of PR titles. It weaves the bucket insights into a themed digest, **grades last week's horizon** (shipped / slipped / emerged-unplanned) and sets the next, and pastes the stats tables verbatim. It reads **no source code** — purely GitHub. The durable archive + live horizon live in one canonical `kind=learning` ledger issue (deduped via `skills/_lib/audit_issue.py`, labelled `audit-meta`); each week's digest is a comment on it; a `📓 Learning log` Slack ping links straight to it. The window anchors to the ledger's `last-run-at` (a missed run widens the next, never drops a week). GitHub gives per-repo Pulse/contributor stats but nothing cross-fleet or per-work-type, so these tables are additive, not a duplicate. Deliberately separate from `/audit-fleet` (code), `/system-map` (architecture — cross-linked, not modified) and `/insights-weekly` (usage metrics). Built to run unattended on a weekly app-launcher job.

### Concurrent same-repo work (claim-or-worktree)

Two agent sessions working the **same** repo used to collide: they shared one working directory, so a `git checkout` in session B rewrote the tree under session A mid-build (fleet-config#143). The collision window isn't branch-cut time — it's the minutes-long *study* phase before either agent writes anything. So the fix claims the repo at the **very first action**, before reading the issue: **first come, first owns `main`.**

`skills/_lib/worktree_claim.py` is the one concurrency primitive. `/issue-start`'s step 0 calls `acquire <repo>`, which atomically `mkdir`s a claim under the repo's shared git dir (`git rev-parse --git-common-dir`, visible from every worktree). The first session wins `MODE=primary` and works in place on `main`, exactly as before; every session after gets `MODE=worktree` and builds in an isolated sibling worktree `<repo>-wt-<N>` on its own branch, sharing the object store — separate HEAD + separate files, no race. `/issue-finish` releases the claim (primary) or tears the worktree down (worktree); the release is a **verified** step (it confirms `status` → `CLAIM=free`, so a foreign or abbreviated finisher can't silently skip it), and `/issue-yolo`'s inline-merge path releases the claim itself in Phase 4 since it has no separate finish (fleet-config#174). A claim left behind anyway self-heals on the next `acquire`: it's reclaimed once it ages past the 8h TTL **or** as soon as its recorded branch no longer exists on the remote (a merged-and-deleted branch is definitionally done — `is_stale` checks `git ls-remote`). Because `/issue-yolo`, `/issue-batch` in-place, and `/cleanup-fleet` all route through `/issue-start`, they inherit the behaviour for free.

Two Windows specifics, both load-bearing. **The `.venv`** — worktrees don't share untracked files, and a 24-repo fleet can't recreate heavy venvs per worktree, so the helper *junctions* (`mklink /J`) the primary's `.venv` into the worktree; a junctioned venv resolves `sys.prefix` and imports through the link, so the verification gate (`& .\.venv\Scripts\python.exe …`) just works. **The teardown order** — `git worktree remove` does a recursive delete that *follows a junction into its target*, so the helper strips the `.venv` junction with `rmdir` (reparse-safe, no `/s`) **before** `git worktree remove`, or it would wipe the primary's real venv (proven the hard way — same junction footgun as `uninstall.ps1`, fleet-config#136). Never hand-roll `git worktree add`/`remove` + a venv junction in a skill; the helper owns both halves. The `<repo>-wt-<N>` sibling naming is deliberate: it still prefix-matches the repo's `cwd_prefix` in `projects.toml`, so `notify_on_idle` names the right project (a `.worktrees/` layout would break that match). `/audit-fleet`'s filesystem crawl skips linked worktrees via a `.git`-is-a-directory guard.

### UX-conformance gate (diff-keyed design check)

`skills/_lib/ux_surface.py` is the second cross-skill primitive — it decides, deterministically, whether an issue's change touched a web app's UX so the issue-workflow skills can gate the fleet design check on the *diff* rather than re-deriving a glob match in three places (fleet-config#195; convention + contract in `project-scaffolding#83`). A repo declares its surface in a `## UX surface` block in its own `CLAUDE.md` (`design spec applies`, `paths`, `key views`); a repo without one — `fleet-config` itself, any non-web repo, a Streamlit spike — yields `SPEC_APPLIES=no`, so the gate is a permanent no-op there. `applies <repo>` (block only, no git) drives the cheap design-aware spec-load at `/issue-start`; `check <repo>` adds `git diff --name-only <main>...HEAD` and reports `TOUCHED` + the `MATCHED` files for the gate at `/issue-finish` (step 3b) and `/issue-yolo` (Phase 3e). When the diff *did* touch UX, the gate fixes material token drift in-branch (not file-and-defer — that's `/design-sync`'s periodic job) and visually verifies the touched view via the `verify` skill. **The screenshot is inspected in-session only and never attached to a PR/issue/comment** — every repo is treated as public, so an uploaded UI screenshot is an information breach; the PR carries a text-only conformance line instead. Its block parser, brace expansion, glob→regex, and diff intersection are pure-logic unit-tested (`tests/test_ux_surface.py`, reached from the one acceptance gate).

## Layout

```
fleet-config/
├── README.md
├── CLAUDE.md                       # short — tells future-Claude how this repo works
├── global-CLAUDE.md                # exposed as ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.pi/agent/AGENTS.md, ~/.copilot/copilot-instructions.md (symlinks) — agent-neutral global instructions
├── design.md                       # exposed as ~/.claude/design.md (symlink) — fleet web-app design system (light); navigation + interaction contract (rationale + references: docs/design-system.md)
├── design.dark.md                  # exposed as ~/.claude/design.dark.md (symlink) — same token names, dark values (Vercel light/dark convention)
├── statusline-command.ps1          # exposed as ~/.claude/statusline-command.ps1 (symlink) — custom statusline (Claude only)
├── .gitignore
├── install.ps1                     # creates junctions/symlinks into the agent homes: ~/.claude, ~/.agents, ~/.codex, ~/.pi/agent, ~/.copilot
├── uninstall.ps1                   # removes only the links install.ps1 created, leaves the homes otherwise untouched
├── hooks/                          # junction → ~/.claude/hooks AND ~/.codex/hooks (Codex)
│   ├── _lib.py                     # shared: project detection, port→PID, stdin-JSON, projects.toml loader
│   ├── projects.toml               # per-project nuance (ports, gate triggers, never-kill ports)
│   ├── run-hook.ps1                # single shared shim — every hook is wired through this via settings.template.json / codex-hooks.json
│   ├── pre_commit_no_ai_trailer.py
│   ├── secret_scan_guard.py
│   ├── gh_body_file_guard.py       # PreToolUse on Bash: nudge gh --body heredocs/backticks → --body-file; PowerShell here-strings in Bash
│   ├── safe_kill_guard.py
│   ├── venv_discipline.py
│   ├── py_syntax_check.py
│   ├── docs_dated_filename_guard.py   # PreToolUse on Write: block dated YYYY-MM-DD- filenames under docs/
│   ├── hub_bypass_warn.py             # PostToolUse on *.py: nudge inline `claude -p` → route through the local hub
│   ├── browser_stealth_lint.py        # PostToolUse: nudge a browser-launch file missing the anti-bot stealth kwargs
│   ├── restart_and_verify_webapp.py   # also exposed as /restart-webapp
│   ├── notify_on_idle.py            # Notification hook (via run-hook.ps1): opt-in Slack ping
│   ├── slack_notify.py              # shared Slack-notify transport (importable + CLI, stdlib-only)
│   ├── notify_complete.py           # deterministic skill-completion ping (issue-* skills call this); finish/yolo carry a work-summary roll-up
│   ├── work_summary.py              # deterministic PR work-summary (file/LOC roll-up + per-file table) from `gh`, no LLM; importable + CLI
│   ├── pi_usage_stats.py            # content-free Pi session usage collector (provider/model/tokens from ~/.pi/agent/sessions)
│   ├── conversation_capture.py     # Stop hook: captures a session to markdown (projects.toml-driven, opt-in; wired from the project's own settings.json)
│   ├── session_index.py            # SessionStart hook: lazily digests settled captures into conversations/index.md
│   ├── conversation_index.py       # the indexer (lib + CLI) session_index runs; digests via the hub
│   └── hub_client.py               # shared stdlib-urllib client for the local LLM hub (OpenAI-shape, fail-open)
├── commands/                       # junction → ~/.claude/commands AND ~/.codex/prompts (Codex prompts)
├── pi/extensions/statusline.ts      # junction via pi/extensions/ → ~/.pi/agent/extensions — custom Pi footer/statusline
├── skills/                         # junction → ~/.claude/skills, ~/.agents/skills (Codex+Pi), ~/.copilot/skills (Copilot) — GLOBAL tier: issue-* workflow, handoff-commit, codebase-audit, design-sync, screen, _lib/, …
├── .claude/skills/                 # project-scoped — FLEET-ONLY tier, loads only in fleet-config: audit-fleet, cleanup-fleet, context-audit, config-map, insights-weekly, learning-log, system-map (fleet-config#161)
├── docs/                           # references: slack-workflow, codex-browser, mcp-context-audit, design-system
├── tests/run_acceptance.py         # drives each hook with a sample stdin payload
├── settings.template.json          # the `hooks` block to merge into your ~/.claude/settings.json (Claude)
└── codex-hooks.json                # exposed as ~/.codex/hooks.json (symlink) — Codex's hooks wiring (same run-hook.ps1 shim)
```

The live `~/.claude/settings.json` is **not** in this repo — it carries machine-local permissions and secrets. Only `settings.template.json` ships, showing the `hooks` block to copy in.

## Install

Windows + PowerShell 7+ (or 5.1):

```powershell
git clone https://github.com/ferraroroberto/fleet-config.git
cd fleet-config
.\install.ps1
```

`install.ps1` exposes the repo's contents inside the supported **agent homes** — `~/.claude` (Claude Code), `~/.agents` (the cross-agent skills location), `~/.codex` ([Codex](https://developers.openai.com/codex/)'s own home), `~/.pi/agent` ([Pi](https://github.com/parallel-web/pi)'s config dir), and `~/.copilot` ([GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)) — via two link kinds:

- **Junctions** for the directory entries. Cross-volume OK, no admin. `hooks/` and `commands/` are each junctioned into **both** `~/.claude` and `~/.codex`, `skills/` into `~/.claude/skills` + `~/.agents/skills` (Codex+Pi) + `~/.copilot/skills` (Copilot), and `pi/extensions/` into `~/.pi/agent/extensions` for Pi's footer extension — so every agent loads the *same live files* and nothing can drift between them.
- **Symlinks** for the single-file entries (`global-CLAUDE.md` → `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, and `~/.copilot/copilot-instructions.md`; `codex-hooks.json` → `~/.codex/hooks.json`; `statusline-command.ps1`). Cross-volume file linking on Windows requires admin or Developer Mode, so the installer self-elevates with **one UAC prompt** the first time it needs to create them. Reinstalls that find the symlinks already in place stay UAC-free.

### Cross-agent parity — one source, every agent

One install, one source of truth: editing a hook, the global `CLAUDE.md`, the statusline, or a skill **once** should be live in every coding agent that can read it. The full matrix — what each agent supports, what is wired, and where a class is a deliberate non-goal (#189):

| Config class | Claude Code | Codex | Pi | Copilot | Antigravity |
|---|---|---|---|---|---|
| **Global instructions** (context file) | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | `~/.pi/agent/AGENTS.md` | `~/.copilot/copilot-instructions.md` | — *(IDE; no user-home context file)* |
| **Design system** | `~/.claude/design.md` + `design.dark.md` | *(read via `~/.claude`)* | *(read via `~/.claude`)* | *(read via `~/.claude`)* | — |
| **Hooks** (code + wiring) | `~/.claude/hooks/` + `settings.json` | `~/.codex/hooks/` + `~/.codex/hooks.json` | ❌ no hook surface | ❌ no hook surface | ❌ (IDE) |
| **Skills / prompts** | `~/.claude/skills/` + `commands/` | `~/.agents/skills/` + `~/.codex/prompts/` | `~/.agents/skills/` (same junction) | `~/.copilot/skills/` (own junction) | ❌ no native skills dir — plugin-only |
| **Statusline** | `~/.claude/statusline-command.ps1` | native TUI footer (`config.toml` `tui.status_line`) | `~/.pi/agent/extensions/statusline.ts` custom footer | ❌ none | ❌ (IDE) |
| **Settings / permissions** | `settings.json` (manual merge — holds secrets) | `~/.codex/config.toml` | `~/.pi/agent/settings.json` *(tool-managed)* | `~/.copilot/config.json` *(tool-managed)* | VS Code settings |

The **wired** classes (a single repo source linked into each home) are the context file (all four CLI agents), hooks/prompts/design for Claude+Codex, Pi's footer extension, and **skills for all four CLI agents** — Claude (`~/.claude/skills`), Codex + Pi (the shared `~/.agents/skills` junction), and Copilot (its own `~/.copilot/skills` junction). The links: `global-CLAUDE.md` → `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, `~/.copilot/copilot-instructions.md`; `codex-hooks.json` → `~/.codex/hooks.json`; `hooks/`, `commands/`, `skills/`, and `pi/extensions/` junctioned per the table.

**`global-CLAUDE.md` is agent-neutral**: it reads correctly as any of those files, and the few genuinely Claude-specific sections (the 3-wide Opus sub-agent cap; the Git-Bash-strips-backslashes-in-`settings.json` gotcha) are marked *(Claude Code only — skip on other agents)*. Unlike Claude's `settings.json` (which mixes in machine-local secrets and so stays a manual merge), Codex's `hooks.json` is hooks-only, so it is symlinked live from `codex-hooks.json` — the same `run-hook.ps1` shim runs on both agents (Codex does **not** route hooks through Git Bash, so its command paths may use backslashes).

**Pi and Copilot context paths were verified empirically, not assumed** (#189): a sentinel context file was placed in each candidate location and the agent run non-interactively (`pi -p` / `copilot -p`) from a neutral cwd to see which path it actually read. Pi reads `~/.pi/agent/AGENTS.md` (its `PI_CODING_AGENT_DIR` config dir — it ignores `~/.pi/AGENTS.md` and a home-dir `~/AGENTS.md`); Copilot reads `~/.copilot/copilot-instructions.md` (not `~/.copilot/AGENTS.md`). Both load plain markdown, so `global-CLAUDE.md` links verbatim with no format translation.

**Documented non-goals** (intentionally *not* wired, so this doesn't get re-attempted):

- **Hooks for Pi / Copilot / Antigravity** — none of them expose a lifecycle-hook mechanism (`pi --help` / `copilot --help` have no hook surface; Antigravity is an IDE). Hooks remain a Claude-Code + Codex-only capability.
- **Settings / permissions for Pi and Copilot** — their settings files (`~/.pi/agent/settings.json`, `~/.copilot/config.json`) are **rewritten by the tool itself**, so symlinking a repo source over them would break the tool or be clobbered on next launch. Left to each tool to manage.
- **Statusline beyond Claude/Codex/Pi** — Copilot and Antigravity have no statusline surface.
- **Skills for Antigravity (`agy`)** — `agy` *does* now ship a CLI, but it has **no native user-skills directory** it auto-scans: `agy --help` exposes no skill/prompt flag, and skills load *only* by installing a plugin (`agy plugin install …` → `~/.gemini/antigravity-cli/plugins/<name>/skills/`). The candidate global paths other tools use (`~/.agents/skills`, `~/.gemini/config/skills`) are not read by `agy` and don't exist on this box, so there is no clean single-source junction to make — wiring it would mean maintaining a forked plugin bundle, the exact drift #160 set out to avoid. Recorded as a non-goal (#160); revisit if `agy` adds a scanned user-skills dir. The other classes (context/hooks/statusline) remain non-goals too — Antigravity is an IDE-first tool with no user-home context/hook/statusline file to link.

**Why Codex skills live in `~/.agents/skills`, not `~/.codex/skills`.** This trips people up because Codex *does* have a `~/.codex/skills/` directory — but that's Codex-owned: it holds Codex's own bundled skills under `~/.codex/skills/.system/` (imagegen, skill-creator, skill-installer, …), marked with a `.codex-system-skills.marker`. The [official skills doc](https://developers.openai.com/codex/skills) lists `$HOME/.agents/skills` as *the* USER skill location — `~/.codex/skills` is **not** a documented user path. So the `~/.agents/skills` junction is correct, and we deliberately do **not** also junction into `~/.codex/skills`: it can't be a whole-directory junction anyway (the installer won't clobber the real `.system/` dir), and exposing each skill via two scanned roots would double-list every skill in Codex's selector (Codex doesn't merge same-named skills — both appear). If you see Codex *guess* a `~/.codex/skills/.system/<name>/SKILL.md` path and 404 before reading the skill from `E:\…\fleet-config\skills`, that's the model fumbling the path once and self-correcting, not a broken link — the skill still loads.

**One format, every agent (#160).** The worry that porting the issue-workflow skills to other agents would need per-agent *translation* turned out to be moot: Claude Code, Codex, Pi, and Copilot have all converged on the **same `SKILL.md` format** (a `<name>/SKILL.md` folder with `name` + `description` frontmatter), and each scans a user-skills directory automatically — Codex/Pi at `~/.agents/skills` ([Codex doc](https://developers.openai.com/codex/skills); [Pi doc](https://pi.dev/docs/latest/skills), which also reads `~/.agents/skills`), Copilot at `~/.copilot/skills` ([Copilot doc](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference), auto-discovered, no enable step or restart). So the whole port is a **junction**, not a translation layer: one `skills/` source, junctioned into each home, edited once. The `_lib/` helper dir has no `SKILL.md`, so every agent's discovery ignores it as a skill (it's still importable by the absolute `C:/Users/rober/.claude/skills/_lib/…` paths the skills call, which resolve on any agent).

**Validated:** all four CLI agents discover these skills live. Codex (gpt-5.5) loads and runs them — e.g. invoking the `screen` skill executed `skills/screen/SKILL.md` straight from the repo. Pi (`pi -p`) and Copilot (`copilot -p`) both confirm `issue-start` in their discovered-skills set via their respective junctions (#160). One Codex-vs-Claude nuance — relevant to **app-launcher#251**, which injects `/issue-start <N>`: Codex's client only treats a message as a slash command when the *whole* message is a registered client command, so a bare `/issue-start 160` line is rejected by the client — invoke a skill mid-message (`check this /screen 3`) or in natural language (`start issue 160`) and it fires. The launcher should inject the natural-language/mid-message form for non-Claude agents.

**Codex sandbox verification.** Sandboxed `codex exec` depends on Codex's Windows sandbox helper binaries under `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\`, which are managed by Codex rather than this repo. If sandboxed sub-agent delegation starts failing with `orchestrator_helper_launch_failed` or `codex-windows-sandbox-setup.exe ... error=program not found`, verify the live runtime path instead of guessing from files on disk:

```powershell
.\install.ps1 -VerifyCodexSandbox
```

That opt-in check runs a real `codex exec --sandbox workspace-write` probe and fails loudly if the helper cannot launch. The same probe can be run directly when debugging:

```powershell
codex exec --sandbox workspace-write "Print exactly: codex-sandbox-ok"
```

If `workspace-write` is broken but Codex still needs to be spawned for urgent fleet work, `--sandbox danger-full-access` is the temporary helper-free fallback. Treat that as a workaround to unblock the run, then repair or update the Codex install and re-run the workspace-write verification before relying on sandboxed sub-agents again.

**Codex statusline parity.** Claude Code's statusline is a custom command (`statusline-command.ps1`) that receives a JSON payload including `context_window.used_percentage`, so it can render `used % | model | project (branch)` with color thresholds. Codex does not use that command surface; it has a native TUI footer configured by `/statusline` or by `~/.codex/config.toml`:

```toml
[tui]
status_line = ["context-used", "model", "current-dir", "git-branch"]
terminal_title = ["project", "git-branch", "model"]
```

This is the closest supported Codex shape today: context first, then model, current directory, and branch. It deliberately uses Codex's built-in `context-used` item because Codex does not currently expose a custom statusline command payload or a bare percentage item equivalent to Claude's compact `0%` segment. To validate locally, restart/open a fresh Codex TUI session and confirm the footer shows those four segments in that order; use `/statusline` inside Codex if you want to inspect or reorder the saved footer interactively.

**Pi statusline parity.** Pi exposes footer replacement through its extension API, so this repo ships `pi/extensions/statusline.ts`, installed as `~/.pi/agent/extensions/statusline.ts`. It renders the Claude-style compact footer: `ctx% | model | dir (branch)`, with the same context thresholds (`<30` success/green, `30–34` warning/yellow, `>=35` error/red) and model-family normalization (`opus` / `sonnet` / `haiku`). Known gap: Pi reports context usage as unknown immediately after compaction and before the next usage-bearing response; in that state the extension omits the context segment rather than faking a percentage, matching Claude's "null means omit" behavior. To validate locally, run `install.ps1`, restart/open a fresh Pi TUI session, and confirm the footer shows the compact three-segment line once context usage is available.

**Codex Browser plugin.** Codex's bundled Browser plugin loads its *instructions* even when its *runtime backend* isn't live — `agent.browsers.list()` can return `[]` despite the plugin being enabled in `config.toml`. That backend is Codex-client/runtime state, not a dependency this repo installs. [`docs/codex-browser.md`](docs/codex-browser.md) is the durable note: how to diagnose `iab` availability from a session, why installed plugin files don't mean a registered backend, and the restart-and-check recovery path.

Edits on either side are visible on the other instantly — no copy step, no sync ritual. The installer is idempotent:
- existing link pointing at the repo → no-op
- existing real file/directory → refuses and prints a one-line "rename it, then re-run"

After `install.ps1`, merge the `hooks` and `env` blocks from `settings.template.json` into your `~/.claude/settings.json` and ensure `statusLine.command` runs `~/.claude/statusline-command.ps1`. Restart Claude Code to pick up the new hooks. The `env` block sets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`, which fires auto-compaction at 40% of the active context window (≈ 400k on the 1M Opus window); the statusline shows the **used** context % color-coded green/yellow/red as you approach that line.

### Migrating an existing `~/.claude/`

If you already have `~/.claude/CLAUDE.md` or `~/.claude/statusline-command.ps1` as real files, the installer refuses with "rename it, then re-run". Move them aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.claude\CLAUDE.md              $env:USERPROFILE\.claude\CLAUDE.md.old
Move-Item $env:USERPROFILE\.claude\statusline-command.ps1 $env:USERPROFILE\.claude\statusline-command.ps1.old
.\install.ps1   # UAC prompt
# verify both symlinks resolve to the repo, then:
Remove-Item $env:USERPROFILE\.claude\CLAUDE.md.old
Remove-Item $env:USERPROFILE\.claude\statusline-command.ps1.old
```

Same story for `~/.agents/skills` (the Codex location): if Codex previously *migrated* the skills there as real copies, the installer refuses to clobber the real directory. Move it aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.agents\skills $env:USERPROFILE\.agents\skills.old
.\install.ps1   # creates the ~/.agents/skills junction (no UAC — junctions need none)
# verify ~/.agents/skills resolves to the repo, then:
Remove-Item $env:USERPROFILE\.agents\skills.old -Recurse -Force
```

Same for `~/.codex` if Codex was bootstrapped with hand-copied files (a real `AGENTS.md`, a real `hooks/` dir, a hand-written `hooks.json`). The installer refuses to clobber real files, so move them aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.codex\AGENTS.md  $env:USERPROFILE\.codex\AGENTS.md.old
Move-Item $env:USERPROFILE\.codex\hooks      $env:USERPROFILE\.codex\hooks.old
Move-Item $env:USERPROFILE\.codex\hooks.json $env:USERPROFILE\.codex\hooks.json.old
.\install.ps1   # one UAC prompt for the AGENTS.md + hooks.json symlinks; the hooks/ + prompts/ junctions need none

# confirm every ~/.codex link resolves back to the repo before deleting the .old copies:
'AGENTS.md','hooks','prompts','hooks.json' | ForEach-Object {
    $p = "$env:USERPROFILE\.codex\$_"; "{0,-11} -> {1}" -f $_, (Get-Item $p -Force).Target
}

Remove-Item $env:USERPROFILE\.codex\AGENTS.md.old, $env:USERPROFILE\.codex\hooks.json.old -Force
Remove-Item $env:USERPROFILE\.codex\hooks.old -Recurse -Force
```

Keeping `codex-hooks.json` byte-identical to the `hooks.json` Codex already trusts means the symlink swap doesn't re-trigger Codex's hook-trust prompt.

## Uninstall

```powershell
.\uninstall.ps1
```

Removes only the junctions/symlinks the installer created (recorded in `~/.claude/.fleet-config-installed.json`). Never touches real data.

## Inspiration

- [garytan-stack](https://github.com/anthropics/skills) and similar community skills/hooks collections.
- Anthropic's [Claude Code hooks docs](https://docs.claude.com/en/docs/claude-code/hooks).

## License

MIT.
