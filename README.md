# claude-config

Versioned home for my user-scope [Claude Code](https://docs.claude.com/en/docs/claude-code) configuration — the hooks, helpers, skills, slash commands, statusline, and global `CLAUDE.md` that live in `~/.claude/` and shape how Claude behaves across every project on my machine.

The hooks here are project-aware via a single `hooks/projects.toml` registry: generic at code level, per-project nuance (ports, pre-ship gate triggers, "never kill these ports") in one TOML file.

## Why this repo exists

`~/.claude/` is a kitchen sink — cache, transcripts, plans — so it can't all be a git repo. But the *config* inside it (hooks, skills, the global `CLAUDE.md`) is real source code: it shapes every Claude session, breaks silently when typoed, and needs to be reviewed, diffed, and reverted like any other code. Before this repo, edits to `~/.claude/hooks/*` and friends were unversioned. Now they aren't.

## What's in here today (Tier 1)

6 hooks under `hooks/` that enforce the rituals I kept correcting Claude on, across the home-stack fleet:

| Hook | Event | What it does |
|---|---|---|
| `pre_commit_no_ai_trailer.py` | `PreToolUse` on `Bash` | Blocks `git commit` messages that include `Co-Authored-By: Claude`, `Generated with Claude Code`, etc. |
| `safe_kill_guard.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks blanket `Stop-Process -Name python(w)?` (would nuke sister hubs), `git push --force` to main, `--no-verify`. Port-scoped kills against the project's own webapp port pass through. |
| `venv_discipline.py` | `PreToolUse` on `Bash` / `PowerShell` | Blocks `python -m venv venv` (the user's canonical name is `.venv`), `.\.venv\Scripts\activate`, bare `python`/`pip` when a project `.venv` exists. |
| `py_syntax_check.py` | `PostToolUse` on `Edit` / `Write` for `*.py` | Runs `py_compile` against the project's `.venv` and surfaces syntax errors inline. ~50 ms per edit. |
| `restart_and_verify_webapp.py` | slash command `/restart-webapp` | Project-aware: looks up the webapp port from `projects.toml`, kills only that PID, runs `tray.bat`, polls `/api/version` until `git_sha == HEAD`, reports the new `asset_hash`. |
| `notify_on_idle.py` | `Notification` | Opt-in (off unless a project/`[global]` `slack_notify_channel` is set): pings Slack via `slack_notify` when a live session needs attention, so an AFK human gets a phone notification. |

Alongside the hooks, `hooks/slack_notify.py` is a shared **Slack-notify transport** (importable + CLI, stdlib-only) any skill / hook / unattended job can call to fire a real bot-identity notification — zero install via the `hooks/` junction. The full Slack story (bot helper vs session hook vs the native "Claude in Slack" remote control, plus one-time setup) is in [`docs/slack-workflow.md`](docs/slack-workflow.md).

Tier 2 (browser-stealth lint, `pwsh`-stub warn, session-start fleet status, etc.) and Tier 3 (preference enforcement) are tracked as follow-up issues — they earn their slot only after a week of Tier 1 in production.

### Skills

Beyond the issue-workflow trio (`/issue-add`, `/issue-start`, `/issue-finish`) and `/handoff-commit`, this repo also ships:

- **`/codebase-audit`** — read-only quality sweep of the resting codebase against its `CLAUDE.md` and senior-dev standards. Bundles findings into at most 6 GitHub issues (one per fixed bucket: duplication, stale/dead code, CLAUDE.md drift, maintainability, bugs, and documentation — README/`docs/` that drift from `CLAUDE.md`, repeat themselves, go stale, or omit a shipped feature), self-assigned, deduped against open issues. Complements the diff-scoped `/code-review` / `/simplify` / `/security-review`. Idempotent: a per-repo `audit-meta` **ledger issue** records the audited commit SHA + rubric hash, so a re-run over an unchanged repo short-circuits before reading any files.
- **`/audit-fleet`** — scatter-gather wrapper that runs `/codebase-audit` across every `ferraroroberto` repo under `E:\automation\`, skipping the unchanged ones via the ledger gate, fanning the changed ones out to parallel sub-agents, and emitting one diff-based weekly digest (Gmail draft + stdout). Built to run unattended on a weekly app-launcher job.

## Layout

```
claude-config/
├── README.md
├── CLAUDE.md                       # short — tells future-Claude how this repo works
├── global-CLAUDE.md                # exposed as ~/.claude/CLAUDE.md (symlink) — user-scope global instructions
├── statusline-command.ps1          # exposed as ~/.claude/statusline-command.ps1 (symlink) — custom statusline
├── .gitignore
├── install.ps1                     # creates junctions/symlinks: ~/.claude/<name> → repo/<name>
├── uninstall.ps1                   # removes only the links install.ps1 created, leaves ~/.claude/ otherwise untouched
├── hooks/                          # junction → ~/.claude/hooks
│   ├── _lib.py                     # shared: project detection, port→PID, stdin-JSON, projects.toml loader
│   ├── projects.toml               # per-project nuance (ports, gate triggers, never-kill ports)
│   ├── pre_commit_no_ai_trailer.py + .ps1 shim
│   ├── safe_kill_guard.py        + .ps1 shim
│   ├── venv_discipline.py        + .ps1 shim
│   ├── py_syntax_check.py        + .ps1 shim
│   ├── restart_and_verify_webapp.py + .ps1 shim   (also exposed as /restart-webapp)
│   ├── notify_on_idle.py            # Notification hook (via run-hook.ps1): opt-in Slack ping
│   └── slack_notify.py              # shared Slack-notify transport (importable + CLI, stdlib-only)
├── commands/                       # junction → ~/.claude/commands (slash commands)
├── skills/                         # junction → ~/.claude/skills (issue-* workflow, handoff-commit, codebase-audit, screen, …)
├── docs/slack-workflow.md          # Slack ↔ Claude reference: bot helper, session hook, native integration
├── tests/run_acceptance.py         # drives each hook with a sample stdin payload
└── settings.template.json          # the `hooks` block to merge into your ~/.claude/settings.json
```

The live `~/.claude/settings.json` is **not** in this repo — it carries machine-local permissions and secrets. Only `settings.template.json` ships, showing the `hooks` block to copy in.

## Install

Windows + PowerShell 7+ (or 5.1):

```powershell
git clone https://github.com/ferraroroberto/claude-config.git
cd claude-config
.\install.ps1
```

`install.ps1` exposes the repo's contents inside `~/.claude/` via three link kinds:

- **Junctions** for the directory entries (`hooks/`, `commands/`, `skills/`). Cross-volume OK, no admin.
- **Symlinks** for the single-file entries (`global-CLAUDE.md` → `~/.claude/CLAUDE.md`, `statusline-command.ps1`). Cross-volume file linking on Windows requires admin or Developer Mode, so the installer self-elevates with **one UAC prompt** the first time it needs to create them. Reinstalls that find the symlinks already in place stay UAC-free.

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

## Uninstall

```powershell
.\uninstall.ps1
```

Removes only the junctions/hardlinks the installer created (recorded in `~/.claude/.claude-config-installed.json`). Never touches real data.

## Inspiration

- [garytan-stack](https://github.com/anthropics/skills) and similar community skills/hooks collections.
- Anthropic's [Claude Code hooks docs](https://docs.claude.com/en/docs/claude-code/hooks).

## License

MIT.
