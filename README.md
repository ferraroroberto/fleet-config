# fleet-config

Versioned home for my user-scope [Claude Code](https://docs.claude.com/en/docs/claude-code) configuration — the hooks, helpers, skills, slash commands, statusline, and global `CLAUDE.md` that live in `~/.claude/` and shape how Claude behaves across every project on my machine.

The hooks here are project-aware via a single `hooks/projects.toml` registry: generic at code level, per-project nuance (ports, pre-ship gate triggers, "never kill these ports") in one TOML file.

Shared guards emit the calling harness's refusal format. Codex `PreToolUse` uses a structured denial; its installed-runtime control/refusal probe is documented in [Adding a coding harness](docs/adding-a-coding-harness.md#codex-refusal-conformance).

Project and package skills now receive individual discovery links for Claude and Codex/Pi at their existing scope. `install.ps1` reconciles registered checkouts; `install.ps1 -ProjectRoot <checkout>` handles one checkout or worktree without changing user homes. Existing real sources and conflicting names are preserved and reported. See [scoped discovery and instruction verification](docs/install.md#scoped-project-discovery).

Python syntax feedback covers every surviving Python target in a successful Codex patch, including renames. Unknown targets or outcomes are explicitly unverified; the [shared edit contract and disposable runtime probe](docs/adding-a-coding-harness.md#shared-edit-events-and-codex-syntax-feedback) document the supported payload.

Codex also carries the applicable command and edit policies from Claude Code: GitHub body quoting, dated-doc blocking, branch-before-edit enforcement, local-hub routing, and browser-launch safety. Their observed block/advice semantics and explicit unsupported surfaces are recorded in the [cross-agent policy coverage table](docs/cross-agent-parity.md#command-and-edit-policy-coverage).

Shared issue/audit workflows bind delegation, result collection and questions to the current session through the [interactive capability contract](docs/workflow-capabilities.md). Claude and Codex native multi-worker proofs are recorded per surface; independent-review and human-review gates remain mandatory when tools are absent.

## Why this repo exists

`~/.claude/` is a kitchen sink — cache, transcripts, plans — so it can't all be a git repo. But the *config* inside it (hooks, skills, the global `CLAUDE.md`) is real source code: it shapes every Claude session, breaks silently when typoed, and needs to be reviewed, diffed, and reverted like any other code. Before this repo, edits to `~/.claude/hooks/*` and friends were unversioned. Now they aren't.

## What's in here

Reference material lives in [`docs/`](docs), one file per topic, so starting
work in this repo doesn't cost the whole catalogue:

- **[`docs/hooks.md`](docs/hooks.md)** — the Tier 1 hook reference: every hook
  in `hooks/`, what it blocks or nudges, its `projects.toml` keys, and why it
  exists. Also the tooling that ships in that tier: the context filter, the
  Telegram notify transport and the deterministic completion ping, the
  conversation-memory engine, and the Pi usage bridge.
- **[`docs/skills.md`](docs/skills.md)** — the three skill tiers (global
  `skills/`, fleet-only `.claude/skills/`, and a subsystem's own
  directory-scoped `.claude/skills/`), the full per-skill inventory, the
  scheduled-run adapter, and the cross-skill `_lib/` primitives:
  claim-or-worktree concurrency, the issue lifecycle marker, the
  UX-conformance gate, the deploy-coverage gate, and stranded index locks.
- **[`docs/cross-agent-parity.md`](docs/cross-agent-parity.md)** — the
  per-agent capability matrix (Claude Code · Codex · Pi · Copilot · Grok ·
  Antigravity), what is wired and what is a documented non-goal, the
  verification probes, and statusline parity.
- **[`docs/install.md`](docs/install.md)** — the link table `install.ps1`
  creates, the other surfaces this repo ships (`tray/`, `stream-deck/`), and
  the migration recipes for an agent home that already holds real files.
- **[`docs/fleet-private-backup.md`](docs/fleet-private-backup.md)** — the
  daily backup of everything git ignores, plus the relocated runtime-data
  root (`C:/sqlite`) that git-derived selection cannot see, including the
  restore procedure.
- **[`docs/architecture.mmd`](docs/architecture.mmd)** — this repo's own
  internal structure diagram, hand-authored and under a same-PR anti-staleness
  contract.

The rest of `docs/` is one file per durable topic — see the directory.

## Layout

```
fleet-config/
├── README.md
├── CLAUDE.md                       # short — tells future-Claude how this repo works
├── global-CLAUDE.md                # exposed as ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.pi/agent/AGENTS.md, ~/.copilot/copilot-instructions.md (symlinks) — agent-neutral global instructions
├── design.md                       # exposed as ~/.claude/design.md (symlink) — fleet web-app design system (light); navigation + interaction contract (rationale + references: docs/design-system.md)
├── design.dark.md                  # exposed as ~/.claude/design.dark.md (symlink) — same token names, dark values (Vercel light/dark convention)
├── statusline-command.ps1          # exposed as ~/.claude/statusline-command.ps1 (symlink) — custom statusline (Claude only)
├── codex_statusline.py             # opt-in, comment-preserving merge of native Codex footer fields into ~/.codex/config.toml
├── .gitignore
├── install.ps1                     # creates junctions/symlinks into the agent homes: ~/.claude, ~/.agents, ~/.codex, ~/.pi/agent, ~/.copilot; also wires shell/claude-otel-project.ps1 into $PROFILE
├── uninstall.ps1                   # mirror of install.ps1: the manifest links + the $PROFILE OTel block, agy plugin, and copilot hook it writes outside it
├── shell/
│   └── claude-otel-project.ps1     # dot-sourced from $PROFILE — wraps `claude` to auto-tag OTEL_RESOURCE_ATTRIBUTES with the repo name (docs/otel-project-attribution.md)
├── hooks/                          # junction → ~/.claude/hooks AND ~/.codex/hooks (Codex)
│   ├── _lib.py                     # shared: project detection, port→PID, stdin-JSON, projects.toml loader
│   ├── projects.toml               # per-project nuance (ports, gate triggers, never-kill ports)
│   ├── run-hook.ps1                # Claude Code shim — settings.template.json routes hooks through this
│   ├── pre_commit_no_ai_trailer.py
│   ├── secret_scan_guard.py
│   ├── gh_body_file_guard.py       # PreToolUse on Bash: nudge gh --body heredocs/backticks → --body-file; PowerShell here-strings in Bash
│   ├── bash_cmdexe_syntax_guard.py # PreToolUse on Bash: nudge cmd.exe-only syntax (%VAR%, dir /s, caret continuation) — Bash tool runs Git Bash, not cmd.exe
│   ├── bash_windows_path_guard.py  # PreToolUse on Bash: block unquoted Windows drive-letter backslash paths — Git Bash strips the backslashes
│   ├── safe_kill_guard.py
│   ├── venv_discipline.py
│   ├── py_syntax_check.py
│   ├── docs_dated_filename_guard.py   # PreToolUse on Write: block dated YYYY-MM-DD- filenames under docs/
│   ├── block_askuserquestion_chief.py # PreToolUse on AskUserQuestion: block for chief-managed sessions — enforce, don't just discourage
│   ├── branch_before_edit_guard.py    # PreToolUse on Edit|Write|MultiEdit: block launcher-dispatched edits on the default branch, resolved from the target path (gitignored targets exempt)
│   ├── hub_bypass_warn.py             # PostToolUse on *.py: nudge inline `claude -p` → route through the local hub
│   ├── browser_stealth_lint.py        # PostToolUse: nudge a browser-launch file missing the anti-bot stealth kwargs
│   ├── chief_handover_sessionstart.py # SessionStart: hand the standing fleet chief its last-written run log back (fleet-config#442)
│   ├── context_filter.py              # local deterministic output compressor used by the context-filter hook/eval
│   ├── context_filter_cli.py          # wrapper/eval CLI: shadow, rewrite, retrieve, fixture benchmark
│   ├── context_filter_hook.py         # PreToolUse rewriter: runs supported commands through the compressor (mode: see Graduation above)
│   ├── restart_and_verify_webapp.py   # also exposed as /restart-webapp
│   ├── notify_on_idle.py            # Notification hook (via run-hook.ps1): opt-in Telegram ping
│   ├── session_state.py             # UserPromptSubmit|Stop|SessionEnd: the Fleet-Board session-row engine (hooks/state/sessions-state.json)
│   ├── session_state_codex.py       # Codex working/needs-you/SessionEnd adapter → shared state
│   ├── session_state_pi.py          # thin Pi adapter → session_state (shelled out to by pi/extensions/session_state.ts)
│   ├── notify_send.py               # shared Telegram transport (importable + CLI, stdlib-only)
│   ├── slack_notify.py              # DEPRECATED shim -> notify_send.py, for sister repos that load it by path (fleet-config#540)
│   ├── notify_complete.py           # deterministic skill-completion ping (issue-* skills call this); finish/yolo carry a work-summary roll-up
│   ├── work_summary.py              # deterministic PR work-summary (file/LOC roll-up + per-file table) from `gh`, no LLM; importable + CLI
│   ├── conversation_capture.py     # Stop hook: captures a session to markdown (projects.toml-driven, opt-in; wired from the project's own settings.json)
│   ├── session_index.py            # SessionStart hook: lazily digests settled captures into conversations/index.md
│   ├── conversation_index.py       # the indexer (lib + CLI) session_index runs; digests via the hub, writes index.md + index.json
│   ├── conversation_search.py      # CLI: ranked FTS5 search over captures, returns each hit's resume command
│   ├── backup_private.py           # thin CLI shim (not a hook — a scheduled program, fleet-config#590) → hooks/backup/'s `main`
│   ├── backup/                     # the daily fleet-private backup engine: config/select/snapshot/retention/report + a thin cli.py (fleet-config#731)
│   ├── run-backup-daily.bat        # its scheduled launcher (app-launcher Job → Task Scheduler)
│   └── hub_client.py               # shared stdlib-urllib client for the local LLM hub (OpenAI-shape, fail-open)
├── tray/                           # junction → ~/.claude/tray — the ONE machine-local tray_lifecycle.ps1 (fleet-config#153)
│   └── tray_lifecycle.ps1          # canonical source: project-scaffolding; every sister tray.bat calls this one file by path
├── stream-deck/                    # source-controlled Elgato Stream Deck plugin (Node/TS, own build+tests) — fleet tray launchers, launch-target / open-coding / back / call-action actions (fleet-config#370, docs/stream-deck-plugin.md)
│   └── .claude/skills/streamdeck-deploy/  # directory-scoped skill tier (docs/skills.md) — build/link/package/profile-diff, loads only under stream-deck/
├── commands/                       # junction → ~/.claude/commands AND ~/.codex/prompts (Codex prompts)
├── pi/extensions/statusline.ts      # junction via pi/extensions/ → ~/.pi/agent/extensions — custom Pi footer/statusline
├── pi/extensions/session_state.ts   # same junction — reports Pi lifecycle events into sessions-state.json (#349)
├── agy/plugins/fleet-context-filter/   # Antigravity `agy` context-filter plugin (plugin.json + hooks.json) — installed by copy, not junction (#546); drift-guarded by tests/run_acceptance.py
├── copilot-hooks/                   # Copilot CLI hook wiring (fleet-context-filter.json: preToolUse + modifiedArgs, #547) — copied into ~/.copilot/hooks/, drift-guarded
├── skills/                         # junction → ~/.claude/skills, ~/.agents/skills (Codex+Pi), ~/.copilot/skills (Copilot) — GLOBAL tier: issue-* workflow, handoff-commit, codebase-audit, design-sync, screen, _lib/, …
├── .claude/skills/                 # project-scoped — FLEET-ONLY tier, loads only in fleet-config: audit-fleet, chief, design-sweep, cleanup-fleet, cleanup-fleet-all, context-audit, context-purge, config-map, fleet-health, insights-weekly, learning-log, sota-watch, system-map (fleet-config#161)
├── .claude/workflows/              # Workflow-tool scripts (no Bash/filesystem access) — cleanup-fleet-all.js
├── architecture/                   # fleet architecture + config maps: fleet.data.js / config.data.js (source of truth), rendered system-map.png / config-map.png / *.mmd, ARCHITECTURE.md
├── docs/                           # durable topic references, one file per topic (+ architecture.mmd, this repo's own internal diagram) — enumerating them here only goes stale; see the directory
├── tests/run_acceptance.py         # drives each hook with a sample stdin payload
├── settings.template.json          # the `hooks` block to merge into your ~/.claude/settings.json (Claude)
└── codex-hooks.json                # exposed as ~/.codex/hooks.json (symlink) — Codex hook wiring (direct Python commands)
```

The live `~/.claude/settings.json` is **not** in this repo — it carries machine-local permissions and secrets. Only `settings.template.json` ships, showing the `hooks` block to copy in.

## Install

Windows + PowerShell 7+ (or 5.1):

```powershell
git clone https://github.com/ferraroroberto/fleet-config.git
cd fleet-config
.\install.ps1
```

`install.ps1` creates junctions for the directory entries and symlinks for the
single-file entries; the full link table, the one-UAC-prompt caveat, and the
recipes for migrating an agent home that already holds real files are in
[`docs/install.md`](docs/install.md). The per-agent capability matrix — what
each agent supports, what is wired, and where a class is a deliberate non-goal
— is in [`docs/cross-agent-parity.md`](docs/cross-agent-parity.md).

To add the supported context, model, location, branch, five-hour-limit, and weekly-limit items to the native Codex terminal footer, run `.\install.ps1 -ConfigureCodexStatusline` from the primary checkout. This opt-in merge preserves existing footer items, their order, comments, `terminal_title`, and unrelated settings; see [`docs/install.md`](docs/install.md#codex-terminal-footer-opt-in).

Edits on either side are visible on the other instantly — no copy step, no sync ritual. The installer is idempotent:
- existing link pointing at the repo → no-op
- existing real file/directory → refuses and prints a one-line "rename it, then re-run"

After `install.ps1`, merge the `hooks`, `env`, and root-level `cleanupPeriodDays`/`feedbackSurveyRate`/`attribution` blocks from `settings.template.json` into your `~/.claude/settings.json` and ensure `statusLine.command` runs `~/.claude/statusline-command.ps1`. Restart Claude Code to pick up the new hooks. The `env` block sets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`, which fires auto-compaction at 70% of the active context window (700k on the 1M Opus window); the statusline shows the **used** context % color-coded green/yellow/red as you approach that line. `DISABLE_ERROR_REPORTING` opts out of Anthropic-side error reporting (unrelated to the separate `CLAUDE_CODE_ENABLE_TELEMETRY`/OTel-export env vars, which stay on to feed `local-llm-hub`); `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is **deliberately not set** — despite the name it also disables the feature-flag check Remote Control depends on, silently breaking mobile/web session pairing with no error anywhere (fleet-config#363). `cleanupPeriodDays: 365` keeps local session data a full year instead of the 30-day default; `feedbackSurveyRate: 0` suppresses the session-quality survey; `attribution: {commit: "", pr: ""}` stops Claude Code auto-appending its own commit/PR trailer (`hooks/pre_commit_no_ai_trailer.py` remains the hard backstop against a trailer showing up any other way — see fleet-config#353).

### Python interpreter — this repo's own `.venv`

Every hook, skill helper, test, and doc example in this repo invokes Python by the **absolute path to this repo's project `.venv`** — `E:/automation/fleet-config/.venv/Scripts/python.exe` (fleet-config#350). Absolute because these files run from *any* repo's cwd (the hooks/skills are junctioned into every agent's home), so an absolute interpreter path resolves identically regardless of caller; a project-owned `.venv` is preferred over depending on a specific machine-wide Python install path. It is **stdlib-only** — no third-party dependencies — so setup is one line and installs nothing:

```powershell
py -m venv .venv     # any Python >=3.12; run once from the repo root
```

Codex safety hooks treat the `Bash` tool label as an unknown execution shell and apply both PowerShell and Bash safety checks. Refusals state that uncertainty; the trusted `~/.codex/hooks/` invocation path identifies the harness without consulting inherited launcher variables. See [the shell contract](docs/adding-a-coding-harness.md#step-3--hooks-and-the-payload-contract).

The live agent wiring picks the venv up automatically, no manual path edit required: Claude Code's hooks route through the junctioned `hooks/run-hook.ps1`, which prefers the venv (falling back to a system Python only if it is absent), and Codex's `~/.codex/hooks.json` is a symlink to this repo's `codex-hooks.json`. Verify anytime with `& .\.venv\Scripts\python.exe tests/run_acceptance.py`.

## Uninstall

```powershell
.\uninstall.ps1
```

Removes the junctions/symlinks the installer created (recorded in `~/.claude/.fleet-config-installed.json`) and its registered project discovery links, plus the OTel `$PROFILE` block, the agy context-filter plugin (`agy plugin uninstall fleet-context-filter`), and `~/.copilot/hooks/fleet-context-filter.json`. Changed link targets and real directories are retained with a collision report. `uninstall.ps1 -ProjectRoot <checkout>` removes only that checkout's owned discovery links; use it before worktree teardown.

## Inspiration

- [garytan-stack](https://github.com/anthropics/skills) and similar community skills/hooks collections.
- Anthropic's [Claude Code hooks docs](https://docs.claude.com/en/docs/claude-code/hooks).

## License

MIT.
