# claude-config

Versioned home for my user-scope [Claude Code](https://docs.claude.com/en/docs/claude-code) configuration — the hooks, helpers, skills, slash commands, statusline, and global `CLAUDE.md` that live in `~/.claude/` and shape how Claude behaves across every project on my machine.

The hooks here are project-aware via a single `hooks/projects.toml` registry: generic at code level, per-project nuance (ports, pre-ship gate triggers, "never kill these ports") in one TOML file.

## Why this repo exists

`~/.claude/` is a kitchen sink — cache, transcripts, plans — so it can't all be a git repo. But the *config* inside it (hooks, skills, the global `CLAUDE.md`) is real source code: it shapes every Claude session, breaks silently when typoed, and needs to be reviewed, diffed, and reverted like any other code. Before this repo, edits to `~/.claude/hooks/*` and friends were unversioned. Now they aren't.

## What's in here today (Tier 1)

8 hooks under `hooks/` that enforce the rituals I kept correcting Claude on, across the home-stack fleet:

| Hook | Event | What it does |
|---|---|---|
| `pre_commit_no_ai_trailer.py` | `PreToolUse` on `Bash` | Blocks `git commit` messages that include `Co-Authored-By: Claude`, `Generated with Claude Code`, etc. |
| `secret_scan_guard.py` | `PreToolUse` on `Bash` | Blocks `git commit` when a live credential is staged — scans `git diff --cached` and the command string for real Slack bot tokens (`xoxb-…`); placeholder forms (`xoxb-…`, `xoxb-<token>`) in docs are deliberately allowed and never trip it. |
| `gh_body_file_guard.py` | `PreToolUse` on `Bash` | Non-blocking nudge: an inline `gh issue/pr create\|comment\|edit --body` carrying a heredoc/backtick (Bash mangles it) → use `--body-file <tmp>`; a PowerShell here-string (`@'…'@`) run through the Bash tool → wrong shell. `--body-file`/`-F` and plain `gh` reads pass silently. |
| `safe_kill_guard.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks blanket `Stop-Process -Name python(w)?` (would nuke sister hubs), `git push --force` to main, `--no-verify`. Port-scoped kills against the project's own webapp port pass through. |
| `venv_discipline.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks `python -m venv venv` (the user's canonical name is `.venv`), `.\.venv\Scripts\activate`, bare `python`/`pip` when a project `.venv` exists. |
| `py_syntax_check.py` | `PostToolUse` on `Edit` / `Write` for `*.py` | Runs `py_compile` against the project's `.venv` and surfaces syntax errors inline. ~50 ms per edit. |
| `restart_and_verify_webapp.py` | slash command `/restart-webapp` | Project-aware: looks up the webapp port from `projects.toml`, kills only that PID, then brings the webapp back — via the project's `restart_cmd` (a `WebappManager` respawn the tray adopts, for tray-owned apps) or `tray.bat` — and polls `/api/version` until `git_sha == HEAD`, reporting the new `asset_hash`. Never touches the `:8446` session-host. |
| `notify_on_idle.py` | `Notification` | Opt-in (off unless a project/`[global]` `slack_notify_channel` is set): pings Slack via `slack_notify` when a live session is **blocked** on input (a permission gate or `AskUserQuestion`), so an AFK human gets a phone notification. No-ops on the 💤 idle nag. |

Alongside the hooks, `hooks/slack_notify.py` is a shared **Slack-notify transport** (importable + CLI, stdlib-only) any skill / hook / unattended job can call to fire a real bot-identity notification — zero install via the `hooks/` junction. It posts text (`chat.postMessage`) or **uploads a file** (`--file`, via Slack's external-upload flow) — e.g. `/system-map` posts its rendered PNG with it. On top of it, `hooks/notify_complete.py` is the **deterministic skill-completion ping** the `issue-*` / fleet skills call (`--kind add|start|finish|yolo|batch|audit|cleanup|recap|finish-batch`): it builds the one canonical message and pulls the real GitHub link from `gh` in Python rather than letting the model paraphrase. The mention decision is single-sourced in `slack_notify.notify()` and defaults off (the `[global] slack_notify_mention` toggle). The full Slack story (bot helper vs session hook vs the native "Claude in Slack" remote control, plus one-time setup) is in [`docs/slack-workflow.md`](docs/slack-workflow.md).

Two more **project-wired** hooks (opt-in per project via `projects.toml` `capture = true`; currently just life-os, and wired from that project's own `settings.json` rather than user-scope) form a **conversation-memory engine**. `conversation_capture.py` (Stop) writes each finished session to markdown; `session_index.py` (SessionStart) lazily runs `conversation_index.py` to digest *settled* captures — once per conversation, after it ends — into a per-folder `index.md` (topic / decisions / open loops, pointing back at the raw capture), so a consumer knows what happened recently without bulk-loading transcripts. The digest goes through `hooks/hub_client.py`, the shared stdlib-`urllib` client for the local LLM hub (OpenAI-shape, fail-open — mirrors `slack_notify`). Routing is config-driven: `capture_routing = "flat"` (one `conversations/` + `index.md` per project, the default) or `"skills"` (per-skill dirs, routed by an `active_marker`; life-os's setup). Generic by design — any repo opts in with one `projects.toml` block.

Tier 2 (browser-stealth lint, `pwsh`-stub warn, session-start fleet status, etc.) and Tier 3 (preference enforcement) are tracked as follow-up issues — they earn their slot only after a week of Tier 1 in production.

### Skills

Beyond the issue-workflow trio (`/issue-add`, `/issue-start`, `/issue-finish`) and `/handoff-commit`, this repo also ships:

- **`/codebase-audit`** — read-only quality sweep of the resting codebase against its `CLAUDE.md` and senior-dev standards. Bundles findings into at most 6 GitHub issues (one per fixed bucket: duplication, stale/dead code, CLAUDE.md drift, maintainability, bugs, and documentation — README/`docs/` that drift from `CLAUDE.md`, repeat themselves, go stale, or omit a shipped feature), self-assigned, deduped against open issues. Complements the diff-scoped `/code-review` / `/simplify` / `/security-review`. Idempotent: a per-repo `audit-meta` **ledger issue** records the audited commit SHA + rubric hash, so a re-run over an unchanged repo short-circuits before reading any files. Each whole-repo audit also posts a counts-only `<!-- audit-snapshot -->` comment to that ledger issue — a per-category findings table (date · sha · the six buckets · total) — so opening a repo's ledger shows its findings *trajectory* over time, kept off the gate's hot path (which only reads the ledger body). Each managed issue (ledger + one per bucket) carries a hidden `<!-- audit-managed: kind=… -->` marker, and the skill upserts through `skills/_lib/audit_issue.py` — so a re-run reuses and merges into the one issue per type rather than ever filing a duplicate.
- **`/audit-fleet`** — scatter-gather wrapper that runs `/codebase-audit` across every `ferraroroberto` repo under `E:\automation\`, skipping the unchanged ones via the ledger gate, auditing the changed ones through a bounded window of up to 3 concurrent sub-agents (a single-message parallel fan-out across the whole fleet trips Anthropic's burst rate limit; the 3-wide cap is the fleet-wide Opus concurrency window defined in the global `CLAUDE.md`), and emitting one diff-based weekly digest (GitHub comment on the digest-state issue + Slack ping + stdout). Built to run unattended on a weekly app-launcher job. Alongside the rot it files, it runs a **second lens on the same read** — a *fleet practices ledger*: each sub-agent surfaces hard-won reusable solutions and generalizable-convention candidates (the inverse of a finding — an asset to remember, not rot to fix), and the orchestrator merges them into one living catalog issue in `project-scaffolding` so the best of each repo is discoverable fleet-wide.
- **`/cleanup-fleet <bucket>`** — the fix-half of the audit loop. Takes one bucket label (`documentation`, `claude-md-drift`, `bug`, …), gathers every open issue carrying it across the fleet, scores each for complexity, and fans out **one background agent per repo** sized to the work: Sonnet for easy issues (full `/issue-yolo` → merged, each firing its own PR-link ping) and Opus for complex ones (build → stop for your review, ≤3 Opus agents in flight via the fleet-wide Opus concurrency window). One agent per repo by construction (the audit files one issue per repo per bucket). `hard` mode (default) presents the plan for approval and runs both tiers; `easy`/`silent` mode runs only the Sonnet tier unattended and never auto-merges hard-scored work — safe to schedule after `/audit-fleet`. Once you've reviewed the Opus branches, `/issue-finish-batch` ships them in parallel (below).
- **`/issue-finish-batch <branches>`** — the parallel-finish step after `/cleanup-fleet` or `/issue-batch` build-and-stop. Given a set of already-reviewed branches, it fans out **one background Sonnet finisher per branch** (all at once — Sonnet is exempt from the Opus cap), each running the full `/issue-finish` flow one-shot (push, PR, CI-advisory, merge, delete branch, tray restart) and reporting back **only** on a genuine blocker a human must resolve. User-triggered, never automatic; per-branch `✅ Done` pings are kept and a single `🏁 Finished batch` roll-up closes the run. Manual `/issue-finish` per branch stays the always-available fallback.
- **`/system-map`** — regenerate the fleet **architecture map**: crawl `hooks/projects.toml` (minus `[global] architecture_ignore`), reconcile it against the single source of truth (`architecture/fleet.data.js`) and the narrative doc (`architecture/ARCHITECTURE.md`), render the light, horizontal, Janis-style infographic to `architecture/system-map.png` with headless Chrome (placeholder specs only — real hardware specs stay in the gitignored `system-map.local.js`), commit the diff when the map changed, and post the fresh image to Slack on every run. The acceptance matrix asserts the fleet ⇄ data file ⇄ doc never drift. Built to run unattended on a weekly app-launcher job.
- **`/insights-weekly`** — turn Claude Code's built-in **`/insights`** report into a week-over-week signal. `/insights` already writes a dated, self-contained `report-<timestamp>.html` into `~/.claude/usage-data/` on every run, so that series *is* the history; this skill refreshes it, hands the **newest two reports to the local LLM hub** (`127.0.0.1:8000`, via `report.py` + `extract.py`) to narrate what changed — no raw-JSON re-aggregation, and the orchestrator doesn't write the narrative itself. The result is saved as a dated, traceable note under `~/.claude/usage-data/weekly/` (user-local, never committed) and a concise digest is posted to Slack. First run captures a baseline instead of a diff; model is `INSIGHTS_DIFF_MODEL`-overridable (default `claude_sonnet`). Built to run unattended on a weekly app-launcher job (first run targeted for a Friday).

## Layout

```
claude-config/
├── README.md
├── CLAUDE.md                       # short — tells future-Claude how this repo works
├── global-CLAUDE.md                # exposed as ~/.claude/CLAUDE.md AND ~/.codex/AGENTS.md (symlinks) — agent-neutral global instructions
├── statusline-command.ps1          # exposed as ~/.claude/statusline-command.ps1 (symlink) — custom statusline (Claude only)
├── .gitignore
├── install.ps1                     # creates junctions/symlinks into three homes: ~/.claude, ~/.agents, ~/.codex
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
│   ├── restart_and_verify_webapp.py   # also exposed as /restart-webapp
│   ├── notify_on_idle.py            # Notification hook (via run-hook.ps1): opt-in Slack ping
│   ├── slack_notify.py              # shared Slack-notify transport (importable + CLI, stdlib-only)
│   ├── notify_complete.py           # deterministic skill-completion ping (issue-* skills call this)
│   ├── conversation_capture.py     # Stop hook: captures a session to markdown (projects.toml-driven, opt-in; wired from the project's own settings.json)
│   ├── session_index.py            # SessionStart hook: lazily digests settled captures into conversations/index.md
│   ├── conversation_index.py       # the indexer (lib + CLI) session_index runs; digests via the hub
│   └── hub_client.py               # shared stdlib-urllib client for the local LLM hub (OpenAI-shape, fail-open)
├── commands/                       # junction → ~/.claude/commands AND ~/.codex/prompts (Codex prompts)
├── skills/                         # junction → ~/.claude/skills AND ~/.agents/skills (Codex) — issue-* workflow, handoff-commit, codebase-audit, screen, …
├── docs/slack-workflow.md          # Slack ↔ Claude reference: bot helper, session hook, native integration
├── tests/run_acceptance.py         # drives each hook with a sample stdin payload
├── settings.template.json          # the `hooks` block to merge into your ~/.claude/settings.json (Claude)
└── codex-hooks.json                # exposed as ~/.codex/hooks.json (symlink) — Codex's hooks wiring (same run-hook.ps1 shim)
```

The live `~/.claude/settings.json` is **not** in this repo — it carries machine-local permissions and secrets. Only `settings.template.json` ships, showing the `hooks` block to copy in.

## Install

Windows + PowerShell 7+ (or 5.1):

```powershell
git clone https://github.com/ferraroroberto/claude-config.git
cd claude-config
.\install.ps1
```

`install.ps1` exposes the repo's contents inside **three homes** — `~/.claude` (Claude Code), `~/.agents` (the cross-agent skills location), and `~/.codex` ([Codex](https://developers.openai.com/codex/)'s own home) — via two link kinds:

- **Junctions** for the directory entries. Cross-volume OK, no admin. `hooks/` and `commands/` are each junctioned into **both** `~/.claude` and `~/.codex`, and `skills/` into `~/.claude/skills` + `~/.agents/skills` — so both agents load the *same live files* and nothing can drift between them.
- **Symlinks** for the single-file entries (`global-CLAUDE.md` → both `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`; `codex-hooks.json` → `~/.codex/hooks.json`; `statusline-command.ps1`). Cross-volume file linking on Windows requires admin or Developer Mode, so the installer self-elevates with **one UAC prompt** the first time it needs to create them. Reinstalls that find the symlinks already in place stay UAC-free.

### Codex parity — one source, both agents

The same files drive Claude Code and Codex; editing once is live in both. The seams:

| What | Claude Code | Codex | Link |
|---|---|---|---|
| Global instructions | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | symlink → `global-CLAUDE.md` |
| Hooks (code) | `~/.claude/hooks/` | `~/.codex/hooks/` | junction → `hooks/` |
| Hooks (wiring) | `settings.json` (merge `settings.template.json`) | `~/.codex/hooks.json` | symlink → `codex-hooks.json` |
| Skills | `~/.claude/skills/` | `~/.agents/skills/` | junction → `skills/` |
| Slash commands / prompts | `~/.claude/commands/` | `~/.codex/prompts/` | junction → `commands/` |
| Statusline | `~/.claude/statusline-command.ps1` | *(no equivalent)* | Claude only |

`global-CLAUDE.md` is **agent-neutral**: it reads correctly as either file, and the few genuinely Claude-specific sections (the 3-wide Opus sub-agent cap; the Git-Bash-strips-backslashes-in-`settings.json` gotcha) are marked *(Claude Code only — skip on other agents)*. Unlike Claude's `settings.json` (which mixes in machine-local secrets and so stays a manual merge), Codex's `hooks.json` is hooks-only, so it is symlinked live from `codex-hooks.json` — the same `run-hook.ps1` shim runs on both agents (Codex does **not** route hooks through Git Bash, so its command paths may use backslashes).

**Why Codex skills live in `~/.agents/skills`, not `~/.codex/skills`.** This trips people up because Codex *does* have a `~/.codex/skills/` directory — but that's Codex-owned: it holds Codex's own bundled skills under `~/.codex/skills/.system/` (imagegen, skill-creator, skill-installer, …), marked with a `.codex-system-skills.marker`. The [official skills doc](https://developers.openai.com/codex/skills) lists `$HOME/.agents/skills` as *the* USER skill location — `~/.codex/skills` is **not** a documented user path. So the `~/.agents/skills` junction is correct, and we deliberately do **not** also junction into `~/.codex/skills`: it can't be a whole-directory junction anyway (the installer won't clobber the real `.system/` dir), and exposing each skill via two scanned roots would double-list every skill in Codex's selector (Codex doesn't merge same-named skills — both appear). If you see Codex *guess* a `~/.codex/skills/.system/<name>/SKILL.md` path and 404 before reading the skill from `E:\…\claude-config\skills`, that's the model fumbling the path once and self-correcting, not a broken link — the skill still loads.

**Validated:** Codex (gpt-5.5) loads and runs these skills live — e.g. invoking the `screen` skill executed `skills/screen/SKILL.md` straight from the repo. One Codex-vs-Claude nuance: Codex's client only treats a message as a slash command when the *whole* message is a registered client command, so a bare `/screen` line is rejected by the client — invoke a skill mid-message (`check this /screen 3`) or in natural language (`run the codebase audit`) and it fires.

**Codex Browser plugin.** Codex's bundled Browser plugin loads its *instructions* even when its *runtime backend* isn't live — `agent.browsers.list()` can return `[]` despite the plugin being enabled in `config.toml`. That backend is Codex-client/runtime state, not a dependency this repo installs. [`docs/codex-browser.md`](docs/codex-browser.md) is the durable note: how to diagnose `iab` availability from a session, why installed plugin files don't mean a registered backend, and the restart-and-check recovery path.

Edits on either side are visible on the other instantly — no copy step, no sync ritual. The installer is idempotent:
- existing link pointing at the repo → no-op
- existing real file/directory → refuses and prints a one-line "rename it, then re-run"

After `install.ps1`, merge the `hooks` block from `settings.template.json` into your `~/.claude/settings.json` and ensure `statusLine.command` runs `~/.claude/statusline-command.ps1`. Restart Claude Code to pick up the new hooks.

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

Removes only the junctions/symlinks the installer created (recorded in `~/.claude/.claude-config-installed.json`). Never touches real data.

## Inspiration

- [garytan-stack](https://github.com/anthropics/skills) and similar community skills/hooks collections.
- Anthropic's [Claude Code hooks docs](https://docs.claude.com/en/docs/claude-code/hooks).

## License

MIT.
